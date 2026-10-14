from pydantic import BaseModel

from app.schemas.common import Email
from app.schemas.users import UserRead

# Re-exported: the session endpoints and the user-management endpoints describe a user the
# same way, and one definition is what keeps them from drifting apart.
__all__ = ["AuthResponse", "DemoCredentials", "LoginRequest", "UserRead"]


class LoginRequest(BaseModel):
    email: Email
    password: str


class AuthResponse(BaseModel):
    user: UserRead


class DemoCredentials(BaseModel):
    """What the sign-in page may publish about the shared demo login.

    The two optional fields are absent rather than empty when the feature is off, so a deployment
    that never enabled it has nothing at all to leak from `GET /auth/demo`. The address is not a
    secret; the password is public *by design* (there is one shared account and a stranger is
    meant to use it), which is the reason it must not be a value anybody reuses.
    """

    enabled: bool
    email: str | None = None
    password: str | None = None
