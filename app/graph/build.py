"""The LangGraph pipeline (PRD section 4)."""
import logging
import time
from collections.abc import Awaitable, Callable

from langgraph.graph import END, START, StateGraph

from app.agents.aggregator import aggregator_node
from app.agents.budget import budget_node
from app.agents.geo_node import geo_node
from app.agents.itinerary import itinerary_node
from app.agents.research import research_node
from app.agents.supervisor import supervisor_node
from app.agents.transport import transport_node
from app.agents.weather_node import weather_node
from app.core.errors import AppError
from app.graph.state import TripState
from app.schemas.plan import NodeMetric

log = logging.getLogger(__name__)
NODES = ["supervisor", "geo", "research", "weather", "transport", "itinerary", "budget", "aggregator"]
Node = Callable[[dict], Awaitable[dict]]


def instrument(name: str, fn: Node) -> Node:
    """Time the node, merge its LLM metrics into one NodeMetric, and turn exceptions into state errors."""

    async def run(state: dict) -> dict:
        if state.get("errors"):
            return {}  # an earlier node failed: skip quietly so the run ends fast
        t0 = time.perf_counter()
        try:
            out = await fn(state) or {}
            ok = True
        except AppError as e:
            log.warning("node %s failed: %s", name, e)
            out, ok = {"errors": [f"{name}: {e.title}. {e.detail}".strip()]}, False
        except Exception as e:  # noqa: BLE001 - keep other branches alive; the API reports the error
            log.exception("node %s crashed", name)
            out, ok = {"errors": [f"{name}: {type(e).__name__}"]}, False
        llm = out.pop("trace", [])
        m = NodeMetric(node=name, ok=ok, ms=int((time.perf_counter() - t0) * 1000),
                       prompt_tokens=sum(x.prompt_tokens for x in llm),
                       completion_tokens=sum(x.completion_tokens for x in llm),
                       llm_calls=sum(x.llm_calls for x in llm), cached=any(x.cached for x in llm),
                       fallback_used=any(x.fallback_used for x in llm),
                       provider=next((x.provider for x in llm if x.provider), None),
                       model=next((x.model for x in llm if x.model), None))
        out["trace"] = [m]
        if "plan" in out:  # the aggregator built the plan before its own metric existed
            out["plan"].trace.append(m)
        return out

    return run


def after_supervisor(state: dict) -> str:
    return END if state.get("errors") or state.get("question") else "geo"


def after_geo(state: dict) -> list[str] | str:
    return END if state.get("errors") else ["research", "weather", "transport"]


def after_budget(state: dict) -> str:
    return "itinerary" if state.get("budget_loop") and not state.get("errors") else "aggregator"


def build_graph():
    g = StateGraph(TripState)
    for name, fn in [("supervisor", supervisor_node), ("geo", geo_node), ("research", research_node),
                     ("weather", weather_node), ("transport", transport_node), ("itinerary", itinerary_node),
                     ("budget", budget_node), ("aggregator", aggregator_node)]:
        g.add_node(name, instrument(name, fn))
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", after_supervisor, ["geo", END])
    g.add_conditional_edges("geo", after_geo, ["research", "weather", "transport", END])
    g.add_edge(["research", "weather", "transport"], "itinerary")  # join waits for all three
    g.add_edge("itinerary", "budget")
    g.add_conditional_edges("budget", after_budget, ["itinerary", "aggregator"])
    g.add_edge("aggregator", END)
    return g.compile()


def mermaid() -> str:
    return build_graph().get_graph().draw_mermaid()
