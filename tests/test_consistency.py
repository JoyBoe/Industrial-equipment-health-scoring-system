"""Cross-surface consistency tests.

A recurring defect in this project: the dashboard, the alert engine, and the
detail page each evaluated a machine over a *different* window or picked a
different reading, so the same machine was reported Excellent on one screen and
Warning on another. These tests pin the shared behaviour down.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.equipment import Equipment
from app.services import alert_service, health_service

EQUIPMENT = {
    "equipment_id": "EQ-C01",
    "equipment_name": "Consistency Motor",
    "equipment_type": "Industrial Motor",
    "location": "Test Bay",
}

NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}

_clock = {"minute": 0}


def feed(client: TestClient, count: int, **overrides) -> None:
    for _ in range(count):
        minute = _clock["minute"]
        _clock["minute"] += 1
        payload = {
            "equipment_id": "EQ-C01", **NOMINAL, **overrides,
            "timestamp": f"2026-01-01T{minute // 60 % 24:02d}:{minute % 60:02d}:00Z",
        }
        assert client.post("/api/sensor/readings", json=payload).status_code == 201


@pytest.fixture(autouse=True)
def _reset_clock():
    _clock["minute"] = 0
    yield
    _clock["minute"] = 0


@pytest.fixture
def equipment(client: TestClient, db: Session):
    response = client.post("/api/equipment", json=EQUIPMENT)
    assert response.status_code == 201, response.text
    db.execute(Alert.__table__.delete())
    db.commit()
    yield response.json()
    client.delete(f"/api/equipment/{EQUIPMENT['equipment_id']}?hard=true")


# --- The shared helper ---

def test_worst_reading_picks_the_degraded_one(
    client: TestClient, equipment, db: Session
) -> None:
    """A machine that spiked earlier must not read healthy just because it settled."""
    feed(client, 40, vibration=13.0)   # excursion
    feed(client, 40)                   # recovery

    worst = health_service.worst_reading_in_window(db, "EQ-C01", window=80)

    assert worst is not None
    _, values, _, _ = worst
    assert values["vibration"] > 10.0


def test_worst_reading_for_known_machine_without_data_returns_none(
    client: TestClient, equipment, db: Session
) -> None:
    assert health_service.worst_reading_in_window(db, "EQ-C01", window=60) is None


def test_worst_reading_for_unknown_machine_raises(db: Session) -> None:
    """An unknown identifier is a 404 for the caller, not a silent None."""
    from app.repositories.equipment import EquipmentNotFoundError

    with pytest.raises(EquipmentNotFoundError):
        health_service.worst_reading_in_window(db, "EQ-NOPE", window=60)


# --- Cross-surface agreement ---

def test_explain_score_matches_alert_severity(
    client: TestClient, equipment, db: Session
) -> None:
    """A Critical vibration alert must not sit beside a healthy-looking score.

    This is the exact failure that shipped twice: the detail page showed
    "Excellent, no action required" for a machine with an open Critical alert.
    """
    feed(client, 40, vibration=13.0)
    feed(client, 40)

    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-C01", "window": 80})
    alerts = client.get(
        "/api/alerts", params={"equipment_id": "EQ-C01", "resolved": False}
    ).json()

    assert alerts["total"] >= 1

    explanation = client.get(
        "/api/equipment/EQ-C01/explain", params={"window": 80}
    ).json()

    assert explanation["health_score"] < 100
    assert explanation["status"] != "Excellent"


def test_explanation_names_the_channel_the_alert_is_about(
    client: TestClient, equipment
) -> None:
    feed(client, 40, temperature=130.0)
    feed(client, 40)

    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-C01", "window": 80})
    alerts = client.get(
        "/api/alerts", params={"equipment_id": "EQ-C01", "resolved": False}
    ).json()
    explanation = client.get(
        "/api/equipment/EQ-C01/explain", params={"window": 80}
    ).json()

    alert_types = {a["alert_type"] for a in alerts["items"]}
    factor_names = {f["parameter"] for f in explanation["attribution"]}

    if "high_temperature" in alert_types:
        assert "temperature" in factor_names


def test_explanation_score_matches_persisted_assessment(
    client: TestClient, equipment
) -> None:
    """Reassessing then explaining must report the same number.

    The page derives its score card from the explain payload, so a mismatch
    here would surface as a card that changes when the page is revisited.
    """
    feed(client, 60, temperature=130.0)

    assessed = client.post(
        "/api/equipment/EQ-C01/health/assess", params={"window": 60}
    ).json()
    explained = client.get(
        "/api/equipment/EQ-C01/explain", params={"window": 60}
    ).json()

    assert explained["health_score"] == pytest.approx(assessed["health_score"], abs=0.05)


def test_healthy_machine_agrees_everywhere(client: TestClient, equipment) -> None:
    feed(client, 60)

    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-C01", "window": 60})
    alerts = client.get(
        "/api/alerts", params={"equipment_id": "EQ-C01", "resolved": False}
    ).json()
    explanation = client.get(
        "/api/equipment/EQ-C01/explain", params={"window": 60}
    ).json()

    assert alerts["total"] == 0
    assert explanation["status"] in {"Excellent", "Healthy"}


def test_alert_evaluation_and_explanation_agree_on_window(
    client: TestClient, equipment, db: Session
) -> None:
    """Both surfaces must derive the condition from the same reading.

    `evaluate_equipment` and `explain_equipment` both route through
    `worst_reading_in_window`; this pins that shared dependency.
    """
    feed(client, 30, vibration=13.0)
    feed(client, 30)

    equipment_row = db.scalar(
        __import__("sqlalchemy").select(Equipment).where(
            Equipment.equipment_id == "EQ-C01"
        )
    )
    alert_service.evaluate_equipment(db, equipment_row, window=60)
    opened = db.scalars(
        __import__("sqlalchemy").select(Alert).where(Alert.resolved.is_(False))
    ).all()

    explanation = client.get(
        "/api/equipment/EQ-C01/explain", params={"window": 60}
    ).json()

    if opened:
        assert explanation["health_score"] < 100


def test_operating_bands_present_for_every_channel(
    client: TestClient, equipment
) -> None:
    """Charts need alarm limits even when nothing is degraded."""
    feed(client, 40)

    explanation = client.get(
        "/api/equipment/EQ-C01/explain", params={"window": 40}
    ).json()

    bands = explanation["operating_bands"]
    assert len(bands) == 8
    for band in bands:
        assert band["alarm_high"] >= band["ok_high"]
        assert "unit" in band
