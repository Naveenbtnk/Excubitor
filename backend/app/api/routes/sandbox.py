from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user
from app.models import User
from app.schemas.sandbox import ChatRequest, ChatResponse
from app.services.llm import get_llm
from app.services.llm.base import LLMError, Message
from app.services.ratelimit import rate_limit

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

SYSTEM_PROMPT = (
    "You are CloudOps AI, an SRE copilot. Give practical, safe, step-by-step operations guidance."
)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    _: User = Depends(get_current_user),
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
    _: User = Depends(get_current_user),
    _limit: None = Depends(rate_limit("sandbox.stream", "llm_rate_limit")),
):
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
            yield {"event": "error", "data": str(exc)}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(events())
