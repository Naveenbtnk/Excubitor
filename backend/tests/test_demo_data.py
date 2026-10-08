"""Demo alerts are a convenience that has to be switchable off.

A production database that starts with three invented incidents is worse than an empty one:
somebody has to work out whether they are real before they can trust the screen.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.config import Settings, settings
from app.db.session import SessionLocal, seed_demo_data
from app.models import Alert


async def alert_count() -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(Alert)) or 0


def test_the_default_follows_the_environment():
    assert Settings(environment="development").should_seed_demo_data is True
    assert Settings(environment="production").should_seed_demo_data is False


def test_the_flag_overrides_the_environment_either_way():
    assert Settings(environment="production", seed_demo_data=True).should_seed_demo_data is True
    assert Settings(environment="development", seed_demo_data=False).should_seed_demo_data is False


async def test_demo_alerts_are_seeded_outside_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "seed_demo_data", None)

    await seed_demo_data()

    assert await alert_count() == 3


async def test_a_clean_start_can_be_asked_for(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "seed_demo_data", False)

    await seed_demo_data()

    assert await alert_count() == 0


async def test_production_is_clean_unless_it_asks_for_demo_data(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "seed_demo_data", None)

    await seed_demo_data()

    assert await alert_count() == 0


async def test_the_flag_can_also_turn_seeding_on_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "seed_demo_data", True)

    await seed_demo_data()

    assert await alert_count() == 3


async def test_seeding_twice_does_not_duplicate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "seed_demo_data", True)

    await seed_demo_data()
    await seed_demo_data()

    assert await alert_count() == 3


async def test_an_empty_alert_list_is_what_a_clean_start_shows(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The UI side of the same decision: no demo rows means an empty queue, not an error."""
    monkeypatch.setattr(settings, "seed_demo_data", False)
    await seed_demo_data()

    body = (await authed_client.get("/alerts")).json()

    assert body["total"] == 0
    assert body["items"] == []
