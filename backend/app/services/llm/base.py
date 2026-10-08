from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class LLMErrorKind(StrEnum):
    """Why a provider call failed, in the only vocabulary safe to show a user.

    A provider's own error text is diagnostic material -- it names quota metrics, project
    ids, models and documentation URLs -- so it is never the thing an API client receives.
    This closed set is: each implementation classifies its own failures into one of these,
    and :mod:`app.services.llm.errors` turns the result into a sentence for a human. Adding
    a provider means picking from this list, not inventing a new wire value; a client that
    does not recognise a code falls back to the server's own safe message.
    """

    # The provider's own allowance is spent. Long by definition: see ``kind_for_wait``.
    QUOTA_EXHAUSTED = "quota_exhausted"
    # Our budget in app.services.llm.quota is spent. Distinct from the above because the
    # remedy is different: an operator can raise LLM_QUOTA_LIMIT, nobody can raise Gemini's.
    BUDGET_EXHAUSTED = "budget_exhausted"
    # A window short enough to wait out inside one session.
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    # Reachable but failing: overloaded, 5xx, a dropped socket.
    UNAVAILABLE = "unavailable"
    # Permanent: bad model, bad credentials, a malformed request.
    PROVIDER_ERROR = "provider_error"


class LLMError(RuntimeError):
    """An LLM provider call failed in a way that is not worth re-attempting.

    ``kind`` is the user-safe classification of the failure; the message stays internal and
    is written to the logs, never to a response body.
    """

    def __init__(
        self, message: str, *, kind: LLMErrorKind = LLMErrorKind.PROVIDER_ERROR
    ) -> None:
        super().__init__(message)
        self.kind = kind


class LLMTransientError(LLMError):
    """An upstream failure that a later attempt can recover from.

    Providers raise this instead of :class:`LLMError` so the retry wrapper stays generic; a
    new provider only has to classify its own errors and gets backoff for free.

    ``replayable`` says whether *this process* should try again right now. A dropped socket
    or an overloaded 5xx clears in milliseconds, so it is replayed; a quota window (429)
    outlives the request, so replaying it would only burn more quota. Those are reported to
    the caller as a 503 with a ``Retry-After`` hint instead.

    ``retry_after`` carries the wait the *provider* asked for, in seconds, when it supplied
    one. It is authoritative in a way our own defaults cannot be: Gemini distinguishes a
    per-minute limit from a daily free-tier cap, and the two differ by hours.
    """

    def __init__(
        self,
        message: str,
        *,
        replayable: bool = True,
        retry_after: float | None = None,
        kind: LLMErrorKind = LLMErrorKind.UNAVAILABLE,
    ) -> None:
        super().__init__(message, kind=kind)
        self.replayable = replayable
        self.retry_after = retry_after


class LLMProvider:
    async def health(self) -> str:
        """Cheap reachability probe. Returns ``"ok"`` or a short failure reason."""
        raise NotImplementedError

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        response_model: type[StructuredModel] | None = None,
    ) -> str | StructuredModel:
        raise NotImplementedError

    async def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError
