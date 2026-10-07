from datetime import datetime

from pydantic import BaseModel, Field

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
    created_at: datetime


class PostMortemRead(OrmModel):
    id: int
    incident_id: int
    content: str
    created_at: datetime
