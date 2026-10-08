from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident


class IncidentEvent(Base):
    """One entry on an incident's timeline: who did what, when, and what it changed.

    Append-only on purpose. Nothing in the application updates or deletes a row here, so the
    timeline is a record of what happened rather than a rendering of current state -- which is
    the whole point of an audit trail, and the reason a status column alone was not enough.

    ``actor`` is the email of the person who acted, so it survives that account being renamed
    or removed. Events written by the system itself (nothing yet, but a future scheduler) say
    ``system`` rather than borrowing a real account's name.
    """

    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(180))
    # The value that changed: the old and new status, or the old and new assignee. Both are
    # NULL for a comment, which changed nothing.
    from_value: Mapped[str | None] = mapped_column(String(180), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(180), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )

    incident: Mapped[Incident] = relationship(back_populates="events")
