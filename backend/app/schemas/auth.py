from pydantic import BaseModel

from app.schemas.common import Email
from app.schemas.users import UserRead

# Re-exported: the session endpoints and the user-management endpoints describe a user the
# same way, and one definition is what keeps them from drifting apart.
__all__ = ["AuthResponse", "LoginRequest", "UserRead"]


class LoginRequest(BaseModel):
    email: Email
    password: str


class AuthResponse(BaseModel):
    user: UserRead
