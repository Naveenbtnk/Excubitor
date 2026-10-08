"""Project a provider failure into something safe to hand a browser.

The provider's own error text is diagnostic, not user-facing. A Gemini 429 body is roughly a
kilobyte of JSON naming the quota metric, the quota id, the model, the project and a
documentation URL; a transport error can carry the request URL. All of that is exactly what an
operator needs in a log line next to a request id -- it is how a per-minute limit is told apart
from an exhausted free tier -- and none of it belongs in a chat bubble, where it is noise at
best and a leak of project-level metadata at worst.

So a failure is projected once, here, into a small closed vocabulary: a
:class:`~app.services.llm.base.LLMErrorKind`, the provider *name* (from our own configuration,
never from the payload), a wait in seconds when one is known, and a request id. The sentence
that accompanies it is written by us. The provider's raw text goes to the ``cloudops.llm``
logger and never crosses the wire.

The wait is also what separates a quota from ordinary rate limiting: see :func:`kind_for_wait`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.services.llm.base import LLMError, LLMErrorKind

logger = logging.getLogger("cloudops.llm")

# A wait at or beyond this is a quota window rather than a momentary limit. Sits between the
# two shapes actually observed in production: Gemini's per-minute limits asked for ~60s, and
# its free-tier daily cap asked for ~56000s (15h33m). Nothing in between has ever shown up.
QUOTA_WAIT_THRESHOLD_SECONDS = 3600

# Display names for the providers we ship. Anything else is titled from its slug, which is
# still our own configuration value rather than anything the provider said.
_PROVIDER_LABELS = {"gemini": "Gemini", "ollama": "Ollama"}

_LOG_LEVELS = {
    LLMErrorKind.QUOTA_EXHAUSTED: logging.WARNING,
    LLMErrorKind.BUDGET_EXHAUSTED: logging.WARNING,
    LLMErrorKind.RATE_LIMITED: logging.INFO,
}


def kind_for_wait(
    retry_after: float | None, *, fallback: LLMErrorKind = LLMErrorKind.RATE_LIMITED
) -> LLMErrorKind:
    """Classify a rate-limit signal by how long the provider says to wait.

    Both a per-minute limit and an exhausted daily quota arrive as 429, and telling them apart
    from the payload means parsing provider-specific quota metadata -- which is precisely the
    material that must not reach a client. The wait separates them without reading any of it:
    a window we can sleep through is rate limiting, a window of hours is a quota.
    """
    if retry_after is None:
        return fallback
    return (
        LLMErrorKind.QUOTA_EXHAUSTED
        if retry_after >= QUOTA_WAIT_THRESHOLD_SECONDS
        else LLMErrorKind.RATE_LIMITED
    )


def format_duration(seconds: int) -> str:
    """Human-readable span for a countdown: 63540 -> ``17 hours 39 minutes``.

    Deliberately coarse. The provider's own numbers are approximate (a 429 hint counted down
    while we watched it), so returning seconds would imply a precision that does not exist.
    """
    if seconds < 60:
        return f"{seconds} second" + ("" if seconds == 1 else "s")
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    hours, remainder = divmod(minutes, 60)
    text = f"{hours} hour" + ("" if hours == 1 else "s")
    if remainder:
        text += f" {remainder} minute" + ("" if remainder == 1 else "s")
    return text


def provider_label(provider: str) -> str:
    """Our own name for a provider, safe to interpolate into a user-facing sentence."""
    return _PROVIDER_LABELS.get(provider, provider.replace("_", " ").title() or "The provider")


def _is_sandbox(operation: str | None) -> bool:
    """The Sandbox is the one surface that spells out the countdown in its own terms; every
    other caller gets the provider-neutral phrasing."""
    return bool(operation) and "sandbox" in operation


def _advice(
    retry_after: int | None, *, provider: str, operation: str | None
) -> str:
    """When it clears, and what the user can do meanwhile.

    Written as one piece so the two halves cannot both say "try again" -- the earlier draft
    produced "Try again in approximately 24 hours. Try again later or switch ...". The wait is
    stated only when the provider gave one: a guessed countdown is worse than none, because a
    user can plan around a number.
    """
    if retry_after is None:
        return (
            " Try again later or switch to the local Ollama provider."
            if provider == "gemini"
            else " Try again later."
        )

    wait = (
        " Sandbox responses will be available again in approximately "
        f"{format_duration(retry_after)}."
        if _is_sandbox(operation)
        else f" Try again in approximately {format_duration(retry_after)}."
    )
    if provider != "gemini":
        return wait
    return wait + " Switch to the local Ollama provider to keep working in the meantime."


def _message(
    kind: LLMErrorKind,
    *,
    provider: str,
    retry_after: int | None,
    operation: str | None = None,
) -> str:
    """Write the sentence a user sees. No provider text is ever interpolated here."""
    label = provider_label(provider)
    wait = (
        f" Try again in approximately {format_duration(retry_after)}."
        if retry_after is not None
        else None
    )

    if kind is LLMErrorKind.QUOTA_EXHAUSTED:
        # "daily" is warranted: this kind is only reached for waits of an hour or more, and
        # every such limit we have observed is a per-day quota.
        return f"{label}'s daily quota has been reached." + _advice(
            retry_after, provider=provider, operation=operation
        )
    if kind is LLMErrorKind.BUDGET_EXHAUSTED:
        return f"The app has used up its {label} call allowance for now." + _advice(
            retry_after, provider=provider, operation=operation
        )
    if kind is LLMErrorKind.RATE_LIMITED:
        return f"Too many {label} requests right now." + (wait or " Try again in a moment.")
    if kind is LLMErrorKind.TIMEOUT:
        return (
            f"{label} took too long to answer." + (wait or " Try again in a moment.")
        )
    if kind is LLMErrorKind.UNAVAILABLE:
        return f"{label} is temporarily unavailable." + (wait or " Try again in a moment.")
    return (
        f"{label} could not complete this request."
        + (wait or " Try again, or share the diagnostic ID with support.")
    )


@dataclass(frozen=True)
class PublicLLMError:
    """A provider failure as an API client sees it: no payload, no URL, no internals."""

    code: str
    message: str
    provider: str
    retry_after: int | None
    request_id: str | None

    def payload(self) -> dict[str, Any]:
        """The structured form. ``retry_after`` is absent when we do not know it, so a client
        can tell "wait this long" from "we have no idea" instead of guessing from a string."""
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
        }
        if self.retry_after is not None:
            payload["retry_after"] = self.retry_after
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload

    def to_json(self) -> str:
        """Serialise for an SSE ``data:`` field.

        Serialised here rather than handing the dict to ``sse_starlette``: its encoder calls
        ``str(data)``, which would emit a Python repr with single quotes -- valid to the eye,
        unparseable by ``JSON.parse``.
        """
        return json.dumps(self.payload())


def public_llm_error(
    exc: LLMError,
    *,
    provider: str,
    request_id: str | None = None,
    operation: str | None = None,
    retry_after: float | None = None,
) -> PublicLLMError:
    """Log the failure in full and return the part that is safe to send.

    One function does both on purpose. Splitting them would let a caller project a failure
    without logging it -- losing the only copy of the diagnostic detail -- or log it without
    projecting it. ``retry_after`` overrides the exception's own hint so the number in the
    message matches the ``Retry-After`` header a caller also receives.
    """
    raw = retry_after if retry_after is not None else getattr(exc, "retry_after", None)
    seconds = max(0, int(raw)) if raw is not None else None

    # The code is passed through verbatim so a client can tell one failure from another even
    # when it does not recognise the value; the wording only needs a kind it has a sentence
    # for, and anything unrecognised gets the generic one.
    declared = getattr(exc, "kind", LLMErrorKind.PROVIDER_ERROR)
    code = str(declared) if declared else str(LLMErrorKind.PROVIDER_ERROR)
    try:
        kind = LLMErrorKind(declared)
    except ValueError:
        kind = LLMErrorKind.PROVIDER_ERROR

    logger.log(
        _LOG_LEVELS.get(kind, logging.ERROR),
        "llm_failure",
        extra={
            "extra_fields": {
                "code": code,
                "provider": provider,
                "operation": operation,
                "replayable": getattr(exc, "replayable", None),
                "retry_after_s": seconds,
                "request_id": request_id,
                # The provider's own words, recorded here and nowhere else. This is the line
                # to read when diagnosing a quota: it carries the metric, model and quota id
                # that a user-facing message deliberately omits.
                "detail": str(exc),
            }
        },
    )

    return PublicLLMError(
        code=code,
        message=_message(
            kind, provider=provider, retry_after=seconds, operation=operation
        ),
        provider=provider,
        retry_after=seconds,
        request_id=request_id,
    )
