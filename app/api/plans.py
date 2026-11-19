"""All /api/v1 endpoints (PRD section 6)."""
import hmac
import json
import logging
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.agents.aggregator import collect_sources, validate_plan
from app.agents.editor import edit_plan
from app.agents.supervisor import MAX_QUERY_CHARS, parse_query
from app.api.auth import optional_user, required_user
from app.api.limits import plan_rate_limit
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import run_id_var
from app.db import repo
from app.llm.providers import build_providers
from app.schemas.plan import Clarification, DayWeather, PlanDiff, PlanSummary, TripPlan
from app.schemas.trip import TripRequest
from app.tools.weather import daily_weather

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=MAX_QUERY_CHARS)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(default=None, min_length=3, max_length=MAX_QUERY_CHARS)
    request: dict | None = None  # validated as TripRequest inside the graph (clear 422 question on failure)

    @model_validator(mode="after")
    def _one(self) -> "PlanIn":
        if (self.query is None) == (self.request is None):
            raise ValueError("send exactly one of 'query' or 'request'")
        return self


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=3, max_length=300)


class EditOut(BaseModel):
    plan: TripPlan | None = None
    diff: PlanDiff | None = None
    needs_clarification: bool = False
    question: str | None = None


class CityOut(BaseModel):
    name: str
    state: str
    lat: float
    lon: float


class Health(BaseModel):
    status: str
    providers: dict[str, bool]
    db: bool
    storage: Literal["server", "browser"] = "server"  # browser: plans are kept on the user's device
    planner: str = "robust"


class StatelessEditIn(BaseModel):
    """Stateless edit: the browser sends the plan it holds; nothing is stored on the server."""
    model_config = ConfigDict(extra="forbid")
    plan: TripPlan
    instruction: str = Field(min_length=3, max_length=300)


def graph(request: Request):
    return request.app.state.graph


def initial_state(body: PlanIn) -> dict:
    s: dict = {"today": date.today()}
    if body.request is not None:
        s["request_input"] = body.request
    else:
        s["raw_query"] = body.query
    return s


def result_or_error(state: dict) -> TripPlan:
    if state.get("question"):
        raise AppError("needs_clarification", "More details needed", state["question"], 422)
    errors = state.get("errors") or []
    if errors:
        status = 422 if any(e.startswith(("geo:", "Could not find", "invalid request")) for e in errors) else 502
        raise AppError("planning_failed", "Could not build a plan", "; ".join(errors)[:500], status)
    return state["plan"]


async def persist(plan: TripPlan, user_id: str | None, run_id: str) -> TripPlan:
    try:
        await repo.insert_metrics(run_id, plan.trace)
    except Exception:  # noqa: BLE001 - metrics must not fail a plan
        log.exception("could not store node metrics")
    if user_id:
        plan.id = await repo.save_plan(user_id, plan)
    return plan


# ---- endpoints -------------------------------------------------------------
@router.get("/health", response_model=Health)
async def health() -> Health:
    configured = build_providers()
    db = await repo.ping()
    return Health(status="ok", providers={n: n in configured for n in ("nvidia", "groq", "gemini")}, db=db,
                  storage="server" if db else "browser", planner=get_settings().planner)


@router.post("/plans/parse", response_model=TripRequest | Clarification)
async def parse(body: QueryIn) -> TripRequest | Clarification:
    req, question, missing, _ = await parse_query(body.query, date.today())
    return req if req else Clarification(question=question or "Could you add more detail?", missing=missing)


@router.post("/plans", response_model=TripPlan, status_code=201, dependencies=[Depends(plan_rate_limit)])
async def create_plan(body: PlanIn, request: Request, user: Annotated[str | None, Depends(optional_user)]):
    run_id = uuid.uuid4().hex[:12]
    run_id_var.set(run_id)
    state = await graph(request).ainvoke(initial_state(body))
    return await persist(result_or_error(state), user, run_id)


@router.post("/plans/stream", dependencies=[Depends(plan_rate_limit)])
async def stream_plan(body: PlanIn, request: Request, user: Annotated[str | None, Depends(optional_user)]):
    run_id = uuid.uuid4().hex[:12]
    g = graph(request)

    async def events():
        run_id_var.set(run_id)
        final: dict = {}
        try:
            async for update in g.astream(initial_state(body), stream_mode="updates"):
                for node, delta in update.items():
                    delta = delta or {}
                    for k in ("question", "plan"):
                        if k in delta:
                            final[k] = delta[k]
                    final.setdefault("errors", []).extend(delta.get("errors") or [])
                    metric = delta["trace"][0] if delta.get("trace") else None
                    status = "skipped" if metric is None else ("done" if metric.ok else "failed")
                    ms = metric.ms if metric else 0
                    yield ServerSentEvent(json.dumps({"node": node, "status": status, "ms": ms}), event="node")
                    for w in delta.get("warnings") or []:
                        yield ServerSentEvent(json.dumps({"message": w}), event="warning")
            plan = await persist(result_or_error(final), user, run_id)
            yield ServerSentEvent(plan.model_dump_json(), event="done")
        except AppError as e:
            yield ServerSentEvent(json.dumps(e.to_dict()), event="error")
        except Exception:  # noqa: BLE001
            log.exception("stream failed")
            yield ServerSentEvent(json.dumps({"type": "internal_error", "title": "Internal server error",
                                              "detail": ""}), event="error")

    return EventSourceResponse(events(), ping=10)


@router.post("/plans/save", response_model=TripPlan, status_code=201)
async def save_existing_plan(plan: TripPlan, user: Annotated[str, Depends(required_user)]) -> TripPlan:
    """Save a trip that was planned before signing in.

    The trip comes from the browser, so it is checked with the same validators as a freshly built plan,
    and its sources list is rebuilt on the server rather than trusted."""
    try:
        validate_plan(plan)
    except AppError as e:
        raise AppError("plan_invalid", "This trip can't be saved", e.title, 422) from None
    plan = plan.model_copy(update={"id": None, "created_at": None})
    plan.sources = collect_sources(plan)
    plan.id = await repo.save_plan(user, plan)
    return plan


@router.get("/plans", response_model=list[PlanSummary])
async def list_plans(user: Annotated[str, Depends(required_user)]) -> list[PlanSummary]:
    return await repo.list_plans(user)


async def _owned(plan_id: str, user: str) -> TripPlan:
    plan = await repo.get_plan(plan_id, user)
    if plan is None:
        raise AppError("not_found", "Plan not found", "", 404)
    return plan


@router.get("/plans/{plan_id}", response_model=TripPlan)
async def get_plan(plan_id: str, user: Annotated[str, Depends(required_user)]) -> TripPlan:
    return await _owned(plan_id, user)


@router.post("/plans/{plan_id}/edit", response_model=EditOut)
async def edit(plan_id: str, body: EditIn, user: Annotated[str, Depends(required_user)]) -> EditOut:
    plan = await _owned(plan_id, user)
    res = await edit_plan(plan, body.instruction)
    if res.question:
        return EditOut(needs_clarification=True, question=res.question)
    if res.metric:
        res.plan.trace = [*res.plan.trace, res.metric]
    await repo.update_plan(plan_id, user, res.plan)
    await repo.add_edit(plan_id, body.instruction, res.diff)
    return EditOut(plan=res.plan, diff=res.diff)


@router.post("/plans/edit", response_model=EditOut, dependencies=[Depends(plan_rate_limit)])
async def edit_stateless(body: StatelessEditIn) -> EditOut:
    """Edit a plan held by the browser (stateless mode). Same validators as a saved plan; nothing persisted."""
    try:
        validate_plan(body.plan)
    except AppError as e:
        raise AppError("plan_invalid", "This trip can't be edited", e.title, 422) from None
    res = await edit_plan(body.plan, body.instruction)
    if res.question:
        return EditOut(needs_clarification=True, question=res.question)
    if res.metric:
        res.plan.trace = [*res.plan.trace, res.metric]
    return EditOut(plan=res.plan, diff=res.diff)


@router.delete("/plans/{plan_id}", status_code=204)
async def delete(plan_id: str, user: Annotated[str, Depends(required_user)]) -> Response:
    if not await repo.delete_plan(plan_id, user):
        raise AppError("not_found", "Plan not found", "", 404)
    return Response(status_code=204)


@router.get("/places/search", response_model=list[CityOut])
async def places_search(q: Annotated[str, Query(min_length=1, max_length=60)]) -> list[CityOut]:
    return [CityOut(name=c.name, state=c.state, lat=c.lat, lon=c.lon) for c in await repo.search_cities(q)]


@router.get("/weather", response_model=list[DayWeather])
async def weather(lat: Annotated[float, Query(ge=-90, le=90)], lon: Annotated[float, Query(ge=-180, le=180)],
                  start: date, end: date) -> list[DayWeather]:
    if end < start or (end - start).days > 15:
        raise AppError("bad_range", "Invalid date range", "end must be after start, at most 16 days", 422)
    return await daily_weather(SimpleNamespace(lat=lat, lon=lon), start, end)


# ---- evaluation (admin) ----------------------------------------------------
class EvalRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: Literal["graph", "baseline", "both"] = "both"
    limit: int = Field(default=3, ge=1, le=40)


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    key = get_settings().admin_key
    if not key or not x_admin_key or not hmac.compare_digest(key.get_secret_value(), x_admin_key):
        raise AppError("forbidden", "Admin key required", "", 403)


@router.post("/eval/run", status_code=202, dependencies=[Depends(require_admin)])
async def eval_run(body: EvalRunIn, tasks: BackgroundTasks) -> dict:
    from eval import report
    from eval.run import DEFAULT_DELAY, main

    systems = ["graph", "baseline"] if body.system == "both" else [body.system]

    async def job() -> None:
        await main(systems, body.limit, DEFAULT_DELAY.get(get_settings().llm_primary, 5.0))
        report.main()

    tasks.add_task(job)
    return {"started": True, "systems": systems, "limit": body.limit}


@router.get("/eval/report")
async def eval_report() -> dict:
    from eval import report

    return report.build()
