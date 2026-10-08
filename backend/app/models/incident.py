from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.incidents import IncidentStatus
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.incident_event import IncidentEvent
    from app.models.postmortem import PostMortem


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(180))
    summary: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(
        String(30), default=IncidentStatus.TRIAGE.value, index=True
    )
    # Who is carrying the incident. Free text rather than a foreign key to users: an incident
    # is often handed to somebody who has not signed in yet, and a response that fails because
    # the assignee has no account is worse than one that records a name.
    assignee: Mapped[str | None] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )

    alert: Mapped[Alert | None] = relationship(back_populates="incidents")
    post_mortem: Mapped[PostMortem | None] = relationship(back_populates="incident")
    events: Mapped[list[IncidentEvent]] = relationship(
        back_populates="incident", order_by="IncidentEvent.id", cascade="all, delete-orphan"
    )
