"""Sensor reading data access."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES
from app.models.equipment import Equipment
from app.models.sensor_reading import SensorReading


def create_reading(
    db: Session,
    equipment: Equipment,
    values: dict,
    timestamp: datetime | None = None,
    data_quality: str = "complete",
) -> SensorReading:
    """Persist one reading.

    `timestamp` is normalised to UTC and stripped of tzinfo for storage:
    SQLite has no native timezone type, so a tz-aware value would round-trip
    inconsistently. All comparisons are performed in UTC.
    """
    payload = {
        "equipment_db_id": equipment.id,
        "data_quality": data_quality,
    }
    if timestamp is not None:
        payload["timestamp"] = _to_utc_naive(timestamp)
    for channel in SENSOR_FEATURES:
        value = values.get(channel)
        if value is not None:
            payload[channel] = float(value)

    reading = SensorReading(**payload)
    db.add(reading)
    return reading


def _to_utc_naive(value: datetime) -> datetime:
    """Convert an aware datetime to UTC and drop the tzinfo.

    SQLite cannot store a timezone. Storing the naive value as-is would keep
    local time and silently shift every subsequent time-window query.
    """
    from datetime import UTC

    if value.tzinfo is None:
        return value.replace(tzinfo=None)
    return value.astimezone(UTC).replace(tzinfo=None)


def get_by_id(db: Session, reading_id: int) -> SensorReading | None:
    """Fetch one reading by primary key."""
    return db.get(SensorReading, reading_id)


def list_readings(
    db: Session,
    equipment: Equipment,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 500,
    offset: int = 0,
    order: str = "asc",
) -> Sequence[SensorReading]:
    """Return a time-windowed slice of one machine's telemetry.

    `order="desc"` returns newest first, which is what a dashboard wants; the
    default is chronological, which is what the feature pipeline wants.
    """
    stmt = select(SensorReading).where(SensorReading.equipment_db_id == equipment.id)

    if start is not None:
        stmt = stmt.where(SensorReading.timestamp >= _to_utc_naive(start))
    if end is not None:
        stmt = stmt.where(SensorReading.timestamp <= _to_utc_naive(end))

    stmt = stmt.order_by(
        SensorReading.timestamp.desc() if order == "desc" else SensorReading.timestamp.asc()
    )
    return db.scalars(stmt.limit(limit).offset(offset)).all()


def latest_reading(db: Session, equipment: Equipment) -> SensorReading | None:
    """Most recent reading for a machine, or None if it has no history."""
    return db.scalar(
        select(SensorReading)
        .where(SensorReading.equipment_db_id == equipment.id)
        .order_by(SensorReading.timestamp.desc())
        .limit(1)
    )


def recent_readings(
    db: Session, equipment: Equipment, count: int = 60
) -> list[SensorReading]:
    """The `count` most recent readings, returned oldest-first.

    Ordering is applied in the query, then reversed, so the caller receives a
    chronological list suitable for rolling-window feature construction.
    """
    rows = db.scalars(
        select(SensorReading)
        .where(SensorReading.equipment_db_id == equipment.id)
        .order_by(SensorReading.timestamp.desc())
        .limit(count)
    ).all()
    return list(reversed(rows))


def reading_count(db: Session, equipment: Equipment) -> int:
    """Total readings stored for a machine."""
    return db.scalar(
        select(func.count(SensorReading.id)).where(
            SensorReading.equipment_db_id == equipment.id
        )
    ) or 0


def latest_timestamps(db: Session, equipment_ids: Sequence[int]) -> dict[int, datetime]:
    """Newest timestamp per equipment id, for fleet-level display.

    One grouped query rather than a lookup per unit, so the dashboard's fleet
    view stays a single round trip.
    """
    if not equipment_ids:
        return {}
    rows = db.execute(
        select(
            SensorReading.equipment_db_id,
            func.max(SensorReading.timestamp),
        )
        .where(SensorReading.equipment_db_id.in_(equipment_ids))
        .group_by(SensorReading.equipment_db_id)
    ).all()
    return {equipment_id: timestamp for equipment_id, timestamp in rows}


def downsample(
    readings: Sequence[SensorReading], max_points: int = 300
) -> list[SensorReading]:
    """Reduce a long series to at most `max_points` evenly spaced samples.

    Charts cannot render thousands of points legibly, and sending them all
    wastes bandwidth on every dashboard poll. The first and last samples are
    always retained so the visible range is preserved.
    """
    total = len(readings)
    if total <= max_points or max_points < 2:
        return list(readings)

    step = total / max_points
    indices = {int(i * step) for i in range(max_points)}
    indices.add(0)
    indices.add(total - 1)
    return [readings[i] for i in sorted(indices) if i < total]


def equipment_ids_with_history(db: Session) -> set[int]:
    """Equipment ids that have at least one reading."""
    rows = db.scalars(
        select(SensorReading.equipment_db_id).distinct()
    ).all()
    return set(rows)


def _base_query(equipment: Equipment) -> Select:
    return select(SensorReading).where(SensorReading.equipment_db_id == equipment.id)
