"""Sensor ingestion and retrieval.

Ingestion is deliberately tolerant of partial data: a gateway that reports
temperature and vibration but loses the pressure channel still produces a
useful record. Rejecting the whole sample would discard good data and, worse,
make the machine appear to have a telemetry outage.

Strict rejection is reserved for readings that cannot be interpreted at all
(unknown equipment, physically impossible channel combinations), and those are
handled by Pydantic and the 404 path respectively.
"""

import logging
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES
from app.models.equipment import Equipment
from app.models.sensor_reading import SensorReading
from app.repositories import sensor_reading as reading_repo
from app.repositories.equipment import EquipmentNotFoundError
from app.schemas.sensor_reading import (
    SENSOR_CHANNELS,
    SensorReadingBatchCreate,
    SensorReadingCreate,
)

logger = logging.getLogger(__name__)

#: Cap on a single bulk ingest, to bound transaction size and memory.
MAX_BATCH = 5000


def ingest_reading(
    db: Session, payload: SensorReadingCreate
) -> tuple[SensorReading, Equipment]:
    """Store one reading for an existing machine.

    Returns the stored reading and the equipment it belongs to, so the caller
    can trigger scoring without a second lookup.
    """
    from app.repositories.equipment import get_or_404

    equipment = get_or_404(db, payload.equipment_id)

    values = {channel: getattr(payload, channel) for channel in SENSOR_CHANNELS}
    reading = reading_repo.create_reading(
        db,
        equipment,
        values,
        timestamp=payload.timestamp,
        data_quality=payload.data_quality,
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("Rejected reading for %s: %s", payload.equipment_id, exc)
        raise

    db.refresh(reading)
    return reading, equipment


def ingest_batch(
    db: Session, payload: SensorReadingBatchCreate
) -> tuple[int, int, list[str]]:
    """Store many readings in a single transaction.

    Individual bad rows are skipped and counted rather than failing the whole
    batch: a 5000-sample gateway replay should not be lost because one sample
    references a machine that has since been deleted.

    Returns (inserted, rejected, equipment_ids).
    """
    readings = payload.readings[:MAX_BATCH]

    # Resolve every referenced machine once, then reuse. Avoids a query per row
    # and tells us which IDs to reject.
    wanted = {reading.equipment_id for reading in readings}
    from app.repositories.equipment import list_equipment

    known_rows, _ = list_equipment(
        db, active_only=False, limit=max(len(wanted), 1) * 10, offset=0
    )
    known = {row.equipment_id: row for row in known_rows if row.equipment_id in wanted}

    inserted = 0
    rejected = 0
    touched: list[str] = []

    for reading in readings:
        equipment = known.get(reading.equipment_id)
        if equipment is None:
            logger.warning("Batch rejected: unknown equipment %s", reading.equipment_id)
            rejected += 1
            continue
        values = {channel: getattr(reading, channel) for channel in SENSOR_CHANNELS}
        reading_repo.create_reading(
            db,
            equipment,
            values,
            timestamp=reading.timestamp,
            data_quality=reading.data_quality,
        )
        inserted += 1
        if equipment.equipment_id not in touched:
            touched.append(equipment.equipment_id)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.error("Batch ingest rolled back: %s", exc)
        raise

    return inserted, rejected, touched


def get_history(
    db: Session,
    equipment_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 500,
    offset: int = 0,
    order: str = "asc",
) -> list[SensorReading]:
    """Return a time window of readings for one machine."""
    equipment = _require_equipment(db, equipment_id)
    return list(
        reading_repo.list_readings(
            db, equipment, start=start, end=end, limit=limit, offset=offset, order=order
        )
    )


def get_downsampled_history(
    db: Session,
    equipment_id: str,
    *,
    hours: int = 24,
    max_points: int = 300,
) -> list[SensorReading]:
    """Return chart-ready telemetry for a recent window.

    Window plus downsampling, because a 48-hour series at 1-minute cadence is
    ~2900 points, which no chart renders usefully.
    """
    from datetime import UTC, timedelta

    equipment = _require_equipment(db, equipment_id)
    # Readings are stored as UTC-naive (SQLite has no tz type), so the window
    # must be computed in UTC. Using local time here silently shifts the window
    # by the host's UTC offset and drops the most recent hours of data.
    end = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    start = end - timedelta(hours=max(hours, 1))
    rows = reading_repo.list_readings(
        db, equipment, start=start, end=end, limit=100_000, order="asc"
    )
    return reading_repo.downsample(list(rows), max_points=max_points)


def get_latest(db: Session, equipment_id: str) -> SensorReading | None:
    """Most recent reading for a machine."""
    return reading_repo.latest_reading(db, _require_equipment(db, equipment_id))


def get_recent(db: Session, equipment_id: str, count: int = 60) -> list[SensorReading]:
    """Recent readings, oldest-first, for feature construction."""
    return reading_repo.recent_readings(db, _require_equipment(db, equipment_id), count)


def reading_count(db: Session, equipment_id: str) -> int:
    """Total stored readings for a machine."""
    return reading_repo.reading_count(db, _require_equipment(db, equipment_id))


def _require_equipment(db: Session, equipment_id: str) -> Equipment:
    """Resolve an equipment identifier or raise the domain error."""
    from app.repositories.equipment import get_or_404

    return get_or_404(db, equipment_id)


__all__ = [
    "EquipmentNotFoundError",
    "MAX_BATCH",
    "SENSOR_CHANNELS",
    "SENSOR_FEATURES",
    "get_downsampled_history",
    "get_history",
    "get_latest",
    "get_recent",
    "ingest_batch",
    "ingest_reading",
    "reading_count",
]
