"""Retry behaviour for transient LLM provider failures.

Two layers are covered: the pure backoff/classification logic (no I/O at all) and the HTTP
surface, where a replayed request must succeed and an exhausted one must turn into a
retryable 503 rather than a hard 502.
"""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from httpx import AsyncClient

from app.api.routes import sandbox as sandbox_module
from app.core.config import Settings, settings
from app.services.llm.base import LLMError, LLMProvider, LLMTransientError, Message
from app.services.llm.gemini import error_body, http_options, translate_error
from app.services.llm.ollama import request_timeout, stream_timeout
from app.services.llm.retry import (
    AttemptTimeouts,
    QuotaGuardedProvider,
    RetryingProvider,
    RetryPolicy,
    build_provider,
    classify_http_error,
    is_retryable_status,
    optional_seconds,
    parse_retry_after,
    requires_backoff,
    retry_hint_from_payload,
)

HELLO = [Message(role="user", content="hello")]


class ScriptedChat(LLMProvider):
    """Returns a scripted sequence where an ``Exception`` entry is raised instead."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def chat(self, messages, *, system=None, response_model=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ScriptedStream(LLMProvider):
    """Streams a scripted sequence of chunk lists, or raises before the first token."""

    def __init__(self, scripts: list[object]) -> None:
        self.scripts = list(scripts)
        self.calls = 0

    async def stream(self, messages, *, system=None) -> AsyncIterator[str]:
        self.calls += 1
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for chunk in script or ():
            yield chunk


class SlowProvider(LLMProvider):
    """Sleeps on its first ``slow_attempts`` calls, so deadlines can be exercised offline."""

    def __init__(self, delay: float, slow_attempts: int = 99) -> None:
        self.delay = delay
        self.slow_attempts = slow_attempts
        self.calls = 0

    async def chat(self, messages, *, system=None, response_model=None):
        self.calls += 1
        if self.calls <= self.slow_attempts:
            await asyncio.sleep(self.delay)
        return "Answered in time."


class SlowHealth(LLMProvider):
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    async def health(self) -> str:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return "ok"


class SlowThenFastStream(LLMProvider):
    """Stalls before its first token, then streams normally on the next attempt."""

    def __init__(self, first_delay: float) -> None:
        self.first_delay = first_delay
        self.calls = 0

    async def stream(self, messages, *, system=None) -> AsyncIterator[str]:
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(self.first_delay)
        yield "recovered"


class PartialThenStall(LLMProvider):
    """Emits one token, then goes quiet for longer than the idle deadline.

    The gap is what proves streamed content is never replayed: the first token already
    reached the client, so a second upstream call would duplicate it.
    """

    def __init__(self, gap: float) -> None:
        self.gap = gap
        self.calls = 0

    async def stream(self, messages, *, system=None) -> AsyncIterator[str]:
        self.calls += 1
        yield "partial"
        await asyncio.sleep(self.gap)
        yield "never delivered"


class EndlessStream(LLMProvider):
    """Streams forever; used to prove an early exit closes the upstream generator."""

    def __init__(self) -> None:
        self.closed = False

    async def stream(self, messages, *, system=None) -> AsyncIterator[str]:
        try:
            while True:
                yield "tick"
                await asyncio.sleep(0)
        finally:
            self.closed = True


class PartialThenFail(ScriptedStream):
    """Emits one token and only then hits a transient upstream failure."""

    async def stream(self, messages, *, system=None) -> AsyncIterator[str]:
        self.calls += 1
        yield "partial"
        raise LLMTransientError("502 UNAVAILABLE. stream broke mid-flight")


@pytest.fixture
def recorded_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replaces the backoff sleep with a recorder so tests stay instantaneous."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return sleeps


def policy(attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(attempts=attempts, base_delay=0.5, max_delay=8.0)


def instant_policy(attempts: int = 3) -> RetryPolicy:
    """Backoff timing has its own tests; deadline tests must not sleep for real."""
    return RetryPolicy(attempts=attempts, base_delay=0.0, max_delay=0.0)


# --- backoff policy -------------------------------------------------------------------


def test_backoff_grows_exponentially_then_stops_at_the_cap():
    backoff = RetryPolicy(attempts=6, base_delay=0.5, max_delay=2.0)
    ceilings = [backoff.delay_for(attempt, rand=lambda _low, high: high) for attempt in range(5)]
    assert ceilings == [0.5, 1.0, 2.0, 2.0, 2.0]


def test_backoff_is_jittered_inside_the_ceiling():
    bounds: list[tuple[float, float]] = []

    def record(low: float, high: float) -> float:
        bounds.append((low, high))
        return low

    assert RetryPolicy(attempts=3, base_delay=1.0, max_delay=10.0).delay_for(2, rand=record) == 0.0
    # full jitter: the whole [0, ceiling] range is available, never a fixed delay
    assert bounds == [(0.0, 4.0)]


def test_backoff_is_zero_when_no_delay_is_configured():
    assert RetryPolicy(attempts=3, base_delay=0.0, max_delay=8.0).delay_for(1) == 0.0
    assert RetryPolicy(attempts=3, base_delay=1.0, max_delay=0.0).delay_for(1) == 0.0


def test_policy_reads_bounded_retries_from_settings():
    configured = Settings(llm_max_retries=4, llm_retry_base_delay=0.1, llm_retry_max_delay=3.0)
    built = RetryPolicy.from_settings(configured)
    assert (built.attempts, built.retries) == (5, 4)
    assert (built.base_delay, built.max_delay) == (0.1, 3.0)

    assert RetryPolicy.from_settings(Settings(llm_max_retries=-3)).attempts == 1
    assert RetryPolicy.from_settings(Settings(llm_max_retries=0)).retries == 0


def test_build_provider_wraps_unless_every_layer_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    wrapped = build_provider(ScriptedChat(["ok"]))
    assert isinstance(wrapped, RetryingProvider)
    assert wrapped.policy.attempts == 3
    assert wrapped.timeouts.request == 30.0
    # The budget is charged inside the retry wrapper, so replayed attempts are counted too.
    assert isinstance(wrapped.inner, QuotaGuardedProvider)

    # Retries off but deadlines on: the per-attempt timeout still has to be enforced.
    monkeypatch.setattr(settings, "llm_max_retries", 0)
    assert isinstance(build_provider(ScriptedChat(["ok"])), RetryingProvider)

    # Retries and deadlines off: the provider budget still applies, since it is the only
    # thing standing between a busy user and a spent daily allowance.
    monkeypatch.setattr(settings, "llm_request_timeout", 0)
    monkeypatch.setattr(settings, "llm_stream_idle_timeout", 0)
    assert isinstance(build_provider(ScriptedChat(["ok"])), QuotaGuardedProvider)

    # Only with the budget disabled as well does the provider go out unwrapped.
    monkeypatch.setattr(settings, "llm_quota_limit", 0)
    plain = ScriptedChat(["ok"])
    assert build_provider(plain) is plain


# --- replay behaviour -----------------------------------------------------------------


async def test_transient_failures_are_replayed(recorded_sleep):
    provider = ScriptedChat([LLMTransientError("503 UNAVAILABLE"), "recovered"])
    wrapped = RetryingProvider(provider, policy())

    assert await wrapped.chat(HELLO) == "recovered"
    assert provider.calls == 2
    assert len(recorded_sleep) == 1
    assert 0.0 <= recorded_sleep[0] <= 0.5


async def test_quota_failures_are_handed_back_to_the_caller(recorded_sleep):
    """A 429 waits on a window this request cannot hold, so replaying it only burns quota."""
    provider = ScriptedChat([LLMTransientError("429 RESOURCE_EXHAUSTED", replayable=False)])
    wrapped = RetryingProvider(provider, policy())

    with pytest.raises(LLMTransientError) as raised:
        await wrapped.chat(HELLO)
    assert raised.value.replayable is False
    assert provider.calls == 1
    assert recorded_sleep == []


async def test_quota_failures_also_bypass_stream_replay(recorded_sleep):
    provider = ScriptedStream([LLMTransientError("429", replayable=False)])
    wrapped = RetryingProvider(provider, policy())

    with pytest.raises(LLMTransientError):
        async for _ in wrapped.stream(HELLO):
            pass
    assert provider.calls == 1
    assert recorded_sleep == []


async def test_permanent_failures_are_never_replayed(recorded_sleep):
    provider = ScriptedChat([LLMError("model not found")])
    wrapped = RetryingProvider(provider, policy())

    with pytest.raises(LLMError) as raised:
        await wrapped.chat(HELLO)
    assert type(raised.value) is LLMError
    assert provider.calls == 1
    assert recorded_sleep == []


async def test_exhausted_retries_raise_the_last_error_without_a_trailing_sleep(recorded_sleep):
    provider = ScriptedChat([LLMTransientError("503")] * 3)
    wrapped = RetryingProvider(provider, policy(attempts=3))

    with pytest.raises(LLMTransientError):
        await wrapped.chat(HELLO)
    assert provider.calls == 3
    assert len(recorded_sleep) == 2


async def test_stream_retries_only_before_the_first_token(recorded_sleep):
    provider = ScriptedStream([LLMTransientError("503 UNAVAILABLE"), ["a", "b"]])
    wrapped = RetryingProvider(provider, policy())

    chunks = [chunk async for chunk in wrapped.stream(HELLO)]
    assert chunks == ["a", "b"]
    assert provider.calls == 2
    assert len(recorded_sleep) == 1


async def test_stream_failure_after_output_is_not_replayed(recorded_sleep):
    provider = PartialThenFail([])
    wrapped = RetryingProvider(provider, policy())

    chunks: list[str] = []
    with pytest.raises(LLMTransientError):
        async for chunk in wrapped.stream(HELLO):
            chunks.append(chunk)

    assert chunks == ["partial"]
    assert provider.calls == 1
    assert recorded_sleep == []


async def test_health_is_never_retried(monkeypatch: pytest.MonkeyPatch):
    class FlakyHealth(LLMProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def health(self) -> str:
            self.calls += 1
            return "unreachable: 503 UNAVAILABLE"

    inner = FlakyHealth()
    assert await RetryingProvider(inner, policy()).health() == "unreachable: 503 UNAVAILABLE"
    assert inner.calls == 1


# --- per-attempt deadlines -------------------------------------------------------------


def test_timeouts_are_read_from_settings_and_zero_disables_the_bound():
    configured = Settings(
        llm_request_timeout=12.5, llm_stream_idle_timeout=90.0, llm_connect_timeout=2.5
    )
    bounds = AttemptTimeouts.from_settings(configured)
    assert (bounds.request, bounds.stream_idle, bounds.connect) == (12.5, 90.0, 2.5)
    assert bounds.is_disabled is False

    disabled = AttemptTimeouts.from_settings(
        Settings(llm_request_timeout=0, llm_stream_idle_timeout=0)
    )
    assert disabled.request is None and disabled.stream_idle is None
    assert disabled.is_disabled is True
    assert optional_seconds(0) is None and optional_seconds(-1) is None
    assert optional_seconds(3.5) == 3.5


def test_socket_scalar_uses_the_laxer_bound_so_it_cannot_cut_a_stream_short():
    assert AttemptTimeouts(request=30.0, stream_idle=60.0).socket_timeout_ms == 60000
    assert AttemptTimeouts(request=45.0, stream_idle=10.0).socket_timeout_ms == 45000
    assert AttemptTimeouts(request=None, stream_idle=None).socket_timeout_ms is None


def test_both_provider_clients_use_the_configured_timeouts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_request_timeout", 12.0)
    monkeypatch.setattr(settings, "llm_stream_idle_timeout", 45.0)
    monkeypatch.setattr(settings, "llm_connect_timeout", 3.0)

    per_attempt, per_idle = request_timeout(), stream_timeout()
    assert (per_attempt.connect, per_attempt.read) == (3.0, 12.0)
    assert (per_idle.connect, per_idle.read) == (3.0, 45.0)

    bounds = AttemptTimeouts.from_settings(settings)
    assert http_options(bounds).timeout == 45000
    assert http_options(AttemptTimeouts(request=None, stream_idle=None)).timeout is None


async def test_a_slow_attempt_times_out_and_is_replayed():
    provider = SlowProvider(delay=30.0, slow_attempts=1)
    wrapped = RetryingProvider(provider, instant_policy(), AttemptTimeouts(request=0.05))

    assert await wrapped.chat(HELLO) == "Answered in time."
    assert provider.calls == 2


async def test_every_attempt_timing_out_raises_a_replayable_error():
    provider = SlowProvider(delay=30.0)
    wrapped = RetryingProvider(provider, instant_policy(), AttemptTimeouts(request=0.05))

    with pytest.raises(LLMTransientError) as raised:
        await wrapped.chat(HELLO)
    assert "attempt timeout" in str(raised.value)
    assert raised.value.replayable is True
    assert provider.calls == 3


async def test_a_disabled_request_deadline_lets_a_slow_call_finish():
    provider = SlowProvider(delay=0.05)
    wrapped = RetryingProvider(
        provider, instant_policy(), AttemptTimeouts(request=None, stream_idle=None)
    )

    assert await wrapped.chat(HELLO) == "Answered in time."
    assert provider.calls == 1


async def test_health_is_bounded_but_still_never_retried():
    provider = SlowHealth(delay=30.0)
    wrapped = RetryingProvider(provider, instant_policy(), AttemptTimeouts(request=0.05))

    with pytest.raises(LLMTransientError) as raised:
        await wrapped.health()
    assert "health exceeded" in str(raised.value)
    assert provider.calls == 1


# --- stream deadlines ------------------------------------------------------------------


async def test_stream_stall_before_the_first_token_is_replayed():
    provider = SlowThenFastStream(first_delay=30.0)
    wrapped = RetryingProvider(provider, instant_policy(), AttemptTimeouts(stream_idle=0.05))

    assert [chunk async for chunk in wrapped.stream(HELLO)] == ["recovered"]
    assert provider.calls == 2


async def test_stream_stall_after_output_is_never_replayed():
    provider = PartialThenStall(gap=30.0)
    wrapped = RetryingProvider(provider, instant_policy(), AttemptTimeouts(stream_idle=0.05))

    chunks: list[str] = []
    with pytest.raises(LLMTransientError) as raised:
        async for chunk in wrapped.stream(HELLO):
            chunks.append(chunk)

    assert chunks == ["partial"]  # nothing is duplicated
    assert "stream exceeded" in str(raised.value)
    assert provider.calls == 1  # and no second paid request


async def test_stream_is_not_cut_off_by_the_idle_deadline_while_it_keeps_talking():
    provider = ScriptedStream([[f"chunk-{i}" for i in range(4)]])
    wrapped = RetryingProvider(provider, instant_policy(), AttemptTimeouts(stream_idle=0.05))

    assert [chunk async for chunk in wrapped.stream(HELLO)] == [
        "chunk-0",
        "chunk-1",
        "chunk-2",
        "chunk-3",
    ]
    assert provider.calls == 1


async def test_an_early_exit_closes_the_upstream_stream():
    provider = EndlessStream()
    wrapped = RetryingProvider(provider, policy())

    chunks = wrapped.stream(HELLO)
    assert await chunks.__anext__() == "tick"
    await chunks.aclose()

    assert provider.closed is True


# --- failure classification -----------------------------------------------------------


def test_retryable_statuses_accept_codes_and_canonical_names():
    assert is_retryable_status(429) and is_retryable_status(503)
    assert is_retryable_status("429") and is_retryable_status("unavailable")
    assert not is_retryable_status(400) and not is_retryable_status("INVALID_ARGUMENT")
    assert not is_retryable_status(None) and not is_retryable_status(True)


def test_only_quota_signals_require_waiting_out_a_window():
    assert requires_backoff(429) and requires_backoff("resource_exhausted")
    assert not requires_backoff(503) and not requires_backoff("UNAVAILABLE")
    assert not requires_backoff(400) and not requires_backoff(None) and not requires_backoff(True)


# The daily free-tier cap on gemini-3.8-flash, captured verbatim from a live 429. The
# provider states how long to wait, and it is hours rather than our own 60-second window.
FREE_TIER_QUOTA_BODY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                        "quotaValue": "20",
                    }
                ],
            },
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "56023.632260421s"},
        ],
    }
}


def test_provider_supplied_delays_are_parsed_in_every_shape_we_saw():
    assert parse_retry_after("60") == 60.0
    assert parse_retry_after("56023.632260421s") == 56023.632260421
    assert parse_retry_after("1.5S") == 1.5
    assert parse_retry_after(30) == 30.0
    assert parse_retry_after(7.5) == 7.5

    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    assert parse_retry_after("") is None
    assert parse_retry_after("-5") is None
    assert parse_retry_after(0) is None
    assert parse_retry_after(None) is None
    assert parse_retry_after(True) is None


def test_the_retry_hint_is_read_from_the_real_quota_body():
    assert retry_hint_from_payload(FREE_TIER_QUOTA_BODY) == 56023.632260421
    assert retry_hint_from_payload({"error": {"details": []}}) is None
    assert retry_hint_from_payload({"error": {"message": "no details"}}) is None
    assert retry_hint_from_payload(None) is None
    assert retry_hint_from_payload("not a body") is None


def test_the_error_body_is_found_whichever_attribute_the_sdk_uses():
    envelope = FREE_TIER_QUOTA_BODY
    assert error_body(SimpleNamespace(response_json=envelope)) is envelope
    assert error_body(SimpleNamespace(details=envelope)) is envelope
    assert error_body(SimpleNamespace()) is None

    # The pinned SDK takes ``response_json`` as a keyword but stores it as ``details``,
    # which is precisely how the hint went missing before this shim existed.
    live_error = genai_errors.ClientError(code=429, response_json=envelope)
    assert error_body(live_error) is envelope
    assert retry_hint_from_payload(error_body(live_error)) == 56023.632260421


def test_a_throttled_http_response_keeps_its_retry_after_header():
    request = httpx.Request("POST", "https://example.test/v1/chat")
    throttled = httpx.HTTPStatusError(
        "rate limited",
        request=request,
        response=httpx.Response(429, headers={"retry-after": "42"}, request=request),
    )
    hintless = httpx.HTTPStatusError(
        "rate limited", request=request, response=httpx.Response(429, request=request)
    )

    assert classify_http_error(throttled).retry_after == 42.0
    assert classify_http_error(hintless).retry_after is None


def test_http_failures_are_classified_by_cause():
    request = httpx.Request("POST", "https://example.test/v1/chat")

    overloaded = httpx.HTTPStatusError(
        "service unavailable", request=request, response=httpx.Response(503, request=request)
    )
    rejected = httpx.HTTPStatusError(
        "bad request", request=request, response=httpx.Response(400, request=request)
    )

    throttled = httpx.HTTPStatusError(
        "rate limited", request=request, response=httpx.Response(429, request=request)
    )

    assert isinstance(classify_http_error(overloaded), LLMTransientError)
    assert classify_http_error(overloaded).replayable is True
    assert isinstance(classify_http_error(httpx.ConnectTimeout("timed out")), LLMTransientError)
    assert isinstance(classify_http_error(httpx.ConnectError("refused")), LLMTransientError)
    assert classify_http_error(throttled).replayable is False
    assert type(classify_http_error(rejected)) is LLMError
    assert type(classify_http_error(httpx.InvalidURL("no scheme"))) is LLMError


def test_genai_failures_are_translated_the_way_they_arrive_in_production():
    overloaded = genai_errors.APIError(
        code=503,
        response_json={"error": {"message": "The model is overloaded.", "status": "UNAVAILABLE"}},
    )
    translated = translate_error(overloaded)
    assert isinstance(translated, LLMTransientError)
    assert translated.replayable is True
    assert "503 UNAVAILABLE" in str(translated)

    quota = genai_errors.ClientError(
        code=429,
        response_json={"error": {"message": "Quota exceeded.", "status": "RESOURCE_EXHAUSTED"}},
    )
    assert translate_error(quota).replayable is False
    assert translate_error(quota).retry_after is None  # no hint in this body

    capped = genai_errors.ClientError(code=429, response_json=FREE_TIER_QUOTA_BODY)
    translated_cap = translate_error(capped)
    assert translated_cap.replayable is False
    assert translated_cap.retry_after == 56023.632260421

    invalid = genai_errors.ClientError(
        code=400, response_json={"error": {"message": "bad model", "status": "INVALID_ARGUMENT"}}
    )
    assert type(translate_error(invalid)) is LLMError

    assert isinstance(translate_error(TimeoutError("deadline exceeded")), LLMTransientError)
    assert isinstance(translate_error(httpx.ReadTimeout("read timed out")), LLMTransientError)
    assert type(translate_error(httpx.InvalidURL("no scheme"))) is LLMError


# --- HTTP surface ---------------------------------------------------------------------


async def test_chat_route_replays_a_transient_provider_failure(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, recorded_sleep
):
    provider = ScriptedChat(
        [LLMTransientError("503 UNAVAILABLE. overloaded"), "Recovered answer."]
    )
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: RetryingProvider(provider, policy()))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 200, response.text
    assert response.json() == {"reply": "Recovered answer."}
    assert provider.calls == 2
    assert len(recorded_sleep) == 1


async def test_chat_route_reports_503_with_retry_after_when_retries_run_out(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, recorded_sleep
):
    provider = ScriptedChat([LLMTransientError("503 UNAVAILABLE")] * 3)
    fast = RetryPolicy(attempts=3, base_delay=0.01, max_delay=0.05)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: RetryingProvider(provider, fast))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]
    assert response.json()["error"]["code"] == "unavailable"
    # The upstream wording ("503 UNAVAILABLE. overloaded") stays in the log line.
    assert "overloaded" not in response.text
    assert response.headers["Retry-After"] == str(max(1, int(settings.llm_retry_max_delay)))
    assert provider.calls == 3


async def test_chat_route_advertises_the_providers_own_wait_over_our_guess(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """A daily cap is hours away, so the provider's number must win over ours."""
    exhausted = LLMTransientError(
        "429 RESOURCE_EXHAUSTED", replayable=False, retry_after=56023.6
    )
    provider = ScriptedChat([exhausted])
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: RetryingProvider(provider, policy()))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "56023"
    assert response.headers["Retry-After"] != str(settings.rate_limit_window_seconds)
    assert provider.calls == 1


async def test_chat_route_advertises_the_quota_window_for_a_rate_limit(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, recorded_sleep
):
    """A quota 429 skips local retries and comes back as 503 with the full window."""
    provider = ScriptedChat([LLMTransientError("429 RESOURCE_EXHAUSTED", replayable=False)])
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: RetryingProvider(provider, policy()))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(settings.rate_limit_window_seconds)
    assert provider.calls == 1
    assert recorded_sleep == []


async def test_chat_route_recovers_when_a_slow_attempt_is_retried(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "llm_request_timeout", 0.05)
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_base_delay", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay", 0.0)
    provider = SlowProvider(delay=30.0, slow_attempts=1)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: build_provider(provider))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 200, response.text
    assert response.json() == {"reply": "Answered in time."}
    assert provider.calls == 2


async def test_chat_route_answers_503_when_every_attempt_times_out(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "llm_request_timeout", 0.05)
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_base_delay", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay", 0.0)
    provider = SlowProvider(delay=30.0)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: build_provider(provider))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "timeout"
    assert response.json()["detail"].startswith("Gemini took too long to answer.")
    # Our own deadline phrasing is internal: it belongs in the log, not in a response.
    assert "attempt timeout" not in response.text
    assert response.headers["Retry-After"] == str(max(1, int(settings.llm_retry_max_delay)))
    assert provider.calls == 2


async def test_stream_route_replays_a_stall_before_the_first_token(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "llm_stream_idle_timeout", 0.05)
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_base_delay", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay", 0.0)
    provider = SlowThenFastStream(first_delay=30.0)
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: build_provider(provider))

    response = await authed_client.post("/sandbox/stream", json={"message": "hello"})

    assert response.status_code == 200
    assert "recovered" in response.text
    assert "event: done" in response.text
    assert provider.calls == 2


async def test_chat_route_keeps_502_for_a_permanent_provider_error(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, recorded_sleep
):
    provider = ScriptedChat([LLMError("GEMINI_API_KEY is not configured")])
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: RetryingProvider(provider, policy()))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    assert "could not complete this request" in response.json()["detail"]
    # Missing credentials are an operator problem: logged, with a diagnostic id, not shown.
    assert "GEMINI_API_KEY" not in response.text
    assert provider.calls == 1
