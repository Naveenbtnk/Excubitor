"""The startup guard: fatal when a critical secret is missing, a warning when it is weak.

The distinction is the point of the feature, so both halves are asserted here: a production
deployment with the shipped default must not start at all, and one with a present-but-weak
value must start with the problem written down.
"""

import logging
import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.startup import (
    production_problems,
    verify_startup_settings,
    weak_setting_warnings,
)

# 40 characters, no placeholder words, nothing that looks like a default.
STRONG_SECRET = "Kf3n2p9wQz8sVb4tRy6uJm1hLd7gXc5eQa0Z"


def build(**overrides) -> Settings:
    """A production deployment that is properly configured, before the overrides.

    Every setting this module reasons about is named here rather than left to the environment.
    ``Settings`` reads a ``.env`` file, so a developer who turned the demo login on locally would
    otherwise get a failure about a deployment this test never described.
    """
    values: dict[str, object] = {
        "environment": "production",
        "jwt_secret": STRONG_SECRET,
        "admin_password": "Str0ng-Passw0rd!",
        "gemini_api_key": "test-key",
        "llm_provider": "gemini",
        "cookie_secure": True,
        "redis_url": "redis://cache:6379/0",
        "alertmanager_webhook_token": "shared-token",
        "demo_account_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_a_properly_configured_deployment_has_nothing_to_report():
    config = build()

    assert production_problems(config) == []
    assert weak_setting_warnings(config) == []
    verify_startup_settings(config)  # does not raise


def test_an_enabled_demo_login_is_announced_at_startup():
    """A deployment publishing a shared credential is healthy, and says so out loud."""
    password = "a-shared-password-nobody-else-uses"
    config = build(demo_account_enabled=True, demo_account_password=password)

    assert production_problems(config) == []
    warnings = weak_setting_warnings(config)

    assert len(warnings) == 1
    assert "demo" in warnings[0]
    assert "Viewer" in warnings[0]
    # The address is what an operator needs to recognise; the password reaches the log through no
    # path at all, however loud the warning is.
    assert password not in warnings[0]


def test_an_enabled_demo_login_without_a_password_is_a_problem_in_production():
    """Enabling the feature without a credential provisions nothing, so production refuses it."""
    config = build(demo_account_enabled=True, demo_account_password="")

    problems = production_problems(config)

    assert len(problems) == 1
    assert "DEMO_ACCOUNT_PASSWORD" in problems[0]


def test_the_demo_login_stays_off_out_of_the_box():
    """No env file and no overrides: the defaults are what a deployment gets for free."""
    config = Settings(_env_file=None)

    assert config.demo_account_enabled is False
    assert config.demo_login_available is False


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"jwt_secret": "change-me-in-production"}, "JWT_SECRET"),
        ({"jwt_secret": ""}, "JWT_SECRET"),
        ({"jwt_secret": "change-this-to-a-long-random-secret-before-deployment"}, "JWT_SECRET"),
        ({"admin_password": ""}, "ADMIN_PASSWORD"),
        ({"gemini_api_key": ""}, "GEMINI_API_KEY"),
        ({"debug": True}, "DEBUG"),
    ],
)
def test_a_production_deployment_refuses_the_shipped_defaults(overrides, expected):
    config = build(**overrides)

    assert any(expected in problem for problem in production_problems(config))
    with pytest.raises(RuntimeError) as raised:
        verify_startup_settings(config)

    assert expected in str(raised.value)
    assert "DEPLOYMENT.md" in str(raised.value)


def test_the_failure_never_quotes_the_value_it_rejected():
    """The log and the exception are read by more people than the secret store is."""
    secret = "change-me-in-production"

    with pytest.raises(RuntimeError) as raised:
        verify_startup_settings(build(jwt_secret=secret, admin_password=""))

    assert secret not in str(raised.value)
    assert all(secret not in problem for problem in production_problems(build(jwt_secret=secret)))


def test_a_local_provider_needs_no_gemini_key():
    config = build(llm_provider="ollama", gemini_api_key="")

    assert production_problems(config) == []
    verify_startup_settings(config)


def test_development_starts_with_the_defaults_it_ships():
    """Otherwise the quickstart in the README would be describing something that cannot run."""
    config = build(
        environment="development",
        jwt_secret="change-me-in-production",
        admin_password="",
    )

    verify_startup_settings(config)  # warns only


def test_a_weak_but_present_secret_warns_and_still_starts(caplog):
    with caplog.at_level(logging.WARNING, logger="cloudops.startup"):
        verify_startup_settings(build(jwt_secret="changeme"))

    warnings = weak_setting_warnings(build(jwt_secret="changeme"))
    assert any("shorter than" in warning for warning in warnings)
    assert any("placeholder" in warning for warning in warnings)

    emitted = [record for record in caplog.records if record.name == "cloudops.startup"]
    assert [record.getMessage() for record in emitted] == ["insecure_setting"] * len(warnings)
    # The warning carries the advice, and the record itself holds only setting *names*.
    assert all("changeme" not in str(record.extra_fields) for record in emitted)


def test_the_optional_integrations_are_reported_when_they_are_off():
    warnings = weak_setting_warnings(
        build(redis_url="", alertmanager_webhook_token="", cookie_secure=False)
    )

    joined = " ".join(warnings)
    assert "REDIS_URL is unset" in joined
    assert "ALERTMANAGER_WEBHOOK_TOKEN is unset" in joined
    assert "COOKIE_SECURE is false" in joined
    # All three are judgement calls, so they must not be fatal.
    verify_startup_settings(build(redis_url="", alertmanager_webhook_token="", cookie_secure=False))


def test_a_proxy_less_patient_than_the_slowest_answer_is_reported():
    """The failure this prevents: the proxy gives up first, so the API's 503 and its request id
    never reach the caller and a bounded retry budget looks like a hang."""
    config = build(proxy_read_timeout_seconds=60.0)  # nginx's own default
    warnings = weak_setting_warnings(config)

    joined = " ".join(warnings)
    assert "PROXY_READ_TIMEOUT_SECONDS" in joined
    # Both numbers, so the fix is arithmetic rather than trial and error.
    assert "60s" in joined
    assert "91.5s" in joined
    # A judgement call like the others, so it must not be fatal.
    verify_startup_settings(config)


def test_the_warning_follows_whichever_bound_is_longer():
    # Just under the slowest answer: still too impatient.
    assert any(
        "PROXY_READ_TIMEOUT_SECONDS" in w
        for w in weak_setting_warnings(build(proxy_read_timeout_seconds=91.4))
    )
    # At or above it, the advice is gone.
    assert not [
        w
        for w in weak_setting_warnings(build(proxy_read_timeout_seconds=91.5))
        if "PROXY_READ_TIMEOUT_SECONDS" in w
    ]

    # A stream is bounded between chunks rather than in total, so a long idle bound is what the
    # proxy has to outwait -- and it can be the longer of the two.
    streaming = build(proxy_read_timeout_seconds=100.0, llm_stream_idle_timeout=150.0)
    assert any("PROXY_READ_TIMEOUT_SECONDS" in w for w in weak_setting_warnings(streaming))


def test_raising_the_retry_budget_without_the_proxy_is_reported():
    patient = build(proxy_read_timeout_seconds=600.0)
    assert not [
        w for w in weak_setting_warnings(patient) if "PROXY_READ_TIMEOUT_SECONDS" in w
    ]

    # More attempts than the proxy can wait for is the same mistake in the other direction.
    assert any(
        "PROXY_READ_TIMEOUT_SECONDS" in w
        for w in weak_setting_warnings(build(proxy_read_timeout_seconds=600.0, llm_max_retries=40))
    )

    # Disabling the deadline is its own warning rather than a comparison: there is no worst
    # case to compare against, and the proxy becomes the only thing that ends a stuck call.
    unbounded = build(llm_max_retries=0, llm_request_timeout=0.0, llm_stream_idle_timeout=0.0)
    reported = " ".join(weak_setting_warnings(unbounded))
    assert "LLM_REQUEST_TIMEOUT is 0" in reported
    assert "PROXY_READ_TIMEOUT_SECONDS" not in reported


ENV_LINE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=")


def documented_settings() -> set[str]:
    """The setting names `backend/.env.example` mentions, commented out or not."""
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    return {match.group(1) for match in map(ENV_LINE.match, text.splitlines()) if match}


def test_env_example_documents_every_setting_the_code_reads():
    """A setting that only exists in the code is a setting nobody can configure."""
    expected = {name.upper() for name in Settings.model_fields}

    assert expected - documented_settings() == set()


def test_env_example_documents_nothing_the_code_does_not_read():
    """And the other way: a documented variable that does nothing is a trap for an operator."""
    expected = {name.upper() for name in Settings.model_fields}

    assert documented_settings() - expected == set()


def test_the_cookie_warning_is_quiet_outside_production():
    """The local stack serves plain HTTP on purpose, and that is not worth a warning."""
    warnings = weak_setting_warnings(build(environment="development", cookie_secure=False))

    assert not any("COOKIE_SECURE" in warning for warning in warnings)
