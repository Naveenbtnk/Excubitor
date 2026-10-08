"""Shared-counter behaviour.

The ceilings this project enforces are only as real as the counters behind them. Kept in
process they are per worker: three workers granting 20 requests a minute is 60 a minute, and
a provider budget of 20 is spent three times over before anyone notices. These tests cover
both backends — the in-memory fallback that is correct for one worker, and the Redis-backed
one that two independent "workers" have to agree on.

The Redis tests need a server. They skip, loudly, unless one answers at ``TEST_REDIS_URL``
(default ``redis://localhost:6379/15``) rather than pretending to pass: CI provides one, and
so does ``docker compose up -d redis``. The multi-worker tests use separate store instances,
which is exactly what a second process has, against the same server.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.api.routes import sandbox as sandbox_module
from app.core.config import settings
from app.core.counter_store import (
    MemoryCounterStore,
    RedisCounterStore,
    close_counter_stores,
)
from app.services.llm.base import LLMProvider
from app.services.llm.quota import current_quota
from app.services.llm.retry import build_provider

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")
TEST_PREFIX = "cloudops-test"
UNREACHABLE_REDIS_URL = "redis://localhost:1/0"


class ChatStub(LLMProvider):
    """Answers everything; the tests that use it only care about what reached the budget."""

    calls = 0

    async def chat(self, messages, *, system=None, response_model=None) -> str:
        ChatStub.calls += 1
        return "ok"


@pytest.fixture
async def redis_url() -> AsyncIterator[str]:
    """A reachable Redis, or a skip that says why nothing was proven."""
    store = RedisCounterStore(TEST_REDIS_URL, prefix=TEST_PREFIX, timeout=2.0)
    try:
        status = await store.ping()
        if status != "ok":
            pytest.skip(f"no Redis at {TEST_REDIS_URL} ({status})")
        await store.reset()
        yield TEST_REDIS_URL
        await store.reset()
    finally:
        await store.close()
        await close_counter_stores()


# --- the in-memory fallback --------------------------------------------------------------


async def test_the_memory_store_counts_within_its_window():
    store = MemoryCounterStore()

    assert await store.state("key", 60) == (0, 0)
    assert await store.check("key", 2, 60) is None
    assert await store.check("key", 2, 60) is None
    assert await store.check("key", 2, 60) >= 1

    used, resets_in = await store.state("key", 60)
    assert used == 2
    assert resets_in >= 1, "the oldest hit is still inside the window"

    await store.reset()
    assert await store.state("key", 60) == (0, 0)


async def test_the_memory_store_keeps_each_window_separate():
    """The 60s limit and the day-long budget must not erode each other's counters.

    A single sliding window prunes every key against the window it was asked about, so a
    day-long provider budget would be wiped by any short-window check. That is why one
    instance serves one window length.
    """
    store = MemoryCounterStore()

    assert await store.check("shared-key", 10, 86400) is None
    assert await store.check("shared-key", 10, 60) is None

    assert (await store.state("shared-key", 86400))[0] == 1, "the day-long counter survived"


async def test_the_memory_fallback_cannot_share_a_ceiling_between_workers():
    """The limitation Redis exists to remove, pinned as a contrast to the tests below."""
    first_worker, second_worker = MemoryCounterStore(), MemoryCounterStore()

    assert await first_worker.check("budget", 1, 86400) is None
    # The second worker has never heard of the first one's spending.
    assert await second_worker.check("budget", 1, 86400) is None


# --- the Redis-backed store --------------------------------------------------------------


async def test_two_workers_share_one_ceiling(redis_url: str):
    first_worker = RedisCounterStore(redis_url, prefix=TEST_PREFIX)
    second_worker = RedisCounterStore(redis_url, prefix=TEST_PREFIX)
    try:
        assert await first_worker.check("budget", 3, 86400) is None
        assert await first_worker.check("budget", 3, 86400) is None
        assert await first_worker.check("budget", 3, 86400) is None

        # The ceiling belongs to the deployment, not to the worker that happened to serve.
        assert await second_worker.check("budget", 3, 86400) >= 1
        assert (await second_worker.state("budget", 86400))[0] == 3
    finally:
        await first_worker.close()
        await second_worker.close()


async def test_the_shared_ceiling_holds_under_concurrency(redis_url: str):
    """Concurrent claims must not lose updates: exactly ``limit`` of them may pass."""
    workers = [RedisCounterStore(redis_url, prefix=TEST_PREFIX) for _ in range(3)]
    try:
        results = await asyncio.gather(
            *[worker.check("burst", 5, 86400) for worker in workers for _ in range(10)]
        )
    finally:
        for worker in workers:
            await worker.close()

    allowed = [result for result in results if result is None]
    assert len(allowed) == 5, f"expected exactly 5 grants, got {len(allowed)}"
    assert len(results) == 30, "every claim answered, none lost"


async def test_the_shared_window_gives_capacity_back(redis_url: str):
    store = RedisCounterStore(redis_url, prefix=TEST_PREFIX)
    try:
        assert await store.check("short", 1, 1) is None
        assert await store.check("short", 1, 1) >= 1

        await asyncio.sleep(1.2)

        assert await store.check("short", 1, 1) is None, "the window rolled over"
    finally:
        await store.close()


async def test_an_unreachable_redis_degrades_to_this_worker(redis_url: str):
    """A lost Redis narrows the ceiling to one worker; it must never remove it."""
    store = RedisCounterStore(UNREACHABLE_REDIS_URL, prefix=TEST_PREFIX, timeout=0.2)
    try:
        assert (await store.ping()).startswith("unreachable: ")

        assert await store.check("degraded", 1, 86400) is None
        assert await store.check("degraded", 1, 86400) >= 1, "still limited, just locally"
        assert (await store.state("degraded", 86400))[0] == 1
    finally:
        await store.close()


# --- the app on a shared store -----------------------------------------------------------


async def test_a_route_draws_on_the_same_budget_as_another_worker(
    authed_client: AsyncClient, monkeypatch, redis_url: str
):
    """The end-to-end claim: one worker spends the budget, the API refuses on it."""
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(settings, "redis_key_prefix", TEST_PREFIX)
    monkeypatch.setattr(settings, "llm_quota_limit", 2)
    monkeypatch.setattr(settings, "llm_quota_window_seconds", 86400)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: build_provider(ChatStub()))

    # Another process would hold its own client and nothing else.
    other_worker = RedisCounterStore(redis_url, prefix=TEST_PREFIX)
    try:
        key = current_quota().key
        assert await other_worker.check(key, 2, 86400) is None
        assert await other_worker.check(key, 2, 86400) is None
    finally:
        await other_worker.close()

    refused = await authed_client.post("/sandbox/chat", json={"message": "refused"})

    assert refused.status_code == 503, refused.text
    assert refused.json()["error"]["code"] == "budget_exhausted"
    assert "allowance for now" in refused.json()["detail"]
    assert ChatStub.calls == 0, "the refused call never reached the provider"

    health = await authed_client.get("/health/llm")
    assert health.json()["quota"]["store"] == "redis"
    assert health.json()["quota"]["used"] == 2


async def test_health_names_the_shared_backend(
    authed_client: AsyncClient, monkeypatch, redis_url: str
):
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(settings, "redis_key_prefix", TEST_PREFIX)

    response = await authed_client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json()["counters"] == "ok"


async def test_health_reports_an_unreachable_shared_backend_without_failing(
    authed_client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "redis_url", UNREACHABLE_REDIS_URL)
    monkeypatch.setattr(settings, "redis_timeout_seconds", 0.2)
    await close_counter_stores()
    try:
        response = await authed_client.get("/health")

        assert response.status_code == 200, "an optional dependency must not fail the probe"
        assert response.json()["counters"].startswith("unreachable: ")
    finally:
        await close_counter_stores()
