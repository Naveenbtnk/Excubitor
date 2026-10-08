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


def _authenticate(provided: str | None) -> None:
    expected = settings.alertmanager_webhook_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Alert ingestion is not configured. Set ALERTMANAGER_WEBHOOK_TOKEN "
                "to accept Alertmanager webhooks."
            ),
        )
    if provided is None or not compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
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
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> WebhookIngestResult:
    _authenticate(x_api_key)

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
