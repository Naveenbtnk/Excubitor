"""Rendering a post-mortem as the artifact people pass around.

The export is the reason the structured sections exist: a reader who was not in the channel wants
the sections in the same order every time, and the action items as a list they can copy into a
tracker. So this is a pure function of a document -- no session, no provider, no optional pieces
-- which means the tests can pin the exact bytes a download produces.

A section that is empty is written as ``_Not recorded._`` rather than omitted. An export that
silently drops a section is indistinguishable from a document that never had one, and the whole
point of a post-mortem is that the gaps are visible.

This module also owns the answer to "who may read this document", because it is one rule and two
routers ask it: the collection under ``/post-mortems`` and the incident-scoped route that
creates one. Spreading that predicate across the routers is how one of them ends up leaking a
draft.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import ColumnElement, Select, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.postmortems import PostMortemStatus
from app.core.rbac import Role, at_least
from app.models import PostMortem, PostMortemAction, User

_EMPTY = "_Not recorded._"


def document_query() -> Select[tuple[PostMortem]]:
    """A query that selects documents with everything the read schema asks for.

    ``PostMortemRead`` carries the incident's title and the action items, and both are lazy
    relationships. Loading them here rather than letting the serializer reach for them is not a
    micro-optimisation: an async session raises when a lazy load is attempted outside its own
    await, so a route that forgets this fails at response time instead of returning a document.
    """
    return select(PostMortem).options(
        selectinload(PostMortem.incident), selectinload(PostMortem.action_items)
    )


def visible_to(user: User) -> ColumnElement[bool]:
    """The predicate a caller's document reads are filtered by.

    A Viewer sees published documents and nothing else: a draft is a claim nobody has stood
    behind yet. Operators and Admins see drafts because the review pipeline needs a reader --
    the person who moves a draft to ``in_review`` and the person who publishes it must be able
    to open it, and a reviewer who can only see their own drafts is not a reviewer.
    """
    if at_least(user.role, Role.OPERATOR):
        return true()
    return PostMortem.status == PostMortemStatus.PUBLISHED.value


async def resolve_owner(session: AsyncSession, email: str | None) -> tuple[int | None, str]:
    """Turn an owner's email into a user reference, keeping the text either way.

    A follow-up is often assigned to somebody who has no account yet, so an email that matches
    nothing is stored as written rather than refused. The reference is what the app can use
    later; the email is what a human typed and can still read.
    """
    if not email or not email.strip():
        return None, ""
    address = email.strip()
    owner_id = await session.scalar(
        select(User.id).where(func.lower(User.email) == address.lower())
    )
    return owner_id, address


def _section(body: str | None) -> str:
    return body.strip() if body and body.strip() else _EMPTY


def _bullet_list(items: list[str]) -> str:
    cleaned = [item.strip() for item in items if item and item.strip()]
    if not cleaned:
        return _EMPTY
    return "\n".join(f"- {item}" for item in cleaned)


def _cell(value: str) -> str:
    """A table cell: pipes and newlines would break the row, so they become plain text."""
    return " ".join(value.split()).replace("|", "\\|")


def _due(value: date | None, today: date | None = None) -> str:
    if value is None:
        return "—"
    rendered = value.isoformat()
    reference = today or date.today()
    if value < reference:
        return f"{rendered} (overdue)"
    return rendered


def render_action_table(items: list[PostMortemAction], *, today: date | None = None) -> str:
    if not items:
        return _EMPTY
    rows = [
        "| Action | Owner | Due | Status |",
        "| --- | --- | --- | --- |",
    ]
    for item in items:
        rows.append(
            "| "
            + " | ".join(
                [
                    _cell(item.text),
                    _cell(item.owner) if item.owner else "—",
                    _due(item.due_date, today),
                    item.status,
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def render_markdown(document: PostMortem, *, today: date | None = None) -> str:
    """The full document: narrative and structured sections, in a fixed order."""
    title = document.incident_title.strip() or f"Incident {document.incident_id}"
    published = (
        document.published_at.astimezone().strftime("%Y-%m-%d %H:%M")
        if document.published_at
        else "—"
    )
    author = document.author or "unknown"
    updated = document.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")

    return "\n\n".join(
        [
            f"# Post-mortem: {title}",
            "\n".join(
                [
                    f"**Status:** {document.status}",
                    f"**Author:** {author}",
                    f"**Published:** {published}",
                    f"**Last updated:** {updated}",
                ]
            ),
            "## Summary\n\n" + _section(document.content),
            "## Timeline\n\n" + _section(document.timeline_summary),
            "## Root cause\n\n" + _section(document.root_cause),
            "## Contributing factors\n\n" + _bullet_list(document.contributing_factors),
            "## Impact\n\n" + _section(document.impact),
            "## Action items\n\n" + render_action_table(document.action_items, today=today),
        ]
    ) + "\n"
