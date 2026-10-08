"""What a caller sees when something we did not plan for fails.

Two paths, one contract: a sentence written by us, a stable code, a request id that matches the
log line, and none of the traceback. The SSE case is the one worth having a test for -- its
status line is committed before the failure happens, so the projection has to happen inside the
stream.
"""

import json
import logging

import pytest
from httpx import AsyncClient

from app.api.routes import alerts as alerts_module
from app.api.routes import sandbox as sandbox_module
from app.core.config import settings
from app.services.llm.base import LLMProvider, Message, StructuredModel

# Anything from this list reaching a response body is the leak these tests exist to catch.
LEAK_MARKERS = [
    "Traceback",
    "RuntimeError",
    "site-packages",
    "cloudops",
    "File \"",
    ".py",
    "raise ",
]


class BreaksMidStream(LLMProvider):
    """Answers one token, then fails the way a lost database connection would."""

    async def health(self) -> str:
        return "ok"

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        response_model: type[StructuredModel] | None = None,
    ) -> str | StructuredModel:
        return "unused"

    async def stream(self, messages: list[Message], *, system: str | None = None):
        yield "Half an answer."
        raise RuntimeError("the runbook index went away during generation")

    async def embed(self, text: str) -> list[float]:
        return [0.0] * settings.embedding_dimensions


def assert_no_internals(body: str) -> None:
    for marker in LEAK_MARKERS:
        assert marker not in body, f"response leaked {marker!r}: {body}"
    assert "the runbook index went away" not in body


async def test_an_unexpected_failure_is_a_generic_json_500(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog
):
    def explode(*_args, **_kwargs):
        raise RuntimeError("the runbook index is unavailable")

    monkeypatch.setattr(alerts_module, "make_alert", explode)

    with caplog.at_level(logging.ERROR, logger="cloudops.error"):
        response = await authed_client.post("/alerts/simulate", json={})

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["detail"] == body["error"]["message"]
    assert "Try again in a moment" in body["error"]["message"]

    assert_no_internals(response.text)

    # The id in the body is the one in the header, and the one the log line carries: that is
    # what makes a bug report actionable.
    request_id = body["error"]["request_id"]
    assert request_id and response.headers["x-request-id"] == request_id

    logged = [record for record in caplog.records if record.name == "cloudops.error"]
    assert [record.getMessage() for record in logged] == ["internal_error"]
    assert logged[0].extra_fields["request_id"] == request_id
    assert "the runbook index is unavailable" in logged[0].extra_fields["detail"]
    assert logged[0].exc_info is not None


async def test_the_caller_supplied_request_id_is_the_one_echoed_back(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    def explode(*_args, **_kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(alerts_module, "make_alert", explode)

    response = await authed_client.post(
        "/alerts/simulate", json={}, headers={"X-Request-ID": "req-from-the-caller"}
    )

    assert response.json()["error"]["request_id"] == "req-from-the-caller"
    assert response.headers["x-request-id"] == "req-from-the-caller"


async def test_a_mid_stream_failure_becomes_a_terminal_error_event(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: BreaksMidStream())

    response = await authed_client.post("/sandbox/stream", json={"message": "Anything"})

    # The status is committed before the first token, so it is still a 200 -- and the failure
    # has to be told inside the stream rather than with a status nobody can change now.
    assert response.status_code == 200
    assert "Half an answer." in response.text
    assert "event: done" in response.text
    assert_no_internals(response.text)

    payloads = [
        json.loads(line.removeprefix("data: ").strip())
        for line in response.text.splitlines()
        if line.startswith("data: ") and line.removeprefix("data: ").strip().startswith("{")
    ]
    error = next(payload for payload in payloads if payload["code"] == "internal_error")
    assert "Try again in a moment" in error["message"]
    assert error["request_id"] == response.headers["x-request-id"]


async def test_an_unexpected_failure_is_never_a_bare_500(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Without the projection this is what a caller would get: nothing to act on."""

    def explode(*_args, **_kwargs):
        raise ValueError("whatever")

    monkeypatch.setattr(alerts_module, "make_alert", explode)

    response = await authed_client.post("/alerts/simulate", json={})

    assert response.text != "Internal Server Error"
    assert "internal_error" in response.text
