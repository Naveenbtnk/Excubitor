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
from app.core.demo import is_demo_account
from app.models import User

__all__ = ["demo_guard", "rate_limit"]


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


def demo_guard(scope: str) -> Callable[..., Awaitable[None]]:
    """A second ceiling, on the shared demo login only.

    The per-user limit bounds one account's burst and the provider budget bounds the project's
    day, and neither of those stops a single visitor from spending that day's whole allowance in
    a minute -- because the demo login is one user, with the same per-minute limit as everybody
    else. This narrows the window to the provider budget's window and the ceiling to
    ``demo_account_rate_limit``, and it does so only for that account: an operator, an admin, or
    a viewer signed in as themselves keeps the ordinary limits.

    The counter is keyed by the configured address rather than by row id, so deleting and
    recreating the account does not hand the next visitor a fresh allowance.
    """

    async def dependency(user: User = Depends(get_current_user)) -> None:
        limit = int(settings.demo_account_rate_limit)
        if limit <= 0 or not is_demo_account(user):
            return
        window = settings.llm_quota_window_seconds
        retry_after = await get_counter_store().check(
            f"{scope}:demo:{user.email.lower()}", limit, window
        )
        if retry_after is None:
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"The shared demo login covers {limit} AI calls per {window}s, so that one "
                f"visitor cannot spend the whole budget. Retry in {retry_after}s, or sign in "
                "with your own account."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    return dependency
