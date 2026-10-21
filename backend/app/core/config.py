from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Excubitor — CloudOps AI"
    environment: str = "development"
    # Off unless a development run asks for it. In production it would return tracebacks to
    # callers, so the startup guard refuses to start with it on.
    debug: bool = False

    # FastAPI's own /docs, /redoc and /openapi.json. A development aid: the schema maps every
    # route, parameter and response shape, which is useful at a desk and is a free reconnaissance
    # pass for anyone else. Left unset it follows the environment -- on outside production, off
    # inside it -- so a local run keeps the UI it expects and a deployment serves only the API and
    # the dashboard. Set it true to publish the schema deliberately, false to keep it off
    # anywhere.
    expose_api_docs: bool | None = None

    database_url: str = "postgresql+asyncpg://cloudops:cloudops@localhost:5434/cloudops_ai"

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_embed_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_embed_model: str = "nomic-embed-text"

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    jwt_secret: str = Field(default="change-me-in-production")
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 8
    cookie_name: str = "cloudops_session"
    cookie_secure: bool = False
    admin_email: str = "admin@cloudops.local"
    admin_password: str = "ChangeMe123!"

    # A shared, read-only login a stranger may use: the "try the demo" account. Off unless a
    # deployment asks for it *and* supplies the password, because this credential is published
    # (see ``GET /auth/demo``) rather than shipped in the dashboard bundle -- the server keeps one
    # copy, and a deployment that never enables the feature has no copy anywhere. Set
    # DEMO_ACCOUNT_PASSWORD to a value used nowhere else; it is public by construction.
    demo_account_enabled: bool = False
    demo_account_email: str = "demo@excubitor.local"
    demo_account_name: str = "Demo Visitor"
    demo_account_password: str = ""
    # AI calls the shared demo login may spend per ``llm_quota_window_seconds``. The per-minute
    # per-user limit bounds a burst but not a day: the demo account is one user, so without this
    # a single visitor could spend the project's whole provider budget (``LLM_QUOTA_LIMIT``)
    # before the next visitor arrived. ``0`` leaves the shared login on the ordinary limits.
    demo_account_rate_limit: int = 5

    # Shared secret for the Alertmanager webhook, which has no user behind it. Empty means the
    # route answers 503 rather than accepting anything: an unconfigured deployment must not be
    # an open write route.
    alertmanager_webhook_token: str = ""

    llm_max_retries: int = 2
    llm_retry_base_delay: float = 0.5
    llm_retry_max_delay: float = 8.0

    # Per-attempt deadlines in seconds; 0 disables the bound.
    llm_request_timeout: float = 30.0
    llm_stream_idle_timeout: float = 60.0
    llm_connect_timeout: float = 5.0

    # How long the reverse proxy in front of this service will wait for a response, as
    # configured there. Nothing here enforces it -- the proxy does -- but the worst case this
    # service can take to answer is arithmetic (see ``llm_patience_seconds``), and a proxy that
    # gives up first turns an honest 503 into a dead connection. The startup guard compares the
    # two and names both numbers when they disagree, and the default matches the examples in
    # DEPLOYMENT.md.
    proxy_read_timeout_seconds: float = 120.0

    llm_rate_limit: int = 20
    embedding_rate_limit: int = 10
    rate_limit_window_seconds: int = 60

    # Project-wide budget for upstream provider calls, counted once per attempt. ``None``
    # falls back to the provider's published free tier (see ``effective_llm_quota_limit``);
    # ``0`` disables the budget so every call is forwarded.
    llm_quota_limit: int | None = None
    llm_quota_window_seconds: int = 24 * 60 * 60

    # Backend for the shared counters. Empty keeps them in-process, which is correct for a
    # single worker and invisible to every other one, making the real ceiling
    # ``workers x limit``. Point this at Redis whenever more than one worker serves traffic.
    redis_url: str = ""
    redis_key_prefix: str = "cloudops"
    redis_timeout_seconds: float = 2.0

    pagination_default_limit: int = 50
    pagination_max_limit: int = 200

    # Demo alerts on an empty database, so a fresh checkout has something to look at. Left
    # unset it follows the environment: on outside production, off inside it, because a
    # production incident list that begins with three invented incidents is worse than an
    # empty one. Set it explicitly to override either way.
    seed_demo_data: bool | None = None

    log_level: str = "INFO"

    @computed_field
    @property
    def should_seed_demo_data(self) -> bool:
        if self.seed_demo_data is not None:
            return self.seed_demo_data
        return not self.is_production

    @computed_field
    @property
    def llm_model_name(self) -> str:
        return self.gemini_model if self.llm_provider == "gemini" else self.ollama_model

    @computed_field
    @property
    def llm_retry_budget_seconds(self) -> float:
        """Worst-case wall time of one non-streaming provider call, retries included.

        Every attempt is bounded by ``llm_request_timeout`` and every replay is preceded by a
        jittered delay, so the ceiling is arithmetic rather than a guess: attempts times the
        deadline, plus the largest delays the backoff policy can insert between them. It exists
        so the requirement a proxy has to meet is a number this service can state instead of a
        note in a document somebody has to remember -- see ``llm_patience_seconds``.
        """
        retries = max(0, self.llm_max_retries)
        backoff = sum(
            min(self.llm_retry_base_delay * 2**attempt, self.llm_retry_max_delay)
            for attempt in range(retries)
        )
        return (retries + 1) * self.llm_request_timeout + backoff

    @computed_field
    @property
    def llm_patience_seconds(self) -> float:
        """How long the slowest possible answer can keep a connection open.

        The larger of the retry budget above and the idle bound on a stream. A stream's total
        length is deliberately unbounded -- it is bounded between chunks, so a slow but actively
        producing generation is never cut off -- which means the longest a proxy may wait before
        giving up is the idle gap, not the whole answer.
        """
        return max(self.llm_retry_budget_seconds, self.llm_stream_idle_timeout)

    @computed_field
    @property
    def effective_llm_quota_limit(self) -> int:
        """Provider calls allowed per ``llm_quota_window_seconds`` before we refuse locally.

        Gemini's free tier allows 20 ``generateContent`` requests per day, per project, per
        model, so the default matches the allowance the app is really drawing on. A local
        runtime has no quota to protect and stays unlimited until one is configured.
        """
        if self.llm_quota_limit is not None:
            return self.llm_quota_limit
        return 0 if self.llm_provider == "ollama" else 20

    @computed_field
    @property
    def demo_login_available(self) -> bool:
        """Whether the demo login may be used at all: asked for, and given a password.

        Both halves are required. Enabled without a password would provision an account nobody
        can sign into, and a password with the feature off would leave a published credential
        that the deployment believes is switched off.
        """
        return self.demo_account_enabled and bool(self.demo_account_password)

    @computed_field
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @computed_field
    @property
    def should_expose_api_docs(self) -> bool:
        """Whether FastAPI mounts its schema, following the environment unless told otherwise.

        An explicit ``EXPOSE_API_DOCS`` always wins, in both directions: publishing the schema
        from production is a choice a deployment may make on purpose, and keeping it off in
        development is how the tests prove the off path without pretending to be production.
        """
        if self.expose_api_docs is not None:
            return self.expose_api_docs
        return not self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
