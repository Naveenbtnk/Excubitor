"""Provider budget behaviour.

A per-minute limit per user cannot protect a per-day project quota. Gemini's free tier
allows 20 ``generateContent`` requests per day, per project, per model, so a single busy
user following ``LLM_RATE_LIMIT`` can spend the whole day's allowance inside a minute and
leave every later caller holding a provider 429 that lasts for hours. The budget in
:mod:`app.services.llm.quota` is that missing ceiling, and it counts upstream *attempts*
rather than HTTP requests: one request can embed once per chunk, and a replayed attempt is
another request as far as the provider is concerned.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

import app.core.window as window_module
from app.api.routes import sandbox as sandbox_module
from app.core.config import Settings, settings
from app.services import rag as rag_module
from app.services.llm.base import LLMProvider, LLMTransientError, Message
from app.services.llm.quota import ProviderQuota, QuotaSnapshot, current_quota
from app.services.llm.retry import (
    QuotaGuardedProvider,
    RetryingProvider,
    RetryPolicy,
    build_provider,
)

HELLO = [Message(role="user", content="hello")]


class ChatStub(LLMProvider):
    """Answers everything and counts the calls that reached it."""

    def __init__(self) -> None:
        self.calls = 0

    async def health(self) -> str:
        return "ok"

    async def chat(self, messages, *, system=None, response_model=None) -> str:
        self.calls += 1
        return "ok"

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        return [0.01] * settings.embedding_dimensions


class FlakyChat(ChatStub):
    """Fails transiently for the first ``failures`` calls, then answers."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def chat(self, messages, *, system=None, response_model=None) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise LLMTransientError("503 UNAVAILABLE")
        return "ok"


class StallOnceStream(ChatStub):
    """Fails before emitting anything once, so the retry wrapper replays the stream."""

    def __init__(self) -> None:
        super().__init__()
        self.streams = 0

    async def stream(self, messages, *, system=None) -> AsyncIterator[str]:
        self.streams += 1
        if self.streams == 1:
            raise LLMTransientError("503 UNAVAILABLE. stream broke before the first token")
        yield "recovered"


class PartialThenBreak(ChatStub):
    """Emits a token and only then fails, so the stream can never be replayed."""

    async def stream(self, messages, *, system=None) -> AsyncIterator[str]:
        yield "partial"
        raise LLMTransientError("503 UNAVAILABLE. stream broke mid-flight")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A monotonic clock we can advance, so window expiry needs no real waiting."""
    now = [1_000.0]
    monkeypatch.setattr(window_module, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skip(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", skip)


def quota(limit: int, window_seconds: int = 3600) -> ProviderQuota:
    return ProviderQuota(key="gemini:gemini-test-model", limit=limit, window_seconds=window_seconds)


def instant_policy(attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(attempts=attempts, base_delay=0.0, max_delay=0.0)


# --- the budget itself -----------------------------------------------------------------


def test_the_default_budget_matches_the_provider_free_tier():
    assert Settings(llm_provider="gemini").effective_llm_quota_limit == 20
    # A local runtime has no quota to protect, so it is not capped unless asked.
    assert Settings(llm_provider="ollama").effective_llm_quota_limit == 0
    # An explicit setting always wins, in both directions.
    assert Settings(llm_provider="gemini", llm_quota_limit=1500).effective_llm_quota_limit == 1500
    assert Settings(llm_provider="ollama", llm_quota_limit=25).effective_llm_quota_limit == 25
    assert Settings(llm_provider="gemini", llm_quota_limit=0).effective_llm_quota_limit == 0


def test_the_budget_refuses_the_call_once_the_allowance_is_spent(clock):
    subject = quota(limit=2)

    assert subject.reserve() is None
    assert subject.reserve() is None
    assert subject.reserve() == 3601, "the whole window is still ahead of the oldest hit"

    clock[0] += 3601
    assert subject.reserve() is None, "capacity comes back once the oldest hit ages out"


def test_the_snapshot_reports_the_remaining_allowance(clock):
    subject = quota(limit=5)

    assert subject.snapshot() == QuotaSnapshot(
        limit=5, used=0, remaining=5, window_seconds=3600, resets_in=0
    )

    subject.reserve()
    subject.reserve()
    assert (subject.snapshot().used, subject.snapshot().remaining) == (2, 3)

    clock[0] += 3601
    assert (subject.snapshot().used, subject.snapshot().remaining) == (0, 5)


def test_a_disabled_budget_never_refuses_and_has_no_snapshot():
    for subject in (quota(limit=0), quota(limit=5, window_seconds=0)):
        assert not subject.enabled
        assert subject.snapshot() is None
        assert [subject.reserve() for _ in range(50)] == [None] * 50


# --- the guard -------------------------------------------------------------------------


async def test_the_guard_refuses_locally_with_the_wait_that_is_left():
    provider = ChatStub()
    guarded = QuotaGuardedProvider(provider, quota(limit=2))

    assert await guarded.chat(HELLO) == "ok"
    assert await guarded.chat(HELLO) == "ok"

    with pytest.raises(LLMTransientError) as caught:
        await guarded.chat(HELLO)

    assert caught.value.replayable is False, "replaying would only burn more quota"
    assert caught.value.retry_after is not None and caught.value.retry_after >= 1
    assert provider.calls == 2, "the refused call never reached the provider"
    assert "LLM_QUOTA_LIMIT" in str(caught.value), "the error names the knob that fixes it"


async def test_health_probes_are_exempt_from_the_budget():
    subject = quota(limit=1)
    guarded = QuotaGuardedProvider(ChatStub(), subject)

    assert await guarded.health() == "ok"
    assert subject.snapshot().used == 0, "a monitoring loop must not spend the allowance"


async def test_a_stream_nobody_consumes_claims_nothing():
    subject = quota(limit=1)
    guarded = QuotaGuardedProvider(ChatStub(), subject)

    stream = guarded.stream(HELLO)
    assert subject.snapshot().used == 0
    await stream.aclose()


# --- how attempts are counted ----------------------------------------------------------


async def test_replayed_attempts_are_charged_too(no_sleep):
    provider = FlakyChat(failures=1)
    subject = quota(limit=5)
    wrapped = RetryingProvider(QuotaGuardedProvider(provider, subject), instant_policy())

    assert await wrapped.chat(HELLO) == "ok"
    assert provider.calls == 2, "the first attempt was replayed"
    assert subject.snapshot().used == 2, "one unit per upstream attempt, not per call"


async def test_a_spent_budget_stops_a_retry_mid_call(no_sleep):
    provider = FlakyChat(failures=99)
    subject = quota(limit=1)
    wrapped = RetryingProvider(QuotaGuardedProvider(provider, subject), instant_policy())

    with pytest.raises(LLMTransientError) as caught:
        await wrapped.chat(HELLO)

    assert provider.calls == 1, "the retry never reached the provider"
    assert caught.value.replayable is False, "the refusal is the budget's, not the upstream 503"
    assert caught.value.retry_after is not None


async def test_a_replayed_stream_claims_one_unit_per_attempt(no_sleep):
    provider = StallOnceStream()
    subject = quota(limit=5)
    wrapped = RetryingProvider(QuotaGuardedProvider(provider, subject), instant_policy())

    chunks = [chunk async for chunk in wrapped.stream(HELLO)]

    assert chunks == ["recovered"]
    assert provider.streams == 2
    assert subject.snapshot().used == 2


async def test_the_built_provider_charges_the_configured_budget(monkeypatch, no_sleep):
    monkeypatch.setattr(settings, "llm_quota_limit", 3)
    monkeypatch.setattr(settings, "llm_quota_window_seconds", 3600)
    provider = FlakyChat(failures=1)

    assert await build_provider(provider).chat(HELLO) == "ok"

    snapshot = current_quota().snapshot()
    assert (snapshot.used, snapshot.remaining) == (2, 1)


def test_the_budget_is_charged_inside_the_retry_wrapper(monkeypatch):
    monkeypatch.setattr(settings, "llm_quota_limit", 5)

    wrapped = build_provider(ChatStub())

    assert isinstance(wrapped, RetryingProvider)
    assert isinstance(wrapped.inner, QuotaGuardedProvider)


# --- the HTTP surface ------------------------------------------------------------------


async def test_health_reports_the_remaining_budget(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "llm_quota_limit", 20)
    monkeypatch.setattr(settings, "llm_quota_window_seconds", 86400)

    response = await client.get("/health/llm")

    assert response.status_code == 200, response.text
    assert response.json()["quota"] == {
        "limit": 20,
        "used": 0,
        "remaining": 20,
        "window_seconds": 86400,
        "resets_in": 0,
    }


async def test_a_disabled_budget_is_reported_as_absent(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "llm_quota_limit", 0)

    response = await client.get("/health/llm")

    assert response.status_code == 200, response.text
    assert response.json()["quota"] is None


async def test_chat_route_answers_503_with_the_wait_that_is_left(
    authed_client: AsyncClient, monkeypatch, no_sleep
):
    monkeypatch.setattr(settings, "llm_quota_limit", 2)
    monkeypatch.setattr(settings, "llm_quota_window_seconds", 86400)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: build_provider(ChatStub()))

    for message in ("one", "two"):
        allowed = await authed_client.post("/sandbox/chat", json={"message": message})
        assert allowed.status_code == 200, allowed.text

    refused = await authed_client.post("/sandbox/chat", json={"message": "three"})

    assert refused.status_code == 503
    assert int(refused.headers["Retry-After"]) >= 86400, "the real wait, not a 60s guess"
    assert "refused locally" in refused.json()["detail"]


async def test_a_stream_refused_mid_response_ends_with_an_error_event(
    authed_client: AsyncClient, monkeypatch
):
    """SSE headers are committed before the first token, so a refusal cannot become a 503.

    Starlette will not rewrite a started response and raises ``RuntimeError`` instead, which
    aborts the connection; a terminal event keeps the failure legible to the caller.
    """
    monkeypatch.setattr(settings, "llm_quota_limit", 1)
    monkeypatch.setattr(settings, "llm_quota_window_seconds", 86400)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: build_provider(ChatStub()))
    spent = await authed_client.post("/sandbox/chat", json={"message": "spend it"})
    assert spent.status_code == 200

    stream = await authed_client.post("/sandbox/stream", json={"message": "refused"})

    assert stream.status_code == 200, "the status line was already sent"
    assert "refused locally" in stream.text
    assert stream.text.index("event: error") < stream.text.index("event: done")


async def test_a_stream_that_breaks_after_output_ends_with_an_error_event(
    authed_client: AsyncClient, monkeypatch, no_sleep
):
    monkeypatch.setattr(settings, "llm_quota_limit", 5)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: build_provider(PartialThenBreak()))

    stream = await authed_client.post("/sandbox/stream", json={"message": "hello"})

    assert stream.status_code == 200
    # The token that did arrive is delivered exactly once, and the failure is announced.
    assert stream.text.count("partial") == 1
    assert "stream broke mid-flight" in stream.text
    assert stream.text.index("event: error") < stream.text.index("event: done")


async def test_one_request_that_embeds_many_chunks_is_charged_per_call(
    authed_client: AsyncClient, monkeypatch, no_sleep
):
    monkeypatch.setattr(settings, "llm_quota_limit", 2)
    monkeypatch.setattr(settings, "llm_quota_window_seconds", 86400)
    provider = ChatStub()
    monkeypatch.setattr(rag_module, "get_llm", lambda: build_provider(provider))
    long_runbook = {
        "title": "Long runbook",
        "source": "manual",
        "content": " ".join(f"step {index}" for index in range(1200)),
    }

    response = await authed_client.post("/knowledge/runbooks", json=long_runbook)

    assert response.status_code == 503, response.text
    assert provider.calls == 2, "the third chunk was refused before it was sent"
    assert current_quota().snapshot().remaining == 0

    # A refused chunk must not leave half a runbook behind.
    listed = await authed_client.get("/knowledge/runbooks")
    assert listed.json()["total"] == 0
