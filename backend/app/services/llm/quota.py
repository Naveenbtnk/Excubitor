"""A project-wide budget for upstream provider calls.

Per-user limits bound how fast one caller may ask for work; this budget bounds how much
provider quota the whole process may spend. The two are not interchangeable. Gemini's free
tier allows 20 ``generateContent`` requests per day, per project, per model, so a single
user following a per-minute limit can spend the entire day's allowance inside a minute and
leave every later caller staring at a provider 429 that lasts for hours.

Counting happens per *attempt*, inside the retry wrapper, because that is the unit the
provider bills:

* one HTTP request can fan out into several calls (a long runbook embeds once per chunk),
* every replayed attempt is another upstream request.

One budget covers chat, streams and embeddings together. That is deliberately conservative:
Gemini meters those methods separately, and refusing a call we might have got away with is
far cheaper than discovering the ceiling mid-demo. Configure ``LLM_QUOTA_LIMIT`` to match
whatever the account really allows, or set it to ``0`` to stop counting.
"""

from dataclasses import dataclass

from app.core.config import Settings, settings
from app.core.window import SlidingWindowLimiter

# Its own instance: the provider window is a day, the per-user limiter's is a minute, and a
# shared instance would prune one against the other's window.
_budget = SlidingWindowLimiter()


def reset_quota() -> None:
    """Drop every counter, for tests that need a fresh allowance."""
    _budget.reset()


@dataclass(frozen=True)
class QuotaSnapshot:
    """What is left of a budget right now. ``resets_in`` counts to the oldest hit expiring."""

    limit: int
    used: int
    remaining: int
    window_seconds: int
    resets_in: int


class ProviderQuota:
    """Shared budget for one provider/model pair."""

    def __init__(self, *, key: str, limit: int, window_seconds: int) -> None:
        self.key = key
        self.limit = limit
        self.window_seconds = window_seconds

    @classmethod
    def from_settings(cls, settings: Settings, *, provider: str, model: str) -> "ProviderQuota":
        return cls(
            key=f"{provider}:{model}",
            limit=settings.effective_llm_quota_limit,
            window_seconds=settings.llm_quota_window_seconds,
        )

    @property
    def enabled(self) -> bool:
        return self.limit > 0 and self.window_seconds > 0

    def reserve(self) -> int | None:
        """Claim one upstream call. Returns the wait in seconds when the budget is spent."""
        if not self.enabled:
            return None
        return _budget.check(self.key, self.limit, self.window_seconds)

    def snapshot(self) -> QuotaSnapshot | None:
        """Current allowance, or ``None`` when the budget is disabled."""
        if not self.enabled:
            return None
        used, resets_in = _budget.state(self.key, self.window_seconds)
        return QuotaSnapshot(
            limit=self.limit,
            used=used,
            remaining=max(0, self.limit - used),
            window_seconds=self.window_seconds,
            resets_in=resets_in,
        )


def current_quota() -> ProviderQuota:
    """The budget :func:`~app.services.llm.retry.build_provider` gives the configured provider."""
    return ProviderQuota.from_settings(
        settings, provider=settings.llm_provider, model=settings.llm_model_name
    )
