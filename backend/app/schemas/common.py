from __future__ import annotations

import re
from typing import Annotated, Generic, TypeVar

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, computed_field

from app.core.config import settings

_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+$")

ItemT = TypeVar("ItemT")


def _validate_email(value: str) -> str:
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        # Internal deployments use reserved domains (admin@cloudops.local, *.internal)
        # that email-validator rejects outright, so fall back to a shape check.
        if _SHAPE.match(value):
            return value.lower()
        raise ValueError(str(exc)) from exc


Email = Annotated[str, AfterValidator(_validate_email)]


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Pagination(BaseModel):
    limit: int = Field(
        default=settings.pagination_default_limit,
        ge=1,
        le=settings.pagination_max_limit,
    )
    offset: int = Field(default=0, ge=0)


class Page(BaseModel, Generic[ItemT]):
    items: list[ItemT]
    total: int
    limit: int
    offset: int

    @computed_field
    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total

    @classmethod
    def of(cls, items: list[ItemT], *, total: int, pagination: Pagination) -> Page[ItemT]:
        return cls(
            items=items,
            total=total,
            limit=pagination.limit,
            offset=pagination.offset,
        )


class HealthResponse(BaseModel):
    status: str
    provider: str
    database: str
    counters: str


class LLMQuotaStatus(BaseModel):
    """Provider budget as a health reader sees it. ``limit`` of 0 never appears here: a
    disabled budget is reported as an absent block rather than as an empty one."""

    limit: int
    used: int
    remaining: int
    window_seconds: int
    resets_in: int
    # "memory" means these numbers are this worker's alone, so the real ceiling is
    # ``workers x limit``; "redis" means every worker is drawing on the same allowance.
    store: str


class LLMHealthResponse(BaseModel):
    status: str
    provider: str
    model: str
    llm: str
    quota: LLMQuotaStatus | None = None
