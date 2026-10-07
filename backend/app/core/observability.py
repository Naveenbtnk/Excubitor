import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

_QUIET_PATHS = frozenset({"/health", "/health/llm"})

request_logger = logging.getLogger("cloudops.request")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Gunicorn and Uvicorn install their own stream handlers before the app loads;
    # keep those streams but emit them through the same JSON formatter.
    for name in ("gunicorn.error", "gunicorn.access", "uvicorn.error", "uvicorn.access"):
        for existing in logging.getLogger(name).handlers:
            existing.setFormatter(JsonFormatter())

    # The raw access logs are superseded by the http_request lines, which carry the request id,
    # duration and authenticated user. Leaving them on would double every request and flood the
    # stream with the 10-second container healthcheck.
    for name in ("gunicorn.access", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def request_id_of(scope: dict[str, Any]) -> str | None:
    state = scope.get("state") or {}
    return state.get("request_id")


class RequestContextMiddleware:
    """Attaches a request id, echoes it back, and emits one structured line per request."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"")
        request_id = incoming.decode("latin-1")[:64] or uuid.uuid4().hex[:16]
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        status_code = 500

        async def send_with_header(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", request_id.encode("latin-1"))
                )
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_header)
        except Exception:
            request_logger.exception(
                "http_request_failed",
                extra={"extra_fields": self._fields(scope, request_id, 500, started)},
            )
            raise
        else:
            fields = self._fields(scope, request_id, status_code, started)
            if scope.get("path") in _QUIET_PATHS and status_code < 400:
                request_logger.debug("http_request", extra={"extra_fields": fields})
            else:
                request_logger.info("http_request", extra={"extra_fields": fields})

    @staticmethod
    def _fields(scope, request_id: str, status_code: int, started: float) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "method": scope.get("method"),
            "path": scope.get("path"),
            "status": status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "user_id": (scope.get("state") or {}).get("user_id"),
        }
