from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.postmortems import ACTION_ITEM_STATUS_VALUES, ActionItemStatus
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.postmortem import PostMortem

_STATUS_CHECK = (
    "status IN (" + ", ".join(f"'{value}'" for value in ACTION_ITEM_STATUS_VALUES) + ")"
)


class PostMortemAction(Base):
    """One follow-up from a post-mortem: the part of the document that has a deadline.

    A row rather than an entry in a JSON column, because these are queried on their own: the
    Overview strip asks for every open item across every post-mortem, oldest due date first, and
    "what is outstanding, by when" is not a question a JSON blob answers without a scan.

    ``owner`` carries the email next to ``owner_id`` for the same reason the narrative keeps its
    author's email: a follow-up is often assigned to somebody who has not signed in yet, and a
    row that loses its owner when an account is deleted is worse than one that records the name.
    """

    __tablename__ = "post_mortem_actions"
    __table_args__ = (CheckConstraint(_STATUS_CHECK, name="ck_post_mortem_actions_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_mortem_id: Mapped[int] = mapped_column(
        ForeignKey("post_mortems.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    owner: Mapped[str] = mapped_column(String(180), default="", server_default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=ActionItemStatus.OPEN.value,
        server_default=ActionItemStatus.OPEN.value,
        index=True,
    )
    # Keeps the order a human arranged them in rather than the order the database happened to
    # insert them, which is what makes an edited list stay edited.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    post_mortem: Mapped[PostMortem] = relationship(back_populates="action_items")
