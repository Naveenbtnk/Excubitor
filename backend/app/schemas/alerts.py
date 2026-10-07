from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import OrmModel


class AlertCreate(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    severity: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=180)
    detail: str = ""


class SimulateAlertRequest(BaseModel):
    scenario: str | None = None


class AlertRead(OrmModel):
    id: int
    source: str
    severity: str
    title: str
    detail: str
    status: str
    created_at: datetime
