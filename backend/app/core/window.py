"""Sliding-window counters.

Two callers use this: the per-user request limiter, and the project-wide provider budget.
They must not share an instance. ``check`` prunes *every* key against the window of the
limit being tested, so one instance holding both a 60-second limit and a day-long budget
would erase the long-lived counters as soon as a short-window key was touched. Give each
window length its own instance and the pruning stays sound.
"""

from collections import deque
from time import monotonic


class SlidingWindowLimiter:
    _PURGE_THRESHOLD = 1024

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, limit: int, window_seconds: int) -> int | None:
        """Record a hit. Returns seconds to wait when the caller is over the limit."""
        now = monotonic()
        window_start = now - window_seconds
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= window_start:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(hits[0] + window_seconds - now) + 1)
        hits.append(now)
        if len(self._hits) > self._PURGE_THRESHOLD:
            self._purge(window_start)
        return None

    def state(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Read-only view of a key as ``(hits_inside_the_window, seconds_until_free)``.

        The second value is when the oldest hit ages out, which is the moment capacity
        starts coming back. Unknown keys report ``(0, 0)`` without being created.
        """
        hits = self._hits.get(key)
        if not hits:
            return 0, 0
        now = monotonic()
        window_start = now - window_seconds
        while hits and hits[0] <= window_start:
            hits.popleft()
        if not hits:
            return 0, 0
        return len(hits), max(0, int(hits[0] + window_seconds - now) + 1)

    def _purge(self, window_start: float) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= window_start]
        for key in stale:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()
