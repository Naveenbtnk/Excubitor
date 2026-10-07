from collections.abc import AsyncIterator

import httpx

from app.core.config import settings
from app.services.llm.base import LLMProvider, Message, StructuredModel
from app.services.llm.retry import classify_http_error, optional_seconds


def _client_timeout(read: float | None) -> httpx.Timeout:
    """Bound connection setup always; ``read`` bounds silence from the response body."""
    return httpx.Timeout(
        connect=optional_seconds(settings.llm_connect_timeout),
        read=read,
        write=None,
        pool=None,
    )


def request_timeout() -> httpx.Timeout:
    """Per-attempt budget for chat, embed and the health probe."""
    return _client_timeout(optional_seconds(settings.llm_request_timeout))


def stream_timeout() -> httpx.Timeout:
    """Idle budget between streamed chunks; a live generation may run as long as it likes."""
    return _client_timeout(optional_seconds(settings.llm_stream_idle_timeout))


class OllamaProvider(LLMProvider):
    async def health(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=request_timeout()) as client:
                response = await client.get(f"{settings.ollama_base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"unreachable: {exc}"
        return "ok"

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        response_model: type[StructuredModel] | None = None,
    ) -> str | StructuredModel:
        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(
            {"role": message.role, "content": message.content} for message in messages
        )
        payload: dict[str, object] = {
            "model": settings.ollama_model,
            "messages": payload_messages,
            "stream": False,
        }
        if response_model is not None:
            payload["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=request_timeout()) as client:
                response = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise classify_http_error(exc) from exc
        content = response.json()["message"]["content"]
        if response_model is not None:
            return response_model.model_validate_json(content)
        return content

    async def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> AsyncIterator[str]:
        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(
            {"role": message.role, "content": message.content} for message in messages
        )
        try:
            async with httpx.AsyncClient(timeout=stream_timeout()) as client:
                async with client.stream(
                    "POST",
                    f"{settings.ollama_base_url}/api/chat",
                    json={
                        "model": settings.ollama_model,
                        "messages": payload_messages,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            chunk = httpx.Response(200, content=line).json()
                            content = chunk.get("message", {}).get("content")
                            if content:
                                yield content
        except httpx.HTTPError as exc:
            raise classify_http_error(exc) from exc

    async def embed(self, text: str) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=request_timeout()) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/embeddings",
                    json={"model": settings.ollama_embed_model, "prompt": text},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise classify_http_error(exc) from exc
        vector = list(response.json()["embedding"])
        if len(vector) >= settings.embedding_dimensions:
            return vector[: settings.embedding_dimensions]
        return vector + [0.0] * (settings.embedding_dimensions - len(vector))
