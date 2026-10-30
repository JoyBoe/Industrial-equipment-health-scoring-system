"""Alert persistence, deduplication, and API tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.equipment import Equipment
from app.services import alert_service

EQUIPMENT = {
    "equipment_id": "EQ-AL1",
    "equipment_name": "Alert API Motor",
    "equipment_type": "Industrial Motor",
    "location": "Test Bay",
}

NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}

OVERHEATING = {**NOMINAL, "temperature": 130.0, "vibration": 13.0}


@pytest.fixture
def equipment(client: TestClient):
    response = client.post("/api/equipment", json=EQUIPMENT)
    assert response.status_code == 201, response.text
    yield response.json()
    client.delete(f"/api/equipment/{EQUIPMENT['equipment_id']}?hard=true")


#: Monotonic offset so repeated `feed` calls produce strictly increasing
#: timestamps. Restarting the clock each call created duplicate timestamps, and
#: the "clean" readings then failed to form a later window at all.
_clock = {"minute": 0}


def feed(client: TestClient, count: int, **overrides) -> None:
    for _ in range(count):
        minute = _clock["minute"]
        _clock["minute"] += 1
        payload = {
            "equipment_id": "EQ-AL1", **NOMINAL, **overrides,
            "timestamp": f"2026-01-01T{minute // 60 % 24:02d}:{minute % 60:02d}:00Z",
        }
        assert client.post("/api/sensor/readings", json=payload).status_code == 201


@pytest.fixture(autouse=True)
def _reset_clock():
    """Reset the synthetic clock so each test starts from a known time."""
    _clock["minute"] = 0
    yield
    _clock["minute"] = 0


def clear_alerts(db: Session) -> None:
    db.execute(Alert.__table__.delete())
    db.commit()


def evaluate(client: TestClient, db: Session, equipment_id: str = "EQ-AL1") -> dict:
    clear_alerts(db)
    response = client.post("/api/alerts/evaluate", json={"equipment_id": equipment_id})
    assert response.status_code == 200, response.text
    return response.json()


# --- Alert generation ---

def test_evaluate_raises_alerts_for_degraded_machine(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40, temperature=130.0, vibration=13.0)

    body = evaluate(client, db)

    assert body["opened"] >= 2
    types = {alert["alert_type"] for alert in body["opened_alerts"]}
    assert "high_temperature" in types
    assert "excessive_vibration" in types


def test_alerts_carry_actionable_fields(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40, temperature=130.0)

    body = evaluate(client, db)
    alert = next(a for a in body["opened_alerts"] if a["alert_type"] == "high_temperature")

    for field in (
        "alert_id", "equipment_id", "timestamp", "alert_type", "severity",
        "description", "recommended_action", "observed_value", "threshold_value",
        "resolved", "acknowledged",
    ):
        assert field in alert, f"missing {field}"
    assert alert["resolved"] is False
    assert alert["recommended_action"]


def test_healthy_machine_raises_nothing(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40)

    body = evaluate(client, db)

    assert body["opened"] == 0


def test_machine_without_telemetry_raises_nothing(
    client: TestClient, equipment, db: Session
) -> None:
    body = evaluate(client, db)

    assert body["opened"] == 0


# --- Severity ---

def test_overheating_alert_is_critical(
    client: TestClient, equipment, db: Session
) -> None:
    """A reading past the alarm limit must not be a mere warning."""
    feed(client, 40, temperature=140.0)

    body = evaluate(client, db)
    alert = next(a for a in body["opened_alerts"] if a["alert_type"] == "high_temperature")

    assert alert["severity"] == "Critical"


# --- Deduplication ---

def test_repeat_evaluation_does_not_duplicate(
    client: TestClient, equipment, db: Session
) -> None:
    """A condition firing repeatedly must not create a second alert."""
    feed(client, 40, temperature=130.0)
    clear_alerts(db)

    first = client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"}).json()
    second = client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"}).json()
    third = client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"}).json()

    assert first["opened"] >= 1
    assert second["opened"] == 0
    assert third["opened"] == 0

    count = db.scalar(select(func.count(Alert.id)))
    assert count == first["opened"]


def test_deduped_alert_keeps_original_timestamp(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40, temperature=130.0)
    clear_alerts(db)
    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"})

    row = db.scalar(select(Alert))
    original = row.timestamp
    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"})

    db.expire_all()
    assert db.scalar(select(Alert)).timestamp == original


# --- Auto-resolution ---

def test_alert_resolves_when_condition_clears(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40, temperature=130.0)
    clear_alerts(db)
    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"})

    feed(client, 60)  # a long clean window follows
    body = client.post(
        "/api/alerts/evaluate", json={"equipment_id": "EQ-AL1", "window": 60}
    ).json()

    assert body["resolved"] >= 1
    remaining = db.scalars(
        select(Alert).where(Alert.resolved.is_(False))
    ).all()
    assert remaining == []


def test_resolved_alert_records_reason(
    client: TestClient, equipment, db: Session
) -> None:
    feed(client, 40, temperature=130.0)
    clear_alerts(db)
    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"})
    feed(client, 60)
    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1", "window": 60})

    row = db.scalar(select(Alert))
    db.expire_all()
    row = db.scalar(select(Alert))
    assert "cleared" in (row.recommended_action or "").lower()


def test_resolved_alert_can_reopen(
    client: TestClient, equipment, db: Session
) -> None:
    """A machine that fails again must raise a fresh alert."""
    feed(client, 40, temperature=130.0)
    clear_alerts(db)
    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1"})
    feed(client, 60)
    client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-AL1", "window": 60})

    feed(client, 40, temperature=130.0)
    body = client.post(
        "/api/alerts/evaluate", json={"equipment_id": "EQ-AL1", "window": 40}
    ).json()

    assert body["opened"] >= 1
    total = db.scalar(select(func.count(Alert.id)))
    assert total >= 2


# --- Listing ---

def test_list_alerts(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0, vibration=13.0)
    evaluate(client, db)

    response = client.get("/api/alerts")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 2
    assert body["unresolved_total"] >= 2
    assert all(item["equipment_id"] == "EQ-AL1" for item in body["items"])


def test_list_alerts_filtered_by_equipment(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0)
    evaluate(client, db)

    body = client.get("/api/alerts", params={"equipment_id": "EQ-AL1"}).json()

    assert body["total"] >= 1


def test_list_alerts_filtered_by_severity(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0)
    evaluate(client, db)

    body = client.get("/api/alerts", params={"severity": "Critical"}).json()

    assert all(item["severity"] == "Critical" for item in body["items"])


def test_list_alerts_filtered_by_type(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0, vibration=13.0)
    evaluate(client, db)

    body = client.get("/api/alerts", params={"alert_type": "high_temperature"}).json()

    assert body["total"] >= 1
    assert all(item["alert_type"] == "high_temperature" for item in body["items"])


def test_list_alerts_newest_first(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0, vibration=13.0)
    evaluate(client, db)

    items = client.get("/api/alerts").json()["items"]

    assert len(items) >= 2
    timestamps = [item["timestamp"] for item in items]
    assert timestamps == sorted(timestamps, reverse=True)


def test_list_alerts_empty(client: TestClient, equipment, db: Session) -> None:
    clear_alerts(db)

    body = client.get("/api/alerts").json()

    assert body["items"] == []
    assert body["total"] == 0


def test_invalid_severity_filter_rejected(client: TestClient) -> None:
    assert client.get("/api/alerts", params={"severity": "nonsense"}).status_code == 422


def test_alert_summary(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0, vibration=13.0)
    evaluate(client, db)

    body = client.get("/api/alerts/summary").json()

    assert body["unresolved_total"] >= 2
    assert body["critical"] >= 1
    assert set(body["counts_by_severity"]) <= {"Info", "Warning", "Critical"}


# --- Resolve / acknowledge ---

def test_resolve_alert(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0)
    evaluate(client, db)
    alert_id = client.get("/api/alerts").json()["items"][0]["alert_id"]

    response = client.post(f"/api/alerts/{alert_id}/resolve", json={"note": "repaired"})

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True
    assert body["resolved_at"] is not None
    assert "repaired" in body["recommended_action"]


def test_resolve_unknown_alert_returns_404(client: TestClient) -> None:
    assert client.post("/api/alerts/nope/resolve").status_code == 404


def test_acknowledge_alert(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0)
    evaluate(client, db)
    alert_id = client.get("/api/alerts").json()["items"][0]["alert_id"]

    body = client.post(f"/api/alerts/{alert_id}/acknowledge").json()

    assert body["acknowledged"] is True
    # Acknowledging must not resolve: the condition is still active.
    assert body["resolved"] is False


def test_acknowledge_unknown_alert_returns_404(client: TestClient) -> None:
    assert client.post("/api/alerts/nope/acknowledge").status_code == 404


# --- Fleet evaluation ---

def test_fleet_evaluation(client: TestClient, equipment, db: Session) -> None:
    feed(client, 40, temperature=130.0)
    clear_alerts(db)

    response = client.post("/api/alerts/evaluate")

    assert response.status_code == 200
    assert response.json()["opened"] >= 1


def test_fleet_evaluation_unknown_equipment(client: TestClient) -> None:
    response = client.post("/api/alerts/evaluate", json={"equipment_id": "EQ-NOPE"})

    assert response.status_code == 404


def test_evaluate_without_body(client: TestClient) -> None:
    response = client.post("/api/alerts/evaluate")

    assert response.status_code == 200
