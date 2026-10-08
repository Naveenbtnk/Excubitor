from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.incidents import IncidentEventKind, IncidentStatus
from app.core.rbac import Role
from app.db.session import get_session
from app.models import Alert, Incident, IncidentEvent, PostMortem, User
from app.schemas.common import Page, Pagination
from app.schemas.incidents import (
    IncidentCommentCreate,
    IncidentEventRead,
    IncidentRead,
    IncidentUpdate,
    PostMortemRead,
)
from app.services.rag import search_runbooks
from app.services.ratelimit import rate_limit
from app.services.timeline import incident_event
from app.services.triage import triage_alert, write_post_mortem

router = APIRouter(prefix="/incidents", tags=["incidents"])

# Reading an incident and its timeline is open to every signed-in role; changing the incident,
# commenting on it and spending provider budget on it is an Operator action.


async def _incident_or_404(session: AsyncSession, incident_id: int) -> Incident:
    incident = await session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    return incident


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


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    return await _incident_or_404(session, incident_id)


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: int,
    payload: IncidentUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
):
    """Change the status, the assignee, or both, recording each change on the timeline.

    A value that is already set is not an error and not an event: re-saving a form that says
    "investigating" does not belong on a log that people read to find out what changed.
    """
    incident = await _incident_or_404(session, incident_id)
    changes = payload.model_dump(exclude_unset=True)
    events: list[IncidentEvent] = []

    if "status" in changes and changes["status"] != incident.status:
        events.append(
            incident_event(
                incident.id,
                IncidentEventKind.STATUS,
                user.email,
                from_value=incident.status,
                to_value=changes["status"],
                body=f"Status changed from {incident.status} to {changes['status']}.",
            )
        )
        incident.status = changes["status"]

    if "assignee" in changes and changes["assignee"] != incident.assignee:
        previous = incident.assignee
        events.append(
            incident_event(
                incident.id,
                IncidentEventKind.ASSIGNEE,
                user.email,
                from_value=previous,
                to_value=changes["assignee"],
                body=(
                    f"Assigned to {changes['assignee']}."
                    if changes["assignee"]
                    else f"Unassigned{f' from {previous}' if previous else ''}."
                ),
            )
        )
        incident.assignee = changes["assignee"]

    session.add_all([*events, incident])
    await session.commit()
    await session.refresh(incident)
    return incident


@router.get("/{incident_id}/activity", response_model=Page[IncidentEventRead])
async def incident_activity(
    incident_id: int,
    pagination: Pagination = Depends(),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    """The timeline, oldest first.

    This is the one list in the API that reads forwards: every other one is a queue whose
    newest entry is the interesting one, while a timeline is read the way it happened. Pages
    therefore walk forwards through it too.
    """
    await _incident_or_404(session, incident_id)
    total = (
        await session.scalar(
            select(func.count())
            .select_from(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
        )
        or 0
    )
    rows = (
        await session.scalars(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(IncidentEvent.created_at.asc(), IncidentEvent.id.asc())
            .limit(pagination.limit)
            .offset(pagination.offset)
        )
    ).all()
    return Page[IncidentEventRead].of(
        [IncidentEventRead.model_validate(row) for row in rows],
        total=total,
        pagination=pagination,
    )


@router.post("/{incident_id}/comments", response_model=IncidentEventRead, status_code=201)
async def add_comment(
    incident_id: int,
    payload: IncidentCommentCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
):
    await _incident_or_404(session, incident_id)
    comment = incident_event(
        incident_id, IncidentEventKind.COMMENT, user.email, body=payload.body
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return comment


@router.post("/triage/{alert_id}", response_model=IncidentRead, status_code=201)
async def triage(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.OPERATOR)),
    _limit: None = Depends(rate_limit("incidents.triage", "llm_rate_limit")),
):
    alert = await session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    hits = await search_runbooks(session, f"{alert.title}\n{alert.detail}")
    incident = await triage_alert(alert, hits)
    alert.status = "triaged"
    session.add_all([alert, incident])
    # The event needs the incident's id, which only exists once the row is in the session.
    await session.flush()
    session.add(
        incident_event(
            incident.id,
            IncidentEventKind.CREATED,
            user.email,
            to_value=incident.status,
            body=(
                f"Created by AI triage from the alert “{alert.title}”, "
                f"with {incident.confidence:.0%} confidence."
            ),
        )
    )
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
    user: User = Depends(require_role(Role.OPERATOR)),
    _limit: None = Depends(rate_limit("incidents.post_mortem", "llm_rate_limit")),
):
    existing = await session.scalar(select(PostMortem).where(PostMortem.incident_id == incident_id))
    if existing:
        # Returning the existing one keeps this idempotent, and deliberately writes no event:
        # nothing happened, so there is nothing to add to the timeline.
        return existing
    incident = await _incident_or_404(session, incident_id)
    content = await write_post_mortem(incident)
    post_mortem = PostMortem(incident_id=incident.id, content=content)
    previous_status = incident.status
    incident.status = IncidentStatus.RESOLVED.value
    session.add_all([incident, post_mortem])
    await session.flush()
    session.add(
        incident_event(
            incident.id,
            IncidentEventKind.POST_MORTEM,
            user.email,
            from_value=previous_status,
            to_value=incident.status,
            body="Generated the post-mortem, which resolves the incident.",
        )
    )
    await session.commit()
    await session.refresh(post_mortem)
    return post_mortem
