from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class LLMError(RuntimeError):
    """An LLM provider call failed in a way that is not worth re-attempting."""


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
    ) -> None:
        super().__init__(message)
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
