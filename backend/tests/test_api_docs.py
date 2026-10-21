"""The API schema is a development aid: served where it helps, absent from a deployment.

FastAPI mounts ``/docs``, ``/redoc`` and ``/openapi.json`` unless it is told not to, so the
previous default was that every deployment -- production included -- published a map of its own
surface to anyone who asked. These tests pin the switch in both directions plus the two
environment defaults, because the failure is silent: nothing breaks when a deployment hands out
its schema, which is exactly why it needs a test rather than a note in a document.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import api_docs_paths, create_app

# 40 characters, no placeholder words, nothing that looks like a default.
STRONG_SECRET = "Kf3n2p9wQz8sVb4tRy6uJm1hLd7gXc5eQa0Z"

SCHEMA_PATHS = ("/docs", "/redoc", "/openapi.json")


def build(**overrides) -> Settings:
    """A production deployment that is properly configured, before the overrides.

    ``Settings`` reads a ``.env`` file, so a developer running the local stack would otherwise
    get a failure about a deployment this test never described. Naming every setting the startup
    guard reasons about keeps the test independent of that file.
    """
    values: dict[str, object] = {
        "environment": "production",
        "jwt_secret": STRONG_SECRET,
        "admin_password": "Str0ng-Passw0rd!",
        "gemini_api_key": "test-key",
        "cookie_secure": True,
        "redis_url": "redis://cache:6379/0",
        "alertmanager_webhook_token": "shared-token",
        "demo_account_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


async def fetch(config: Settings, path: str):
    """One request against an app built from `config`, without its lifespan.

    The lifespan would seed the database, and none of this needs a database: the question is
    only whether FastAPI mounted the routes at all.
    """
    transport = ASGITransport(app=create_app(config))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


@pytest.mark.parametrize("path", SCHEMA_PATHS)
async def test_the_schema_is_served_when_the_setting_is_on(path):
    response = await fetch(build(expose_api_docs=True), path)

    assert response.status_code == 200


async def test_the_openapi_document_describes_the_real_routes():
    """A 200 is not enough on its own: the document has to be the app's own schema."""
    response = await fetch(build(expose_api_docs=True), "/openapi.json")

    document = response.json()
    assert document["info"]["title"]
    assert "/auth/login" in document["paths"]


@pytest.mark.parametrize("path", SCHEMA_PATHS)
async def test_every_schema_path_is_gone_when_the_setting_is_off(path):
    """404 rather than a redirect or a page saying the feature is disabled.

    An "off" route that answers 200 with an explanation is still a route a scanner finds and a
    stranger reads; absence is the only answer that does not describe the deployment.
    """
    response = await fetch(build(expose_api_docs=False), path)

    assert response.status_code == 404


def test_production_defaults_to_off():
    config = build()

    assert config.should_expose_api_docs is False
    assert api_docs_paths(config) == {
        "openapi_url": None,
        "docs_url": None,
        "redoc_url": None,
    }


def test_a_local_run_defaults_to_on():
    """The quickstart keeps the docs UI: taking it away outside production would be a surprise."""
    config = build(environment="development", cookie_secure=False)

    assert config.should_expose_api_docs is True


def test_a_deployment_may_publish_the_schema_on_purpose():
    """The switch is a choice in both directions, not a lock."""
    config = build(expose_api_docs=True)

    assert config.should_expose_api_docs is True


def test_keeping_the_schema_off_locally_is_expressible():
    """Which is also how the off path is tested without pretending to be production."""
    config = build(environment="development", expose_api_docs=False, cookie_secure=False)

    assert config.should_expose_api_docs is False


def test_the_switch_does_not_follow_debug():
    """DEBUG returns tracebacks; publishing the schema is its own decision, not a side effect."""
    assert build(debug=False).should_expose_api_docs is False
    assert build(environment="development", debug=False).should_expose_api_docs is True
