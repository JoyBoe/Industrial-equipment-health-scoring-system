"""Sensor telemetry routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES
from app.database import get_db
from app.repositories.equipment import EquipmentNotFoundError
from app.schemas.sensor_reading import (
    SENSOR_CHANNELS,
    SensorReadingBatchCreate,
    SensorReadingBatchResponse,
    SensorReadingCreate,
    SensorReadingRead,
)
from app.services import sensor_service

router = APIRouter(prefix="/sensor", tags=["sensors"])


def _to_schema(reading, equipment_id: str) -> SensorReadingRead:
    """Attach the external equipment_id, which the ORM row does not store."""
    return SensorReadingRead(
        id=reading.id,
        equipment_id=equipment_id,
        timestamp=reading.timestamp,
        temperature=reading.temperature,
        vibration=reading.vibration,
        pressure=reading.pressure,
        rpm=reading.rpm,
        current=reading.current,
        voltage=reading.voltage,
        power=reading.power,
        load=reading.load,
        data_quality=reading.data_quality,
    )


@router.get(
    "/channels",
    summary="List sensor channels and units",
)
def list_channels() -> dict[str, object]:
    """The channels the system ingests, with their units.

    Exposed so the frontend can label charts and forms from a single source of
    truth instead of duplicating the mapping.
    """
    return {
        "channels": list(SENSOR_FEATURES),
        "units": {
            "temperature": "degC",
            "vibration": "mm/s RMS",
            "pressure": "bar",
            "rpm": "rev/min",
            "current": "A",
            "voltage": "V",
            "power": "kW",
            "load": "% of rated",
        },
    }


@router.post(
    "/readings",
    response_model=SensorReadingRead,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"description": "Equipment not found"}, 422: {"description": "Invalid reading"}},
    summary="Ingest a sensor reading",
)
def create_reading(
    payload: SensorReadingCreate, db: Session = Depends(get_db)
) -> SensorReadingRead:
    """Store one telemetry sample.

    Accepts partial readings: at least one channel is required, and missing
    channels are stored as NULL and imputed downstream rather than rejected.
    """
    try:
        reading, equipment = sensor_service.ingest_reading(db, payload)
    except EquipmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _to_schema(reading, equipment.equipment_id)


@router.post(
    "/readings/batch",
    response_model=SensorReadingBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest many readings",
)
def create_readings_batch(
    payload: SensorReadingBatchCreate, db: Session = Depends(get_db)
) -> SensorReadingBatchResponse:
    """Store up to 5000 readings in one transaction.

    Rows referencing unknown equipment are skipped and counted in `rejected`
    rather than failing the batch.
    """
    inserted, rejected, equipment_ids = sensor_service.ingest_batch(db, payload)
    return SensorReadingBatchResponse(
        inserted=inserted, rejected=rejected, equipment_ids=equipment_ids
    )


@router.get(
    "/readings/{equipment_id}",
    response_model=list[SensorReadingRead],
    responses={404: {"description": "Equipment not found"}},
    summary="Read raw telemetry history",
)
def list_readings(  # noqa: PLR0913 - mirrors the charting controls
    equipment_id: str,
    start: datetime | None = Query(None, description="Window start (ISO 8601)"),
    end: datetime | None = Query(None, description="Window end (ISO 8601)"),
    limit: int = Query(500, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> list[SensorReadingRead]:
    """Return raw readings for one machine, unfiltered by completeness."""
    try:
        rows = sensor_service.get_history(
            db, equipment_id, start=start, end=end, limit=limit, offset=offset, order=order
        )
    except EquipmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [_to_schema(row, equipment_id.upper()) for row in rows]


@router.get(
    "/readings/{equipment_id}/chart",
    responses={404: {"description": "Equipment not found"}},
    summary="Chart-ready telemetry",
)
def chart_readings(
    equipment_id: str,
    hours: int = Query(24, ge=1, le=24 * 90, description="Trailing window in hours"),
    max_points: int = Query(300, ge=10, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Return telemetry downsampled for plotting.

    Downsampling is applied here rather than in the browser so a 48-hour
    window does not ship ~2900 points on every dashboard poll.
    """
    try:
        rows = sensor_service.get_downsampled_history(
            db, equipment_id, hours=hours, max_points=max_points
        )
    except EquipmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    upper = equipment_id.upper()
    return {
        "equipment_id": upper,
        "hours": hours,
        "count": len(rows),
        "channels": list(SENSOR_CHANNELS),
        "readings": [
            {
                "timestamp": row.timestamp,
                **{channel: getattr(row, channel) for channel in SENSOR_CHANNELS},
            }
            for row in rows
        ],
    }


@router.get(
    "/readings/{equipment_id}/latest",
    response_model=SensorReadingRead,
    responses={404: {"description": "Equipment not found or no readings"}},
    summary="Most recent reading",
)
def latest_reading(equipment_id: str, db: Session = Depends(get_db)) -> SensorReadingRead:
    """The newest sample for a machine."""
    try:
        reading = sensor_service.get_latest(db, equipment_id)
    except EquipmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if reading is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No readings recorded for '{equipment_id}'"
        )
    return _to_schema(reading, equipment_id.upper())
