"""Anomaly detection API tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import app
from app.models.anomaly import Anomaly
from app.models.equipment import Equipment

EQUIPMENT = {
    "equipment_id": "EQ-A01",
    "equipment_name": "Anomaly Test Pump",
    "equipment_type": "Hydraulic Pump",
    "location": "Test Bay",
}

NORMAL_READING = {
    "temperature": 62.0,
    "vibration": 3.2,
    "pressure": 100.0,
    "rpm": 1450.0,
    "current": 23.0,
    "voltage": 400.0,
    "power": 13.5,
    "load": 70.0,
}


@pytest.fixture
def equipment(client: TestClient):
    response = client.post("/api/equipment", json=EQUIPMENT)
    assert response.status_code == 201, response.text
    yield response.json()
    client.delete(f"/api/equipment/{EQUIPMENT['equipment_id']}?hard=true")


def feed(client: TestClient, count: int, **overrides) -> None:
    """Write a run of readings so history exists."""
    for i in range(count):
        payload = {
            "equipment_id": "EQ-A01",
            **NORMAL_READING,
            **overrides,
            "timestamp": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
        }
        response = client.post("/api/sensor/readings", json=payload)
        assert response.status_code == 201, response.text


# --- Ad-hoc detection ---

def test_detect_normal_reading(client: TestClient, equipment) -> None:
    response = client.post(
        "/api/anomaly/detect", json={"equipment_id": "EQ-A01", **NORMAL_READING}
    )

    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["anomaly_score"] <= 1.0
    assert body["anomaly_detected"] is False
    assert body["affected_parameters"] == []


def test_detect_overheating(client: TestClient, equipment) -> None:
    response = client.post(
        "/api/anomaly/detect",
        json={"equipment_id": "EQ-A01", **{**NORMAL_READING, "temperature": 130.0}},
    )

    body = response.json()
    assert body["anomaly_detected"] is True
    assert body["anomaly_score"] > 0.5
    assert "temperature" in body["affected_parameters"]


def test_detect_excessive_vibration(client: TestClient, equipment) -> None:
    response = client.post(
        "/api/anomaly/detect",
        json={"equipment_id": "EQ-A01", **{**NORMAL_READING, "vibration": 14.0}},
    )

    body = response.json()
    assert body["anomaly_detected"] is True
    assert "vibration" in body["affected_parameters"]


def test_detect_cavitation_pressure_collapse(client: TestClient, equipment) -> None:
    response = client.post(
        "/api/anomaly/detect",
        json={"equipment_id": "EQ-A01", **{**NORMAL_READING, "pressure": 55.0}},
    )

    body = response.json()
    assert body["anomaly_detected"] is True
    assert "pressure" in body["affected_parameters"]


def test_detect_stalled_machine(client: TestClient, equipment) -> None:
    """Low speed on a running machine is a stall, not a quiet operating point."""
    response = client.post(
        "/api/anomaly/detect",
        json={"equipment_id": "EQ-A01", **{**NORMAL_READING, "rpm": 900.0}},
    )

    body = response.json()
    assert body["anomaly_detected"] is True
    assert "rpm" in body["affected_parameters"]


def test_detect_by_equipment_type_without_id(client: TestClient) -> None:
    response = client.post(
        "/api/anomaly/detect",
        json={"equipment_type": "Hydraulic Pump", "temperature": 62.0, "vibration": 3.0},
    )

    assert response.status_code == 200
    assert response.json()["equipment_type"] == "Hydraulic Pump"


def test_detect_same_value_differs_by_equipment_type(client: TestClient) -> None:
    """Per-type profiles must actually change the verdict."""
    values = {"temperature": 170.0, "vibration": 3.0}
    motor = client.post(
        "/api/anomaly/detect", json={"equipment_type": "Industrial Motor", **values}
    ).json()
    boiler = client.post(
        "/api/anomaly/detect", json={"equipment_type": "Boiler", **values}
    ).json()

    assert motor["anomaly_detected"] is True
    assert boiler["anomaly_detected"] is False


def test_detect_unknown_equipment_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/anomaly/detect", json={"equipment_id": "EQ-NOPE", "temperature": 60.0}
    )

    assert response.status_code == 404


def test_detect_requires_a_target(client: TestClient) -> None:
    response = client.post("/api/anomaly/detect", json={"temperature": 60.0})

    assert response.status_code == 422


def test_detect_requires_at_least_one_channel(client: TestClient, equipment) -> None:
    response = client.post("/api/anomaly/detect", json={"equipment_id": "EQ-A01"})

    assert response.status_code == 422


def test_detect_rejects_out_of_range_value(client: TestClient, equipment) -> None:
    response = client.post(
        "/api/anomaly/detect", json={"equipment_id": "EQ-A01", "temperature": 9999}
    )

    assert response.status_code == 422


def test_type_only_request_is_not_persisted(db: Session) -> None:
    """Exploratory checks against a bare type must not pollute history."""
    client = TestClient(app)
    before = db.scalar(select(Anomaly))

    client.post(
        "/api/anomaly/detect",
        json={"equipment_type": "Hydraulic Pump", "temperature": 130.0},
    )

    assert db.scalar(select(Anomaly)) == before


def test_request_naming_equipment_is_persisted(
    client: TestClient, equipment, db: Session
) -> None:
    """A result describing real equipment belongs in the assessment history."""
    client.post(
        "/api/anomaly/detect", json={"equipment_id": "EQ-A01", "temperature": 130.0}
    )

    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-A01"))
    count = db.scalar(
        select(func.count(Anomaly.id)).where(Anomaly.equipment_db_id == row.id)
    )
    assert count == 1


def test_detect_reports_method_and_version(client: TestClient, equipment) -> None:
    body = client.post(
        "/api/anomaly/detect", json={"equipment_id": "EQ-A01", **NORMAL_READING}
    ).json()

    assert body["method"]
    assert body["model_version"]


def test_detect_includes_per_parameter_detail(client: TestClient, equipment) -> None:
    body = client.post(
        "/api/anomaly/detect", json={"equipment_id": "EQ-A01", **NORMAL_READING}
    ).json()

    detail = body["parameter_scores"]
    assert "temperature" in detail
    assert detail["temperature"]["direction"] == "normal"
    assert 0.0 <= detail["temperature"]["degradation"] <= 1.0


# --- History detection ---

def test_detect_history_persists_results(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40)

    response = client.post("/api/anomaly/detect/EQ-A01")

    assert response.status_code == 200
    body = response.json()
    assert body["assessed"] == 40
    assert body["detected"] >= 0

    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-A01"))
    stored = db.scalars(
        select(Anomaly).where(Anomaly.equipment_db_id == row.id)
    ).all()
    assert len(stored) > 0


def test_detect_history_unknown_equipment(client: TestClient) -> None:
    assert client.post("/api/anomaly/detect/EQ-NOPE").status_code == 404


def test_detect_history_without_readings(client: TestClient, equipment) -> None:
    response = client.post("/api/anomaly/detect/EQ-A01")

    assert response.status_code == 200
    assert response.json()["assessed"] == 0


def test_detect_history_flags_overheated_machine(
    client: TestClient, equipment, db: Session
) -> None:
    """A whole series above the alarm band must be flagged."""
    feed(client, 60, temperature=135.0)

    client.post("/api/anomaly/detect/EQ-A01")
    row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-A01"))
    detected = db.scalars(
        select(Anomaly).where(
            Anomaly.equipment_db_id == row.id, Anomaly.anomaly_detected.is_(True)
        )
    ).all()

    assert len(detected) > 30


# --- History and summary ---

def test_anomaly_history_is_newest_first(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40)
    client.post("/api/anomaly/detect/EQ-A01")

    rows = client.get("/api/anomaly/EQ-A01").json()

    assert len(rows) > 0
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_anomaly_history_filter_detected_only(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40, temperature=135.0)
    client.post("/api/anomaly/detect/EQ-A01")

    rows = client.get("/api/anomaly/EQ-A01", params={"detected_only": True}).json()

    assert rows
    assert all(r["anomaly_detected"] for r in rows)


def test_anomaly_history_unknown_equipment(client: TestClient) -> None:
    assert client.get("/api/anomaly/EQ-NOPE").status_code == 404


def test_anomaly_summary(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40)
    client.post("/api/anomaly/detect/EQ-A01")

    body = client.get("/api/anomaly/EQ-A01/summary").json()

    assert body["equipment_id"] == "EQ-A01"
    assert body["assessed"] == 40
    assert 0.0 <= body["detection_rate"] <= 1.0
    assert 0.0 <= body["max_anomaly_score"] <= 1.0


def test_anomaly_summary_identifies_common_parameter(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 60, vibration=14.0)
    client.post("/api/anomaly/detect/EQ-A01")

    body = client.get("/api/anomaly/EQ-A01/summary").json()

    assert "vibration" in body["most_common_parameters"]


def test_anomaly_summary_unknown_equipment(client: TestClient) -> None:
    assert client.get("/api/anomaly/EQ-NOPE/summary").status_code == 404
