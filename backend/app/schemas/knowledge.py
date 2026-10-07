from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import OrmModel


class RunbookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=10)
    source: str = "manual"


class RunbookRead(OrmModel):
    id: int
    title: str
    source: str
    content: str
    created_at: datetime


class KnowledgeQuery(BaseModel):
    question: str = Field(min_length=2)


class KnowledgeHit(BaseModel):
    title: str
    source: str
    content: str


class KnowledgeAnswer(BaseModel):
    answer: str
    hits: list[KnowledgeHit]
