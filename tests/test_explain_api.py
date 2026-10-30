"""Explainability API and service tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

EQUIPMENT = {
    "equipment_id": "EQ-E01",
    "equipment_name": "Explain Test Motor",
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
            "equipment_id": "EQ-E01", **NOMINAL, **overrides,
            "timestamp": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
        }
        assert client.post("/api/sensor/readings", json=payload).status_code == 201


REQUIRED_FIELDS = (
    "equipment_type", "health_score", "status", "risk_level",
    "failure_probability", "anomaly_score", "explanation", "recommendation",
    "score_breakdown", "contributing_factors", "attribution",
    "trending_parameters", "suppressed_by_policy", "risk", "trend",
)


# --- POST /explain ---

def test_explain_healthy_values(client: TestClient) -> None:
    response = client.post(
        "/api/explain", json={"equipment_type": "Industrial Motor", **NOMINAL}
    )

    assert response.status_code == 200
    body = response.json()
    for field in REQUIRED_FIELDS:
        assert field in body, f"missing {field}"
    assert body["status"] == "Excellent"
    assert "within" in body["explanation"].lower()


def test_explain_degraded_values(client: TestClient) -> None:
    body = client.post(
        "/api/explain",
        json={"equipment_type": "Industrial Motor", **NOMINAL, "vibration": 12.5},
    ).json()

    assert body["health_score"] < 100
    assert body["attribution"]
    assert body["attribution"][0]["parameter"] == "vibration"
    assert body["explanation"]


def test_explanation_contains_the_score(client: TestClient) -> None:
    body = client.post(
        "/api/explain",
        json={"equipment_type": "Industrial Motor", **NOMINAL, "vibration": 12.0},
    ).json()

    assert f"{body['health_score']:g}" in body["explanation"]


def test_explanation_numbers_reconcile(client: TestClient) -> None:
    """The prose must not contradict the published arithmetic."""
    body = client.post(
        "/api/explain",
        json={"equipment_type": "Industrial Motor", **NOMINAL, "vibration": 12.0, "temperature": 90.0},
    ).json()
    breakdown = body["score_breakdown"]
    total = breakdown["sensor_penalty"] + breakdown["anomaly_penalty"] + breakdown["risk_penalty"]

    assert body["health_score"] == pytest.approx(100.0 - total, abs=0.05)
    assert sum(f["penalty"] for f in body["contributing_factors"]) == pytest.approx(
        total, abs=0.05
    )


def test_explain_includes_risk_detail(client: TestClient) -> None:
    body = client.post(
        "/api/explain", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()

    assert body["risk"]["horizon_hours"] == 720
    assert body["risk"]["calibrated"] is False
    assert body["risk"]["method"] == "degradation_hazard"


def test_explain_reports_trend_unavailable_without_history(client: TestClient) -> None:
    body = client.post(
        "/api/explain", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()

    assert body["trend"]["available"] is False
    assert body["trend"]["reason"]


def test_explain_by_equipment_id(client: TestClient, equipment) -> None:
    body = client.post(
        "/api/explain", json={"equipment_id": "EQ-E01", **NOMINAL}
    ).json()

    assert body["equipment_id"] == "EQ-E01"
    assert body["equipment_type"] == "Industrial Motor"


def test_explain_unknown_equipment(client: TestClient) -> None:
    assert client.post(
        "/api/explain", json={"equipment_id": "EQ-NOPE", **NOMINAL}
    ).status_code == 404


def test_explain_requires_target(client: TestClient) -> None:
    assert client.post("/api/explain", json=NOMINAL).status_code == 422


def test_explain_requires_a_channel(client: TestClient) -> None:
    assert client.post(
        "/api/explain", json={"equipment_type": "Industrial Motor"}
    ).status_code == 422


def test_explain_rejects_out_of_range(client: TestClient) -> None:
    assert client.post(
        "/api/explain", json={"equipment_type": "Industrial Motor", "temperature": 9999}
    ).status_code == 422


# --- GET /equipment/{id}/explain ---

def test_explain_equipment_from_telemetry(client: TestClient, equipment) -> None:
    feed(client, 40)

    response = client.get("/api/equipment/EQ-E01/explain")

    assert response.status_code == 200
    body = response.json()
    for field in REQUIRED_FIELDS:
        assert field in body, f"missing {field}"
    assert body["equipment_id"] == "EQ-E01"


def test_explain_equipment_reports_observed_time(client: TestClient, equipment) -> None:
    feed(client, 40)

    body = client.get("/api/equipment/EQ-E01/explain").json()

    assert body["observed_at"] is not None
    assert body["generated_at"] is not None


def test_explain_equipment_reports_trend_after_scoring(
    client: TestClient, equipment
) -> None:
    """The trend section must use stored assessment history."""
    feed(client, 40)
    for temperature in (68.0, 85.0, 105.0):
        client.post(
            "/api/health/score",
            json={"equipment_id": "EQ-E01", **NOMINAL, "temperature": temperature},
        )

    body = client.get("/api/equipment/EQ-E01/explain").json()

    assert body["trend"]["available"] is True
    assert body["trend"]["samples"] == 3
    assert body["trend"]["direction"] == "declining"


def test_explain_equipment_trend_stable(client: TestClient, equipment) -> None:
    feed(client, 40)
    for _ in range(3):
        client.post("/api/health/score", json={"equipment_id": "EQ-E01", **NOMINAL})

    body = client.get("/api/equipment/EQ-E01/explain").json()

    assert body["trend"]["direction"] == "stable"


def test_explain_equipment_without_telemetry_returns_404(
    client: TestClient, equipment
) -> None:
    response = client.get("/api/equipment/EQ-E01/explain")

    assert response.status_code == 404


def test_explain_equipment_unknown_returns_404(client: TestClient) -> None:
    assert client.get("/api/equipment/EQ-NOPE/explain").status_code == 404


# -- Policy masking surfaced end to end --

def test_masked_channel_surfaced_by_api(client: TestClient) -> None:
    """The masking warning must reach the consumer, not just the library."""
    body = client.post(
        "/api/explain",
        json={"equipment_type": "Industrial Motor", **NOMINAL,
              "vibration": 13.0, "temperature": 95.0},
    ).json()

    assert body["suppressed_by_policy"]
    assert any(item["parameter"] == "temperature" for item in body["suppressed_by_policy"])


# -- Drift --

def test_drift_section_present_even_when_empty(client: TestClient) -> None:
    """Always present so the UI never has to handle a missing key."""
    body = client.post(
        "/api/explain", json={"equipment_type": "Industrial Motor", **NOMINAL}
    ).json()

    assert isinstance(body["trending_parameters"], list)


def test_equipment_explanation_includes_drift_section(client: TestClient, equipment) -> None:
    feed(client, 60, vibration=6.0)

    body = client.get("/api/equipment/EQ-E01/explain").json()

    assert isinstance(body["trending_parameters"], list)


# -- No side effects --

def test_explain_does_not_persist_anything(
    client: TestClient, equipment, db: Session
) -> None:
    """Explaining is a read operation and must not write history."""
    from sqlalchemy import func, select

    from app.models.health_score import HealthScore

    feed(client, 40)
    client.get("/api/equipment/EQ-E01/explain")
    client.post("/api/explain", json={"equipment_id": "EQ-E01", **NOMINAL})

    count = db.scalar(select(func.count(HealthScore.id)))
    assert count == 0
