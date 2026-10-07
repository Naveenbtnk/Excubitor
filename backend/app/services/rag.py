from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunbookChunk
from app.schemas.knowledge import KnowledgeHit, RunbookCreate
from app.services.llm import get_llm


def chunk_text(text: str, *, size: int = 900, overlap: int = 120) -> list[str]:
    clean = " ".join(text.split())
    if len(clean) <= size:
        return [clean]
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        chunks.append(clean[start : start + size])
        start += size - overlap
    return chunks


async def add_runbook(session: AsyncSession, payload: RunbookCreate) -> list[RunbookChunk]:
    chunks: list[RunbookChunk] = []
    for content in chunk_text(payload.content):
        embedding = await get_llm().embed(f"{payload.title}\n{content}")
        chunks.append(
            RunbookChunk(
                title=payload.title, source=payload.source, content=content, embedding=embedding
            )
        )
    session.add_all(chunks)
    await session.commit()
    for chunk in chunks:
        await session.refresh(chunk)
    return chunks


async def search_runbooks(
    session: AsyncSession, query: str, *, limit: int = 4
) -> list[KnowledgeHit]:
    embedding = await get_llm().embed(query)
    statement = (
        select(RunbookChunk)
        .order_by(RunbookChunk.embedding.cosine_distance(embedding))
        .limit(limit)
    )
    rows = (await session.scalars(statement)).all()
    return [KnowledgeHit(title=row.title, source=row.source, content=row.content) for row in rows]
