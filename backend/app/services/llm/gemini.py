from collections.abc import AsyncIterator

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.core.config import settings
from app.services.llm.base import (
    LLMError,
    LLMProvider,
    LLMTransientError,
    Message,
    StructuredModel,
)
from app.services.llm.retry import (
    AttemptTimeouts,
    classify_http_error,
    is_retryable_status,
    requires_backoff,
    retry_hint_from_payload,
)

# The SDK wraps HTTP failures in genai errors, but a bare httpx error can still escape from
# the transport, and timeouts and dropped sockets are transient by definition.
UPSTREAM_ERRORS = (errors.APIError, httpx.HTTPError, TimeoutError)


def http_options(timeouts: AttemptTimeouts | None = None) -> types.HttpOptions:
    """Client-level socket bounds for the SDK.

    The strict per-attempt deadline is enforced by :class:`RetryingProvider`; this only stops
    a dead socket from lingering, so it uses the laxer of the two bounds and can never cut a
    live stream short. ``retry_options`` is deliberately left unset: the SDK does not retry
    anything by default, which keeps our bounded policy the only source of extra requests.
    """
    bounds = timeouts or AttemptTimeouts.from_settings(settings)
    return types.HttpOptions(timeout=bounds.socket_timeout_ms)


def error_body(exc: Exception) -> object:
    """The raw error envelope, wherever this SDK version puts it.

    ``APIError`` is constructed with ``response_json`` but exposes it as ``details`` in the
    version we pin, so both names are checked rather than trusting either one.
    """
    return getattr(exc, "response_json", None) or getattr(exc, "details", None)


def translate_error(exc: Exception) -> LLMError:
    """Map an upstream Gemini failure onto the provider contract."""
    if isinstance(exc, httpx.HTTPError):
        return classify_http_error(exc)
    if isinstance(exc, TimeoutError):
        return LLMTransientError(str(exc))
    # Genai puts the HTTP status in ``code`` and the canonical name in ``status``; either
    # may be absent depending on which transport raised.
    candidates = (getattr(exc, "code", None), getattr(exc, "status", None))
    if any(requires_backoff(candidate) for candidate in candidates):
        return LLMTransientError(
            str(exc),
            replayable=False,
            retry_after=retry_hint_from_payload(error_body(exc)),
        )
    if any(is_retryable_status(candidate) for candidate in candidates):
        return LLMTransientError(str(exc))
    return LLMError(str(exc))


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not configured")
        self.client = genai.Client(
            api_key=settings.gemini_api_key, http_options=http_options()
        )

    async def health(self) -> str:
        try:
            await self.client.aio.models.get(model=settings.gemini_model)
        except UPSTREAM_ERRORS as exc:
            return f"unreachable: {exc}"
        return "ok"

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        response_model: type[StructuredModel] | None = None,
    ) -> str | StructuredModel:
        contents = "\n\n".join(f"{message.role}: {message.content}" for message in messages)
        config = types.GenerateContentConfig(system_instruction=system) if system else None
        if response_model is not None:
            config = types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=response_model,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
        try:
            response = await self.client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
        except UPSTREAM_ERRORS as exc:
            raise translate_error(exc) from exc
        if response_model is not None:
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, BaseModel):
                return parsed
            if isinstance(response.text, str):
                return response_model.model_validate_json(response.text)
            raise LLMError("Gemini did not return structured JSON")
        return response.text or ""

    async def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> AsyncIterator[str]:
        contents = "\n\n".join(f"{message.role}: {message.content}" for message in messages)
        config = types.GenerateContentConfig(system_instruction=system) if system else None
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except UPSTREAM_ERRORS as exc:
            raise translate_error(exc) from exc

    async def embed(self, text: str) -> list[float]:
        try:
            response = await self.client.aio.models.embed_content(
                model=settings.gemini_embed_model,
                contents=text,
                config={"output_dimensionality": settings.embedding_dimensions},
            )
        except UPSTREAM_ERRORS as exc:
            raise translate_error(exc) from exc
        embeddings = getattr(response, "embeddings", None)
        if embeddings:
            return list(embeddings[0].values)
        embedding = getattr(response, "embedding", None)
        if embedding:
            return list(embedding.values)
        raise LLMError("Gemini did not return an embedding")
