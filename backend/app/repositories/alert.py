"""Alert data access."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.equipment import Equipment


def _base(equipment: Equipment) -> Select:
    return select(Alert).where(Alert.equipment_db_id == equipment.id)


def create(
    db: Session,
    equipment: Equipment,
    *,
    alert_id: str,
    alert_type: str,
    severity: str,
    description: str,
    recommended_action: str | None = None,
    observed_value: str | None = None,
    threshold_value: str | None = None,
    timestamp: datetime | None = None,
) -> Alert:
    """Insert one alert.

    Callers are responsible for deduplication; `get_active_by_type` reports
    what is already open so the rules engine can skip.
    """
    alert = Alert(
        alert_id=alert_id,
        equipment_db_id=equipment.id,
        alert_type=alert_type,
        severity=severity,
        description=description,
        recommended_action=recommended_action,
        observed_value=observed_value,
        threshold_value=threshold_value,
    )
    if timestamp is not None:
        alert.timestamp = _naive_utc(timestamp)
    db.add(alert)
    db.flush()
    return alert


def _naive_utc(value: datetime) -> datetime:
    from datetime import UTC

    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def get_active_by_type(
    db: Session, equipment: Equipment, alert_type: str
) -> Alert | None:
    """The currently open alert of a given type for a machine, if any."""
    return db.scalar(
        _base(equipment)
        .where(Alert.alert_type == alert_type, Alert.resolved.is_(False))
        .limit(1)
    )


def get_active(db: Session, equipment: Equipment) -> Sequence[Alert]:
    """Every open alert for a machine."""
    return db.scalars(
        _base(equipment).where(Alert.resolved.is_(False)).order_by(Alert.timestamp.desc())
    ).all()


def get_by_alert_id(db: Session, alert_id: str) -> Alert | None:
    """Fetch one alert by its public identifier."""
    return db.scalar(select(Alert).where(Alert.alert_id == alert_id))


def list_alerts(
    db: Session,
    *,
    equipment_id: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    resolved: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[Sequence[Alert], int]:
    """Return a filtered page of alerts plus the unpaginated total.

    Without `equipment_id` the newest alerts across the whole fleet are
    returned, which is what an operator wants on opening the alerts view.
    """
    stmt = select(Alert)
    count_stmt = select(func.count(Alert.id))

    if equipment_id:
        stmt = stmt.join(Equipment, Alert.equipment_db_id == Equipment.id).where(
            func.upper(Equipment.equipment_id) == equipment_id.strip().upper()
        )
        count_stmt = count_stmt.join(Equipment, Alert.equipment_db_id == Equipment.id).where(
            func.upper(Equipment.equipment_id) == equipment_id.strip().upper()
        )
    if severity:
        stmt = stmt.where(Alert.severity == severity)
        count_stmt = count_stmt.where(Alert.severity == severity)
    if alert_type:
        stmt = stmt.where(Alert.alert_type == alert_type)
        count_stmt = count_stmt.where(Alert.alert_type == alert_type)
    if resolved is not None:
        stmt = stmt.where(Alert.resolved.is_(resolved))
        count_stmt = count_stmt.where(Alert.resolved.is_(resolved))

    total = db.scalar(count_stmt) or 0
    stmt = stmt.order_by(Alert.timestamp.desc(), Alert.id.desc()).limit(limit).offset(offset)
    return db.scalars(stmt).all(), total


def resolve(db: Session, alert: Alert, note: str | None = None) -> Alert:
    """Mark an alert resolved."""
    from app.models.base import utcnow

    alert.resolved = True
    alert.resolved_at = utcnow()
    if note:
        # Recorded on the recommendation so the reason for manual closure is
        # not lost; there is no dedicated column for it.
        existing = alert.recommended_action
        alert.recommended_action = f"{existing} | Resolved: {note}" if existing else f"Resolved: {note}"
    db.commit()
    db.refresh(alert)
    return alert


def acknowledge(db: Session, alert: Alert) -> Alert:
    """Mark an alert as seen, without resolving it."""
    alert.acknowledged = True
    db.commit()
    db.refresh(alert)
    return alert


def resolve_by_type(
    db: Session, equipment: Equipment, alert_type: str, note: str | None = None
) -> Alert | None:
    """Resolve the open alert of a type, if one exists."""
    alert = get_active_by_type(db, equipment, alert_type)
    if alert is None:
        return None
    return resolve(db, alert, note)


def counts_by_severity(db: Session, *, resolved: bool | None = False) -> dict[str, int]:
    """Alert counts per severity, in one query, for dashboard badges."""
    stmt = select(Alert.severity, func.count(Alert.id)).group_by(Alert.severity)
    if resolved is not None:
        stmt = stmt.where(Alert.resolved.is_(resolved))
    return {severity: count for severity, count in db.execute(stmt).all()}


def unresolved_total(db: Session) -> int:
    """Total open alerts across the fleet."""
    return db.scalar(
        select(func.count(Alert.id)).where(Alert.resolved.is_(False))
    ) or 0
