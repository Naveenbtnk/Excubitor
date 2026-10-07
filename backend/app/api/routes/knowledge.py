from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models import RunbookChunk, User
from app.schemas.common import Page, Pagination
from app.schemas.knowledge import KnowledgeAnswer, KnowledgeQuery, RunbookCreate, RunbookRead
from app.services.llm import get_llm
from app.services.llm.base import Message
from app.services.rag import add_runbook, search_runbooks
from app.services.ratelimit import rate_limit

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/runbooks", response_model=Page[RunbookRead])
async def list_runbooks(
    pagination: Pagination = Depends(),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
):
    total = await session.scalar(select(func.count()).select_from(RunbookChunk)) or 0
    rows = (
        await session.scalars(
            select(RunbookChunk)
            .order_by(RunbookChunk.created_at.desc(), RunbookChunk.id.desc())
            .limit(pagination.limit)
            .offset(pagination.offset)
        )
    ).all()
    return Page[RunbookRead].of(
        [RunbookRead.model_validate(row) for row in rows], total=total, pagination=pagination
    )


@router.post("/runbooks", response_model=list[RunbookRead], status_code=201)
async def create_runbook(
    payload: RunbookCreate,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
    _limit: None = Depends(rate_limit("knowledge.index", "embedding_rate_limit")),
):
    return await add_runbook(session, payload)


@router.post("/runbooks/upload", response_model=list[RunbookRead], status_code=201)
async def upload_runbook(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
    _limit: None = Depends(rate_limit("knowledge.index", "embedding_rate_limit")),
):
    content = (await file.read()).decode("utf-8")
    payload = RunbookCreate(
        title=file.filename or "Uploaded runbook", source="upload", content=content
    )
    return await add_runbook(session, payload)


@router.post("/query", response_model=KnowledgeAnswer)
async def query_knowledge(
    payload: KnowledgeQuery,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
    _limit: None = Depends(rate_limit("knowledge.query", "llm_rate_limit")),
):
    hits = await search_runbooks(session, payload.question)
    context = "\n\n".join(f"[{hit.title}]\n{hit.content}" for hit in hits) or "No runbooks indexed."
    reply = await get_llm().chat(
        [
            Message(
                role="user", content=f"Question: {payload.question}\n\nRunbook context:\n{context}"
            )
        ],
        system=(
            "Answer like a concise SRE assistant. If context is missing, "
            "say what runbook would help."
        ),
    )
    assert isinstance(reply, str)
    return KnowledgeAnswer(answer=reply, hits=hits)
