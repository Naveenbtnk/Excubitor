from dataclasses import asdict

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.common import HealthResponse, LLMHealthResponse, LLMQuotaStatus
from app.services.llm import get_llm
from app.services.llm.base import LLMError
from app.services.llm.quota import current_quota

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    database = "ok"
    try:
        async with SessionLocal() as session:
            await session.execute(text("select 1"))
    except Exception:
        database = "unreachable"
    return HealthResponse(status="ok", provider=settings.llm_provider, database=database)


@router.get("/health/llm", response_model=LLMHealthResponse)
async def health_llm():
    try:
        llm_status = await get_llm().health()
    except (LLMError, NotImplementedError) as exc:
        llm_status = f"unreachable: {exc}"
    # The probe above is exempt from the budget, so reading the snapshot here costs nothing
    # and answers the question an operator actually has: how many calls are left today?
    snapshot = current_quota().snapshot()
    return LLMHealthResponse(
        status="ok" if llm_status == "ok" else "degraded",
        provider=settings.llm_provider,
        model=settings.llm_model_name,
        llm=llm_status,
        quota=LLMQuotaStatus(**asdict(snapshot)) if snapshot else None,
    )
