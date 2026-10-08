from pydantic import BaseModel, Field

from app.core.rbac import Role
from app.schemas.common import Email, OrmModel

# Long enough to be worth hashing, short enough that nobody pastes a novel into the field.
PASSWORD_MIN_LENGTH = 8


class UserRead(OrmModel):
    id: int
    email: Email
    name: str
    role: Role


class UserCreate(BaseModel):
    email: Email
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)
    name: str = Field(default="", max_length=120)
    # Least privilege by default: an administrator creating an account has to say so
    # explicitly to hand out more than reading.
    role: Role = Role.VIEWER


class UserUpdate(BaseModel):
    """Every field is optional; absent means "leave it alone"."""

    name: str | None = Field(default=None, max_length=120)
    role: Role | None = None
    password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=128)
