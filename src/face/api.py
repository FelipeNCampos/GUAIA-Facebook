from __future__ import annotations

from contextlib import asynccontextmanager

from common.logging import clear_log_context, configure_logging, get_logger
from fastapi import FastAPI, HTTPException, status

from face.config import get_settings
from face.models import (
    CreateQueriesRequest,
    CreateQueriesResponse,
    HealthResponse,
    QueryStatusResponse,
)
from face.queues import QueueNames, RabbitMQPublisher
from face.repository import FaceJobRepository, QueryNotFoundError, create_session_factory
from face.services import QueryConflictError, QueryService

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.app_log_level, json_logs=settings.app_log_json)
    logger.info("face-api starting", extra={"service": "face-api"})
    yield
    logger.info("face-api stopping", extra={"service": "face-api"})


app = FastAPI(title="InfoPolitica Facebook Module", version="0.1.0", lifespan=lifespan)


def get_query_service() -> QueryService:
    if hasattr(app.state, "query_service"):
        return app.state.query_service

    query_service = QueryService(
        repository=FaceJobRepository(create_session_factory()),
        publisher=RabbitMQPublisher(),
        queue_names=QueueNames(),
    )
    app.state.query_service = query_service
    return query_service


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", service="face-api", environment=settings.app_env)


@app.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ready", service="face-api", environment=settings.app_env)


@app.post(
    "/facebook/queries",
    response_model=CreateQueriesResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_queries(
    payload: CreateQueriesRequest,
) -> CreateQueriesResponse:
    service = get_query_service()
    try:
        accepted = await service.create_queries(payload)
        return CreateQueriesResponse(queries=accepted)
    except QueryConflictError as exc:
        clear_log_context()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        clear_log_context()
        logger.exception("Failed to create and publish queries", extra={"service": "face-api"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create query batch",
        ) from exc
    finally:
        clear_log_context()


@app.get("/facebook/queries/{id_query}", response_model=QueryStatusResponse)
async def get_query_status(id_query: str) -> QueryStatusResponse:
    service = get_query_service()
    try:
        return service.get_query_status(id_query)
    except QueryNotFoundError as exc:
        clear_log_context()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    finally:
        clear_log_context()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("face.api:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    main()
