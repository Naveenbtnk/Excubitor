"""Alertmanager ingestion: authentication, mapping and the shape of the answer.

The payload below is a real Alertmanager v4 notification group, copied rather than
paraphrased: a group with one still-firing alert and one that has just resolved, which is
the case that decides whether the per-alert status is read or the group's status is used for
everything.
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services.alertmanager import DEFAULT_SEVERITY, DEFAULT_SOURCE, DEFAULT_TITLE

TOKEN = "alertmanager-test-token"
WRONG_TOKEN = "alertmanager-test-token "  # one character out, same length

PAYLOAD = {
    "receiver": "cloudops-ai",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "PostgresPoolExhausted",
                "cluster": "prod-eu-1",
                "instance": "orders-api-5f9c:9187",
                "job": "postgres-exporter",
                "severity": "CRITICAL",
            },
            "annotations": {
                "summary": "Postgres connection pool above 95% on orders-api",
                "description": "pg_stat_activity reports 98 of 100 connections for 6m.",
                "runbook_url": "https://runbooks.internal/postgres-pool",
            },
            "startsAt": "2026-10-08T09:12:44.512Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus.internal:9090/graph?g0.expr=pg_pool_ratio",
            "fingerprint": "0f1f8a4f2b7c9d10",
        },
        {
            "status": "resolved",
            "labels": {
                "alertname": "HighCPU",
                "instance": "node-3:9100",
                "job": "node-exporter",
                "severity": "warning",
            },
            "annotations": {"summary": "CPU back under 80% on node-3"},
            "startsAt": "2026-10-08T08:40:00Z",
            "endsAt": "2026-10-08T09:05:00Z",
            "generatorURL": "http://prometheus.internal:9090/graph?g0.expr=node_cpu",
            "fingerprint": "aa11bb22cc33dd44",
        },
    ],
    "groupLabels": {"alertname": "PostgresPoolExhausted"},
    "commonLabels": {
        "alertname": "PostgresPoolExhausted",
        "job": "postgres-exporter",
        "severity": "critical",
    },
    "commonAnnotations": {"summary": "Postgres connection pool above 95% on orders-api"},
    "externalURL": "http://alertmanager.internal:9093",
    "version": "4",
    "groupKey": '{}/{alertname="PostgresPoolExhausted"}:{job="postgres-exporter"}',
    "truncatedAlerts": 0,
}


@pytest.fixture(autouse=True)
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no token configured, whatever ``backend/.env`` happens to hold."""
    monkeypatch.setattr(settings, "alertmanager_webhook_token", "")


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "alertmanager_webhook_token", TOKEN)
    return TOKEN


def headers(token: str) -> dict[str, str]:
    return {"X-API-Key": token}


async def test_the_group_is_mapped_field_by_field(configured, client: AsyncClient, authed_client):
    response = await client.post(
        "/webhooks/alertmanager", json=PAYLOAD, headers=headers(configured)
    )

    assert response.status_code == 202, response.text
    assert response.json()["accepted"] == 2
    assert response.json()["opened"] == 1
    assert response.json()["resolved"] == 1

    items = (await authed_client.get("/alerts")).json()["items"]
    assert len(items) == 2

    firing = next(item for item in items if item["title"].startswith("PostgresPoolExhausted"))
    assert firing["title"] == "PostgresPoolExhausted on orders-api-5f9c:9187"
    assert firing["severity"] == "critical"  # normalised down from CRITICAL
    assert firing["source"] == "postgres-exporter"  # labels.job
    assert firing["status"] == "open"  # "firing" means it is open on our side
    assert "Postgres connection pool above 95% on orders-api" in firing["detail"]
    assert "pg_stat_activity reports 98 of 100 connections for 6m." in firing["detail"]
    assert "job=postgres-exporter" in firing["detail"]
    assert "startsAt: 2026-10-08T09:12:44.512Z" in firing["detail"]
    assert "generatorURL: http://prometheus.internal:9090/graph" in firing["detail"]

    resolved = next(item for item in items if item["title"].startswith("HighCPU"))
    assert resolved["title"] == "HighCPU on node-3:9100"
    assert resolved["status"] == "resolved"
    # The group is "firing" as a whole; the recovered alert must not inherit that.
    assert resolved["severity"] == "warning"


async def test_the_created_ids_come_back_in_order(client: AsyncClient, configured):
    body = (
        await client.post("/webhooks/alertmanager", json=PAYLOAD, headers=headers(configured))
    ).json()

    assert len(body["alert_ids"]) == 2
    assert all(isinstance(alert_id, int) for alert_id in body["alert_ids"])
    assert body["alert_ids"] == sorted(body["alert_ids"])


async def test_missing_labels_fall_back_to_sensible_defaults(configured, client, authed_client):
    payload = {"version": "4", "status": "firing", "alerts": [{"status": "firing"}]}

    assert (
        await client.post("/webhooks/alertmanager", json=payload, headers=headers(configured))
    ).status_code == 202

    alert = (await authed_client.get("/alerts")).json()["items"][0]
    assert alert["title"] == DEFAULT_TITLE
    assert alert["severity"] == DEFAULT_SEVERITY
    assert alert["source"] == DEFAULT_SOURCE
    assert alert["status"] == "open"


async def test_an_unset_token_refuses_rather_than_accepts(client: AsyncClient, authed_client):
    # Nothing configured at all: the safe answer is "not configured", never "come in".
    response = await client.post("/webhooks/alertmanager", json=PAYLOAD)

    assert response.status_code == 503
    assert "ALERTMANAGER_WEBHOOK_TOKEN" in response.json()["detail"]
    assert (await authed_client.get("/alerts")).json()["total"] == 0


async def test_an_unset_token_refuses_even_a_well_formed_key(client: AsyncClient):
    response = await client.post("/webhooks/alertmanager", json=PAYLOAD, headers=headers(TOKEN))
    assert response.status_code == 503


async def test_a_missing_key_is_rejected(client: AsyncClient, configured, authed_client):
    response = await client.post("/webhooks/alertmanager", json=PAYLOAD)

    assert response.status_code == 401
    assert (await authed_client.get("/alerts")).json()["total"] == 0


async def test_a_wrong_key_is_rejected(client: AsyncClient, configured, authed_client):
    response = await client.post(
        "/webhooks/alertmanager", json=PAYLOAD, headers=headers(WRONG_TOKEN)
    )

    assert response.status_code == 401
    assert (await authed_client.get("/alerts")).json()["total"] == 0


async def test_a_wrong_key_is_rejected_before_the_body_is_read(client: AsyncClient, configured):
    """The refusal comes first: a bad key learns nothing about the payload shape."""
    response = await client.post(
        "/webhooks/alertmanager", json={"alerts": "not-a-list"}, headers=headers(WRONG_TOKEN)
    )

    assert response.status_code == 401

    # The same body with the right key is a payload problem, as it should be.
    accepted = await client.post(
        "/webhooks/alertmanager", json={"alerts": "not-a-list"}, headers=headers(configured)
    )
    assert accepted.status_code == 422


async def test_the_key_is_the_whole_identity_not_a_session(configured, client: AsyncClient):
    """One cookie-less client: refused on the user routes, accepted on the webhook.

    Only ``client`` is requested here on purpose. ``authed_client`` signs in on that same
    object, so asking for both would hand this test a client that *is* authenticated and
    the 401 below would prove nothing.
    """
    assert (await client.get("/alerts")).status_code == 401

    accepted = await client.post(
        "/webhooks/alertmanager", json=PAYLOAD, headers=headers(configured)
    )
    assert accepted.status_code == 202, accepted.text


async def test_an_empty_group_is_accepted_without_creating_anything(
    client: AsyncClient, configured, authed_client
):
    body = (
        await client.post(
            "/webhooks/alertmanager",
            json={"version": "4", "status": "firing", "alerts": []},
            headers=headers(configured),
        )
    ).json()

    assert body == {"accepted": 0, "opened": 0, "resolved": 0, "alert_ids": []}
    assert (await authed_client.get("/alerts")).json()["total"] == 0


async def test_an_unknown_status_is_treated_as_still_open(configured, client, authed_client):
    payload = {"alerts": [{"status": "suppressed", "labels": {"alertname": "Odd"}}]}

    await client.post("/webhooks/alertmanager", json=payload, headers=headers(configured))

    alert = (await authed_client.get("/alerts")).json()["items"][0]
    assert alert["status"] == "open"


async def test_a_body_that_is_not_json_is_rejected(client: AsyncClient, configured, authed_client):
    response = await client.post(
        "/webhooks/alertmanager",
        content=b"{this is not json",
        headers={**headers(configured), "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert (await authed_client.get("/alerts")).json()["total"] == 0


async def test_alerts_must_be_a_list(client: AsyncClient, configured):
    response = await client.post(
        "/webhooks/alertmanager",
        json={"alerts": "PostgresPoolExhausted"},
        headers=headers(configured),
    )

    assert response.status_code == 422
    assert "alerts" in response.text


async def test_a_repeated_delivery_creates_the_alerts_again(configured, client, authed_client):
    """Documents the fingerprint-dedupe gap rather than implying it is handled.

    Alertmanager re-sends a firing group on every repeat_interval and on every resolution,
    so in production this route will see the same alert more than once. Per-fingerprint
    coalescing needs a decision about whether a resolved alert reopens the original row, so
    it is listed under "Known limitations" in the README instead of half-done here.
    """
    for _ in range(2):
        response = await client.post(
            "/webhooks/alertmanager", json=PAYLOAD, headers=headers(configured)
        )
        assert response.status_code == 202

    assert (await authed_client.get("/alerts")).json()["total"] == 4
