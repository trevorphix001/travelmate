import time
import uuid
from datetime import UTC, datetime

import httpx
import jwt
import pytest
import respx

from app.api import auth, limits
from app.core.config import Settings
from app.db import repo
from app.graph.build import build_graph
from app.main import create_app
from app.schemas.plan import PlanSummary

from ..fakes import FakeLLM, install_repo, mock_http

SECRET = "test-only-secret-not-used-anywhere-else"
USER_A, USER_B = str(uuid.uuid4()), str(uuid.uuid4())


def token(sub: str, exp_in: int = 3600) -> str:
    claims = {"sub": sub, "aud": "authenticated", "exp": int(time.time()) + exp_in, "role": "authenticated"}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def bearer(sub: str) -> dict:
    return {"Authorization": f"Bearer {token(sub)}"}


class FakePlanRepo:
    def __init__(self) -> None:
        self.plans: dict[str, tuple[str, dict]] = {}
        self.edits: list = []

    def install(self, mp) -> None:
        async def save_plan(user_id, plan):
            pid = str(uuid.uuid4())
            self.plans[pid] = (user_id, plan.model_dump(mode="json", exclude={"id", "created_at"}))
            return pid

        async def get_plan(plan_id, user_id):
            row = self.plans.get(plan_id)
            if not row or row[0] != user_id:
                return None
            return repo.plan_from_row(row[1], plan_id, datetime.now(UTC))

        async def update_plan(plan_id, user_id, plan):
            if self.plans.get(plan_id, ("",))[0] == user_id:
                self.plans[plan_id] = (user_id, plan.model_dump(mode="json", exclude={"id", "created_at"}))

        async def list_plans(user_id):
            return [PlanSummary(id=k, title=v["title"], start_date=v["request"]["start_date"],
                                end_date=v["request"]["end_date"], total=v["budget"]["grand_total"],
                                created_at=datetime.now(UTC)) for k, (u, v) in self.plans.items() if u == user_id]

        async def delete_plan(plan_id, user_id):
            if self.plans.get(plan_id, ("",))[0] == user_id:
                del self.plans[plan_id]
                return True
            return False

        async def add_edit(plan_id, instruction, diff):
            self.edits.append((plan_id, instruction, diff))

        async def noop(*a, **k):
            return None

        for name, fn in [("save_plan", save_plan), ("get_plan", get_plan), ("update_plan", update_plan),
                         ("list_plans", list_plans), ("delete_plan", delete_plan), ("add_edit", add_edit),
                         ("insert_metrics", noop)]:
            mp.setattr(repo, name, fn)


@pytest.fixture
def plan_repo(monkeypatch):
    r = FakePlanRepo()
    r.install(monkeypatch)
    install_repo(monkeypatch)
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(_env_file=None, supabase_jwt_secret=SECRET))
    limits.reset()
    return r


@pytest.fixture
def llm(monkeypatch):
    from app.agents import editor

    fake = FakeLLM().install(monkeypatch)
    monkeypatch.setattr(editor, "complete_json", fake)
    return fake


@pytest.fixture
async def client(plan_repo, llm):
    app = create_app()
    app.state.graph = build_graph()
    with respx.mock(assert_all_called=False) as router:
        mock_http(router)
        router.route(host="test").pass_through()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
