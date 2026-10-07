from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models import Alert, Incident, PostMortem, User
from app.schemas.common import Page, Pagination
from app.schemas.incidents import IncidentRead, PostMortemRead
from app.services.rag import search_runbooks
from app.services.ratelimit import rate_limit
from app.services.triage import triage_alert, write_post_mortem

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=Page[IncidentRead])
async def list_incidents(
    pagination: Pagination = Depends(),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    total = await session.scalar(select(func.count()).select_from(Incident)) or 0
    rows = (
        await session.scalars(
            select(Incident)
            .order_by(Incident.created_at.desc(), Incident.id.desc())
            .limit(pagination.limit)
            .offset(pagination.offset)
        )
    ).all()
    return Page[IncidentRead].of(
        [IncidentRead.model_validate(row) for row in rows], total=total, pagination=pagination
    )


@router.post("/triage/{alert_id}", response_model=IncidentRead, status_code=201)
async def triage(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
    _limit: None = Depends(rate_limit("incidents.triage", "llm_rate_limit")),
):
    alert = await session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    hits = await search_runbooks(session, f"{alert.title}\n{alert.detail}")
    incident = await triage_alert(alert, hits)
    alert.status = "triaged"
    session.add_all([alert, incident])
    await session.commit()
    await session.refresh(incident)
    return incident


@router.get("/{incident_id}/post-mortem", response_model=PostMortemRead)
async def get_post_mortem(
    incident_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    post_mortem = await session.scalar(
        select(PostMortem).where(PostMortem.incident_id == incident_id)
    )
    if not post_mortem:
        raise HTTPException(404, "Post-mortem not generated")
    return post_mortem


@router.post("/{incident_id}/post-mortem", response_model=PostMortemRead, status_code=201)
async def generate_post_mortem(
    incident_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
    _limit: None = Depends(rate_limit("incidents.post_mortem", "llm_rate_limit")),
):
    existing = await session.scalar(select(PostMortem).where(PostMortem.incident_id == incident_id))
    if existing:
        return existing
    incident = await session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    content = await write_post_mortem(incident)
    post_mortem = PostMortem(incident_id=incident.id, content=content)
    incident.status = "resolved"
    session.add_all([incident, post_mortem])
    await session.commit()
    await session.refresh(post_mortem)
    return post_mortem
