from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.postmortems import POST_MORTEM_STATUS_VALUES, PostMortemStatus
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.postmortem_action import PostMortemAction

# Built from the vocabulary so the database cannot accept a status the application would then
# have to interpret. Spelled out rather than left schemaless, for the same reason users.role is.
_STATUS_CHECK = "status IN (" + ", ".join(f"'{value}'" for value in POST_MORTEM_STATUS_VALUES) + ")"


class PostMortem(Base):
    """An incident's write-up: a narrative plus the sections a team actually reviews.

    The structured columns and the free-text ``content`` are both kept. The narrative is what the
    model wrote and what a reader skims; the structured fields are what the review, the export
    and the action-item strip are built from. Deriving one from the other would mean a parser
    deciding what a paragraph meant.

    ``author`` is the email of whoever generated the document, snapshotted the way timeline
    events snapshot their actor, so a rename or a deleted account does not erase who wrote it.
    ``author_id`` is the live reference, and the reason a draft is visible to its author: role
    alone cannot answer "may this person see an unpublished document".
    """

    __tablename__ = "post_mortems"
    __table_args__ = (CheckConstraint(_STATUS_CHECK, name="ck_post_mortems_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), unique=True
    )
    # The narrative, as generated and then edited by a human.
    content: Mapped[str] = mapped_column(Text, default="")

    timeline_summary: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    # A list of sentences, not a comma-separated string: the export renders it as a list and a
    # future review screen may want to attach evidence to each factor.
    contributing_factors: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    impact: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(
        String(20),
        default=PostMortemStatus.DRAFT.value,
        server_default=PostMortemStatus.DRAFT.value,
        index=True,
    )
    author: Mapped[str] = mapped_column(String(180), default="", server_default="")
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default="now()",
    )

    incident: Mapped[Incident] = relationship(back_populates="post_mortem")
    action_items: Mapped[list[PostMortemAction]] = relationship(
        back_populates="post_mortem",
        order_by="PostMortemAction.position, PostMortemAction.id",
        cascade="all, delete-orphan",
    )

    @property
    def incident_title(self) -> str:
        """The incident's title, for a list that would otherwise show only an id.

        Read through the relationship, so a caller listing documents has to eager-load it --
        which is the price of not duplicating a title that can be edited on the incident.
        """
        return self.incident.title if self.incident else ""
