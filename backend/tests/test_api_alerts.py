from httpx import AsyncClient

from app.core.config import settings

ALERT = {
    "source": "kubernetes",
    "severity": "critical",
    "title": "Database pool exhaustion",
    "detail": "Pool at 100% for 4 minutes on orders-api.",
}


async def _create_alert(client: AsyncClient, **overrides) -> int:
    response = await client.post("/alerts", json={**ALERT, **overrides})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_list_alerts_starts_with_an_empty_page(authed_client: AsyncClient):
    response = await authed_client.get("/alerts")
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "limit": settings.pagination_default_limit,
        "offset": 0,
        "has_more": False,
    }


async def test_create_then_list_alert(authed_client: AsyncClient):
    alert_id = await _create_alert(authed_client)

    listed = (await authed_client.get("/alerts")).json()
    assert listed["total"] == 1
    assert [item["id"] for item in listed["items"]] == [alert_id]
    assert listed["items"][0]["status"] == "open"


async def test_create_alert_validates_payload(authed_client: AsyncClient):
    response = await authed_client.post("/alerts", json={**ALERT, "title": ""})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(item["msg"] for item in detail)


async def test_scenarios_are_available(authed_client: AsyncClient):
    response = await authed_client.get("/alerts/scenarios")
    assert response.status_code == 200
    assert len(response.json()["scenarios"]) >= 1


async def test_simulate_uses_the_named_scenario(authed_client: AsyncClient):
    response = await authed_client.post("/alerts/simulate", json={"scenario": "S3 403 burst"})
    assert response.status_code == 201
    assert response.json()["title"] == "S3 403 burst"


async def test_simulate_without_payload_picks_a_known_scenario(authed_client: AsyncClient):
    response = await authed_client.post("/alerts/simulate", json={})
    assert response.status_code == 201
    scenarios = (await authed_client.get("/alerts/scenarios")).json()["scenarios"]
    assert response.json()["title"] in scenarios


async def test_acknowledge_alert(authed_client: AsyncClient):
    alert_id = await _create_alert(authed_client)
    response = await authed_client.post(f"/alerts/{alert_id}/ack")
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"


async def test_acknowledge_unknown_alert_returns_404(authed_client: AsyncClient):
    assert (await authed_client.post("/alerts/9999/ack")).status_code == 404


async def test_alert_pages_split_and_do_not_overlap(authed_client: AsyncClient):
    for index in range(5):
        await _create_alert(authed_client, title=f"Alert {index}")

    first = (await authed_client.get("/alerts", params={"limit": 2, "offset": 0})).json()
    second = (await authed_client.get("/alerts", params={"limit": 2, "offset": 2})).json()
    third = (await authed_client.get("/alerts", params={"limit": 2, "offset": 4})).json()

    assert first["total"] == second["total"] == third["total"] == 5
    assert len(first["items"]) == len(second["items"]) == 2
    assert len(third["items"]) == 1
    assert first["has_more"] is True
    assert third["has_more"] is False

    seen = [item["id"] for item in first["items"] + second["items"] + third["items"]]
    assert len(set(seen)) == 5


async def test_alert_pagination_rejects_limits_above_the_cap(authed_client: AsyncClient):
    response = await authed_client.get(
        "/alerts", params={"limit": settings.pagination_max_limit + 1}
    )
    assert response.status_code == 422


async def test_alert_pagination_rejects_negative_offset(authed_client: AsyncClient):
    assert (await authed_client.get("/alerts", params={"offset": -1})).status_code == 422


async def test_overview_summarises_alerts_and_model(authed_client: AsyncClient):
    acknowledged = await _create_alert(authed_client)
    await _create_alert(authed_client, title="Still open")
    await authed_client.post(f"/alerts/{acknowledged}/ack")

    body = (await authed_client.get("/overview")).json()
    assert body["status"] == "Degraded"
    assert body["active_alerts"] == 1
    assert body["log_entries"] == 2
    assert body["model"] == {"provider": "gemini", "name": settings.gemini_model}


async def test_overview_is_healthy_without_active_alerts(authed_client: AsyncClient):
    body = (await authed_client.get("/overview")).json()
    assert body["status"] == "Healthy"
    assert body["active_alerts"] == 0
