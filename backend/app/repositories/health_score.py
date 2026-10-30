"""Health score data access."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.equipment import Equipment
from app.models.health_score import HealthScore


def create(
    db: Session,
    equipment: Equipment,
    *,
    health_score: float,
    status: str,
    risk_level: str,
    anomaly_score: float | None = None,
    failure_probability: float | None = None,
    score_breakdown: dict | None = None,
    contributing_factors: list[dict] | None = None,
    explanation: str | None = None,
    recommendation: str | None = None,
    model_version: str | None = None,
    source_reading_id: int | None = None,
    timestamp: datetime | None = None,
) -> HealthScore:
    """Append one immutable assessment."""
    row = HealthScore(
        equipment_db_id=equipment.id,
        health_score=health_score,
        status=status,
        risk_level=risk_level,
        anomaly_score=anomaly_score,
        failure_probability=failure_probability,
        score_breakdown=score_breakdown,
        contributing_factors=contributing_factors,
        explanation=explanation,
        recommendation=recommendation,
        model_version=model_version,
        source_reading_id=source_reading_id,
    )
    if timestamp is not None:
        row.timestamp = _naive_utc(timestamp)
    db.add(row)
    db.flush()
    return row


def _naive_utc(value: datetime) -> datetime:
    from datetime import UTC

    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def latest(db: Session, equipment: Equipment) -> HealthScore | None:
    """Most recent assessment for a machine."""
    return db.scalar(
        select(HealthScore)
        .where(HealthScore.equipment_db_id == equipment.id)
        .order_by(HealthScore.timestamp.desc(), HealthScore.id.desc())
        .limit(1)
    )


def previous(db: Session, equipment: Equipment) -> HealthScore | None:
    """The assessment before the most recent one, for delta reporting."""
    latest_row = latest(db, equipment)
    if latest_row is None:
        return None
    return db.scalar(
        select(HealthScore)
        .where(
            HealthScore.equipment_db_id == equipment.id,
            HealthScore.id != latest_row.id,
        )
        .order_by(HealthScore.timestamp.desc(), HealthScore.id.desc())
        .limit(1)
    )


def history(
    db: Session,
    equipment: Equipment,
    *,
    limit: int = 200,
    ascending: bool = True,
) -> Sequence[HealthScore]:
    """Assessment history, oldest first by default for charting."""
    stmt = select(HealthScore).where(HealthScore.equipment_db_id == equipment.id)
    order = (
        (HealthScore.timestamp.asc(), HealthScore.id.asc())
        if ascending
        else (HealthScore.timestamp.desc(), HealthScore.id.desc())
    )
    return db.scalars(stmt.order_by(*order).limit(limit)).all()


def fleet_latest(db: Session) -> dict[int, HealthScore]:
    """Newest assessment per equipment id, in one query.

    The dashboard needs this for every unit on each poll; a per-unit query
    would turn one page load into N round trips.
    """
    latest_ids = (
        select(HealthScore.equipment_db_id, HealthScore.id)
        .where(
            HealthScore.id.in_(
                select(HealthScore.id)
                .order_by(HealthScore.timestamp.desc(), HealthScore.id.desc())
                .distinct(HealthScore.equipment_db_id)
            )
        )
    )
    rows = db.scalars(select(HealthScore).where(HealthScore.id.in_(latest_ids))).all()
    return {row.equipment_db_id: row for row in rows}
