from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

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


class QueryRecordResponse(BaseModel):
    id: int
    id_query: str
    url: str
    url_normalized: str | None
    category: str | None
    status: str
    payload: dict[str, Any] | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class QueryRecordsResponse(BaseModel):
    id_query: str
    records: list[QueryRecordResponse]


class QueryExportResponse(BaseModel):
    id: int
    id_query: str
    export_format: str
    storage_path: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None


class QueryExportsResponse(BaseModel):
    id_query: str
    exports: list[QueryExportResponse]


class CreateExportRequest(BaseModel):
    export_format: Literal["json", "xlsx"]


class ExportAcceptedResponse(BaseModel):
    export_id: int
    id_query: str
    export_format: str
    status: str
    created_at: datetime


class RetryQueryStageRequest(BaseModel):
    stage: Literal["search", "enrich", "export"]
    record_ids: list[int] | None = Field(default=None, min_length=1)
    export_id: int | None = Field(default=None, gt=0)
    export_format: Literal["json", "xlsx"] | None = None

    @model_validator(mode="after")
    def validate_stage_specific_fields(self) -> RetryQueryStageRequest:
        if self.stage != "enrich" and self.record_ids is not None:
            raise ValueError("record_ids is only supported for the enrich stage")
        if self.stage != "export" and self.export_id is not None:
            raise ValueError("export_id is only supported for the export stage")
        if self.stage != "export" and self.export_format is not None:
            raise ValueError("export_format is only supported for the export stage")
        return self


class RetryAcceptedResponse(BaseModel):
    id_query: str
    stage: Literal["search", "enrich", "export"]
    status_current: str
    retried_records: int | None = None
    export_id: int | None = None
    export_format: str | None = None
