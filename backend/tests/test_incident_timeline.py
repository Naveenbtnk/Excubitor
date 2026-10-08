"""The incident timeline: what gets written, who may write it, and in what order it reads.

Everything here goes through the HTTP API, because the point of the feature is that a change
made by one person is visible to the next one -- a unit test of the insert would not notice a
route that forgot to record anything.
"""

import pytest
from httpx import AsyncClient

from app.core.rbac import Role

ALERT = {
    "source": "kubernetes",
    "severity": "critical",
    "title": "Database pool exhaustion",
    "detail": "Pool at 100% for 4 minutes on orders-api.",
}

VIEWER = "viewer@cloudops.local"
OPERATOR = "operator@cloudops.local"


async def triaged(client: AsyncClient) -> int:
    """Create an alert and triage it, returning the incident id."""
    alert_id = (await client.post("/alerts", json=ALERT)).json()["id"]
    response = await client.post(f"/incidents/triage/{alert_id}")
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def activity(client: AsyncClient, incident_id: int) -> list[dict]:
    response = await client.get(f"/incidents/{incident_id}/activity")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_triage_is_the_first_entry_on_the_timeline(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)

    entries = await activity(authed_client, incident_id)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kind"] == "created"
    assert entry["actor"] == "admin@cloudops.local"
    assert entry["from_value"] is None
    assert entry["to_value"] == "triage"
    assert "75% confidence" in entry["body"]


async def test_a_status_change_records_both_sides(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)

    response = await authed_client.patch(
        f"/incidents/{incident_id}", json={"status": "investigating"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "investigating"

    entry = (await activity(authed_client, incident_id))[-1]
    assert entry["kind"] == "status"
    assert entry["from_value"] == "triage"
    assert entry["to_value"] == "investigating"
    assert entry["actor"] == "admin@cloudops.local"


async def test_resaving_the_same_status_is_not_an_event(authed_client: AsyncClient):
    """The timeline is read to find out what changed, so a no-op must not appear on it."""
    incident_id = await triaged(authed_client)

    assert (
        await authed_client.patch(f"/incidents/{incident_id}", json={"status": "triage"})
    ).status_code == 200

    assert [entry["kind"] for entry in await activity(authed_client, incident_id)] == ["created"]


async def test_assigning_and_unassigning_are_both_recorded(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)

    assigned = await authed_client.patch(
        f"/incidents/{incident_id}", json={"assignee": "night-ops@cloudops.local"}
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee"] == "night-ops@cloudops.local"

    cleared = await authed_client.patch(f"/incidents/{incident_id}", json={"assignee": None})
    assert cleared.json()["assignee"] is None

    entries = await activity(authed_client, incident_id)
    assignee_entries = [entry for entry in entries if entry["kind"] == "assignee"]
    assert len(assignee_entries) == 2
    assert assignee_entries[0]["to_value"] == "night-ops@cloudops.local"
    assert "Assigned to night-ops@cloudops.local." in assignee_entries[0]["body"]
    assert assignee_entries[1]["from_value"] == "night-ops@cloudops.local"
    assert assignee_entries[1]["to_value"] is None
    assert assignee_entries[1]["body"] == "Unassigned from night-ops@cloudops.local."


async def test_an_empty_assignee_field_clears_it(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)
    await authed_client.patch(f"/incidents/{incident_id}", json={"assignee": "someone"})

    response = await authed_client.patch(f"/incidents/{incident_id}", json={"assignee": "   "})

    assert response.status_code == 200
    assert response.json()["assignee"] is None


async def test_a_comment_is_recorded_with_its_author(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)

    created = await authed_client.post(
        f"/incidents/{incident_id}/comments", json={"body": "  Failing over to the replica.  "}
    )
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "comment"
    # Stripped on the way in: leading whitespace in a chat-style entry is invisible otherwise.
    assert created.json()["body"] == "Failing over to the replica."
    assert created.json()["actor"] == "admin@cloudops.local"
    assert created.json()["from_value"] is None

    timeline = await activity(authed_client, incident_id)
    assert timeline[-1]["body"] == "Failing over to the replica."


async def test_an_empty_comment_is_refused(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)

    response = await authed_client.post(f"/incidents/{incident_id}/comments", json={"body": "   "})

    assert response.status_code == 422
    assert len(await activity(authed_client, incident_id)) == 1


async def test_the_timeline_reads_forwards(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)
    await authed_client.patch(f"/incidents/{incident_id}", json={"status": "investigating"})
    await authed_client.post(f"/incidents/{incident_id}/comments", json={"body": "Third entry"})

    entries = await activity(authed_client, incident_id)

    assert [entry["kind"] for entry in entries] == ["created", "status", "comment"]
    assert entries == sorted(entries, key=lambda entry: entry["id"])
    assert [entry["id"] for entry in entries] == sorted(entry["id"] for entry in entries)


async def test_generating_a_post_mortem_resolves_the_incident_on_the_timeline(
    authed_client: AsyncClient,
):
    incident_id = await triaged(authed_client)

    generated = await authed_client.post(f"/incidents/{incident_id}/post-mortem")
    assert generated.status_code == 201

    entry = (await activity(authed_client, incident_id))[-1]
    assert entry["kind"] == "post_mortem"
    assert entry["from_value"] == "triage"
    assert entry["to_value"] == "resolved"
    assert (await authed_client.get(f"/incidents/{incident_id}")).json()["status"] == "resolved"


async def test_a_post_mortem_that_already_exists_adds_no_entry(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)
    await authed_client.post(f"/incidents/{incident_id}/post-mortem")
    before = len(await activity(authed_client, incident_id))

    await authed_client.post(f"/incidents/{incident_id}/post-mortem")

    assert len(await activity(authed_client, incident_id)) == before


async def test_the_actor_is_the_person_who_acted(authed_client: AsyncClient, make_user, login_as):
    incident_id = await triaged(authed_client)
    await make_user(OPERATOR, Role.OPERATOR.value, name="Ops Person")
    await login_as(OPERATOR)

    await authed_client.patch(f"/incidents/{incident_id}", json={"status": "mitigated"})
    await authed_client.post(f"/incidents/{incident_id}/comments", json={"body": "Over to you."})

    entries = await activity(authed_client, incident_id)
    assert [entry["actor"] for entry in entries][-2:] == [OPERATOR, OPERATOR]


async def test_an_unknown_status_is_refused(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)

    response = await authed_client.patch(f"/incidents/{incident_id}", json={"status": "on fire"})

    assert response.status_code == 422


async def test_a_patch_with_nothing_in_it_is_refused(authed_client: AsyncClient):
    incident_id = await triaged(authed_client)

    assert (await authed_client.patch(f"/incidents/{incident_id}", json={})).status_code == 422


async def test_incident_endpoints_return_404_for_an_unknown_incident(authed_client: AsyncClient):
    assert (await authed_client.get("/incidents/9999")).status_code == 404
    assert (await authed_client.get("/incidents/9999/activity")).status_code == 404
    assert (
        await authed_client.patch("/incidents/9999", json={"status": "resolved"})
    ).status_code == 404
    assert (
        await authed_client.post("/incidents/9999/comments", json={"body": "hello"})
    ).status_code == 404


@pytest.mark.parametrize("path", ["/incidents", "/incidents/{id}/activity"])
async def test_a_viewer_may_read_the_timeline(
    path: str, authed_client: AsyncClient, make_user, login_as
):
    incident_id = await triaged(authed_client)
    await make_user(VIEWER, Role.VIEWER.value)
    await login_as(VIEWER)

    response = await authed_client.get(path.format(id=incident_id))

    assert response.status_code == 200


async def test_a_viewer_cannot_change_or_comment_on_an_incident(
    authed_client: AsyncClient, make_user, login_as
):
    incident_id = await triaged(authed_client)
    await make_user(VIEWER, Role.VIEWER.value)
    await login_as(VIEWER)

    refused = [
        await authed_client.patch(f"/incidents/{incident_id}", json={"status": "resolved"}),
        await authed_client.patch(f"/incidents/{incident_id}", json={"assignee": "me"}),
        await authed_client.post(f"/incidents/{incident_id}/comments", json={"body": "hi"}),
    ]

    assert [response.status_code for response in refused] == [403, 403, 403]

    # And nothing reached the timeline: a refused write must not leave a trace of itself.
    entries = await authed_client.get(f"/incidents/{incident_id}/activity")
    assert [entry["kind"] for entry in entries.json()["items"]] == ["created"]
