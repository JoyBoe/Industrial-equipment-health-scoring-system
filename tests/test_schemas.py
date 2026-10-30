"""Pydantic validation tests, independent of the HTTP layer."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.equipment import EquipmentCreate, EquipmentUpdate
from app.schemas.sensor_reading import SENSOR_CHANNELS, SensorReadingCreate


# --- Equipment ---

def test_equipment_id_is_upper_cased_and_trimmed() -> None:
    payload = EquipmentCreate(
        equipment_id="  eq-900  ",
        equipment_name="Pump",
        equipment_type="Hydraulic Pump",
        location="Bay 1",
    )

    assert payload.equipment_id == "EQ-900"


def test_equipment_name_whitespace_is_stripped() -> None:
    payload = EquipmentCreate(
        equipment_id="EQ-901",
        equipment_name="  Pump  ",
        equipment_type="Hydraulic Pump",
        location="  Bay 1  ",
    )

    assert payload.equipment_name == "Pump"
    assert payload.location == "Bay 1"


def test_blank_location_rejected() -> None:
    with pytest.raises(ValidationError):
        EquipmentCreate(
            equipment_id="EQ-902",
            equipment_name="Pump",
            equipment_type="Hydraulic Pump",
            location="   ",
        )


def test_update_allows_partial_payload() -> None:
    payload = EquipmentUpdate(location="Bay 2")

    assert payload.location == "Bay 2"
    assert payload.equipment_name is None
    assert payload.model_dump(exclude_unset=True) == {"location": "Bay 2"}


# --- Sensor readings ---

def test_reading_requires_at_least_one_channel() -> None:
    with pytest.raises(ValidationError, match="at least one sensor channel"):
        SensorReadingCreate(equipment_id="EQ-001")


def test_reading_single_channel_is_accepted() -> None:
    reading = SensorReadingCreate(equipment_id="EQ-001", temperature=65.0)

    assert reading.provided_channels == ["temperature"]
    assert reading.data_quality == "sparse"


def test_reading_quality_classification() -> None:
    full = SensorReadingCreate(
        equipment_id="EQ-001", **{channel: 1.0 for channel in SENSOR_CHANNELS}
    )
    assert full.data_quality == "complete"

    partial = SensorReadingCreate(
        equipment_id="EQ-001", temperature=1.0, vibration=1.0, pressure=1.0, rpm=1.0,
        current=1.0, voltage=1.0,
    )
    assert partial.data_quality == "partial"


@pytest.mark.parametrize(
    ("channel", "value"),
    [
        ("temperature", 1000),    # above any physical ceiling
        ("temperature", -200),
        ("vibration", -1),         # magnitude cannot be negative
        ("pressure", 9999),
        ("rpm", 200000),
        ("current", -5),
        ("voltage", 5000),
        ("power", -1),
        ("load", 500),
    ],
)
def test_implausible_sensor_values_rejected(channel: str, value: float) -> None:
    with pytest.raises(ValidationError):
        SensorReadingCreate(equipment_id="EQ-001", **{channel: value})


def test_zero_power_with_zero_current_is_rejected() -> None:
    with pytest.raises(ValidationError, match="current is zero"):
        SensorReadingCreate(equipment_id="EQ-001", current=0.0, power=5.0)


def test_current_with_zero_voltage_is_rejected() -> None:
    with pytest.raises(ValidationError, match="voltage is zero"):
        SensorReadingCreate(equipment_id="EQ-001", voltage=0.0, current=5.0)


def test_extreme_but_plausible_values_accepted() -> None:
    # Must NOT be rejected: these are alarming but physically possible, and
    # the scoring engine is responsible for penalising them, not validation.
    reading = SensorReadingCreate(
        equipment_id="EQ-001",
        temperature=180.0,
        vibration=60.0,
        pressure=250.0,
        rpm=6000.0,
        current=120.0,
        voltage=480.0,
        power=95.0,
        load=150.0,
    )

    assert reading.temperature == 180.0
    assert reading.data_quality == "complete"


def test_future_timestamp_rejected() -> None:
    future = datetime.now(UTC) + timedelta(days=1)

    with pytest.raises(ValidationError, match="future"):
        SensorReadingCreate(equipment_id="EQ-001", temperature=50.0, timestamp=future)


def test_past_timestamp_accepted() -> None:
    past = datetime.now(UTC) - timedelta(days=30)

    assert SensorReadingCreate(equipment_id="EQ-001", temperature=50.0, timestamp=past)


def test_slight_clock_skew_tolerated() -> None:
    # A gateway a couple of minutes ahead must not be rejected.
    slightly_ahead = datetime.now(UTC) + timedelta(minutes=2)

    assert SensorReadingCreate(equipment_id="EQ-001", temperature=50.0, timestamp=slightly_ahead)


def test_naive_timestamp_is_accepted_as_utc() -> None:
    """Gateways commonly send local time with no offset.

    Comparing a naive value against an aware `now()` raised TypeError, which
    the global exception handler turned into a 500 on a perfectly ordinary
    payload. A naive timestamp must be interpreted as UTC instead.
    """
    from datetime import datetime as dt

    reading = SensorReadingCreate(
        equipment_id="EQ-001",
        temperature=50.0,
        timestamp=dt(2026, 1, 1, 12, 0, 0),  # naive, no tzinfo
    )

    assert reading.timestamp.tzinfo is None


def test_naive_future_timestamp_still_rejected() -> None:
    """Interpreting naive as UTC must not weaken the future check."""
    from datetime import datetime as dt, timedelta as td

    naive_tomorrow = (dt.now(UTC) + td(days=1)).replace(tzinfo=None)

    with pytest.raises(ValidationError, match="future"):
        SensorReadingCreate(
            equipment_id="EQ-001", temperature=50.0, timestamp=naive_tomorrow
        )


def test_naive_timestamp_accepted_through_the_api(client) -> None:
    """End to end: a naive timestamp from a gateway must not produce a 500."""
    payload = {
        "equipment_id": "EQ-NAIVE",
        "equipment_name": "Naive Timestamp Pump",
        "equipment_type": "Hydraulic Pump",
        "location": "Test Bay",
    }
    assert client.post("/api/equipment", json=payload).status_code == 201
    try:
        response = client.post(
            "/api/sensor/readings",
            json={
                "equipment_id": "EQ-NAIVE",
                "temperature": 50.0,
                "timestamp": "2026-01-01T12:00:00",
            },
        )
        assert response.status_code == 201
    finally:
        client.delete("/api/equipment/EQ-NAIVE?hard=true")
