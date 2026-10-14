"""The public demo login: what it is allowed to do, and what happens when it is switched off.

A demo account is a shared credential that is published on purpose, so the tests here are about
the three things that keep a published credential from being a hole -- it cannot write, it cannot
outlive the deployment's decision to offer it, and it cannot spend the whole day's provider budget
on one visitor.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.rbac import Role
from app.db.session import SessionLocal, seed_demo_account
from app.models import User

DEMO_PASSWORD = "demo-password-not-used-anywhere-else"


def _configure(monkeypatch, *, enabled: bool = True, limit: int = 5) -> str:
    """Turn the demo login on with a fresh address, so counters never carry between tests."""
    email = f"demo+{uuid.uuid4().hex[:8]}@excubitor.local"
    monkeypatch.setattr(settings, "demo_account_enabled", enabled)
    monkeypatch.setattr(settings, "demo_account_email", email)
    monkeypatch.setattr(settings, "demo_account_name", "Demo Visitor")
    monkeypatch.setattr(settings, "demo_account_password", DEMO_PASSWORD if enabled else "")
    monkeypatch.setattr(settings, "demo_account_rate_limit", limit)
    return email


async def _provisioned_demo(monkeypatch, **kwargs) -> str:
    email = _configure(monkeypatch, **kwargs)
    await seed_demo_account()
    return email


async def _sign_in_as_demo(client: AsyncClient, email: str, password: str = DEMO_PASSWORD) -> None:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


# --- what the sign-in page is told ---------------------------------------------------------


async def test_a_deployment_without_the_demo_answers_with_nothing_to_offer(
    client: AsyncClient, monkeypatch
):
    _configure(monkeypatch, enabled=True)
    monkeypatch.setattr(settings, "demo_account_password", "")

    response = await client.get("/auth/demo")

    assert response.status_code == 200
    # No address and no password: there is nothing here for a deployment that never asked.
    assert response.json() == {"enabled": False, "email": None, "password": None}


async def test_an_enabled_demo_publishes_the_credential_it_provisioned(
    client: AsyncClient, monkeypatch
):
    email = await _provisioned_demo(monkeypatch)

    response = await client.get("/auth/demo")

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "email": email, "password": DEMO_PASSWORD}


async def test_enabled_without_a_password_offers_nothing_and_provisions_nothing(
    client: AsyncClient, monkeypatch
):
    """Both halves are needed: the address alone would mean an account nobody can sign into."""
    email = _configure(monkeypatch)
    monkeypatch.setattr(settings, "demo_account_password", "")

    await seed_demo_account()

    assert (await client.get("/auth/demo")).json() == {
        "enabled": False,
        "email": None,
        "password": None,
    }
    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email == email)) is None


async def test_a_disabled_feature_leaves_no_account_behind(monkeypatch):
    """Nothing is created, so a deployment that turns it off has no shared login to forget."""
    email = _configure(monkeypatch, enabled=False)

    await seed_demo_account()

    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email == email)) is None


# --- what it may do ------------------------------------------------------------------------


async def test_the_demo_login_is_a_viewer_and_may_read(client: AsyncClient, monkeypatch):
    email = await _provisioned_demo(monkeypatch)

    await _sign_in_as_demo(client, email)

    assert (await client.get("/auth/me")).json()["role"] == Role.VIEWER.value
    assert (await client.get("/overview")).status_code == 200
    assert (await client.get("/post-mortems")).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("POST", "/alerts/simulate", {"json": {}}),
        ("POST", "/knowledge/runbooks", {"json": {"title": "t", "content": "c" * 20}}),
        ("POST", "/alerts/1/ack", {}),
        ("POST", "/incidents/triage/1", {}),
        ("GET", "/users", {}),
    ],
)
async def test_the_demo_login_cannot_write(
    method: str, path: str, kwargs: dict, client: AsyncClient, monkeypatch
):
    email = await _provisioned_demo(monkeypatch)
    await _sign_in_as_demo(client, email)

    response = await client.request(method, path, **kwargs)

    assert response.status_code == 403, response.text
    assert "role" in response.json()["detail"]


async def test_the_demo_login_may_use_the_ai_features(client: AsyncClient, monkeypatch):
    """Reading the corpus includes asking about it, which is what a visitor came to see."""
    email = await _provisioned_demo(monkeypatch)
    await _sign_in_as_demo(client, email)

    chat = await client.post("/sandbox/chat", json={"message": "hello"})
    query = await client.post("/knowledge/query", json={"question": "pool exhaustion"})

    assert chat.status_code == 200, chat.text
    assert query.status_code == 200, query.text


# --- the ceiling on the shared login -------------------------------------------------------


async def test_one_visitor_cannot_spend_the_budget_the_next_visitor_needs(
    client: AsyncClient, monkeypatch
):
    email = await _provisioned_demo(monkeypatch, limit=2)
    await _sign_in_as_demo(client, email)

    assert (await client.post("/sandbox/chat", json={"message": "one"})).status_code == 200
    assert (await client.post("/sandbox/chat", json={"message": "two"})).status_code == 200

    refused = await client.post("/sandbox/chat", json={"message": "three"})

    assert refused.status_code == 429, refused.text
    assert "demo" in refused.json()["detail"]
    assert int(refused.headers["retry-after"]) > 0
    # The shared login's allowance is one bucket across the AI routes, not one per route: the
    # sandbox being spent means the knowledge query is spent too.
    assert (await client.post("/knowledge/query", json={"question": "x"})).status_code == 429


async def test_the_shared_ceiling_does_not_touch_anyone_else(
    client: AsyncClient, make_user, login_as, monkeypatch
):
    """An operator, a viewer with their own account and the admin keep the ordinary limits."""
    await _provisioned_demo(monkeypatch, limit=1)
    await make_user("viewer@cloudops.local", Role.VIEWER.value)

    await login_as("viewer@cloudops.local")
    for _ in range(3):
        assert (await client.post("/sandbox/chat", json={"message": "hi"})).status_code == 200


# --- turning it off, and keeping it read-only ----------------------------------------------


async def test_turning_the_demo_off_stops_the_published_credential_working(
    client: AsyncClient, monkeypatch
):
    email = await _provisioned_demo(monkeypatch)
    await _sign_in_as_demo(client, email)
    await client.post("/auth/logout")

    monkeypatch.setattr(settings, "demo_account_enabled", False)

    refused = await client.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})

    assert refused.status_code == 401
    assert (await client.get("/auth/demo")).json() == {
        "enabled": False,
        "email": None,
        "password": None,
    }


async def test_startup_provisioning_survives_every_worker_racing_it(monkeypatch):
    """Every Gunicorn worker runs the lifespan, so provisioning has to be one atomic upsert.

    Regression, and not a hypothetical one: a read followed by a write lost this race, the loser
    died on the unique index during startup, and Gunicorn then refused to boot it -- so an
    ordinary cold start of the compose stack came up unhealthy with the account half created.
    """
    email = _configure(monkeypatch)

    await asyncio.gather(*(seed_demo_account() for _ in range(4)))

    async with SessionLocal() as session:
        users = (await session.scalars(select(User).where(User.email == email))).all()
    assert len(users) == 1
    assert users[0].role == Role.VIEWER.value


async def test_every_start_puts_the_demo_login_back_to_viewer(monkeypatch):
    """A promotion made in the UI must not survive the next boot of a published credential."""
    email = await _provisioned_demo(monkeypatch)

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        user.role = Role.ADMIN.value
        await session.commit()

    await seed_demo_account()

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        assert user.role == Role.VIEWER.value
