"""Anomaly data access."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.anomaly import Anomaly
from app.models.equipment import Equipment


def create(
    db: Session,
    equipment: Equipment,
    *,
    anomaly_score: float,
    anomaly_detected: bool,
    affected_parameters: list[str] | None = None,
    parameter_scores: dict | None = None,
    reading_id: int | None = None,
    timestamp: datetime | None = None,
    method: str = "isolation_forest+band+ewma",
    model_version: str | None = None,
) -> Anomaly:
    """Persist one assessment.

    Assessments are stored whether or not an anomaly was found, so the
    false-negative rate stays measurable.
    """
    anomaly = Anomaly(
        equipment_db_id=equipment.id,
        reading_id=reading_id,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_detected,
        affected_parameters=affected_parameters or [],
        parameter_scores=parameter_scores,
        method=method,
        model_version=model_version,
    )
    if timestamp is not None:
        anomaly.timestamp = _naive_utc(timestamp)
    db.add(anomaly)
    db.flush()
    return anomaly


def _naive_utc(value: datetime) -> datetime:
    from datetime import UTC

    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def list_for_equipment(
    db: Session,
    equipment: Equipment,
    *,
    detected_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[Anomaly]:
    """Recent anomaly history for one machine, newest first."""
    stmt = select(Anomaly).where(Anomaly.equipment_db_id == equipment.id)
    if detected_only:
        stmt = stmt.where(Anomaly.anomaly_detected.is_(True))
    stmt = (
        stmt.order_by(Anomaly.timestamp.desc()).limit(limit).offset(offset)
    )
    return db.scalars(stmt).all()


def latest(db: Session, equipment: Equipment) -> Anomaly | None:
    """Most recent assessment for a machine."""
    return db.scalar(
        select(Anomaly)
        .where(Anomaly.equipment_db_id == equipment.id)
        .order_by(Anomaly.timestamp.desc())
        .limit(1)
    )


def latest_detected(db: Session, equipment: Equipment) -> Anomaly | None:
    """Most recent assessment that actually flagged an anomaly."""
    return db.scalar(
        select(Anomaly)
        .where(
            Anomaly.equipment_db_id == equipment.id,
            Anomaly.anomaly_detected.is_(True),
        )
        .order_by(Anomaly.timestamp.desc())
        .limit(1)
    )


def unresolved_count(db: Session, equipment: Equipment) -> int:
    """How many anomaly rows have flagged, for fleet-level display."""
    return db.scalar(
        select(func.count(Anomaly.id)).where(
            Anomaly.equipment_db_id == equipment.id,
            Anomaly.anomaly_detected.is_(True),
        )
    ) or 0


def fleet_counts(db: Session) -> dict[int, int]:
    """Detected-anomaly count per equipment id, in one query.

    Avoids a per-unit query when the dashboard needs this for the whole fleet.
    """
    rows = db.execute(
        select(Anomaly.equipment_db_id, func.count(Anomaly.id))
        .where(Anomaly.anomaly_detected.is_(True))
        .group_by(Anomaly.equipment_db_id)
    ).all()
    return {equipment_id: count for equipment_id, count in rows}


def parameter_frequency(
    db: Session, equipment: Equipment, limit: int = 5
) -> list[tuple[str, int]]:
    """How often each parameter was named as affected.

    Computed in Python from the JSON column rather than in SQL: SQLite has no
    JSON aggregation, and the per-machine row count here is small.
    """
    rows = db.scalars(
        select(Anomaly.affected_parameters).where(
            Anomaly.equipment_db_id == equipment.id,
            Anomaly.anomaly_detected.is_(True),
        )
    ).all()

    counts: dict[str, int] = {}
    for parameters in rows:
        for parameter in parameters or []:
            counts[parameter] = counts.get(parameter, 0) + 1

    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
