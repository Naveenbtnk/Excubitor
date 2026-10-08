from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.incidents import IncidentStatus
from app.schemas.common import OrmModel


class TriageResult(BaseModel):
    summary: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    remediation_steps: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class IncidentRead(OrmModel):
    id: int
    alert_id: int | None
    title: str
    summary: str
    root_cause: str
    recommendation: str
    confidence: float
    status: str
    assignee: str | None
    created_at: datetime


class IncidentUpdate(BaseModel):
    """A partial update: absent means "leave it", and an explicit ``null`` assignee clears it.

    The distinction is the reason this model is validated against ``model_fields_set`` rather
    than against the values. ``assignee: str | None = None`` cannot tell "unassign" from "do
    not touch the assignee", and treating them the same would either silently unassign people
    whenever a status changed or make unassigning impossible.
    """

    status: IncidentStatus | None = None
    assignee: str | None = Field(default=None, max_length=180)

    @field_validator("assignee")
    @classmethod
    def _blank_means_unassigned(cls, value: str | None) -> str | None:
        # An empty string from a form field is a cleared assignee, not an assignee whose name
        # is "". Storing the empty string would put a nameless entry on the timeline.
        return None if value is None or not value.strip() else value.strip()

    @model_validator(mode="after")
    def _something_has_to_change(self) -> "IncidentUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide a status, an assignee, or both")
        return self


class IncidentCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("A comment cannot be empty")
        return text


class IncidentEventRead(OrmModel):
    id: int
    incident_id: int
    kind: str
    actor: str
    from_value: str | None
    to_value: str | None
    body: str
    created_at: datetime


class PostMortemRead(OrmModel):
    id: int
    incident_id: int
    content: str
    created_at: datetime
