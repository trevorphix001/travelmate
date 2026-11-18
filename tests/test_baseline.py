import json

import respx

from app.baseline import agent
from app.schemas.plan import NodeMetric
from app.schemas.trip import TripRequest

from .fakes import START, demo_draft, install_repo, mock_http

REQ = TripRequest(**{k: v for k, v in demo_draft().items() if k not in ("num_days", "pace")},
                  end_date=START, pace="normal")


class Scripted:
    def __init__(self, actions):
        self.actions, self.calls = list(actions), 0

    async def __call__(self, role, schema, system, user, max_tokens, temperature=0.2, node="llm"):
        self.calls += 1
        a = self.actions.pop(0) if self.actions else {"tool": "geocode", "args": {"query": "Shillong"}}
        if callable(a):
            a = a(json.loads(user))
        return schema.model_validate(a), NodeMetric(node=node, provider="fake", llm_calls=1, prompt_tokens=50)


def pick_ids(history):
    places = next(h["result"]["places"] for h in history if isinstance(h, dict) and h.get("action", {}).get("tool")
                  == "find_places")
    return {"tool": "finish", "args": {"days": [{"date": START.isoformat(),
                                                 "place_ids": [places[0]["id"], "osm:node/invented"]}],
                                       "stay_style": "budget"}}


async def test_scripted_run_reaches_finish(monkeypatch):
    install_repo(monkeypatch)
    llm = Scripted([{"tool": "geocode", "args": {"query": "Guwahati"}},
                    {"tool": "find_places", "args": {"place": "Shillong", "interests": ["waterfalls"]}},
                    {"tool": "daily_weather", "args": {"place": "Shillong"}},
                    {"tool": "cost_lookup", "args": {}},
                    pick_ids])
    monkeypatch.setattr(agent, "complete_json", llm)
    with respx.mock(assert_all_called=False) as r:
        mock_http(r, days=1)
        res = await agent.run_baseline(REQ)
    assert res.error is None and res.steps == 5 and llm.calls == 5
    assert res.unknown_ids == 1 and len(res.plan.days[0].stops) == 1
    assert res.plan.budget.grand_total == sum(x.total for x in res.plan.budget.lines)
    assert len(res.trace) == 5


async def test_step_cap(monkeypatch):
    install_repo(monkeypatch)
    llm = Scripted([])  # never finishes
    monkeypatch.setattr(agent, "complete_json", llm)
    with respx.mock(assert_all_called=False) as r:
        mock_http(r, days=1)
        res = await agent.run_baseline(REQ)
    assert res.plan is None and res.steps == agent.MAX_STEPS and llm.calls == agent.MAX_STEPS
    assert "step limit" in res.error
