"""Retry and timeout layer for LLM provider calls.

Providers translate an upstream failure into :class:`LLMTransientError` when the request is
worth another attempt (rate limit, 5xx, timeout, dropped socket) and into plain
:class:`LLMError` when it is not. :class:`RetryingProvider` wraps any provider and replays
only the transient ones with exponential backoff and full jitter, so swapping or adding a
provider keeps the same resilience without every implementation growing its own retry loop.

Every attempt is also bounded by :class:`AttemptTimeouts`, enforced here rather than in each
SDK: one deadline for a whole non-streaming call and a separate idle deadline for the gap
between streamed chunks, so a slow but live generation is never cut off. A deadline that
fires is a replayable transient failure, which means it flows through the same bounded
retry policy as any other upstream hiccup.

Retries are bounded: ``settings.llm_max_retries`` additional attempts (2 by default, so 3
attempts total) and ``settings.llm_retry_max_delay`` caps a single sleep. ``LLM_MAX_RETRIES=0``
with both timeout bounds set to 0 removes the retry wrapper entirely, so a single attempt
goes straight to the provider.

:class:`QuotaGuardedProvider` sits *inside* the retry wrapper so that the shared budget in
:mod:`app.services.llm.quota` is charged per attempt rather than per call. When the budget
is spent it refuses locally with the same non-replayable signal an upstream 429 produces,
so callers keep seeing one contract: 503 with an honest ``Retry-After``.
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx

from app.core.config import Settings, settings
from app.services.llm.base import (
    LLMError,
    LLMProvider,
    LLMTransientError,
    Message,
    StructuredModel,
)
from app.services.llm.quota import ProviderQuota

logger = logging.getLogger("cloudops.llm")

ResultT = TypeVar("ResultT")

# Overload and quota pressure arrive as these statuses. 408/425 cover a stalled upload and
# 409 a conflicting concurrent run; everything else is permanent (bad request, auth,
# unknown model) and is never retried.
RETRYABLE_HTTP_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# gRPC-style canonical codes, which some providers report in place of an HTTP status.
RETRYABLE_STATUS_NAMES = frozenset(
    {"UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "INTERNAL"}
)
# Quota and rate-limit signals. Transient, but on a clock (usually a one-minute window)
# that is longer than a request can be held open, so the caller is told to come back later.
BACKOFF_STATUS = frozenset({429})
BACKOFF_STATUS_NAMES = frozenset({"RESOURCE_EXHAUSTED"})


def optional_seconds(value: float) -> float | None:
    """Treat a non-positive setting as "no bound" instead of an instantly expiring one."""
    return value if value > 0 else None


def parse_retry_after(value: object) -> float | None:
    """Seconds from a provider-supplied delay: ``60``, ``"60"`` or Gemini's ``"56023.6s"``.

    Only numeric forms are understood. An unparseable value returns ``None`` so the caller
    falls back to a configured bound rather than inventing a wait.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("s", "S")):
        text = text[:-1].strip()
    try:
        seconds = float(text)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def retry_hint_from_payload(payload: object) -> float | None:
    """Pull the provider's own retry delay out of an error body.

    Gemini reports it as ``RetryInfo.retryDelay``, and it distinguishes a per-minute limit
    from a daily free-tier cap — a difference of hours that no local default can guess.
    """
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return None
    for detail in details:
        if isinstance(detail, dict) and "RetryInfo" in str(detail.get("@type", "")):
            hint = parse_retry_after(detail.get("retryDelay"))
            if hint is not None:
                return hint
    return None


def _match_status(value: object, codes: frozenset[int], names: frozenset[str]) -> bool:
    """Match an HTTP status code or canonical name against one of the two vocabularies."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value in codes
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped) in codes
        return stripped.upper() in names
    try:
        return int(value) in codes  # http.HTTPStatus and friends
    except (TypeError, ValueError):
        return False


def is_retryable_status(value: object) -> bool:
    """True for a retryable status, in numeric or canonical-name form."""
    return _match_status(value, RETRYABLE_HTTP_STATUS, RETRYABLE_STATUS_NAMES)


def requires_backoff(value: object) -> bool:
    """True for a quota signal that must wait out a window instead of being replayed."""
    return _match_status(value, BACKOFF_STATUS, BACKOFF_STATUS_NAMES)


def classify_http_error(exc: httpx.HTTPError) -> LLMError:
    """Classify a transport failure, which is retryable only for some causes.

    Timeouts, refused connections and truncated responses are transient by definition; an
    HTTP status is transient only if it is in :data:`RETRYABLE_HTTP_STATUS`; a malformed
    URL or undecodable body is a permanent client-side bug.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return LLMTransientError(str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if requires_backoff(status):
            return LLMTransientError(
                str(exc),
                replayable=False,
                retry_after=parse_retry_after(exc.response.headers.get("retry-after")),
            )
        if is_retryable_status(status):
            return LLMTransientError(str(exc))
    return LLMError(str(exc))


@dataclass(frozen=True)
class AttemptTimeouts:
    """Per-attempt deadlines in seconds; ``None`` disables that bound.

    ``request`` bounds one whole non-streaming attempt. ``stream_idle`` bounds the silence
    between two streamed chunks rather than the total length, so a long but actively
    producing generation is never cut off. ``connect`` is handed to the provider's own HTTP
    client to bound connection setup even before a request is sent.
    """

    request: float | None = 30.0
    stream_idle: float | None = 60.0
    connect: float = 5.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "AttemptTimeouts":
        return cls(
            request=optional_seconds(settings.llm_request_timeout),
            stream_idle=optional_seconds(settings.llm_stream_idle_timeout),
            connect=max(0.0, settings.llm_connect_timeout),
        )

    @property
    def is_disabled(self) -> bool:
        """True when no deadline is enforced, which lets the wrapper be skipped entirely."""
        return self.request is None and self.stream_idle is None

    @property
    def socket_timeout_ms(self) -> int | None:
        """One scalar, in milliseconds, for SDKs that accept only a single socket timeout.

        Uses the laxer of the two bounds so a client-level timer can never fire ahead of the
        per-attempt deadline enforced here.
        """
        bounds = [bound for bound in (self.request, self.stream_idle) if bound is not None]
        return int(max(bounds) * 1000) if bounds else None


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter, capped at ``max_delay`` seconds."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetryPolicy":
        return cls(
            attempts=max(1, settings.llm_max_retries + 1),
            base_delay=max(0.0, settings.llm_retry_base_delay),
            max_delay=max(0.0, settings.llm_retry_max_delay),
        )

    @property
    def retries(self) -> int:
        """Number of re-attempts after the initial try."""
        return self.attempts - 1

    def delay_for(
        self, attempt: int, *, rand: Callable[[float, float], float] = random.uniform
    ) -> float:
        """Seconds to sleep after ``attempt`` (0-based) has failed.

        Full jitter: pick uniformly inside ``[0, base * 2**attempt]`` so a fleet of workers
        that hit the same outage does not retry in lockstep.
        """
        if self.base_delay <= 0 or self.max_delay <= 0:
            return 0.0
        ceiling = min(self.max_delay, self.base_delay * 2**attempt)
        return rand(0.0, ceiling)


class QuotaGuardedProvider(LLMProvider):
    """Refuses a call locally once the shared provider budget is spent.

    It belongs *inside* :class:`RetryingProvider`: wrapping the other way round would charge
    the budget once per logical call and let replayed attempts slip past the ceiling.

    A refusal is raised as a non-replayable :class:`LLMTransientError`, exactly what an
    upstream 429 becomes in :mod:`app.services.llm.gemini`. Both mean the same thing -- no
    allowance left, wait this long -- so callers get a single 503 + ``Retry-After`` contract
    instead of two codes for one condition. The difference is that ours fires before the
    request is sent, so the wait we advertise is the one that is genuinely left.
    """

    def __init__(self, inner: LLMProvider, quota: ProviderQuota) -> None:
        self.inner = inner
        self.quota = quota

    async def health(self) -> str:
        # Probes are exempt. A monitoring loop polls this endpoint far more often than the
        # budget allows and must not be able to spend the allowance users need.
        return await self.inner.health()

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        response_model: type[StructuredModel] | None = None,
    ) -> str | StructuredModel:
        self._claim("chat")
        return await self.inner.chat(messages, system=system, response_model=response_model)

    async def embed(self, text: str) -> list[float]:
        self._claim("embed")
        return await self.inner.embed(text)

    async def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> AsyncIterator[str]:
        # An async generator body runs on the first pull, which is when the request is
        # really opened: a stream nobody consumes claims nothing.
        self._claim("stream")
        async for chunk in self.inner.stream(messages, system=system):
            yield chunk

    def _claim(self, operation: str) -> None:
        wait = self.quota.reserve()
        if wait is None:
            return
        raise LLMTransientError(
            f"{operation} refused locally: all {self.quota.limit} calls allowed per "
            f"{self.quota.window_seconds}s for {self.quota.key} are spent. Raise "
            "LLM_QUOTA_LIMIT to allow more calls, or set it to 0 to stop counting.",
            replayable=False,
            retry_after=wait,
        )


class RetryingProvider(LLMProvider):
    """Decorates a provider with per-attempt deadlines and bounded retries."""

    def __init__(
        self,
        inner: LLMProvider,
        policy: RetryPolicy | None = None,
        timeouts: AttemptTimeouts | None = None,
    ) -> None:
        self.inner = inner
        self.policy = policy or RetryPolicy()
        self.timeouts = timeouts or AttemptTimeouts()

    async def health(self) -> str:
        # A probe must answer promptly and is never retried, but it is still bounded.
        return await self._bounded("health", self.inner.health())

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        response_model: type[StructuredModel] | None = None,
    ) -> str | StructuredModel:
        return await self._call(
            "chat",
            lambda: self.inner.chat(messages, system=system, response_model=response_model),
        )

    async def embed(self, text: str) -> list[float]:
        # Embeddings are pure functions of their input, so a replay is always safe.
        return await self._call("embed", lambda: self.inner.embed(text))

    async def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> AsyncIterator[str]:
        attempt = 0
        while True:
            delivered = False
            try:
                # aclosing releases the upstream stream when the consumer stops early, e.g.
                # when an SSE client disconnects mid-response.
                async with contextlib.aclosing(
                    self._stream_attempt(messages, system=system)
                ) as chunks:
                    async for chunk in chunks:
                        delivered = True
                        yield chunk
                return
            except LLMTransientError as exc:
                # Once a token reached the client the response cannot be replayed without
                # duplicating text, so a mid-stream failure is surfaced as-is.
                if delivered or not exc.replayable or attempt >= self.policy.retries:
                    raise
                delay = self.policy.delay_for(attempt)
                self._log_retry("stream", attempt, delay, exc)
                await asyncio.sleep(delay)
                attempt += 1

    async def _call(self, operation: str, call: Callable[[], Awaitable[ResultT]]) -> ResultT:
        for attempt in range(self.policy.attempts):
            try:
                return await self._bounded(operation, call())
            except LLMTransientError as exc:
                if not exc.replayable or attempt >= self.policy.retries:
                    raise
                delay = self.policy.delay_for(attempt)
                self._log_retry(operation, attempt, delay, exc)
                await asyncio.sleep(delay)
        raise AssertionError(f"retry loop exhausted without result: {operation}")

    async def _bounded(self, operation: str, awaitable: Awaitable[ResultT]) -> ResultT:
        """Await one provider call under the per-attempt deadline."""
        timeout = self.timeouts.request
        if timeout is None:
            return await awaitable
        try:
            async with asyncio.timeout(timeout):
                return await awaitable
        except TimeoutError as exc:
            raise self._timed_out(operation, timeout) from exc

    async def _stream_attempt(
        self, messages: list[Message], *, system: str | None
    ) -> AsyncIterator[str]:
        async with contextlib.aclosing(self.inner.stream(messages, system=system)) as chunks:
            iterator = chunks.__aiter__()
            while True:
                try:
                    yield await self._next_chunk(iterator)
                except StopAsyncIteration:
                    return

    async def _next_chunk(self, iterator: AsyncIterator[str]) -> str:
        """Pull one chunk, bounded by the idle deadline rather than a total one."""
        timeout = self.timeouts.stream_idle
        if timeout is None:
            return await iterator.__anext__()
        try:
            async with asyncio.timeout(timeout):
                return await iterator.__anext__()
        except TimeoutError as exc:
            raise self._timed_out("stream", timeout) from exc

    @staticmethod
    def _timed_out(operation: str, timeout: float) -> LLMTransientError:
        return LLMTransientError(
            f"{operation} exceeded the {timeout:g}s attempt timeout", replayable=True
        )

    def _log_retry(
        self, operation: str, attempt: int, delay: float, exc: LLMTransientError
    ) -> None:
        logger.warning(
            "llm_retry",
            extra={
                "extra_fields": {
                    "operation": operation,
                    "attempt": attempt + 1,
                    "of": self.policy.attempts,
                    "delay_s": round(delay, 3),
                    "error": str(exc),
                }
            },
        )


def build_provider(provider: LLMProvider) -> LLMProvider:
    """Wrap ``provider`` with the quota, retries and deadlines it is configured for."""
    quota = ProviderQuota.from_settings(
        settings, provider=settings.llm_provider, model=settings.llm_model_name
    )
    guarded: LLMProvider = QuotaGuardedProvider(provider, quota) if quota.enabled else provider
    policy = RetryPolicy.from_settings(settings)
    timeouts = AttemptTimeouts.from_settings(settings)
    if policy.retries == 0 and timeouts.is_disabled:
        return guarded
    return RetryingProvider(guarded, policy, timeouts)
