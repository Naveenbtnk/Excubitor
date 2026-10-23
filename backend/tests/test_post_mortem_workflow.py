"""The post-mortem as a document: its sections, its review state, its export and its follow-ups.

The thing under test is a workflow rather than a route. A post-mortem used to be a blob that
appeared fully formed and was readable by everyone the moment it existed; now it has structured
sections, a state it moves through, a person who wrote it, and items with owners and deadlines
hanging off it. Each test below pins one of those claims, and the ones that matter most are the
negative ones: a Viewer must not see a draft, and a write must not be reachable by forging one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import TEST_USER_PASSWORD
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.rbac import Role
from app.db.session import SessionLocal
from app.main import app

ALERT = {
    "source": "kubernetes",
    "severity": "critical",
    "title": "Database pool exhaustion",
    "detail": "Pool at 100% for 4 minutes on orders-api.",
}

VIEWER = "viewer@cloudops.local"
OPERATOR = "operator@cloudops.local"
OWNER = "owner@cloudops.local"


async def _generated(client: AsyncClient) -> dict:
    """Triage an alert and generate the post-mortem, the way the UI starts one."""
    alert_id = (await client.post("/alerts", json=ALERT)).json()["id"]
    incident_id = (await client.post(f"/incidents/triage/{alert_id}")).json()["id"]
    response = await client.post(f"/incidents/{incident_id}/post-mortem")
    assert response.status_code == 201, response.text
    return response.json()


async def _session_for(email: str, password: str = TEST_USER_PASSWORD) -> AsyncClient:
    """A second signed-in client, so the admin's own cookie jar stays untouched."""
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")
    signed_in = await client.post("/auth/login", json={"email": email, "password": password})
    assert signed_in.status_code == 200, signed_in.text
    return client


# --- generation fills the document -------------------------------------------------------


async def test_generation_fills_the_sections_and_starts_a_draft(authed_client: AsyncClient):
    document = await _generated(authed_client)

    assert document["status"] == "draft"
    assert document["published_at"] is None
    # The narrative is still the model's own text; the sections sit beside it.
    assert document["content"] == "Fake answer from the LLM."
    assert document["timeline_summary"].startswith("Pool exhaustion opened at 09:12")
    assert document["root_cause"] == "Connection leak"
    assert document["contributing_factors"] == ["Pool size unchanged since March"]
    assert document["impact"].startswith("orders-api returned 500s")
    assert document["incident_title"] == "Database pool exhaustion"
    assert document["author"] == "admin@cloudops.local"
    assert document["updated_at"]

    # The model proposes the work; it does not invent owners or deadlines for it.
    items = document["action_items"]
    assert [item["text"] for item in items] == ["Drain idle sessions", "Restart orders-api"]
    assert all(item["status"] == "open" for item in items)
    assert all(item["owner"] == "" and item["owner_id"] is None for item in items)
    assert all(item["due_date"] is None for item in items)


# --- editing -----------------------------------------------------------------------------


async def test_an_operator_edits_the_sections_and_the_follow_ups(
    authed_client: AsyncClient, make_user
):
    document = await _generated(authed_client)
    await make_user(OWNER, Role.OPERATOR.value)
    items = document["action_items"]

    edited = await authed_client.patch(
        f"/post-mortems/{document['id']}",
        json={
            "timeline_summary": "09:12 alert, 09:13 triage, 09:31 mitigated.",
            "root_cause": "The pool was sized for the old replica count.",
            "contributing_factors": [
                "Pool size unchanged since March",
                "  ",
                "No alert on waiters",
            ],
            "impact": "Four minutes of 500s on checkout.",
            "action_items": [
                {"id": items[0]["id"], "text": items[0]["text"], "status": "done"},
                {
                    "text": "Add a pool-waiter alert",
                    "owner": OWNER,
                    "due_date": "2026-11-01",
                },
                {"text": "Review pool sizing each quarter", "owner": "nobody@cloudops.local"},
            ],
        },
    )

    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["root_cause"] == "The pool was sized for the old replica count."
    # Blank factors are dropped rather than stored as empty bullets.
    assert body["contributing_factors"] == [
        "Pool size unchanged since March",
        "No alert on waiters",
    ]

    kept, added, unmatched = body["action_items"]
    assert kept["id"] == items[0]["id"] and kept["status"] == "done"
    # An owner with an account is a real reference, and the address is kept beside it.
    assert added["owner"] == OWNER
    assert added["owner_id"] is not None
    assert added["due_date"] == "2026-11-01"
    assert added["status"] == "open"
    # Somebody without an account can still be named: the reference is absent, the text is not.
    assert unmatched["owner"] == "nobody@cloudops.local"
    assert unmatched["owner_id"] is None
    # The item the form dropped is gone, not orphaned.
    assert len(body["action_items"]) == 3

    fetched = await authed_client.get(f"/post-mortems/{document['id']}")
    assert fetched.json()["action_items"] == body["action_items"]


async def test_an_action_item_can_be_closed_on_its_own(authed_client: AsyncClient):
    document = await _generated(authed_client)
    item_id = document["action_items"][0]["id"]

    closed = await authed_client.patch(
        f"/post-mortems/{document['id']}/actions/{item_id}", json={"status": "done"}
    )

    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "done"
    assert closed.json()["text"] == "Drain idle sessions"

    # An item id from another document is not addressable through this one.
    assert (
        await authed_client.patch(
            f"/post-mortems/9999/actions/{item_id}", json={"status": "done"}
        )
    ).status_code == 404


async def test_a_document_edit_must_say_what_changes(authed_client: AsyncClient):
    document = await _generated(authed_client)

    empty = await authed_client.patch(f"/post-mortems/{document['id']}", json={})
    assert empty.status_code == 422, empty.text

    unknown_status = await authed_client.patch(
        f"/post-mortems/{document['id']}", json={"status": "approved"}
    )
    assert unknown_status.status_code == 422, unknown_status.text

    blank_item = await authed_client.patch(
        f"/post-mortems/{document['id']}",
        json={"action_items": [{"text": "   "}]},
    )
    assert blank_item.status_code == 422, blank_item.text


# --- the review pipeline ------------------------------------------------------------------


async def test_publishing_records_the_transition_on_the_timeline(authed_client: AsyncClient):
    document = await _generated(authed_client)
    incident_id = document["incident_id"]

    review = await authed_client.patch(
        f"/post-mortems/{document['id']}", json={"status": "in_review"}
    )
    assert review.status_code == 200, review.text
    assert review.json()["status"] == "in_review"
    assert review.json()["published_at"] is None

    published = await authed_client.patch(
        f"/post-mortems/{document['id']}", json={"status": "published"}
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    activity = (await authed_client.get(f"/incidents/{incident_id}/activity")).json()["items"]
    # Only the review transitions: the entry that records the generation itself moves the
    # incident from triage to resolved, which is a different kind of change.
    document_states = {"draft", "in_review", "published"}
    transitions = [
        (entry["from_value"], entry["to_value"])
        for entry in activity
        if entry["kind"] == "post_mortem" and entry["from_value"] in document_states
    ]
    assert transitions == [("draft", "in_review"), ("in_review", "published")]
    assert all(entry["actor"] == "admin@cloudops.local" for entry in activity)

    # Leaving published is possible, and takes the timestamp with it.
    reverted = await authed_client.patch(
        f"/post-mortems/{document['id']}", json={"status": "draft"}
    )
    assert reverted.json()["published_at"] is None


async def test_a_viewer_sees_published_documents_and_never_a_draft(
    authed_client: AsyncClient, make_user
):
    document = await _generated(authed_client)
    await make_user(VIEWER, Role.VIEWER.value)
    viewer = await _session_for(VIEWER)

    try:
        hidden = await viewer.get(f"/post-mortems/{document['id']}")
        assert hidden.status_code == 404, hidden.text
        assert (await viewer.get("/post-mortems")).json()["items"] == []
        # The incident-scoped route answers the same way, so a viewer cannot infer that a
        # document exists from the difference between the two.
        assert (
            await viewer.get(f"/incidents/{document['incident_id']}/post-mortem")
        ).status_code == 404
        assert (
            await viewer.get(f"/post-mortems/{document['id']}/export.md")
        ).status_code == 404

        published = await authed_client.patch(
            f"/post-mortems/{document['id']}", json={"status": "published"}
        )
        assert published.status_code == 200, published.text

        listing = (await viewer.get("/post-mortems")).json()
        assert [item["id"] for item in listing["items"]] == [document["id"]]
        assert (await viewer.get(f"/post-mortems/{document['id']}")).status_code == 200
        assert (
            await viewer.get(f"/incidents/{document['incident_id']}/post-mortem")
        ).status_code == 200
        assert (await viewer.get(f"/post-mortems/{document['id']}/export.md")).status_code == 200
    finally:
        await viewer.aclose()


async def test_a_reviewer_can_read_the_draft_they_are_asked_to_publish(
    authed_client: AsyncClient, make_user
):
    """Otherwise "only Admin/Operator can publish" would be true of nobody but the author."""
    document = await _generated(authed_client)
    await make_user(OPERATOR, Role.OPERATOR.value)
    reviewer = await _session_for(OPERATOR)

    try:
        listing = await reviewer.get("/post-mortems")
        assert [item["id"] for item in listing.json()["items"]] == [document["id"]]

        published = await reviewer.patch(
            f"/post-mortems/{document['id']}", json={"status": "published"}
        )
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"
    finally:
        await reviewer.aclose()


async def test_a_viewer_cannot_edit_or_publish_a_document(
    authed_client: AsyncClient, make_user
):
    """Reading a published document is not permission to change it.

    The browser hides these controls from a Viewer; this is the test that says a Viewer with an
    HTTP client of their own is refused anyway, and that the refusal changed nothing.
    """
    document = await _generated(authed_client)
    await authed_client.patch(f"/post-mortems/{document['id']}", json={"status": "published"})
    item_id = document["action_items"][0]["id"]
    await make_user(VIEWER, Role.VIEWER.value)
    viewer = await _session_for(VIEWER)

    try:
        attempts = [
            await viewer.patch(f"/post-mortems/{document['id']}", json={"impact": "forged"}),
            await viewer.patch(
                f"/post-mortems/{document['id']}/actions/{item_id}", json={"status": "done"}
            ),
        ]
        for response in attempts:
            assert response.status_code == 403, response.text
            # The message names the role that would be enough, so a 403 is actionable.
            assert "operator" in response.json()["detail"]

        unchanged = (await viewer.get(f"/post-mortems/{document['id']}")).json()
        assert unchanged["impact"] != "forged"
        assert unchanged["action_items"][0]["status"] == "open"
    finally:
        await viewer.aclose()


async def test_the_list_can_be_narrowed_to_the_review_queue(authed_client: AsyncClient):
    first = await _generated(authed_client)
    alert_id = (await authed_client.post("/alerts", json=ALERT)).json()["id"]
    second_incident = (await authed_client.post(f"/incidents/triage/{alert_id}")).json()["id"]
    second = (await authed_client.post(f"/incidents/{second_incident}/post-mortem")).json()
    await authed_client.patch(f"/post-mortems/{second['id']}", json={"status": "published"})

    drafts = (await authed_client.get("/post-mortems", params={"status": "draft"})).json()
    assert [item["id"] for item in drafts["items"]] == [first["id"]]

    by_incident = (
        await authed_client.get("/post-mortems", params={"incident_id": second_incident})
    ).json()
    assert [item["id"] for item in by_incident["items"]] == [second["id"]]


# --- the export ---------------------------------------------------------------------------


async def test_the_export_is_the_document_teams_pass_around(authed_client: AsyncClient):
    document = await _generated(authed_client)
    await authed_client.patch(
        f"/post-mortems/{document['id']}",
        json={
            "impact": "",  # nothing recorded, which the export has to say out loud
            "action_items": [
                {"text": "Add a pool-waiter alert", "owner": "ops@cloudops.local"},
                {"text": "Fix | the pipe", "due_date": "2026-01-01", "status": "done"},
            ],
        },
    )

    response = await authed_client.get(f"/post-mortems/{document['id']}/export.md")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/markdown")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="post-mortem-{document["incident_id"]}.md"'
    )

    markdown = response.text
    assert markdown.startswith("# Post-mortem: Database pool exhaustion")
    assert "**Status:** draft" in markdown
    assert "**Author:** admin@cloudops.local" in markdown
    assert "## Summary" in markdown and "Fake answer from the LLM." in markdown
    assert "## Timeline" in markdown and document["timeline_summary"] in markdown
    assert "## Contributing factors" in markdown
    assert "- Pool size unchanged since March" in markdown
    # An empty section is reported as missing rather than dropped: a reader has to be able to
    # tell the difference between "not recorded" and "not in this file".
    assert "## Impact\n\n_Not recorded._" in markdown
    assert "| Action | Owner | Due | Status |" in markdown
    assert "| Add a pool-waiter alert | ops@cloudops.local | — | open |" in markdown
    # A pipe in the text would end the cell it is in; it is escaped instead.
    assert r"| Fix \| the pipe | — | 2026-01-01 (overdue) | done |" in markdown
    assert markdown.endswith("\n")


async def test_an_empty_document_still_exports_its_sections(authed_client: AsyncClient):
    """A post-mortem with nothing but a narrative is thin, but the shape must not wobble."""
    document = await _generated(authed_client)
    await authed_client.patch(
        f"/post-mortems/{document['id']}",
        json={
            "timeline_summary": "",
            "root_cause": "",
            "contributing_factors": [],
            "impact": "",
            "action_items": [],
        },
    )

    markdown = (await authed_client.get(f"/post-mortems/{document['id']}/export.md")).text

    assert markdown.count("_Not recorded._") == 5


# --- the overview strip -------------------------------------------------------------------


async def test_the_overview_strip_counts_open_follow_ups_oldest_first(
    authed_client: AsyncClient,
):
    document = await _generated(authed_client)
    overdue = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    soon = (datetime.now(UTC).date() + timedelta(days=7)).isoformat()

    await authed_client.patch(
        f"/post-mortems/{document['id']}",
        json={
            "action_items": [
                {"text": "Add a pool-waiter alert", "due_date": soon},
                {"text": "Right-size the pool", "due_date": overdue, "owner": "ops@cloudops.local"},
                {"text": "Already handled", "status": "done"},
            ]
        },
    )

    strip = (await authed_client.get("/overview")).json()["action_items"]

    assert strip["open"] == 2
    assert strip["overdue"] == 1
    # The oldest deadline leads, and it is the one with a date that has passed.
    assert [item["text"] for item in strip["items"]] == [
        "Right-size the pool",
        "Add a pool-waiter alert",
    ]
    assert strip["items"][0]["overdue"] is True
    assert strip["items"][1]["overdue"] is False
    assert strip["items"][0]["owner"] == "ops@cloudops.local"
    # Enough to link back to where the item came from.
    assert strip["items"][0]["post_mortem_id"] == document["id"]
    assert strip["items"][0]["incident_id"] == document["incident_id"]
    assert strip["items"][0]["incident_title"] == "Database pool exhaustion"


async def test_the_strip_does_not_leak_a_draft_to_a_viewer(
    authed_client: AsyncClient, make_user
):
    document = await _generated(authed_client)
    await authed_client.patch(
        f"/post-mortems/{document['id']}",
        json={"action_items": [{"text": "Unpublished promise", "due_date": "2026-01-01"}]},
    )
    await make_user(VIEWER, Role.VIEWER.value)
    viewer = await _session_for(VIEWER)

    try:
        hidden = (await viewer.get("/overview")).json()["action_items"]
        assert hidden["open"] == 0 and hidden["items"] == []

        await authed_client.patch(f"/post-mortems/{document['id']}", json={"status": "published"})
        visible = (await viewer.get("/overview")).json()["action_items"]
        assert visible["open"] == 1
        assert visible["items"][0]["text"] == "Unpublished promise"
        assert visible["overdue"] == 1
    finally:
        await viewer.aclose()


async def test_a_finished_item_leaves_the_strip(authed_client: AsyncClient):
    document = await _generated(authed_client)
    await authed_client.patch(
        f"/post-mortems/{document['id']}",
        json={"action_items": [{"text": "Only item", "due_date": "2026-01-01"}]},
    )
    item_id = (await authed_client.get(f"/post-mortems/{document['id']}")).json()["action_items"][
        0
    ]["id"]

    await authed_client.patch(
        f"/post-mortems/{document['id']}/actions/{item_id}", json={"status": "done"}
    )

    strip = (await authed_client.get("/overview")).json()["action_items"]
    assert strip["open"] == 0 and strip["overdue"] == 0 and strip["items"] == []


async def test_a_row_written_the_old_way_is_still_a_valid_document():
    """The new columns have to accept a row that only knows the old ones.

    This is the shape a pre-migration row has -- an incident, a narrative and nothing else --
    and the reason every added column is defaulted rather than required. It also proves the
    default is in the schema and not only in the code path that generates documents.
    """
    async with SessionLocal() as session:
        incident_id = await session.scalar(
            text(
                "INSERT INTO incidents (title, summary, root_cause, recommendation, confidence,"
                " status, created_at)"
                " VALUES ('Legacy incident', '', '', '', 0.0, 'resolved', now()) RETURNING id"
            )
        )
        await session.execute(
            text(
                "INSERT INTO post_mortems (incident_id, content, created_at)"
                " VALUES (:incident_id, 'the whole record, as it used to be', now())"
            ),
            {"incident_id": incident_id},
        )
        await session.commit()

        row = (
            await session.execute(
                text(
                    "SELECT status, author, contributing_factors, published_at, updated_at"
                    " FROM post_mortems WHERE incident_id = :incident_id"
                ),
                {"incident_id": incident_id},
            )
        ).one()

    assert row.status == "draft"
    assert row.author == ""
    assert row.contributing_factors == []
    assert row.published_at is None
    assert row.updated_at is not None
