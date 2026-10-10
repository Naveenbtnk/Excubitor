"""The post-mortem as a document with a life of its own.

Generating one is an incident action and lives in the incidents router; everything that happens
afterwards is about the document -- reading it, editing its sections, moving it through review,
and downloading it -- so it lives here, under its own collection.

Reading is a role question (see :func:`app.services.postmortems.visible_to`) and every write is
an Operator action. The two are deliberately different: a Viewer may read a published document,
and may not move it, edit it or export a draft.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.incidents import IncidentEventKind
from app.core.postmortems import PostMortemStatus
from app.core.rbac import Role
from app.db.session import get_session
from app.models import PostMortem, PostMortemAction, User
from app.schemas.common import Page, Pagination
from app.schemas.postmortems import (
    ActionItemRead,
    ActionItemUpdate,
    ActionItemWrite,
    PostMortemRead,
    PostMortemUpdate,
)
from app.services.postmortems import (
    document_query,
    render_markdown,
    resolve_owner,
    visible_to,
)
from app.services.timeline import incident_event

router = APIRouter(prefix="/post-mortems", tags=["post-mortems"])

OPERATOR = require_role(Role.OPERATOR)

async def _replace_action_items(
    session: AsyncSession, document: PostMortem, items: list[ActionItemWrite]
) -> None:
    """Make the document's items match the list the client sent.

    Replace semantics, done row by row rather than by clearing and re-inserting: an item that
    comes back with its id is the same item, so hovering a link or a half-finished edit on
    another screen does not point at a row that was deleted and recreated underneath it. What
    the client no longer lists is removed, because a form that edits a set of rows has no other
    way to say "this one is gone".
    """
    existing = {item.id: item for item in document.action_items}
    retained: set[int] = set()

    for position, payload in enumerate(items):
        owner_id, owner = await resolve_owner(session, payload.owner)
        if payload.id is None:
            document.action_items.append(
                PostMortemAction(
                    text=payload.text,
                    owner_id=owner_id,
                    owner=owner,
                    due_date=payload.due_date,
                    status=payload.status.value,
                    position=position,
                )
            )
            continue

        current = existing.get(payload.id)
        if current is None:
            raise HTTPException(422, f"Action item {payload.id} is not part of this post-mortem")
        current.text = payload.text
        current.owner_id = owner_id
        current.owner = owner
        current.due_date = payload.due_date
        current.status = payload.status.value
        current.position = position
        retained.add(current.id)

    for item_id, row in existing.items():
        if item_id not in retained:
            document.action_items.remove(row)


async def _document_or_404(session: AsyncSession, document_id: int, user: User) -> PostMortem:
    """Load a document the caller may read, or 404.

    404 rather than 403 for a draft a Viewer asked for by id: the answer to "does this hidden
    thing exist" is itself information, and a viewer has no use for it.
    """
    document = await session.scalar(
        document_query().where(PostMortem.id == document_id, visible_to(user))
    )
    if not document:
        raise HTTPException(404, "Post-mortem not found")
    return document


@router.get("", response_model=Page[PostMortemRead])
async def list_post_mortems(
    pagination: Pagination = Depends(),
    status: PostMortemStatus | None = Query(default=None),
    incident_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Every document this caller may read, most recently touched first."""
    filters = [visible_to(user)]
    if status is not None:
        filters.append(PostMortem.status == status.value)
    if incident_id is not None:
        filters.append(PostMortem.incident_id == incident_id)

    total = (
        await session.scalar(select(func.count()).select_from(PostMortem).where(*filters)) or 0
    )
    rows = (
        await session.scalars(
            document_query()
            .where(*filters)
            .order_by(PostMortem.updated_at.desc(), PostMortem.id.desc())
            .limit(pagination.limit)
            .offset(pagination.offset)
        )
    ).all()
    return Page[PostMortemRead].of(
        [PostMortemRead.model_validate(row) for row in rows],
        total=total,
        pagination=pagination,
    )


@router.get("/{document_id}", response_model=PostMortemRead)
async def get_post_mortem(
    document_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return await _document_or_404(session, document_id, user)


@router.patch("/{document_id}", response_model=PostMortemRead)
async def update_post_mortem(
    document_id: int,
    payload: PostMortemUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(OPERATOR),
):
    """Edit the sections, replace the action items, and move the document through review.

    Any status change is recorded on the incident's timeline, which is where people look for it:
    "who published this, and when" is the question a review pipeline exists to answer. Transitions
    are not forced through the middle state -- a small fix-up can go straight from draft to
    published -- but every step is written down.
    """
    document = await _document_or_404(session, document_id, user)
    changes = payload.model_dump(exclude_unset=True)

    for field in ("content", "timeline_summary", "root_cause", "impact"):
        if field in changes:
            setattr(document, field, changes[field])
    if "contributing_factors" in changes:
        document.contributing_factors = changes["contributing_factors"]

    events = []
    if "status" in changes and changes["status"] != document.status:
        previous = document.status
        document.status = changes["status"]
        document.published_at = (
            datetime.now(UTC) if changes["status"] == PostMortemStatus.PUBLISHED.value else None
        )
        events.append(
            incident_event(
                document.incident_id,
                IncidentEventKind.POST_MORTEM,
                user.email,
                from_value=previous,
                to_value=document.status,
                body=f"Post-mortem moved from {previous} to {document.status}.",
            )
        )

    if payload.action_items is not None:
        await _replace_action_items(session, document, payload.action_items)

    session.add_all([document, *events])
    await session.commit()
    return await _document_or_404(session, document_id, user)


@router.patch("/{document_id}/actions/{action_id}", response_model=ActionItemRead)
async def update_action_item(
    document_id: int,
    action_id: int,
    payload: ActionItemUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(OPERATOR),
):
    """Edit one follow-up, so marking an item done is one small request from any screen."""
    await _document_or_404(session, document_id, user)
    item = await session.scalar(
        select(PostMortemAction).where(
            PostMortemAction.id == action_id, PostMortemAction.post_mortem_id == document_id
        )
    )
    if not item:
        raise HTTPException(404, "Action item not found")

    changes = payload.model_dump(exclude_unset=True)
    if "text" in changes and changes["text"]:
        item.text = changes["text"].strip()
    if "status" in changes and changes["status"]:
        item.status = changes["status"]
    if "due_date" in changes:
        item.due_date = changes["due_date"]
    if "owner" in changes:
        item.owner_id, item.owner = await resolve_owner(session, changes["owner"])

    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@router.get("/{document_id}/export.md")
async def export_post_mortem(
    document_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """The document as a file, which is the form in which it gets shared."""
    document = await _document_or_404(session, document_id, user)
    markdown = render_markdown(document)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="post-mortem-{document.incident_id}.md"'
        },
    )
