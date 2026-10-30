"""Failure prediction API tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

EQUIPMENT = {
    "equipment_id": "EQ-R01",
    "equipment_name": "Risk Test Motor",
    "equipment_type": "Industrial Motor",
    "location": "Test Bay",
}

NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}


@pytest.fixture
def equipment(client: TestClient):
    response = client.post("/api/equipment", json=EQUIPMENT)
    assert response.status_code == 201, response.text
    yield response.json()
    client.delete(f"/api/equipment/{EQUIPMENT['equipment_id']}?hard=true")


def feed(client: TestClient, count: int, **overrides) -> None:
    for i in range(count):
        payload = {
            "equipment_id": "EQ-R01", **NOMINAL, **overrides,
            "timestamp": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
        }
        assert client.post("/api/sensor/readings", json=payload).status_code == 201


# --- POST /predict ---

def test_predict_healthy(client: TestClient) -> None:
    response = client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", **NOMINAL}
    )

    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["failure_probability"] <= 1.0
    assert body["risk_level"] == "Low"
    assert body["failure_probability"] > 0.0


def test_predict_reports_horizon(client: TestClient) -> None:
    body = client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()

    assert body["horizon_hours"] == 720
    assert set(body["horizon_probabilities"]) == {"24", "168", "720"}


def test_predict_reports_calibration_state(client: TestClient) -> None:
    """The response must never imply these are measured failure rates."""
    body = client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()

    assert body["calibrated"] is False
    assert body["notes"]


def test_predict_degraded_machine(client: TestClient) -> None:
    body = client.post(
        "/api/predict",
        json={"equipment_type": "Industrial Motor", **NOMINAL, "vibration": 12.5},
    ).json()

    assert body["failure_probability"] > 0.1
    assert body["risk_level"] in {"Moderate", "High", "Severe"}
    assert body["dominant_driver"] == "vibration"


def test_predict_higher_risk_than_healthy(client: TestClient) -> None:
    healthy = client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()
    degraded = client.post(
        "/api/predict",
        json={"equipment_type": "Industrial Motor", **NOMINAL, "vibration": 12.0},
    ).json()

    assert degraded["failure_probability"] > healthy["failure_probability"]


def test_predict_reports_drivers(client: TestClient) -> None:
    body = client.post(
        "/api/predict",
        json={"equipment_type": "Industrial Motor", **NOMINAL, "vibration": 11.0, "temperature": 92.0},
    ).json()

    assert len(body["drivers"]) >= 2
    for driver in body["drivers"]:
        assert "parameter" in driver
        assert "share" in driver
    assert sum(d["share"] for d in body["drivers"]) == pytest.approx(1.0, abs=0.01)


def test_predict_anomaly_override_raises_risk(client: TestClient) -> None:
    """An override lets a caller inject detector evidence explicitly."""
    without = client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()
    with_anomaly = client.post(
        "/api/predict",
        json={"equipment_type": "Industrial Motor", **NOMINAL, "anomaly_score": 0.9},
    ).json()

    assert with_anomaly["failure_probability"] > without["failure_probability"]


def test_predict_same_value_differs_by_equipment_type(client: TestClient) -> None:
    values = {"temperature": 170.0, "vibration": 2.8}
    motor = client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", **values}
    ).json()
    boiler = client.post(
        "/api/predict", json={"equipment_type": "Boiler", **values}
    ).json()

    assert motor["failure_probability"] > boiler["failure_probability"]


def test_predict_includes_health_context(client: TestClient) -> None:
    body = client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()

    assert body["health_score"] > 0
    assert body["status"] in {"Excellent", "Healthy", "Warning", "Poor", "Critical"}


def test_predict_by_equipment_id(client: TestClient, equipment) -> None:
    body = client.post(
        "/api/predict", json={"equipment_id": "EQ-R01", **NOMINAL}
    ).json()

    assert body["equipment_id"] == "EQ-R01"
    assert body["equipment_type"] == "Industrial Motor"


def test_predict_does_not_create_health_records(
    client: TestClient, equipment, db: Session
) -> None:
    """A risk query is not a health assessment and must not create history."""
    from app.models.health_score import HealthScore
    from sqlalchemy import func, select

    client.post("/api/predict", json={"equipment_id": "EQ-R01", **NOMINAL})

    row = db.scalar(
        select(HealthScore)
    )
    count = db.scalar(select(func.count(HealthScore.id)))
    assert count == 0
    assert row is None


def test_predict_unknown_equipment(client: TestClient) -> None:
    response = client.post("/api/predict", json={"equipment_id": "EQ-NOPE", **NOMINAL})

    assert response.status_code == 404


def test_predict_requires_target(client: TestClient) -> None:
    assert client.post("/api/predict", json=NOMINAL).status_code == 422


def test_predict_rejects_out_of_range(client: TestClient) -> None:
    assert client.post(
        "/api/predict", json={"equipment_type": "Industrial Motor", "temperature": 9999}
    ).status_code == 422


def test_predict_rejects_invalid_horizon(client: TestClient) -> None:
    assert client.post(
        "/api/predict",
        json={"equipment_type": "Industrial Motor", "temperature": 60, "horizon_hours": 0},
    ).status_code == 422


def test_predict_rejects_invalid_anomaly_override(client: TestClient) -> None:
    assert client.post(
        "/api/predict",
        json={"equipment_type": "Industrial Motor", "temperature": 60, "anomaly_score": 5},
    ).status_code == 422


# --- Equipment risk from telemetry ---

def test_equipment_risk_from_telemetry(client: TestClient, equipment) -> None:
    feed(client, 40)

    response = client.get("/api/equipment/EQ-R01/risk")

    assert response.status_code == 200
    body = response.json()
    assert body["equipment_id"] == "EQ-R01"
    assert 0.0 <= body["failure_probability"] <= 1.0
    assert body["calibrated"] is False


def test_equipment_risk_reflects_degraded_telemetry(
    client: TestClient, equipment
) -> None:
    feed(client, 40, vibration=12.5)

    body = client.get("/api/equipment/EQ-R01/risk").json()

    assert body["failure_probability"] > 0.1
    assert body["risk_level"] in {"Moderate", "High", "Severe"}


def test_equipment_risk_without_telemetry_returns_404(
    client: TestClient, equipment
) -> None:
    response = client.get("/api/equipment/EQ-R01/risk")

    assert response.status_code == 404
    assert "no telemetry" in response.json()["detail"].lower()


def test_equipment_risk_unknown_equipment(client: TestClient) -> None:
    assert client.get("/api/equipment/EQ-NOPE/risk").status_code == 404


# --- Model info ---

def test_model_info_declares_calibration_state(client: TestClient) -> None:
    body = client.get("/api/predict/model-info").json()

    assert body["method"] == "degradation_hazard"
    assert body["calibrated"] is False
    assert "placeholder" in body["caveat"].lower()
    assert body["implied_mtbf_hours"] > 0


def test_model_info_documents_the_method(client: TestClient) -> None:
    body = client.get("/api/predict/model-info").json()

    assert "exp" in body["description"]
    assert body["horizon_labels"]["720"] == "30 days"


def test_model_info_matches_running_config(client: TestClient) -> None:
    from app.config import get_settings

    body = client.get("/api/predict/model-info").json()
    settings = get_settings()

    assert body["base_hazard_per_hour"] == settings.base_hazard_per_hour
    assert body["hazard_sensitivity"] == settings.risk_hazard_sensitivity
