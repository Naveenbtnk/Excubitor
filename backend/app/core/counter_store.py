"""Shared sliding-window counters for the rate limiter and the provider budget.

Both ceilings are only as real as the counters behind them. Kept in process, they are per
worker: three Gunicorn workers serving 20 requests a minute each is 60, and a provider budget
of 20 is spent three times over before anyone notices. This module gives those counters one
home that every worker can see.

Redis is optional. ``REDIS_URL`` empty means the in-memory implementation, which is exactly
the older behaviour and remains the right choice for a single worker, a local run, and the
test suite. When Redis is configured but unreachable, the Redis-backed store degrades to a
process-local check rather than failing open: a shared ceiling that quietly disappears under
load is worse than a stricter one that only covers its own worker.
"""

import logging
import secrets
from time import monotonic
from typing import Protocol

from redis import asyncio as redis_asyncio
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.window import SlidingWindowLimiter

logger = logging.getLogger("cloudops.counters")

# Atomic check-and-claim. Redis serialises scripts, so two workers cannot both observe a
# count below the limit and both be allowed. TIME comes from the Redis server, not the
# caller, so worker clock skew cannot shorten or lengthen a window.
_CHECK_LUA = """
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
local used = redis.call('ZCARD', KEYS[1])
if used >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local wait_s = math.ceil(((tonumber(oldest[2]) + window_ms) - now_ms) / 1000)
  if wait_s < 1 then wait_s = 1 end
  return wait_s
end
redis.call('ZADD', KEYS[1], now_ms, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window_ms)
return 0
"""

_STATE_LUA = """
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local window_ms = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
local used = redis.call('ZCARD', KEYS[1])
if used == 0 then
  return {0, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local wait_s = math.ceil(((tonumber(oldest[2]) + window_ms) - now_ms) / 1000)
if wait_s < 0 then wait_s = 0 end
return {used, wait_s}
"""


class CounterStore(Protocol):
    """A windowed counter every worker can agree on."""

    name: str

    async def check(self, key: str, limit: int, window_seconds: int) -> int | None:
        """Record a hit; return seconds to wait when the caller is over the limit."""

    async def state(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Read-only ``(hits_in_window, seconds_until_capacity_returns)``."""

    async def reset(self) -> None:
        """Drop every counter, for tests and for a deliberate operator reset."""

    async def ping(self) -> str:
        """``"ok"`` when the store is usable, otherwise a short reason."""

    async def close(self) -> None:
        """Release pooled connections."""


class MemoryCounterStore:
    """Process-local counters: correct for one worker, invisible to any other."""

    name = "memory"

    def __init__(self) -> None:
        # One limiter per window length. check() prunes every key against the window it was
        # asked about, so serving a 60s limit and a day-long budget from one instance would
        # erase the longer counters; see app.core.window.
        self._windows: dict[int, SlidingWindowLimiter] = {}

    async def check(self, key: str, limit: int, window_seconds: int) -> int | None:
        return self._window(window_seconds).check(key, limit, window_seconds)

    async def state(self, key: str, window_seconds: int) -> tuple[int, int]:
        return self._window(window_seconds).state(key, window_seconds)

    async def reset(self) -> None:
        self._windows.clear()

    async def ping(self) -> str:
        return "ok"

    async def close(self) -> None:
        return None

    def _window(self, window_seconds: int) -> SlidingWindowLimiter:
        return self._windows.setdefault(window_seconds, SlidingWindowLimiter())


class RedisCounterStore:
    """Counters in Redis, shared by every worker pointing at the same instance."""

    name = "redis"
    _WARN_INTERVAL_SECONDS = 60.0

    def __init__(self, url: str, *, prefix: str = "cloudops", timeout: float = 2.0) -> None:
        self.url = url
        self.prefix = prefix
        self.timeout = timeout
        self._client: redis_asyncio.Redis | None = None
        # Used only while Redis is unreachable, so an outage narrows the limit to this worker
        # instead of removing it.
        self._degraded = MemoryCounterStore()
        self._last_warning = 0.0

    async def check(self, key: str, limit: int, window_seconds: int) -> int | None:
        try:
            wait = await self._connection().eval(
                _CHECK_LUA,
                1,
                self._key(key),
                window_seconds * 1000,
                limit,
                secrets.token_hex(8),
            )
        except (RedisError, OSError) as exc:
            self._warn(exc)
            return await self._degraded.check(key, limit, window_seconds)
        return int(wait) if int(wait) > 0 else None

    async def state(self, key: str, window_seconds: int) -> tuple[int, int]:
        try:
            used, resets_in = await self._connection().eval(
                _STATE_LUA, 1, self._key(key), window_seconds * 1000
            )
        except (RedisError, OSError) as exc:
            self._warn(exc)
            return await self._degraded.state(key, window_seconds)
        return int(used), int(resets_in)

    async def reset(self) -> None:
        try:
            keys = [key async for key in self._connection().scan_iter(f"{self.prefix}:*")]
            if keys:
                await self._connection().delete(*keys)
        except (RedisError, OSError) as exc:
            self._warn(exc)
        await self._degraded.reset()

    async def ping(self) -> str:
        try:
            await self._connection().ping()
        except (RedisError, OSError) as exc:
            return f"unreachable: {exc}"
        return "ok"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _connection(self) -> redis_asyncio.Redis:
        if self._client is None:
            self._client = redis_asyncio.from_url(
                self.url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=self.timeout,
                socket_connect_timeout=self.timeout,
            )
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def _warn(self, exc: Exception) -> None:
        # A Redis outage would otherwise log on every request, which buries the signal.
        now = monotonic()
        if now - self._last_warning < self._WARN_INTERVAL_SECONDS:
            return
        self._last_warning = now
        logger.warning(
            "counter_store_unreachable",
            extra={
                "extra_fields": {
                    "url": self.url,
                    "error": str(exc),
                    "fallback": "per-process counters until Redis answers again",
                }
            },
        )


_stores: dict[tuple[str, str, float], CounterStore] = {}


def get_counter_store() -> CounterStore:
    """The process-wide store, rebuilt whenever ``REDIS_URL`` changes."""
    url = settings.redis_url.strip()
    cache_key = (url, settings.redis_key_prefix, settings.redis_timeout_seconds)
    store = _stores.get(cache_key)
    if store is None:
        store = (
            RedisCounterStore(
                url, prefix=settings.redis_key_prefix, timeout=settings.redis_timeout_seconds
            )
            if url
            else MemoryCounterStore()
        )
        _stores[cache_key] = store
    return store


async def close_counter_stores() -> None:
    """Close pooled connections on shutdown."""
    for store in list(_stores.values()):
        await store.close()
    _stores.clear()
