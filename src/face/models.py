from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str


class QueryCreateInput(BaseModel):
    id_query: str | None = Field(default=None, min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=512)
    query_source: str = Field(min_length=1, max_length=128)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> QueryCreateInput:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be less than or equal to end_date")
        return self


class CreateQueriesRequest(BaseModel):
    queries: list[QueryCreateInput] = Field(min_length=1)


class QueryAcceptedResponse(BaseModel):
    id_query: str
    status_current: str
    created_at: datetime


class CreateQueriesResponse(BaseModel):
    queries: list[QueryAcceptedResponse]


class JobEventResponse(BaseModel):
    event_type: str
    payload: dict[str, Any] | None
    created_at: datetime


class QueryStatusResponse(BaseModel):
    id_query: str
    subject: str
    query_source: str
    start_date: date | None
    end_date: date | None
    status_current: str
    created_at: datetime
    updated_at: datetime
    recent_events: list[JobEventResponse]
