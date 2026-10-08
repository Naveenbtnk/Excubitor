"""What a caller gets when something failed that we did not expect.

A 500 is the one response nobody plans for, which is why it is the easiest place to leak. The
three shapes this module exists to prevent:

- a traceback in the body, naming the file, the line and often the query or the path;
- a bare "Internal Server Error" with nothing that ties it to a log line, which turns one bug
  report into a search through every request of the day;
- a reset connection, which tells the caller even less than the above.

So an unexpected failure is projected the same way a provider failure is: the exception and its
traceback go to the log next to the request id, and the caller receives a sentence, a stable
code and that id. The same shape as the provider errors in ``app.services.llm.errors``, so the
browser parses it with the code it already has.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse

from app.core.observability import request_id_of

logger = logging.getLogger("cloudops.error")

# Deliberately says nothing about what failed. The request id is the part that helps, and the
# log line carrying it holds everything that would.
INTERNAL_ERROR_MESSAGE = (
    "Something went wrong on our side. Try again in a moment, and quote the diagnostic ID if "
    "it keeps happening."
)


@dataclass(frozen=True)
class PublicError:
    """A failure of ours as a client sees it: no exception text, no stack, no internals."""

    code: str
    message: str
    request_id: str | None
    operation: str | None = None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload

    def to_json(self) -> str:
        """Serialise for an SSE ``data:`` field, which has no other way to carry an object."""
        return json.dumps(self.payload())


def public_internal_error(
    exc: BaseException, *, request_id: str | None, operation: str | None
) -> PublicError:
    """Log the failure in full and return the part that is safe to send.

    One function for both, exactly as ``public_llm_error`` does: projecting without logging
    would lose the only copy of the diagnosis, and logging without projecting tends to grow a
    second, unshaped way of telling the caller.
    """
    logger.error(
        "internal_error",
        # A real logging kwarg, not an `extra` key: `extra` cannot overwrite a record attribute,
        # and passing the exception here (rather than a formatted string) is what makes the JSON
        # formatter attach the traceback.
        exc_info=exc,
        extra={
            "extra_fields": {
                "code": "internal_error",
                "operation": operation,
                "request_id": request_id,
                "exception": type(exc).__name__,
                # The message, recorded here and nowhere else.
                "detail": f"{type(exc).__name__}: {exc}",
            }
        },
    )
    return PublicError(
        code="internal_error",
        message=INTERNAL_ERROR_MESSAGE,
        request_id=request_id,
        operation=operation,
    )


class InternalErrorMiddleware:
    """Give an unexpected exception the same shaped 500 every client already parses.

    Registered outermost, so it sees anything the request context has not already turned into a
    response. A response that has already started is left alone and re-raised: its status line
    is on the wire, and pretending otherwise would produce a second, contradictory response.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def track(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track)
        except Exception as exc:
            if started:
                raise
            request_id = request_id_of(scope)
            public = public_internal_error(
                exc, request_id=request_id, operation=scope.get("path")
            )
            headers = {"X-Request-ID": request_id} if request_id else None
            response = JSONResponse(
                status_code=500,
                content={"detail": public.message, "error": public.payload()},
                headers=headers,
            )
            await response(scope, receive, send)
