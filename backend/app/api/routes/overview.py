from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.postmortems import ActionItemStatus
from app.db.session import get_session
from app.models import Alert, PostMortem, PostMortemAction, RunbookChunk, User
from app.services.postmortems import visible_to

router = APIRouter(prefix="/overview", tags=["overview"])

# How many follow-ups the strip shows. The count above it is the number that matters; this is
# the handful of them worth acting on today.
STRIP_LIMIT = 5


async def _action_items(session: AsyncSession, user: User) -> dict:
    """Open follow-ups across the documents this caller may read, oldest due date first.

    Ordering by due date with ``NULLS LAST`` is what "oldest overdue first" means in one sort:
    the earliest deadline is at the top, an item with no deadline sits below everything that has
    one, and the strip can show the overdue subset without a second query.
    """
    open_filters = [
        visible_to(user),
        PostMortemAction.status == ActionItemStatus.OPEN.value,
    ]
    today = datetime.now(UTC).date()
    total = await session.scalar(
        select(func.count())
        .select_from(PostMortemAction)
        .join(PostMortem, PostMortem.id == PostMortemAction.post_mortem_id)
        .where(*open_filters)
    )
    overdue = await session.scalar(
        select(func.count())
        .select_from(PostMortemAction)
        .join(PostMortem, PostMortem.id == PostMortemAction.post_mortem_id)
        .where(*open_filters, PostMortemAction.due_date.is_not(None))
        .where(PostMortemAction.due_date < today)
    )
    rows = (
        await session.scalars(
            select(PostMortemAction)
            .join(PostMortem, PostMortem.id == PostMortemAction.post_mortem_id)
            .options(
                selectinload(PostMortemAction.post_mortem).selectinload(PostMortem.incident)
            )
            .where(*open_filters)
            .order_by(
                PostMortemAction.due_date.asc().nulls_last(),
                PostMortemAction.id.asc(),
            )
            .limit(STRIP_LIMIT)
        )
    ).all()

    def _overdue(due: date | None) -> bool:
        return due is not None and due < today

    return {
        "open": total or 0,
        "overdue": overdue or 0,
        "items": [
            {
                "id": row.id,
                "text": row.text,
                "owner": row.owner or None,
                "due_date": row.due_date,
                "overdue": _overdue(row.due_date),
                "post_mortem_id": row.post_mortem_id,
                "incident_id": row.post_mortem.incident_id,
                "incident_title": row.post_mortem.incident_title,
            }
            for row in rows
        ],
    }


@router.get("")
async def overview(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    active_alerts = await session.scalar(
        select(func.count()).select_from(Alert).where(Alert.status.in_(["open", "triage"]))
    )
    incident_count = await session.scalar(select(func.count()).select_from(Alert))
    chunks = (
        await session.scalars(
            select(RunbookChunk).order_by(RunbookChunk.created_at.desc()).limit(6)
        )
    ).all()
    seen: set[str] = set()
    runbooks = []
    for chunk in chunks:
        if chunk.title in seen:
            continue
        seen.add(chunk.title)
        runbooks.append({"doc_id": str(chunk.id), "title": chunk.title, "source": chunk.source})
    return {
        "status": "Healthy" if active_alerts == 0 else "Degraded",
        "active_alerts": active_alerts or 0,
        "log_entries": incident_count or 0,
        "model": {
            "provider": settings.llm_provider,
            "name": settings.llm_model_name,
        },
        "runbooks": runbooks,
        "action_items": await _action_items(session, user),
    }
