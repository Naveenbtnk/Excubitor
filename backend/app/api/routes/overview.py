from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_session
from app.models import Alert, RunbookChunk, User

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("")
async def overview(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
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
    }
