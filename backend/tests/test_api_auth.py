import pytest
from httpx import AsyncClient

from app.core.config import settings

PROTECTED_ROUTES = [
    ("GET", "/overview", {}),
    ("GET", "/alerts", {}),
    (
        "POST",
        "/alerts",
        {"json": {"source": "kubernetes", "severity": "high", "title": "t", "detail": "d"}},
    ),
    ("POST", "/alerts/simulate", {"json": {}}),
    ("GET", "/alerts/scenarios", {}),
    ("POST", "/alerts/1/ack", {}),
    ("GET", "/knowledge/runbooks", {}),
    ("POST", "/knowledge/runbooks", {"json": {"title": "t", "content": "c" * 20}}),
    (
        "POST",
        "/knowledge/runbooks/upload",
        {"files": {"file": ("runbook.txt", b"a runbook body", "text/plain")}},
    ),
    ("POST", "/knowledge/query", {"json": {"question": "how do I fix this?"}}),
    ("POST", "/sandbox/chat", {"json": {"message": "hello"}}),
    ("POST", "/sandbox/stream", {"json": {"message": "hello"}}),
    ("GET", "/incidents", {}),
    ("POST", "/incidents/triage/1", {}),
    ("GET", "/incidents/1/post-mortem", {}),
    ("POST", "/incidents/1/post-mortem", {}),
    ("GET", "/auth/me", {}),
]


async def test_health_is_public(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "provider": "gemini", "database": "ok"}


async def test_llm_health_is_public_and_reports_provider(client: AsyncClient):
    response = await client.get("/health/llm")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "provider": "gemini",
        "model": settings.gemini_model,
        "llm": "ok",
        # The probe above is exempt from the provider budget, so an untouched window is all
        # this public endpoint should be revealing.
        "quota": {
            "limit": settings.effective_llm_quota_limit,
            "used": 0,
            "remaining": settings.effective_llm_quota_limit,
            "window_seconds": settings.llm_quota_window_seconds,
            "resets_in": 0,
        },
    }


@pytest.mark.parametrize(("method", "path", "kwargs"), PROTECTED_ROUTES)
async def test_data_routes_reject_anonymous_requests(
    client: AsyncClient, method: str, path: str, kwargs: dict
):
    response = await client.request(method, path, **kwargs)
    assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


async def test_login_rejects_wrong_password(client: AsyncClient):
    response = await client.post(
        "/auth/login", json={"email": settings.admin_email, "password": "not-the-password"}
    )
    assert response.status_code == 401


async def test_login_rejects_unknown_user(client: AsyncClient):
    response = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": settings.admin_password}
    )
    assert response.status_code == 401


async def test_login_sets_http_only_session_cookie(client: AsyncClient):
    response = await client.post(
        "/auth/login", json={"email": settings.admin_email, "password": settings.admin_password}
    )
    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert settings.cookie_name in set_cookie
    assert "HttpOnly" in set_cookie
    assert response.json()["user"]["email"] == settings.admin_email


async def test_me_returns_authenticated_user(authed_client: AsyncClient):
    response = await authed_client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == settings.admin_email
    assert response.json()["role"] == "admin"


async def test_logout_revokes_the_session(authed_client: AsyncClient):
    assert (await authed_client.post("/auth/logout")).status_code == 204
    assert (await authed_client.get("/auth/me")).status_code == 401
