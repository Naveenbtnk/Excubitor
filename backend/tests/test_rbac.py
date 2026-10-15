"""Roles decide what a caller may change, and both ends of that are pinned here.

The failure worth guarding against is not a Viewer whose browser hides the button -- it is a
Viewer whose HTTP client does not care what the browser shows. So every write route is
exercised with a Viewer session and asserted to answer 403, and the same route is exercised
with an Operator to show the gate is a gate rather than a blanket refusal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.core.rbac import Role, at_least, rank
from app.db.session import SessionLocal
from app.main import app
from app.models import Alert, User
from app.schemas.users import PASSWORD_MIN_LENGTH
from tests.conftest import TEST_USER_PASSWORD

VIEWER = "viewer@cloudops.local"
OPERATOR = "operator@cloudops.local"
OTHER_ADMIN = "second-admin@cloudops.local"

ALERT_BODY = {
    "source": "prometheus",
    "severity": "high",
    "title": "Postgres pool exhausted",
    "detail": "orders-api is waiting on connections",
}
RUNBOOK_BODY = {
    "title": "Pool exhaustion",
    "source": "test",
    "content": "Drain idle sessions and raise the pool size in small steps.",
}

# Writes any Operator may perform. The value is the request kwargs.
#
# The sandbox used to be listed here and is not any more: asking the model about the corpus is
# reading, and what bounds it is the provider budget rather than a role -- see
# ``test_a_viewer_may_ask_the_sandbox``. Nothing was loosened by that: every route that changes
# something is still in this list.
OPERATOR_WRITES: list[tuple[str, str, dict]] = [
    ("POST", "/alerts", {"json": ALERT_BODY}),
    ("POST", "/alerts/simulate", {}),
    ("POST", "/knowledge/runbooks", {"json": RUNBOOK_BODY}),
]

# Routes that need a row to exist before they can be reached at all.
ROW_WRITES = ("ack", "triage", "post-mortem")

READS: list[str] = [
    "/alerts",
    "/alerts/scenarios",
    "/incidents",
    "/knowledge/runbooks",
    "/overview",
    # A Viewer's post-mortem list is not empty in general: it is the published documents. The
    # draft case is the permission test in test_post_mortem_workflow.py.
    "/post-mortems",
    "/auth/me",
]


async def _make_alert() -> int:
    async with SessionLocal() as session:
        alert = Alert(**ALERT_BODY, status="open")
        session.add(alert)
        await session.commit()
        return alert.id


async def _operator(client: AsyncClient, make_user: Callable[..., Awaitable[None]]) -> AsyncClient:
    await make_user(OPERATOR, Role.OPERATOR.value)
    response = await client.post(
        "/auth/login", json={"email": OPERATOR, "password": TEST_USER_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


# --- the vocabulary itself -------------------------------------------------------------


def test_roles_are_ordered_and_unknown_values_hold_nothing():
    assert at_least(Role.ADMIN.value, Role.VIEWER)
    assert at_least(Role.ADMIN.value, Role.ADMIN)
    assert at_least(Role.OPERATOR.value, Role.OPERATOR)
    assert not at_least(Role.OPERATOR.value, Role.ADMIN)
    assert at_least(Role.VIEWER.value, Role.VIEWER)
    assert not at_least(Role.VIEWER.value, Role.OPERATOR)
    # A typo, or a value from some future vocabulary, must not read as privilege.
    assert rank("root") == -1
    assert not at_least("root", Role.VIEWER)


# --- reads stay open -------------------------------------------------------------------


@pytest.mark.parametrize("path", READS)
async def test_a_viewer_may_read(
    path: str, client: AsyncClient, make_user, login_as
):
    await make_user(VIEWER, Role.VIEWER.value)
    await login_as(VIEWER)

    response = await client.get(path)

    assert response.status_code == 200, response.text


async def test_a_viewer_may_search_the_knowledge_base(client: AsyncClient, make_user, login_as):
    """Reading runbooks includes asking a question about them; only writing them is gated."""
    await make_user(VIEWER, Role.VIEWER.value)
    await login_as(VIEWER)

    response = await client.post("/knowledge/query", json={"question": "pool exhaustion"})

    assert response.status_code == 200, response.text


async def test_a_viewer_may_ask_the_sandbox(client: AsyncClient, make_user, login_as):
    """The same reading rule, for the sandbox: it spends provider budget, not state.

    What that costs is bounded twice over already -- the project-wide quota in
    ``app.services.llm.quota``, and ``demo_guard`` on the shared demo login -- so a role is the
    wrong place to bound it, and a Viewer who can read every incident may ask about one.
    """
    await make_user(VIEWER, Role.VIEWER.value)
    await login_as(VIEWER)

    chat = await client.post("/sandbox/chat", json={"message": "how do I fix this?"})
    stream = await client.post("/sandbox/stream", json={"message": "how do I fix this?"})

    assert chat.status_code == 200, chat.text
    assert stream.status_code == 200, stream.text


# --- writes are gated ------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "kwargs"), OPERATOR_WRITES)
async def test_a_viewer_cannot_write(
    method: str, path: str, kwargs: dict, client: AsyncClient, make_user, login_as
):
    await make_user(VIEWER, Role.VIEWER.value)
    await login_as(VIEWER)

    response = await client.request(method, path, **kwargs)

    assert response.status_code == 403, response.text
    assert "role" in response.json()["detail"]
    # The message names the role that would be enough, so a 403 is actionable.
    assert "operator" in response.json()["detail"]


@pytest.mark.parametrize(("method", "path", "kwargs"), OPERATOR_WRITES)
async def test_an_operator_may_write(
    method: str, path: str, kwargs: dict, client: AsyncClient, make_user
):
    await _operator(client, make_user)

    response = await client.request(method, path, **kwargs)

    assert response.status_code not in (401, 403), response.text


@pytest.mark.parametrize("action", ROW_WRITES)
async def test_a_viewer_cannot_act_on_an_existing_row(
    action: str, client: AsyncClient, make_user, login_as
):
    alert_id = await _make_alert()
    await make_user(VIEWER, Role.VIEWER.value)
    await login_as(VIEWER)

    if action == "ack":
        response = await client.post(f"/alerts/{alert_id}/ack")
    elif action == "triage":
        response = await client.post(f"/incidents/triage/{alert_id}")
    else:
        response = await client.post(f"/incidents/{alert_id}/post-mortem")

    assert response.status_code == 403, response.text


async def test_an_operator_may_acknowledge_triage_and_write_a_post_mortem(
    client: AsyncClient, make_user
):
    alert_id = await _make_alert()
    await _operator(client, make_user)

    acknowledged = await client.post(f"/alerts/{alert_id}/ack")
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "acknowledged"

    triaged = await client.post(f"/incidents/triage/{alert_id}")
    assert triaged.status_code == 201, triaged.text
    incident_id = triaged.json()["id"]

    post_mortem = await client.post(f"/incidents/{incident_id}/post-mortem")
    assert post_mortem.status_code == 201, post_mortem.text


async def test_a_write_without_a_session_is_401_not_403(client: AsyncClient):
    """Unauthenticated is a different answer from unauthorized; clients act on both."""
    response = await client.post("/alerts", json=ALERT_BODY)

    assert response.status_code == 401


# --- user management -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/users", {}),
        ("POST", "/users", {"json": {"email": "new@cloudops.local", "password": "password123"}}),
        ("PATCH", "/users/1", {"json": {"role": "admin"}}),
        ("DELETE", "/users/1", {}),
    ],
)
async def test_user_management_is_admin_only(
    method: str, path: str, kwargs: dict, client: AsyncClient, make_user, login_as
):
    await make_user(OPERATOR, Role.OPERATOR.value)
    await login_as(OPERATOR)

    response = await client.request(method, path, **kwargs)

    assert response.status_code == 403, response.text


async def test_the_seeded_admin_is_still_an_admin(authed_client: AsyncClient):
    """The upgrade path has to leave the account people actually sign in with working."""
    me = await authed_client.get("/auth/me")

    assert me.status_code == 200, me.text
    assert me.json()["role"] == "admin"

    users = await authed_client.get("/users")
    assert users.status_code == 200, users.text
    assert users.json()["total"] >= 1


async def test_an_admin_creates_a_user_who_can_then_write(
    authed_client: AsyncClient, make_user, login_as
):
    created = await authed_client.post(
        "/users",
        json={
            "email": OPERATOR,
            "password": TEST_USER_PASSWORD,
            "name": "Ops",
            "role": Role.OPERATOR.value,
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["role"] == "operator"

    await login_as(OPERATOR)
    written = await authed_client.post("/alerts", json=ALERT_BODY)
    assert written.status_code == 201, written.text


async def test_a_new_account_defaults_to_viewer(authed_client: AsyncClient, make_user, login_as):
    created = await authed_client.post(
        "/users", json={"email": VIEWER, "password": TEST_USER_PASSWORD}
    )

    assert created.status_code == 201, created.text
    assert created.json()["role"] == Role.VIEWER.value

    await login_as(VIEWER)
    assert (await authed_client.post("/alerts", json=ALERT_BODY)).status_code == 403


async def test_duplicate_emails_and_unknown_roles_are_refused(
    authed_client: AsyncClient, make_user
):
    await make_user(VIEWER, Role.VIEWER.value)

    duplicate = await authed_client.post(
        "/users", json={"email": VIEWER, "password": TEST_USER_PASSWORD}
    )
    assert duplicate.status_code == 409, duplicate.text

    unknown_role = await authed_client.post(
        "/users",
        json={"email": "root@cloudops.local", "password": TEST_USER_PASSWORD, "role": "root"},
    )
    assert unknown_role.status_code == 422, unknown_role.text

    short_password = await authed_client.post(
        "/users",
        json={"email": "short@cloudops.local", "password": "x" * (PASSWORD_MIN_LENGTH - 1)},
    )
    assert short_password.status_code == 422, short_password.text


async def test_a_role_change_takes_effect_on_the_next_request(
    authed_client: AsyncClient, make_user
):
    """No re-login: the session cookie is unchanged and the role is read per request.

    Pinning this keeps a later optimisation from moving the role into the token, where a
    demotion would not be felt until the token expired.
    """
    await make_user(OPERATOR, Role.OPERATOR.value)
    listing = await authed_client.get("/users")
    operator_id = next(
        user["id"] for user in listing.json()["items"] if user["email"] == OPERATOR
    )

    # A second session on purpose: the admin's own cookie jar must stay untouched, since the
    # point of the test is what happens to a session that is never re-authenticated.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as operator:
        signed_in = await operator.post(
            "/auth/login", json={"email": OPERATOR, "password": TEST_USER_PASSWORD}
        )
        assert signed_in.status_code == 200, signed_in.text
        assert (await operator.post("/alerts", json=ALERT_BODY)).status_code == 201

        demoted = await authed_client.patch(
            f"/users/{operator_id}", json={"role": Role.VIEWER.value}
        )
        assert demoted.status_code == 200, demoted.text

        assert (await operator.post("/alerts", json=ALERT_BODY)).status_code == 403


async def test_the_last_administrator_cannot_be_demoted(authed_client: AsyncClient):
    me = await authed_client.get("/auth/me")
    admin_id = me.json()["id"]

    refused = await authed_client.patch(f"/users/{admin_id}", json={"role": Role.VIEWER.value})

    assert refused.status_code == 400, refused.text
    assert "last administrator" in refused.json()["detail"]


async def test_the_last_administrator_cannot_be_deleted(authed_client: AsyncClient):
    """Deleting the only administrator is also deleting yourself, and the answer names the loss."""
    me = await authed_client.get("/auth/me")
    admin_id = me.json()["id"]

    refused = await authed_client.delete(f"/users/{admin_id}")

    assert refused.status_code == 400, refused.text
    assert "last administrator" in refused.json()["detail"]
    # And the account is still there to sign in with.
    assert (await authed_client.get("/auth/me")).status_code == 200


async def test_an_administrator_cannot_delete_their_own_account(
    authed_client: AsyncClient, make_user
):
    """With a second administrator standing beside them, the refusal switches to the self guard."""
    await make_user(OTHER_ADMIN, Role.ADMIN.value)
    me = await authed_client.get("/auth/me")

    refused = await authed_client.delete(f"/users/{me.json()['id']}")

    assert refused.status_code == 400, refused.text
    assert "your own account" in refused.json()["detail"]


async def test_deleting_an_account_removes_it_and_revokes_its_session(
    authed_client: AsyncClient, make_user
):
    await make_user(VIEWER, Role.VIEWER.value)
    listing = await authed_client.get("/users")
    viewer_id = next(user["id"] for user in listing.json()["items"] if user["email"] == VIEWER)

    # A session of its own: what matters is what the deleted account's cookie is worth afterwards.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as viewer:
        signed_in = await viewer.post(
            "/auth/login", json={"email": VIEWER, "password": TEST_USER_PASSWORD}
        )
        assert signed_in.status_code == 200, signed_in.text
        assert (await viewer.get("/auth/me")).status_code == 200

        deleted = await authed_client.delete(f"/users/{viewer_id}")
        assert deleted.status_code == 204, deleted.text

        # The cookie still resolves; the row it named does not, so the session is over.
        assert (await viewer.get("/auth/me")).status_code == 401

    remaining = await authed_client.get("/users")
    assert VIEWER not in [user["email"] for user in remaining.json()["items"]]


async def test_deleting_an_account_that_does_not_exist_is_404(authed_client: AsyncClient):
    response = await authed_client.delete("/users/999999")

    assert response.status_code == 404, response.text


async def test_an_admin_can_change_a_name_and_a_password(
    authed_client: AsyncClient, make_user, login_as
):
    await make_user(VIEWER, Role.VIEWER.value)
    listing = await authed_client.get("/users")
    viewer_id = next(
        user["id"] for user in listing.json()["items"] if user["email"] == VIEWER
    )

    renamed = await authed_client.patch(f"/users/{viewer_id}", json={"name": "Renamed"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed"

    rotated = "Different-Passw0rd!"
    assert (
        await authed_client.patch(f"/users/{viewer_id}", json={"password": rotated})
    ).status_code == 200
    await login_as(VIEWER, rotated)


async def test_the_database_refuses_a_role_it_does_not_know():
    """The constraint is part of the schema, not just of this application."""
    async with SessionLocal() as session:
        session.add(
            User(
                email="smuggled@cloudops.local",
                password_hash="not-a-hash",
                name="Smuggled",
                role="root",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
