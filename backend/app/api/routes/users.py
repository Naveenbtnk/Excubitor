"""Administrator-only user management.

The role gate here is the point of the router: an Operator who could reach these endpoints
could promote themselves to Admin, so every one of them requires Admin explicitly rather than
inheriting the route group's usual "signed in" check.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.rbac import Role
from app.core.security import hash_password
from app.db.session import get_session
from app.models import User
from app.schemas.common import Page, Pagination
from app.schemas.users import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

ADMIN_ONLY = require_role(Role.ADMIN)


async def _admin_count(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(User).where(User.role == Role.ADMIN.value)
        )
    ) or 0


@router.get("", response_model=Page[UserRead])
async def list_users(
    pagination: Pagination = Depends(),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(ADMIN_ONLY),
):
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    rows = (
        await session.scalars(
            select(User).order_by(User.email).limit(pagination.limit).offset(pagination.offset)
        )
    ).all()
    return Page[UserRead].of(
        [UserRead.model_validate(row) for row in rows], total=total, pagination=pagination
    )


@router.post("", response_model=UserRead, status_code=201)
async def create_user(
    payload: UserCreate,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(ADMIN_ONLY),
):
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name or payload.email.split("@")[0],
        role=payload.role.value,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        ) from None
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(ADMIN_ONLY),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.role is not None and payload.role.value != user.role:
        # A deployment with no administrator left has no way back in: nobody could create the
        # next one. Refusing the last demotion is cheaper than a support ticket about it.
        if (
            user.role == Role.ADMIN.value
            and payload.role.value != Role.ADMIN.value
            and await _admin_count(session) <= 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This is the last administrator; promote someone else before "
                    "changing this role."
                ),
            )
        user.role = payload.role.value
    if payload.name is not None:
        user.name = payload.name
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)

    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    caller: User = Depends(ADMIN_ONLY),
):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Two guards, and the order is the message. The last administrator is also the caller --
    # whoever asks has to be an administrator, so if only one exists the two rows are the same
    # one -- and that check runs first because it names what would actually be lost and the way
    # out. The self check then answers the remaining case, an administrator with colleagues.
    if user.role == Role.ADMIN.value and await _admin_count(session) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This is the last administrator; promote someone else before "
                "deleting this account."
            ),
        )
    if user.id == caller.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account; ask another administrator.",
        )

    # The rows an account leaves behind keep their text: post-mortems and follow-ups reference
    # the user with ON DELETE SET NULL, so a departed colleague's document stays readable.
    await session.delete(user)
    await session.commit()
