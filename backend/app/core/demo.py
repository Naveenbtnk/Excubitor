"""The public demo login, and the two questions anything else has to ask about it.

A shared, read-only account a stranger may sign in as: that is the feature, so its password is
*published* by ``GET /auth/demo`` instead of being baked into the dashboard bundle. The server
holds the only copy, and a deployment that never enables the feature holds none.

Two rules follow from "the password is public", and both live here rather than at the call sites:

**Off has to mean unusable, not merely unadvertised.** Anyone who kept the credential can still
post it, so a disabled demo login is refused at the sign-in route exactly as an unknown account
would be.

**It has to stay a Viewer.** Its role is the only thing between a stranger and every write route,
so provisioning re-asserts the role on every start (see :func:`app.db.session.seed_demo_account`).
"""

from __future__ import annotations

from app.core.config import settings
from app.models import User

__all__ = ["demo_credentials", "is_demo_account"]


def is_demo_account(user: User) -> bool:
    """Whether this account is the shared demo login.

    By email, and independent of whether the feature is currently enabled: the caller decides what
    "it is the demo account" means. ``app.api.routes.auth`` refuses it a session when the feature
    is off, and the rate limiter caps it whether or not it is on.
    """
    configured = (settings.demo_account_email or "").strip().lower()
    return bool(configured) and user.email.lower() == configured


def demo_credentials() -> dict[str, str] | None:
    """The credential the sign-in page may offer, or ``None`` when there is nothing to offer.

    ``None`` covers both "the deployment did not ask for this" and "it asked but never set a
    password", which the caller should treat identically: there is no usable account either way.
    """
    if not settings.demo_login_available:
        return None
    return {"email": settings.demo_account_email, "password": settings.demo_account_password}
