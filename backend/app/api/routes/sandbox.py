from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_role
from app.core.config import settings
from app.core.errors import public_internal_error
from app.core.observability import request_id_of
from app.core.rbac import Role
from app.models import User
from app.schemas.sandbox import ChatRequest, ChatResponse
from app.services.llm import get_llm
from app.services.llm.base import LLMError, Message
from app.services.llm.errors import public_llm_error
from app.services.ratelimit import rate_limit

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

SYSTEM_PROMPT = (
    "You are Excubitor, an SRE copilot. Give practical, safe, step-by-step operations guidance."
)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    _: User = Depends(require_role(Role.OPERATOR)),
    _limit: None = Depends(rate_limit("sandbox.chat", "llm_rate_limit")),
):
    reply = await get_llm().chat(
        [Message(role="user", content=payload.message)], system=SYSTEM_PROMPT
    )
    assert isinstance(reply, str)
    return ChatResponse(reply=reply)


@router.post("/stream")
async def stream(
    payload: ChatRequest,
    request: Request,
    _: User = Depends(require_role(Role.OPERATOR)),
    _limit: None = Depends(rate_limit("sandbox.stream", "llm_rate_limit")),
):
    request_id = request_id_of(request.scope)

    async def events():
        try:
            async for chunk in get_llm().stream(
                [Message(role="user", content=payload.message)], system=SYSTEM_PROMPT
            ):
                yield {"event": "message", "data": chunk}
        except LLMError as exc:
            # The status line is already gone: this response committed to 200 and SSE headers
            # before the first token was pulled, so a provider failure cannot become a 503 any
            # more. Starlette refuses to rewrite a started response and raises instead, which
            # would abort the connection with a traceback. A terminal error event is the only
            # honest way left to tell the caller that the rest is not coming.
            #
            # What travels in that event is a fixed, provider-agnostic description: a code, the
            # provider name, an optional wait and a request id. The upstream text -- which for a
            # Gemini 429 is a kilobyte of JSON naming the quota metric, the model and a docs URL
            # -- is logged by `public_llm_error` and goes no further. The browser is a rendering
            # surface for our wording, not a console for the provider's.
            public = public_llm_error(
                exc,
                provider=settings.llm_provider,
                request_id=request_id,
                operation="sandbox.stream",
            )
            yield {"event": "error", "data": public.to_json()}
        except Exception as exc:
            # Anything unexpected, from a bug here to a database that went away mid-answer. The
            # status line and the SSE headers are already on the wire, so raising would reset
            # the connection with a traceback on the server and nothing at all for the reader.
            # The traceback is logged against the request id; the terminal event carries the
            # sentence and that id, which is the same contract as a 500 on a JSON route.
            public = public_internal_error(
                exc, request_id=request_id, operation="sandbox.stream"
            )
            yield {"event": "error", "data": public.to_json()}
        yield {"event": "done", "data": ""}

    # `no-transform` is load-bearing, not decoration: a proxy in front of this service (the
    # Next.js server the dashboard runs behind) will happily gzip an event stream, and a gzip
    # encoder buffers until it has a block to emit. The tokens then arrive in one lump at the
    # end instead of as they are written, which defeats the whole endpoint while looking fine
    # to any client that does not send `Accept-Encoding` - browsers always do.
    return EventSourceResponse(events(), headers={"Cache-Control": "no-store, no-transform"})
