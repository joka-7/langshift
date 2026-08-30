"""One real smoke test exercising the entrypoint end to end, per the skill's rules."""

from __future__ import annotations

from fastapi.testclient import TestClient

from example_service.main import create_app


def test_health_endpoint_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
