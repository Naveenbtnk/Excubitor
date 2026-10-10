"""Integration test harness.

These tests talk to a real PostgreSQL/pgvector database. They use an isolated
``cloudops_ai_test`` database on the same host as the configured ``DATABASE_URL``
(override with ``TEST_DATABASE_URL``), so the development database is never touched.
The LLM provider is replaced with an offline fake: no test may call a paid API.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://cloudops:cloudops@localhost:5434/cloudops_ai_test"


def _dotenv_database_url() -> str | None:
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "DATABASE_URL" and value.strip():
            return value.strip().strip("\"'")
    return None


def _test_database_url() -> str:
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        return explicit
    configured = _dotenv_database_url()
    if configured and "/" in configured:
        return configured.rsplit("/", 1)[0] + "/cloudops_ai_test"
    return DEFAULT_TEST_DATABASE_URL


TEST_DATABASE_URL = _test_database_url()
TEST_DATABASE_NAME = TEST_DATABASE_URL.rsplit("/", 1)[1]
ADMIN_DATABASE_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"

# Settings and the SQLAlchemy engine are built at import time, so the environment has to be
# redirected to the test database before any app module is imported.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "integration-test-secret-long-enough-for-hs256"
os.environ["ADMIN_EMAIL"] = "admin@cloudops.local"
os.environ["ADMIN_PASSWORD"] = "ChangeMe123!"
os.environ["GEMINI_API_KEY"] = "integration-test-key"
os.environ["GEMINI_MODEL"] = "gemini-test-model"
os.environ["LLM_PROVIDER"] = "gemini"
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.counter_store import get_counter_store  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal, seed_admin  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.schemas.postmortems import PostMortemDraft  # noqa: E402
from app.services import llm as llm_module  # noqa: E402
from app.services.llm.base import LLMProvider, Message, StructuredModel  # noqa: E402

TABLES = (
    "incident_events",
    "post_mortem_actions",
    "post_mortems",
    "runbook_chunks",
    "incidents",
    "alerts",
    "users",
)

# Password for accounts the RBAC tests create; anything hashed the same way is fine.
TEST_USER_PASSWORD = "Role-Test-Passw0rd!"


class FakeLLM(LLMProvider):
    """Deterministic offline stand-in for the provider-swappable LLM layer."""

    def __init__(self) -> None:
        self.chat_calls: list[list[Message]] = []

    async def health(self) -> str:
        return "ok"

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        response_model: type[StructuredModel] | None = None,
    ) -> str | StructuredModel:
        self.chat_calls.append(messages)
        if response_model is PostMortemDraft:
            return response_model(
                narrative="Fake answer from the LLM.",
                timeline_summary="Pool exhaustion opened at 09:12 and was triaged at 09:13.",
                root_cause="Connection leak",
                contributing_factors=["Pool size unchanged since March"],
                impact="orders-api returned 500s for four minutes.",
                action_items=["Drain idle sessions", "Restart orders-api"],
            )
        if response_model is not None:
            # TriageResult and PostMortemDraft are the two structured calls the app makes.
            return response_model(
                summary="Pool exhausted on orders-api",
                root_cause="Connection leak",
                remediation_steps=["Drain idle sessions", "Restart orders-api"],
                confidence=0.75,
            )
        return "Fake answer from the LLM."

    async def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> AsyncIterator[str]:
        for token in ("Fake ", "streamed ", "answer."):
            yield token

    async def embed(self, text: str) -> list[float]:
        return [0.01] * settings.embedding_dimensions


async def _create_database_if_missing() -> None:
    engine = create_async_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("select 1 from pg_database where datname = :name"),
                {"name": TEST_DATABASE_NAME},
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE_NAME}"'))
    finally:
        await engine.dispose()


def _run_migrations() -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def prepared_database() -> Iterator[None]:
    # Both steps are synchronous on purpose: alembic's env.py calls asyncio.run() itself.
    asyncio.run(_create_database_if_missing())
    _run_migrations()
    # The test database is left in place so repeat runs stay fast; every test truncates.
    yield


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    provider = FakeLLM()
    original = llm_module.get_llm
    for module in list(sys.modules.values()):
        try:
            if getattr(module, "get_llm", None) is original:
                monkeypatch.setattr(module, "get_llm", lambda: provider)
        except Exception:  # pragma: no cover - defensive against exotic module objects
            continue
    return provider


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncIterator[None]:
    async with SessionLocal() as session:
        await session.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
    await seed_admin()
    # Both ceilings draw on the same store, so one reset covers the per-user limits and the
    # provider budget. Without it the budget would be shared across the whole session and
    # tests late in the run would be refused instead of exercising their own subject.
    await get_counter_store().reset()
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
async def authed_client(client: AsyncClient) -> AsyncClient:
    response = await client.post(
        "/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def make_user():
    """Create an account with a role, for tests that need someone other than the admin."""

    async def _make(email: str, role: str, name: str = "Test User") -> None:
        async with SessionLocal() as session:
            session.add(
                User(
                    email=email,
                    password_hash=hash_password(TEST_USER_PASSWORD),
                    name=name,
                    role=role,
                )
            )
            await session.commit()

    return _make


@pytest.fixture
def login_as(client: AsyncClient):
    """Sign the shared client in as an account created by ``make_user`` (or the admin)."""

    async def _login(email: str, password: str = TEST_USER_PASSWORD) -> AsyncClient:
        response = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, response.text
        return client

    return _login
