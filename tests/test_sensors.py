"""Sensor ingestion, retrieval, and time-window tests."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.equipment import Equipment
from app.models.sensor_reading import SensorReading

EQUIPMENT = {
    "equipment_id": "EQ-S01",
    "equipment_name": "Sensor Test Pump",
    "equipment_type": "Hydraulic Pump",
    "location": "Test Bay",
}

READING = {
    "equipment_id": "EQ-S01",
    "temperature": 65.0,
    "vibration": 3.2,
    "pressure": 100.0,
    "rpm": 1450.0,
    "current": 22.0,
    "voltage": 400.0,
    "power": 13.5,
    "load": 70.0,
}


@pytest.fixture
def equipment(client: TestClient):
    """Create a unit for ingestion tests and remove it afterwards."""
    response = client.post("/api/equipment", json=EQUIPMENT)
    assert response.status_code == 201, response.text
    try:
        yield response.json()
    finally:
        client.delete(f"/api/equipment/{EQUIPMENT['equipment_id']}?hard=true")


# --- Ingestion ---

def test_ingest_full_reading(client: TestClient, equipment) -> None:
    response = client.post("/api/sensor/readings", json=READING)

    assert response.status_code == 201
    body = response.json()
    assert body["temperature"] == 65.0
    assert body["data_quality"] == "complete"
    assert body["id"] > 0


def test_ingest_partial_reading_keeps_nulls(client: TestClient, equipment) -> None:
    """A partial sample must be stored, not rejected.

    Losing one channel is normal on a real gateway; discarding the whole
    sample would throw away good data.
    """
    response = client.post(
        "/api/sensor/readings", json={"equipment_id": "EQ-S01", "temperature": 64.0}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["temperature"] == 64.0
    assert body["vibration"] is None
    assert body["data_quality"] == "sparse"


def test_reading_for_unknown_equipment_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/sensor/readings", json={**READING, "equipment_id": "EQ-NOPE"}
    )

    assert response.status_code == 404


def test_equipment_id_is_case_insensitive(client: TestClient, equipment) -> None:
    response = client.post("/api/sensor/readings", json={**READING, "equipment_id": "eq-s01"})

    assert response.status_code == 201
    assert response.json()["equipment_id"] == "EQ-S01"


def test_extreme_but_valid_reading_accepted(client: TestClient, equipment) -> None:
    """Alarming values are the scorer's business, not the validator's."""
    response = client.post(
        "/api/sensor/readings",
        json={**READING, "temperature": 195.0, "vibration": 48.0, "pressure": 22.0},
    )

    assert response.status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        {"equipment_id": "EQ-S01"},
        {"equipment_id": "EQ-S01", "temperature": 5000},
        {"equipment_id": "EQ-S01", "vibration": -2},
        {"temperature": 50},
        {"equipment_id": "EQ-S01", "current": 0, "power": 10},
    ],
)
def test_invalid_readings_rejected(client: TestClient, equipment, payload) -> None:
    response = client.post("/api/sensor/readings", json=payload)

    assert response.status_code == 422


# --- Batch ---

def test_batch_ingest(client: TestClient, equipment) -> None:
    readings = [{**READING, "temperature": 60 + i * 0.1} for i in range(25)]

    response = client.post("/api/sensor/readings/batch", json={"readings": readings})

    assert response.status_code == 201
    assert response.json()["inserted"] == 25


def test_batch_skips_unknown_equipment_without_failing(
    client: TestClient, equipment
) -> None:
    """One bad row must not discard a whole gateway replay."""
    response = client.post(
        "/api/sensor/readings/batch",
        json={
            "readings": [
                READING,
                {**READING, "equipment_id": "EQ-GONE"},
                {**READING, "temperature": 66},
            ]
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["inserted"] == 2
    assert body["rejected"] == 1


def test_empty_batch_rejected(client: TestClient) -> None:
    assert client.post("/api/sensor/readings/batch", json={"readings": []}).status_code == 422


# --- Retrieval ---

def test_list_readings(client: TestClient, equipment) -> None:
    for i in range(10):
        client.post("/api/sensor/readings", json={**READING, "temperature": 60 + i})

    response = client.get("/api/sensor/readings/EQ-S01")

    assert response.status_code == 200
    assert len(response.json()) == 10


def test_list_readings_ordering(client: TestClient, equipment) -> None:
    for i in range(5):
        client.post("/api/sensor/readings", json={**READING, "temperature": 60 + i})

    ascending = [r["temperature"] for r in client.get("/api/sensor/readings/EQ-S01").json()]
    descending = [
        r["temperature"]
        for r in client.get("/api/sensor/readings/EQ-S01", params={"order": "desc"}).json()
    ]

    assert ascending == sorted(ascending)
    assert descending == sorted(descending, reverse=True)


def test_list_readings_unknown_equipment(client: TestClient) -> None:
    assert client.get("/api/sensor/readings/EQ-NOPE").status_code == 404


def test_latest_reading(client: TestClient, equipment) -> None:
    client.post("/api/sensor/readings", json={**READING, "temperature": 61})
    client.post("/api/sensor/readings", json={**READING, "temperature": 62})

    body = client.get("/api/sensor/readings/EQ-S01/latest").json()

    assert body["temperature"] == 62


def test_latest_reading_with_no_data(client: TestClient, equipment) -> None:
    response = client.get("/api/sensor/readings/EQ-S01/latest")

    assert response.status_code == 404
    assert "no readings" in response.json()["detail"].lower()


def test_channels_endpoint_documents_units(client: TestClient) -> None:
    body = client.get("/api/sensor/channels").json()

    assert body["channels"] == list(
        ("temperature", "vibration", "pressure", "rpm", "current", "voltage", "power", "load")
    )
    assert set(body["units"]) == set(body["channels"])


# --- Time windows and timezone handling ---

def test_chart_window_is_computed_in_utc(client: TestClient, equipment, db: Session) -> None:
    """The window must be anchored to UTC, not host local time.

    Readings are stored as UTC-naive, so a local-time window silently shifts by
    the host's UTC offset and hides the most recent samples.
    """
    equipment_row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-S01"))
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)

    for offset_minutes in (0, 30, 60):
        db.add(
            SensorReading(
                equipment_db_id=equipment_row.id,
                timestamp=now - timedelta(minutes=offset_minutes),
                temperature=65.0,
                vibration=3.0,
                pressure=100.0,
                rpm=1450.0,
                current=22.0,
                voltage=400.0,
                power=13.0,
                load=70.0,
            )
        )
    db.commit()

    body = client.get("/api/sensor/readings/EQ-S01/chart", params={"hours": 1}).json()

    # The sample written "now" must appear in a one-hour window.
    assert body["count"] == 3
    assert body["readings"][-1]["timestamp"].startswith(now.strftime("%Y-%m-%dT%H:%M"))


def test_timestamps_are_stored_as_utc(client: TestClient, equipment, db: Session) -> None:
    """An aware timestamp must be converted to UTC, not stored as local."""
    aware = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    client.post("/api/sensor/readings", json={**READING, "timestamp": aware.isoformat()})

    row = db.scalar(select(SensorReading).order_by(SensorReading.id.desc()))
    assert row.timestamp.replace(tzinfo=UTC) == aware


def test_aware_timestamp_converts_from_other_zone(client: TestClient, equipment, db: Session) -> None:
    from zoneinfo import ZoneInfo

    # 18:00 at UTC+5:30 is 12:30 UTC.
    local = datetime(2026, 6, 1, 18, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    client.post("/api/sensor/readings", json={**READING, "timestamp": local.isoformat()})

    row = db.scalar(select(SensorReading).order_by(SensorReading.id.desc()))
    assert row.timestamp.hour == 12
    assert row.timestamp.minute == 30


def test_downsampling_preserves_endpoints(client: TestClient, equipment, db: Session) -> None:
    equipment_row = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-S01"))
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)

    for i in range(500):
        db.add(
            SensorReading(
                equipment_db_id=equipment_row.id,
                timestamp=now - timedelta(minutes=500 - i),
                temperature=60.0 + i * 0.01,
                vibration=3.0,
                pressure=100.0,
                rpm=1450.0,
                current=22.0,
                voltage=400.0,
                power=13.0,
                load=70.0,
            )
        )
    db.commit()

    body = client.get(
        "/api/sensor/readings/EQ-S01/chart", params={"hours": 24, "max_points": 100}
    ).json()

    assert 0 < body["count"] <= 101
    values = [r["temperature"] for r in body["readings"]]
    assert values == sorted(values), "downsampled series must stay chronological"
    # Endpoints retained so the visible range is complete.
    assert body["readings"][0]["temperature"] == pytest.approx(60.0, abs=0.05)
    assert body["readings"][-1]["temperature"] == pytest.approx(64.99, abs=0.05)


# --- Repository ---

def test_reading_count(db: Session, equipment, client: TestClient) -> None:
    """`reading_count` must agree with an independent count of the same rows.

    This test used to skip unconditionally. It looked up EQ-S01 directly, but
    every test that creates that unit does so through the function-scoped
    `equipment` fixture, whose teardown hard-deletes it — so the row was never
    there when this test ran and it asserted nothing while still appearing in
    the suite. Taking the fixture and writing our own rows makes it non-vacuous.
    """
    from app.services import sensor_service

    client_equipment = db.scalar(select(Equipment).where(Equipment.equipment_id == "EQ-S01"))
    assert client_equipment is not None, "the equipment fixture must create the unit"

    for temperature in (61.0, 62.0):
        response = client.post(
            "/api/sensor/readings", json={**READING, "temperature": temperature}
        )
        assert response.status_code == 201, response.text

    assert sensor_service.reading_count(db, "EQ-S01") == 2, (
        "reading_count must count the rows just written; 0 here would mean the "
        "test is vacuous again."
    )
    assert sensor_service.reading_count(db, "EQ-S01") == db.scalar(
        select(func.count(SensorReading.id)).where(
            SensorReading.equipment_db_id == client_equipment.id
        )
    )
