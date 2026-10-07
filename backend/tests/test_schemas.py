import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.config import settings
from app.schemas.auth import LoginRequest, UserRead
from app.schemas.common import Email, Page, Pagination

email_adapter = TypeAdapter(Email)


def test_email_accepts_reserved_local_domain():
    # The default seeded admin uses a reserved TLD, which email-validator rejects outright.
    assert email_adapter.validate_python("admin@cloudops.local") == "admin@cloudops.local"


def test_email_accepts_internal_domain():
    assert email_adapter.validate_python("ops@cloudops.internal") == "ops@cloudops.internal"


def test_email_normalises_domain_case():
    assert email_adapter.validate_python("Ops.Team@Example.com") == "Ops.Team@example.com"


@pytest.mark.parametrize("value", ["not-an-email", "no-domain@", "@no-local", "spaces in@mail.com"])
def test_email_rejects_malformed_values(value: str):
    with pytest.raises(ValidationError):
        email_adapter.validate_python(value)


def test_login_request_accepts_the_seeded_admin():
    request = LoginRequest(email="admin@cloudops.local", password="secret")
    assert request.email == "admin@cloudops.local"


def test_user_read_accepts_the_seeded_admin_email():
    user = UserRead(id=1, email="admin@cloudops.local", name="Cloud Admin", role="admin")
    assert user.email == "admin@cloudops.local"


def test_page_reports_more_results_available():
    page = Page[int].of([1, 2], total=5, pagination=Pagination(limit=2, offset=0))
    assert page.model_dump() == {
        "items": [1, 2],
        "total": 5,
        "limit": 2,
        "offset": 0,
        "has_more": True,
    }


def test_page_reports_the_final_page():
    page = Page[int].of([5], total=5, pagination=Pagination(limit=2, offset=4))
    assert page.has_more is False


def test_page_on_empty_result_set():
    page = Page[int].of([], total=0, pagination=Pagination(limit=2, offset=0))
    assert page.has_more is False
    assert page.items == []


def test_pagination_defaults_to_the_configured_limit():
    assert Pagination().limit == settings.pagination_default_limit
    assert Pagination().offset == 0


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"offset": -1}, {"limit": 10_000}])
def test_pagination_rejects_out_of_range_values(kwargs: dict):
    with pytest.raises(ValidationError):
        Pagination(**kwargs)
