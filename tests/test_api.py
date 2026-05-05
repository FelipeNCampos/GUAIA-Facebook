from __future__ import annotations

from face.api import app
from fastapi.testclient import TestClient


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
