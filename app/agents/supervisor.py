"""Supervisor: free text -> validated TripRequest, or one clarification question. Structured input skips the LLM."""
import json
import logging
from datetime import date, timedelta

from pydantic import BaseModel, ValidationError

from app.llm import prompts
from app.llm.client import complete_json
from app.schemas.plan import NodeMetric
from app.schemas.trip import Interest, Pace, TripRequest

log = logging.getLogger(__name__)
MAX_QUERY_CHARS = 300


class TripRequestDraft(BaseModel):
    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    num_days: int | None = None
    travellers: int | None = None
    budget_inr: int | None = None
    interests: list[str] = []
    pace: str | None = None


class SupervisorOut(BaseModel):
    request: TripRequestDraft
    missing: list[str] = []
    question: str | None = None


FIELD_LABELS = {"origin": "where you're starting from", "destination": "where you want to go",
                "start_date": "your start date", "end_date": "how many days", "travellers": "how many people",
                "budget_inr": "your total budget in Rs"}


def _question_for(fields: list[str]) -> str:
    parts = [FIELD_LABELS.get(f, f) for f in fields]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Could you tell me {joined}?"


def _friendly_error(e: ValidationError) -> str:
    err = e.errors()[0]
    loc = ".".join(str(x) for x in err["loc"])
    return f"{loc}: {err['msg']}" if loc else err["msg"].removeprefix("Value error, ")


def draft_to_request(d: TripRequestDraft) -> tuple[TripRequest | None, list[str], str | None]:
    """Python-side completion of the LLM draft. Returns (request, missing_fields, validation_error)."""
    end = d.end_date
    if end is None and d.start_date and d.num_days and d.num_days > 0:
        end = d.start_date + timedelta(days=d.num_days - 1)
    fields = {"origin": d.origin, "destination": d.destination, "start_date": d.start_date, "end_date": end,
              "travellers": d.travellers, "budget_inr": d.budget_inr}
    missing = [k for k, v in fields.items() if v in (None, "")]
    if missing:
        return None, missing, None
    interests = [i for i in (x.strip().lower() for x in d.interests) if i in Interest.__members__]
    pace = d.pace if d.pace in Pace.__members__ else Pace.normal
    try:
        return TripRequest(**fields, interests=interests, pace=pace), [], None
    except ValidationError as e:
        return None, [], _friendly_error(e)


CALENDAR_DAYS = 35


def calendar(today: date) -> list[str]:
    """Next five weeks as 'YYYY-MM-DD Weekday', so the LLM copies dates instead of doing date arithmetic."""
    return [f"{d.isoformat()} {d:%a}" for d in (today + timedelta(days=i) for i in range(CALENDAR_DAYS))]


async def parse_query(query: str, today: date) -> tuple[TripRequest | None, str | None, list[str], NodeMetric]:
    """Returns (request, question, missing, metric). Exactly one LLM call (role=extract)."""
    user = json.dumps({"TODAY": f"{today.isoformat()} {today:%A}", "CALENDAR": calendar(today),
                       "text": query[:MAX_QUERY_CHARS]})
    out, metric = await complete_json("extract", SupervisorOut, prompts.SUPERVISOR_SYSTEM, user, max_tokens=400,
                                      temperature=0, node="supervisor")
    req, missing, error = draft_to_request(out.request)
    if req:
        return req, None, [], metric
    if error:
        return None, f"That request doesn't work yet: {error}. Could you adjust it?", [], metric
    return None, _question_for(missing), missing, metric


async def supervisor_node(state: dict) -> dict:
    today = state.get("today") or date.today()
    if state.get("request_input") is not None:
        try:
            return {"request": TripRequest.model_validate(state["request_input"])}
        except ValidationError as e:
            msg = _friendly_error(e)
            return {"errors": [f"invalid request: {msg}"], "question": f"Please fix: {msg}"}
    req, question, _missing, metric = await parse_query(state.get("raw_query", ""), today)
    if req is None:
        return {"question": question, "errors": ["needs_clarification"], "trace": [metric]}
    return {"request": req, "trace": [metric]}
