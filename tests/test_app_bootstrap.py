"""Stage 2 tests: application wiring, configuration, and the health endpoint."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES, Settings, get_settings
from app.database import get_db


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["sensor_features"] == list(SENSOR_FEATURES)


def test_root_advertises_docs(client: TestClient) -> None:
    body = client.get("/").json()

    assert body["docs"] == "/docs"
    assert body["api"] == "/api"


def test_openapi_schema_is_generated(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/health" in paths


def test_health_reports_503_when_database_unreachable(client: TestClient) -> None:
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database unreachable")

    client.app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        response = client.get("/api/health")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


def test_unknown_route_returns_404(client: TestClient) -> None:
    assert client.get("/api/does-not-exist").status_code == 404


def test_settings_parse_cors_origins() -> None:
    # Init kwargs use field names; the APP_ prefix applies to environment
    # variables only.
    settings = Settings(cors_origins=" http://a.test , http://b.test ")

    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_settings_are_cached() -> None:
    assert get_settings() is get_settings()


def test_scoring_weights_are_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(vibration_weight=5.0)

    with pytest.raises(ValidationError):
        Settings(vibration_weight=-1.0)


def test_scoring_weight_accepts_valid_range() -> None:
    assert Settings(anomaly_weight=0.0).anomaly_weight == 0.0
    assert Settings(anomaly_weight=1.0).anomaly_weight == 1.0


def test_unknown_env_keys_are_ignored() -> None:
    assert Settings(unrelated_key="x").app_name


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VIBRATION_WEIGHT", "0.45")

    assert Settings().vibration_weight == pytest.approx(0.45)


def test_database_session_rolls_back_cleanly(db: Session) -> None:
    # A session handed out by get_db must be usable and transactional.
    result = db.execute(text("SELECT 1")).scalar_one()

    assert result == 1
    db.rollback()
