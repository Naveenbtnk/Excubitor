from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CloudOps AI"
    environment: str = "development"
    api_prefix: str = "/api"

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

    llm_max_retries: int = 2
    llm_retry_base_delay: float = 0.5
    llm_retry_max_delay: float = 8.0

    # Per-attempt deadlines in seconds; 0 disables the bound.
    llm_request_timeout: float = 30.0
    llm_stream_idle_timeout: float = 60.0
    llm_connect_timeout: float = 5.0

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

    log_level: str = "INFO"

    @computed_field
    @property
    def llm_model_name(self) -> str:
        return self.gemini_model if self.llm_provider == "gemini" else self.ollama_model

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
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
