from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.rbac import ROLE_VALUES, Role
from app.db.base import Base

# Built from the role vocabulary so the database cannot accept a value this code would then
# have to interpret. A schemaless column here is how a typo becomes an administrator.
_ROLE_CHECK = "role IN (" + ", ".join(f"'{value}'" for value in ROLE_VALUES) + ")"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_ROLE_CHECK, name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(120), default="Cloud Admin")
    # Least privilege by default: a row created without saying otherwise can read and nothing
    # else. The seeded administrator states its role explicitly.
    role: Mapped[str] = mapped_column(
        String(40), default=Role.VIEWER.value, server_default=Role.VIEWER.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
