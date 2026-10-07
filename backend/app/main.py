from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import alerts, auth, health, incidents, knowledge, overview, sandbox
from app.core.config import settings
from app.core.observability import RequestContextMiddleware, configure_logging
from app.db.session import close_db, create_schema, seed_admin, seed_demo_alerts
from app.services.llm.base import LLMError, LLMTransientError


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.is_production:
        await create_schema()
    await seed_admin()
    if not settings.is_production:
        await seed_demo_alerts()
    yield
    await close_db()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(LLMTransientError)
    async def llm_transient_error_handler(_: Request, exc: LLMTransientError):
        # Tell the caller the dependency is temporarily down rather than broken, so it knows
        # a later attempt can succeed. When the provider stated its own wait, that beats any
        # local guess: a quota can be a per-minute limit or a daily cap, hours apart.
        fallback = (
            settings.llm_retry_max_delay
            if exc.replayable
            else settings.rate_limit_window_seconds
        )
        retry_after = max(1, int(exc.retry_after if exc.retry_after is not None else fallback))
        return JSONResponse(
            status_code=503,
            content={"detail": f"LLM provider temporarily unavailable: {exc}"},
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(LLMError)
    async def llm_error_handler(_: Request, exc: LLMError):
        return JSONResponse(status_code=502, content={"detail": f"LLM provider error: {exc}"})

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(overview.router)
    app.include_router(alerts.router)
    app.include_router(knowledge.router)
    app.include_router(sandbox.router)
    app.include_router(incidents.router)
    return app


app = create_app()
