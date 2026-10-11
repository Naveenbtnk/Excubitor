"""Refuse to start a production deployment that is missing a secret or leaking one.

Two different failures, handled two different ways.

**Missing or still-the-default** is fatal. ``JWT_SECRET`` ships with a placeholder so a fresh
checkout runs, and a deployment that never replaced it would be signing session cookies with a
value published in the source -- the worst kind of failure, because everything looks like it
works. Same for an empty ``ADMIN_PASSWORD`` (no way in, or worse, a guessable way in) and for
``GEMINI_API_KEY`` when the provider is Gemini. ``DEBUG`` is fatal too: it returns tracebacks.

**Weak-but-present** is a warning. Only the operator knows what their secret looks like, and a
process that refuses to start because a string contains the word "test" is a process that gets
its guard removed. So a short secret, a placeholder-looking one, a session cookie that will
travel over plain HTTP, an in-process counter store, a silent webhook or a reverse proxy that
will not wait as long as the provider layer can are all reported at startup and then accepted --
the log is the right place for that judgement call.

Neither path ever prints a value. Names only: the whole point is that the log is not a copy of
the secret store.
"""

from __future__ import annotations

import logging

from app.core.config import Settings

logger = logging.getLogger("cloudops.startup")

# The values this repository ships with. Anything here is a placeholder by definition, because
# it is in the source and therefore public.
DEFAULT_SECRETS = frozenset(
    {
        "change-me-in-production",
        "change-this-to-a-long-random-secret-before-deployment",
        "replace-with-a-long-random-secret",
    }
)

# Substrings that mark a value as something somebody meant to replace later.
PLACEHOLDER_MARKERS = ("change", "replace", "placeholder", "example", "your-")
MIN_JWT_SECRET_LENGTH = 32


def production_problems(config: Settings) -> list[str]:
    """Why a production deployment must not start. Setting *names*, never values."""
    problems: list[str] = []
    if not config.jwt_secret or config.jwt_secret in DEFAULT_SECRETS:
        problems.append("JWT_SECRET is unset or is still the value this repository ships with")
    if not config.admin_password:
        problems.append("ADMIN_PASSWORD is unset, so the seeded administrator has no password")
    if config.llm_provider == "gemini" and not config.gemini_api_key:
        problems.append("GEMINI_API_KEY is unset while LLM_PROVIDER is gemini")
    if config.debug:
        problems.append("DEBUG is on, which would return tracebacks to callers")
    return problems


def weak_setting_warnings(config: Settings) -> list[str]:
    """Settings that will work but that an operator probably wants to know about."""
    warnings: list[str] = []
    secret = config.jwt_secret or ""
    if secret and len(secret) < MIN_JWT_SECRET_LENGTH:
        warnings.append(
            f"JWT_SECRET is shorter than {MIN_JWT_SECRET_LENGTH} characters; "
            "a signing key should be long and random (see DEPLOYMENT.md)"
        )
    if any(marker in secret.lower() for marker in PLACEHOLDER_MARKERS):
        warnings.append("JWT_SECRET still reads like a placeholder")
    if config.is_production and not config.cookie_secure:
        warnings.append(
            "COOKIE_SECURE is false, so the session cookie is sent over plain HTTP; "
            "set it to true behind HTTPS"
        )
    if not config.redis_url:
        warnings.append(
            "REDIS_URL is unset, so each worker keeps its own rate-limit and provider-budget "
            "counters and the real ceiling is workers x limit"
        )
    if not config.alertmanager_webhook_token:
        warnings.append(
            "ALERTMANAGER_WEBHOOK_TOKEN is unset, so POST /webhooks/alertmanager answers 503"
        )
    # A deadline the proxy will not wait out is not a deadline: the caller gets whatever error
    # the proxy produces -- usually a bare 502 or 504 with none of the request id or the sentence
    # the API was about to send -- and the retry policy that caused it looks like a hang. Both
    # numbers are ours, so this is a comparison rather than advice.
    if config.llm_request_timeout <= 0:
        warnings.append(
            "LLM_REQUEST_TIMEOUT is 0, so no attempt is bounded and the proxy in front is the "
            "only thing that will end a stuck provider call -- see DEPLOYMENT.md"
        )
    elif config.proxy_read_timeout_seconds < config.llm_patience_seconds:
        warnings.append(
            f"PROXY_READ_TIMEOUT_SECONDS is {config.proxy_read_timeout_seconds:g}s but this "
            f"service can take {config.llm_patience_seconds:g}s to answer "
            "(LLM_REQUEST_TIMEOUT x (LLM_MAX_RETRIES + 1) plus backoff, or "
            "LLM_STREAM_IDLE_TIMEOUT between streamed chunks), so a proxy in front would give "
            "up first; raise the proxy's read timeout for /api/ (see DEPLOYMENT.md)"
        )
    return warnings


def verify_startup_settings(config: Settings) -> None:
    """Log what should be fixed, and refuse to start when it is fatal.

    Called once, while the app is being built, so a misconfigured production deployment fails
    at startup instead of at the first login attempt.
    """
    for warning in weak_setting_warnings(config):
        logger.warning(
            "insecure_setting",
            extra={"extra_fields": {"setting": warning, "environment": config.environment}},
        )

    if not config.is_production:
        return

    problems = production_problems(config)
    if problems:
        raise RuntimeError(
            "Refusing to start: "
            + "; ".join(problems)
            + ". Set real values in the environment (see DEPLOYMENT.md)."
        )
