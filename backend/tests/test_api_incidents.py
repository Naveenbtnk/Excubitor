from httpx import AsyncClient

ALERT = {
    "source": "kubernetes",
    "severity": "critical",
    "title": "Database pool exhaustion",
    "detail": "Pool at 100% for 4 minutes on orders-api.",
}


async def _triaged_incident(client: AsyncClient) -> tuple[int, int]:
    alert_id = (await client.post("/alerts", json=ALERT)).json()["id"]
    response = await client.post(f"/incidents/triage/{alert_id}")
    assert response.status_code == 201, response.text
    return alert_id, response.json()["id"]


async def test_list_incidents_starts_empty(authed_client: AsyncClient):
    body = (await authed_client.get("/incidents")).json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_triage_creates_an_incident_and_updates_the_alert(authed_client: AsyncClient):
    alert_id, incident_id = await _triaged_incident(authed_client)

    incidents = (await authed_client.get("/incidents")).json()
    assert incidents["total"] == 1
    incident = incidents["items"][0]
    assert incident["id"] == incident_id
    assert incident["alert_id"] == alert_id
    assert incident["summary"] == "Pool exhausted on orders-api"
    assert incident["root_cause"] == "Connection leak"
    assert incident["recommendation"] == "1. Drain idle sessions\n2. Restart orders-api"
    assert incident["confidence"] == 0.75
    assert incident["status"] == "triage"

    alerts = (await authed_client.get("/alerts")).json()
    assert alerts["items"][0]["status"] == "triaged"


async def test_triage_unknown_alert_returns_404(authed_client: AsyncClient):
    assert (await authed_client.post("/incidents/triage/9999")).status_code == 404


async def test_post_mortem_is_generated_once_and_reused(authed_client: AsyncClient):
    _, incident_id = await _triaged_incident(authed_client)

    assert (await authed_client.get(f"/incidents/{incident_id}/post-mortem")).status_code == 404

    generated = await authed_client.post(f"/incidents/{incident_id}/post-mortem")
    assert generated.status_code == 201, generated.text
    assert generated.json()["content"] == "Fake answer from the LLM."

    again = await authed_client.post(f"/incidents/{incident_id}/post-mortem")
    assert again.status_code == 201
    assert again.json()["id"] == generated.json()["id"]

    fetched = await authed_client.get(f"/incidents/{incident_id}/post-mortem")
    assert fetched.status_code == 200
    assert fetched.json()["content"] == generated.json()["content"]

    incidents = (await authed_client.get("/incidents")).json()
    assert incidents["items"][0]["status"] == "resolved"


async def test_post_mortem_for_unknown_incident_returns_404(authed_client: AsyncClient):
    assert (await authed_client.post("/incidents/9999/post-mortem")).status_code == 404


async def test_incident_pagination(authed_client: AsyncClient):
    for _ in range(3):
        await _triaged_incident(authed_client)

    first = (await authed_client.get("/incidents", params={"limit": 2})).json()
    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert first["has_more"] is True

    second = (await authed_client.get("/incidents", params={"limit": 2, "offset": 2})).json()
    assert len(second["items"]) == 1
    assert second["has_more"] is False
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )
