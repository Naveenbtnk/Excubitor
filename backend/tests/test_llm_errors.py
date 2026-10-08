"""The provider's own words must not reach the browser.

These tests are mostly one invariant stated several ways: whatever raw text a provider attaches
to a failure -- a Gemini 429 body alone is about a kilobyte of JSON naming the quota metric, the
model, the project and a docs URL -- never appears in a response body or an SSE frame. The
classification that replaces it is a fixed vocabulary, and the raw material is asserted to be
present in the *logs*, so sanitising a response cannot silently destroy the diagnostic.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import AsyncClient

from app.api.routes import sandbox as sandbox_module
from app.services.llm.base import (
    LLMError,
    LLMErrorKind,
    LLMProvider,
    LLMTransientError,
    Message,
)
from app.services.llm.errors import (
    QUOTA_WAIT_THRESHOLD_SECONDS,
    format_duration,
    kind_for_wait,
    provider_label,
    public_llm_error,
)

# Verbatim shape of the free-tier 429 that motivated all of this, captured from a live call.
RAW_QUOTA_BODY = {
    "error": {
        "code": 429,
        "message": (
            "You exceeded your current quota, please check your plan and billing details. "
            "For more information on this error, head to: "
            "https://ai.google.dev/gemini-api/docs/rate-limits."
        ),
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "quotaMetric": (
                            "generativelanguage.googleapis.com/"
                            "generate_content_free_tier_requests"
                        ),
                        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                        "quotaValue": "20",
                    }
                ],
            },
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": "56023.632260421s",
            },
        ],
    }
}

RAW_TEXT = f"429 RESOURCE_EXHAUSTED. {json.dumps(RAW_QUOTA_BODY)}"

# Anything in this list appearing in a user-facing string is a leak. Chosen from the payload
# above: they are the identifiers that make it diagnostic *and* the ones that make it
# unsuitable for a chat bubble.
LEAK_MARKERS = (
    "generativelanguage.googleapis.com",
    "QUOTA_EXHAUSTED",
    "quotaMetric",
    "quotaId",
    "quotaValue",
    "RetryInfo",
    "ai.google.dev",
    "@type",
    "RateLimit",
    "free_tier",
)


def assert_clean(text: str) -> None:
    for marker in LEAK_MARKERS:
        assert marker not in text, f"provider internals leaked into {text!r}"


def quota_failure(
    retry_after: float | None = 56023.632260421,
    *,
    kind: LLMErrorKind | None = None,
) -> LLMTransientError:
    """A 429 carrying the raw body above, classified the way the Gemini provider classifies."""
    return LLMTransientError(
        RAW_TEXT,
        replayable=False,
        retry_after=retry_after,
        kind=kind if kind is not None else kind_for_wait(retry_after),
    )


class _Collect(logging.Handler):
    """Capture records from one logger.

    pytest's ``caplog`` cannot see them: ``configure_logging`` replaces the root handlers at
    import time, which removes the capture handler the plugin installed there. Attaching to
    ``cloudops.llm`` directly also keeps the assertion on the logger the app actually uses.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def captured_llm_logs() -> Iterator[_Collect]:
    collector = _Collect()
    logger = logging.getLogger("cloudops.llm")
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)


class RawFailureProvider(LLMProvider):
    """A provider that fails exactly the way Gemini did, on the first pull."""

    def __init__(self, exc: LLMError) -> None:
        self.exc = exc

    async def health(self) -> str:
        return "ok"

    async def chat(self, messages: list[Message], *, system=None, response_model=None):
        raise self.exc

    async def stream(self, messages: list[Message], *, system=None) -> AsyncIterator[str]:
        raise self.exc
        yield ""  # pragma: no cover - makes this an async generator

    async def embed(self, text: str) -> list[float]:
        raise self.exc


# --- classification -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, LLMErrorKind.RATE_LIMITED),
        (0.5, LLMErrorKind.RATE_LIMITED),
        (60.0, LLMErrorKind.RATE_LIMITED),
        (QUOTA_WAIT_THRESHOLD_SECONDS - 1, LLMErrorKind.RATE_LIMITED),
        (float(QUOTA_WAIT_THRESHOLD_SECONDS), LLMErrorKind.QUOTA_EXHAUSTED),
        (56023.632260421, LLMErrorKind.QUOTA_EXHAUSTED),
    ],
)
def test_a_long_wait_is_what_makes_it_a_quota(seconds, expected):
    """429 is ambiguous; the wait is not, and reading the quota metadata would be the leak."""
    assert kind_for_wait(seconds) is expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 seconds"),
        (1, "1 second"),
        (59, "59 seconds"),
        (60, "1 minute"),
        (540, "9 minutes"),
        (3600, "1 hour"),
        (7260, "2 hours 1 minute"),
        (63540, "17 hours 39 minutes"),
        (80520, "22 hours 22 minutes"),
    ],
)
def test_durations_read_the_way_a_person_would_say_them(seconds, expected):
    assert format_duration(seconds) == expected


def test_unknown_providers_are_labelled_from_our_own_configuration():
    assert provider_label("gemini") == "Gemini"
    assert provider_label("ollama") == "Ollama"
    assert provider_label("together") == "Together"


# --- the projection -------------------------------------------------------------------


def test_a_quota_failure_becomes_a_sentence_with_a_countdown():
    with captured_llm_logs() as captured:
        public = public_llm_error(
            quota_failure(), provider="gemini", request_id="abc123", operation="sandbox.stream"
        )

    assert public.code == "quota_exhausted"
    assert public.retry_after == 56023
    assert public.request_id == "abc123"
    # The copy the Sandbox page is supposed to show, verbatim: what happened, when it clears,
    # and what to do about it -- with no trace of the provider's own payload.
    assert public.message == (
        "Gemini's daily quota has been reached. "
        "Sandbox responses will be available again in approximately 15 hours 33 minutes. "
        "Switch to the local Ollama provider to keep working in the meantime."
    )
    assert_clean(public.message)
    assert_clean(json.dumps(public.payload()))

    # The detail is not discarded, it is moved: one structured line carrying the raw body and
    # the request id, which is what an operator has to read to diagnose a quota.
    assert len(captured.records) == 1
    assert captured.records[0].levelno == logging.WARNING
    assert captured.records[0].getMessage() == "llm_failure"
    fields = captured.records[0].extra_fields  # type: ignore[attr-defined]
    assert fields["detail"] == RAW_TEXT
    assert fields["request_id"] == "abc123"
    assert fields["code"] == "quota_exhausted"
    assert fields["operation"] == "sandbox.stream"
    assert fields["replayable"] is False
    assert fields["retry_after_s"] == 56023


def test_an_unknown_wait_drops_the_countdown_instead_of_guessing():
    """A 429 with no RetryInfo is still a quota; it just cannot say when it clears."""
    public = public_llm_error(
        quota_failure(None, kind=LLMErrorKind.QUOTA_EXHAUSTED), provider="gemini"
    )

    assert public.retry_after is None
    assert public.message == (
        "Gemini's daily quota has been reached. "
        "Try again later or switch to the local Ollama provider."
    )
    # Absent rather than null: a client can tell "no idea" from "wait 0 seconds".
    assert "retry_after" not in public.payload()


def test_the_ollama_alternative_is_not_offered_when_ollama_is_the_provider():
    public = public_llm_error(quota_failure(), provider="ollama")

    assert public.message == (
        "Ollama's daily quota has been reached. Try again in approximately 15 hours 33 minutes."
    )
    assert "switch to" not in public.message


def test_the_local_budget_says_the_remedy_is_ours_to_fix():
    exc = LLMTransientError(
        "chat refused locally: all 20 calls allowed per 86400s are spent. "
        "Raise LLM_QUOTA_LIMIT to allow more calls, or set it to 0 to stop counting.",
        replayable=False,
        retry_after=86400,
        kind=LLMErrorKind.BUDGET_EXHAUSTED,
    )

    public = public_llm_error(exc, provider="gemini")

    assert public.code == "budget_exhausted"
    assert public.message == (
        "The app has used up its Gemini call allowance for now. "
        "Try again in approximately 24 hours. "
        "Switch to the local Ollama provider to keep working in the meantime."
    )
    assert "LLM_QUOTA_LIMIT" not in public.message
    assert_clean(public.message)


def test_every_kind_produces_wording_a_user_can_act_on():
    """A code without a sentence is as useless as a sentence without a code."""
    for kind in LLMErrorKind:
        public = public_llm_error(
            LLMTransientError("upstream said something unprintable", kind=kind),
            provider="gemini",
        )
        assert public.code == str(kind)
        assert public.message.endswith((".", "support."))
        assert_clean(public.message)


def test_a_permanent_error_hides_our_own_internals_too():
    """``GEMINI_API_KEY is not configured`` is our text, and still not a user's business."""
    public = public_llm_error(LLMError("GEMINI_API_KEY is not configured"), provider="gemini")

    assert public.code == "provider_error"
    assert public.message.startswith("Gemini could not complete this request.")
    assert "GEMINI_API_KEY" not in public.message


def test_the_sse_serialisation_is_json_not_a_python_repr():
    """sse_starlette's encoder calls ``str(data)``, so the dict has to be serialised here."""
    payload = json.loads(public_llm_error(quota_failure(), provider="gemini").to_json())

    assert payload["code"] == "quota_exhausted"
    assert payload["provider"] == "gemini"
    assert payload["retry_after"] == 56023


# --- the HTTP surface -----------------------------------------------------------------


async def test_chat_route_503_carries_a_code_and_a_diagnostic_id(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        sandbox_module, "get_llm", lambda: RawFailureProvider(quota_failure())
    )

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["detail"] == (
        "Gemini's daily quota has been reached. "
        "Sandbox responses will be available again in approximately 15 hours 33 minutes. "
        "Switch to the local Ollama provider to keep working in the meantime."
    )
    assert body["error"]["code"] == "quota_exhausted"
    assert body["error"]["retry_after"] == 56023
    # The id the caller needs for support is the one already on the response.
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert_clean(json.dumps(body))


async def test_chat_route_502_leaks_nothing_either(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        sandbox_module, "get_llm", lambda: RawFailureProvider(LLMError("bad model: gemini-9"))
    )

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    assert "gemini-9" not in response.text
    assert_clean(response.text)


async def test_a_stream_failure_sends_a_structured_frame_not_the_payload(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The one path where a failure is an event in a 200 response rather than an error status."""
    monkeypatch.setattr(
        sandbox_module, "get_llm", lambda: RawFailureProvider(quota_failure())
    )

    response = await authed_client.post("/sandbox/stream", json={"message": "hello"})

    assert response.status_code == 200, "the status line was already sent"
    assert_clean(response.text)
    match = re.search(r"data: (\{.*\})", response.text)
    assert match, response.text
    frame = json.loads(match.group(1))
    assert frame["code"] == "quota_exhausted"
    assert frame["provider"] == "gemini"
    assert frame["retry_after"] == 56023
    assert frame["request_id"] == response.headers["x-request-id"]
    assert "15 hours 33 minutes" in frame["message"]
    assert response.text.index("event: error") < response.text.index("event: done")


async def test_an_unknown_code_still_yields_the_servers_own_safe_sentence(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """A client that does not know a code renders the server's sentence, which is safe by
    construction -- it is why the wording is projected server-side at all."""
    unknown = LLMTransientError("upstream said something unprintable", kind="something_new")
    monkeypatch.setattr(sandbox_module, "get_llm", lambda: RawFailureProvider(unknown))

    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "something_new"
    assert "Gemini could not complete this request." in response.json()["detail"]
    assert_clean(response.text)
