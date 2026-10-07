from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models import Alert, User
from app.schemas.alerts import AlertCreate, AlertRead, SimulateAlertRequest
from app.schemas.common import Page, Pagination
from app.services.simulator import make_alert, scenario_names

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=Page[AlertRead])
async def list_alerts(
    pagination: Pagination = Depends(),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    total = await session.scalar(select(func.count()).select_from(Alert)) or 0
    rows = (
        await session.scalars(
            select(Alert)
            .order_by(Alert.created_at.desc(), Alert.id.desc())
            .limit(pagination.limit)
            .offset(pagination.offset)
        )
    ).all()
    return Page[AlertRead].of(
        [AlertRead.model_validate(row) for row in rows], total=total, pagination=pagination
    )


@router.post("", response_model=AlertRead, status_code=201)
async def create_alert(
    payload: AlertCreate,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    alert = Alert(**payload.model_dump())
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return alert


@router.post("/simulate", response_model=AlertRead, status_code=201)
async def simulate_alert(
    payload: SimulateAlertRequest | None = None,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    alert = Alert(**make_alert(payload.scenario if payload else None))
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return alert


@router.get("/scenarios")
async def scenarios(_: User = Depends(get_current_user)):
    return {"scenarios": scenario_names()}


@router.post("/{alert_id}/ack", response_model=AlertRead)
async def acknowledge_alert(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    alert = await session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.status = "acknowledged"
    await session.commit()
    await session.refresh(alert)
    return alert
