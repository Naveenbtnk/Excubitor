from collections.abc import AsyncIterator

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.rbac import Role
from app.core.security import hash_password
from app.db.base import Base
from app.models import Alert, User
from app.services.simulator import SCENARIOS

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def create_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


async def seed_admin() -> None:
    statement = (
        insert(User)
        .values(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            name="Cloud Admin",
            role="admin",
        )
        .on_conflict_do_nothing(index_elements=[User.email])
    )
    async with SessionLocal() as session:
        await session.execute(statement)
        await session.commit()


async def seed_demo_account() -> None:
    """Provision the shared read-only login, and keep it read-only.

    Idempotent by design, and deliberately not "create it once and then leave it alone": every
    start re-asserts the two properties that make this account safe to publish. The role is
    forced back to Viewer, because a promotion made in the UI -- by mistake or by someone who
    found the account -- must not survive a restart. The password hash is rewritten from the
    environment, so the configured value remains the only one that works and rotating it is a
    one-line change rather than a database edit.

    Written as one upsert rather than a read followed by a write. Every worker runs the lifespan,
    so two of them reach this at the same instant on a cold start; a select-then-insert loses that
    race, and the loser dies on the unique constraint before it serves a request -- which takes
    the whole service down with it, since Gunicorn refuses to boot a worker that cannot start.
    The insert-or-update is atomic, so the workers cannot disagree about the outcome.

    A disabled or password-less feature provisions nothing at all; the sign-in route refuses the
    credential in that case, so an account left behind by an earlier deployment cannot be used.
    """
    if not settings.demo_login_available:
        return

    statement = (
        insert(User)
        .values(
            email=settings.demo_account_email,
            password_hash=hash_password(settings.demo_account_password),
            name=settings.demo_account_name,
            role=Role.VIEWER.value,
        )
        .on_conflict_do_update(
            index_elements=[User.email],
            set_={
                "role": Role.VIEWER.value,
                "password_hash": hash_password(settings.demo_account_password),
            },
        )
    )
    async with SessionLocal() as session:
        await session.execute(statement)
        await session.commit()


async def seed_demo_alerts() -> None:
    """Three invented alerts, so a fresh database is not an empty screen."""
    async with SessionLocal() as session:
        has_alerts = await session.scalar(select(Alert.id).limit(1))
        if has_alerts:
            return
        session.add_all(Alert(**scenario) for scenario in SCENARIOS[:3])
        await session.commit()


async def seed_demo_data() -> None:
    """Seed the demo alerts unless the deployment has turned them off.

    Split out from the call site so the decision is one function with a name, rather than an
    `if` in the lifespan that has to be read to find out what it does.
    """
    if settings.should_seed_demo_data:
        await seed_demo_alerts()


async def close_db() -> None:
    await engine.dispose()
