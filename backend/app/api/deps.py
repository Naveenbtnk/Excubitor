from collections.abc import Awaitable, Callable

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import Role, at_least
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models import User


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    token: str | None = Cookie(default=None, alias=settings.cookie_name),
) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    email = decode_access_token(token)
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await session.scalar(select(User).where(User.email == email))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    request.state.user_id = user.id
    return user


def require_role(minimum: Role) -> Callable[..., Awaitable[User]]:
    """Build a dependency that resolves the caller and insists on a role.

    This is the enforcement point, not the UI: every route that changes something declares
    the role it needs, so hiding a button can only ever spare a user a pointless click.

    The role is read from the user row on every request -- ``get_current_user`` looks it up
    anyway -- so a demotion takes effect on the next request rather than at the next login,
    which is what revoking someone's access during an incident has to mean.
    """

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if not at_least(user.role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires the {minimum.value} role or higher; "
                    f"your account has the {user.role} role."
                ),
            )
        return user

    return dependency
