from __future__ import annotations

from contextlib import asynccontextmanager

from common.logging import configure_logging, get_logger
from fastapi import FastAPI

from face.config import get_settings
from face.models import HealthResponse

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.app_log_level, json_logs=settings.app_log_json)
    logger.info("face-api starting", extra={"service": "face-api"})
    yield
    logger.info("face-api stopping", extra={"service": "face-api"})


app = FastAPI(title="InfoPolitica Facebook Module", version="0.1.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", service="face-api", environment=settings.app_env)


@app.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ready", service="face-api", environment=settings.app_env)


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("face.api:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    main()
