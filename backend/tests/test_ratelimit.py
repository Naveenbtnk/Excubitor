from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import User

RUNBOOK = {
    "title": "Pool exhaustion",
    "source": "manual",
    "content": "Drain idle sessions, then restart the deployment.",
}


async def test_llm_route_returns_429_once_the_budget_is_spent(
    authed_client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "llm_rate_limit", 2)

    assert (await authed_client.post("/sandbox/chat", json={"message": "one"})).status_code == 200
    assert (await authed_client.post("/sandbox/chat", json={"message": "two"})).status_code == 200

    limited = await authed_client.post("/sandbox/chat", json={"message": "three"})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert "Rate limit reached" in limited.json()["detail"]


async def test_rate_limits_are_tracked_per_scope(authed_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "llm_rate_limit", 1)

    assert (await authed_client.post("/sandbox/chat", json={"message": "one"})).status_code == 200
    assert (await authed_client.post("/sandbox/chat", json={"message": "two"})).status_code == 429

    # a separate LLM route keeps its own budget
    query = await authed_client.post("/knowledge/query", json={"question": "why?"})
    assert query.status_code == 200


async def test_embedding_and_llm_budgets_are_independent(authed_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "embedding_rate_limit", 1)
    monkeypatch.setattr(settings, "llm_rate_limit", 1)

    assert (await authed_client.post("/knowledge/runbooks", json=RUNBOOK)).status_code == 201
    assert (await authed_client.post("/knowledge/runbooks", json=RUNBOOK)).status_code == 429

    assert (await authed_client.post("/sandbox/chat", json={"message": "hi"})).status_code == 200


async def test_budgets_are_per_user(authed_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "llm_rate_limit", 1)

    async with SessionLocal() as session:
        session.add(
            User(
                email="second.operator@cloudops.local",
                password_hash=hash_password(settings.admin_password),
                name="Second Operator",
                role="admin",
            )
        )
        await session.commit()

    first = await authed_client.post("/sandbox/chat", json={"message": "first"})
    second = await authed_client.post("/sandbox/chat", json={"message": "second"})
    assert first.status_code == 200
    assert second.status_code == 429

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as other:
        login = await other.post(
            "/auth/login",
            json={"email": "second.operator@cloudops.local", "password": settings.admin_password},
        )
        assert login.status_code == 200, login.text
        assert (await other.post("/sandbox/chat", json={"message": "hi"})).status_code == 200
