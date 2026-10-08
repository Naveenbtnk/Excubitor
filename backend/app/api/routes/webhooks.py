"""Machine-to-machine alert ingestion.

The caller is Prometheus Alertmanager, not a person: it has no session cookie and no role,
so the shared token in ``X-API-Key`` is its whole identity. That is why this route sits
outside the user-JWT surface instead of borrowing a role from it -- there is no user to
check a role against, and inventing a service account would only move the same secret
somewhere harder to rotate.

Comparison is constant-time, because a token check that returns early on the first wrong
byte is a token oracle. An unset token answers ``503`` rather than accepting anything: a
deployment that has not been configured to ingest alerts must not be an open write route.
"""

from __future__ import annotations

from secrets import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models import Alert
from app.schemas.webhooks import AlertmanagerWebhook, WebhookIngestResult
from app.services.alertmanager import OPEN, RESOLVED, to_alert_rows

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Reject the caller before anything parses the body.

    A dependency rather than a call inside the route, because FastAPI validates the request
    body before the endpoint body runs: an unauthenticated caller would otherwise get a schema
    error back -- which is both an odd thing to tell someone with a wrong key and a slow way to
    learn the shape of a private API.
    """
    expected = settings.alertmanager_webhook_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Alert ingestion is not configured. Set ALERTMANAGER_WEBHOOK_TOKEN "
                "to accept Alertmanager webhooks."
            ),
        )
    if x_api_key is None or not compare_digest(
        x_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key"
        )


@router.post(
    "/alertmanager",
    response_model=WebhookIngestResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a Prometheus Alertmanager notification group",
)
async def ingest_alertmanager(
    payload: AlertmanagerWebhook,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_api_key),
) -> WebhookIngestResult:
    rows = to_alert_rows(payload)
    alerts = [Alert(**row) for row in rows]
    if alerts:
        session.add_all(alerts)
        # Flush inside the transaction so the ids exist for the response; a commit that has
        # not been flushed would leave the caller without the rows it just created.
        await session.flush()
        await session.commit()

    return WebhookIngestResult(
        accepted=len(alerts),
        opened=sum(1 for alert in alerts if alert.status == OPEN),
        resolved=sum(1 for alert in alerts if alert.status == RESOLVED),
        alert_ids=[alert.id for alert in alerts],
    )
