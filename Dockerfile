FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations

RUN python -m pip install --upgrade pip && \
    python -m pip install -e . && \
    python -m playwright install --with-deps chromium

CMD ["uvicorn", "face.api:app", "--host", "0.0.0.0", "--port", "8000"]
