from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.demo import demo_credentials, is_demo_account
from app.core.security import create_access_token, verify_password
from app.db.session import get_session
from app.models import User
from app.schemas.auth import AuthResponse, DemoCredentials, LoginRequest, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)
):
    user = await session.scalar(select(User).where(User.email == payload.email))
    if user is not None and is_demo_account(user) and not settings.demo_login_available:
        # The demo credential is published, so switching the feature off has to make it stop
        # working rather than merely stop being advertised. Treating the account as absent keeps
        # this answer identical to a wrong password.
        user = None
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user.email)
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return AuthResponse(user=UserRead.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(settings.cookie_name)


@router.get("/demo", response_model=DemoCredentials)
async def demo() -> DemoCredentials:
    """Tell the sign-in page whether there is a demo login to offer, and hand it over if so.

    Unauthenticated on purpose: this is the one route that runs before a session exists, and it
    is what decides whether the page shows the button at all. A deployment that never enabled the
    demo account gets `{"enabled": false}` and nothing else, so there is no address and no
    password to find in the response.

    When it is enabled the password travels with it, because a button that signs a stranger into
    a *shared* account either hands over a credential or the account is not shared. That is
    exactly why the value has to be one used nowhere else, and why disabling the feature refuses
    it at `/auth/login` instead of just hiding the button.
    """
    credentials = demo_credentials()
    if credentials is None:
        return DemoCredentials(enabled=False)
    return DemoCredentials(enabled=True, **credentials)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)):
    return user
