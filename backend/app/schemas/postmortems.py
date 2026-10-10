from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.postmortems import ActionItemStatus, PostMortemStatus
from app.schemas.common import OrmModel


class ActionItemWrite(BaseModel):
    """One follow-up as a client sends it.

    ``id`` is how a client says "this is the item you already have" during a full replace of the
    list; omitting it creates a new one. ``owner`` is an email rather than a user id, because the
    person who will do the work usually does have an account and the one who types the form does
    not necessarily know their id -- the API resolves it to a user when one matches.
    """

    id: int | None = None
    text: str = Field(min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=180)
    due_date: date | None = None
    status: ActionItemStatus = ActionItemStatus.OPEN

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("An action item cannot be empty")
        return text

    @field_validator("owner")
    @classmethod
    def _blank_means_unowned(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else value.strip()


class ActionItemRead(OrmModel):
    id: int
    post_mortem_id: int
    text: str
    owner: str
    owner_id: int | None
    due_date: date | None
    status: str


class PostMortemRead(OrmModel):
    id: int
    incident_id: int
    incident_title: str
    content: str
    timeline_summary: str
    root_cause: str
    contributing_factors: list[str]
    impact: str
    status: str
    author: str
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    action_items: list[ActionItemRead]


class PostMortemUpdate(BaseModel):
    """A partial edit of a document.

    Every field is optional and an absent one means "leave it", the same distinction
    :class:`app.schemas.incidents.IncidentUpdate` makes. ``action_items`` is the exception: when
    it is present it replaces the list, because a form that edits a set of rows has no way to
    name the rows it removed.
    """

    content: str | None = None
    timeline_summary: str | None = None
    root_cause: str | None = None
    contributing_factors: list[str] | None = None
    impact: str | None = None
    status: PostMortemStatus | None = None
    action_items: list[ActionItemWrite] | None = None

    @field_validator("contributing_factors")
    @classmethod
    def _factors_are_trimmed(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        factors = [factor.strip() for factor in value if factor.strip()]
        return factors

    @model_validator(mode="after")
    def _something_has_to_change(self) -> "PostMortemUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        return self


class ActionItemUpdate(BaseModel):
    """A single item's edit, so a checkbox on the Overview can be a one-field request."""

    text: str | None = Field(default=None, min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=180)
    due_date: date | None = None
    status: ActionItemStatus | None = None

    @field_validator("owner")
    @classmethod
    def _blank_means_unowned(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else value.strip()

    @model_validator(mode="after")
    def _something_has_to_change(self) -> "ActionItemUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        return self


class PostMortemDraft(BaseModel):
    """What the model is asked to produce, in one call.

    Structured output rather than a markdown blob plus a second parser: the sections the review
    pipeline and the export depend on are fields here, so a provider that returns prose in the
    wrong place fails validation instead of quietly becoming a document nobody can query.

    Action items from the model are text only. Owners and due dates are commitments between
    people, and inventing them would put a fabricated deadline on somebody's list.
    """

    narrative: str = Field(min_length=1)
    timeline_summary: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    contributing_factors: list[str] = Field(default_factory=list)
    impact: str = Field(min_length=1)
    action_items: list[str] = Field(default_factory=list)
