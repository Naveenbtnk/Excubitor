"""Per-user rate limiting for routes that call paid LLM APIs.

These limits bound how fast one user may ask for work. They are *not* a provider budget:
``llm_rate_limit`` requests per minute says nothing about whether the account can afford
them. That accounting lives in :mod:`app.services.llm.quota`, which counts upstream
attempts instead of HTTP requests.

The counters live behind :mod:`app.core.counter_store`, so with ``REDIS_URL`` set the
ceiling is shared by every worker rather than multiplied by the number of them.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.counter_store import get_counter_store
from app.models import User

__all__ = ["rate_limit"]


def rate_limit(scope: str, limit_setting: str) -> Callable[..., Awaitable[None]]:
    """Build a dependency enforcing ``settings.<limit_setting>`` hits per user, per scope."""

    async def dependency(user: User = Depends(get_current_user)) -> None:
        limit = int(getattr(settings, limit_setting))
        retry_after = await get_counter_store().check(
            f"{scope}:{user.id}", limit, settings.rate_limit_window_seconds
        )
        if retry_after is None:
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit reached for {scope}: {limit} requests per "
                f"{settings.rate_limit_window_seconds}s. Retry in {retry_after}s."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    return dependency
