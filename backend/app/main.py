from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    alerts,
    auth,
    health,
    incidents,
    knowledge,
    overview,
    sandbox,
    users,
    webhooks,
)
from app.core.config import settings
from app.core.counter_store import close_counter_stores
from app.core.errors import InternalErrorMiddleware
from app.core.observability import RequestContextMiddleware, configure_logging, request_id_of
from app.core.startup import verify_startup_settings
from app.db.session import close_db, create_schema, seed_admin, seed_demo_data
from app.services.llm.base import LLMError, LLMTransientError
from app.services.llm.errors import public_llm_error


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.is_production:
        await create_schema()
    await seed_admin()
    # Demo data is opt-out as well as opt-in: see `should_seed_demo_data`.
    await seed_demo_data()
    yield
    await close_counter_stores()
    await close_db()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    # Before anything else is built: a production deployment missing a secret must fail here,
    # at startup, rather than serve requests it cannot sign or authenticate.
    verify_startup_settings(settings)
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan, debug=settings.debug)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    # Added last, so it is the outermost: anything the middleware below it did not turn into a
    # response -- including a bug in one of them -- becomes a shaped 500 instead of a bare
    # error page or a reset connection.
    app.add_middleware(InternalErrorMiddleware)

    @app.exception_handler(LLMTransientError)
    async def llm_transient_error_handler(request: Request, exc: LLMTransientError):
        # Tell the caller the dependency is temporarily down rather than broken, so it knows
        # a later attempt can succeed. When the provider stated its own wait, that beats any
        # local guess: a quota can be a per-minute limit or a daily cap, hours apart.
        fallback = (
            settings.llm_retry_max_delay
            if exc.replayable
            else settings.rate_limit_window_seconds
        )
        retry_after = max(1, int(exc.retry_after if exc.retry_after is not None else fallback))
        # `detail` stays a plain readable sentence, which is what every existing client reads;
        # `error` carries the same thing in structured form for a UI that wants to render its
        # own wording. Neither contains a byte of the provider's own text -- that is logged.
        public = public_llm_error(
            exc,
            provider=settings.llm_provider,
            request_id=request_id_of(request.scope),
            operation=request.url.path,
            retry_after=retry_after,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": public.message, "error": public.payload()},
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(LLMError)
    async def llm_error_handler(request: Request, exc: LLMError):
        public = public_llm_error(
            exc,
            provider=settings.llm_provider,
            request_id=request_id_of(request.scope),
            operation=request.url.path,
        )
        return JSONResponse(
            status_code=502, content={"detail": public.message, "error": public.payload()}
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(overview.router)
    app.include_router(alerts.router)
    app.include_router(knowledge.router)
    app.include_router(sandbox.router)
    app.include_router(incidents.router)
    app.include_router(users.router)
    # Registered like any other router, but authenticated by shared token rather than a
    # session cookie: the caller is Alertmanager, which has no user to check a role against.
    app.include_router(webhooks.router)
    return app


app = create_app()
