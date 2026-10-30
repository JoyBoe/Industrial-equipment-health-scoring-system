"""Dashboard summary.

A single endpoint for the KPI row, so the dashboard's first paint is one
request rather than one per statistic. Each count is derived from the cached
health columns on `equipment`, which the scoring service keeps current.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.alert import Alert
from app.models.base import AlertSeverity, HealthStatus
from app.models.equipment import Equipment
from app.models.health_score import HealthScore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", summary="Fleet KPIs for the dashboard")
def get_summary(db: Session = Depends(get_db)) -> dict:
    """Counts, average score, and the machines needing attention.

    Machines that have never been assessed are reported separately rather than
    folded into a health band, so an unscored fleet is visibly unscored instead
    of looking healthy.
    """
    total = db.scalar(
        select(func.count(Equipment.id)).where(Equipment.is_active.is_(True))
    ) or 0

    status_rows = db.execute(
        select(Equipment.status, func.count(Equipment.id))
        .where(Equipment.is_active.is_(True))
        .group_by(Equipment.status)
    ).all()
    by_status = {status: count for status, count in status_rows if status}

    assessed = db.scalar(
        select(func.count(Equipment.id)).where(
            Equipment.is_active.is_(True), Equipment.health_score.isnot(None)
        )
    ) or 0

    average = db.scalar(
        select(func.avg(Equipment.health_score)).where(
            Equipment.is_active.is_(True), Equipment.health_score.isnot(None)
        )
    )

    # Worst machines first, so the dashboard can show a "needs attention" list
    # without a second request.
    needs_attention = db.scalars(
        select(Equipment)
        .where(
            Equipment.is_active.is_(True),
            Equipment.status.in_(
                [
                    HealthStatus.WARNING.value,
                    HealthStatus.POOR.value,
                    HealthStatus.CRITICAL.value,
                ]
            ),
        )
        .order_by(Equipment.health_score.asc())
        .limit(5)
    ).all()

    unread = db.scalar(select(func.count(Alert.id)).where(Alert.resolved.is_(False))) or 0
    severity_rows = db.execute(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.resolved.is_(False))
        .group_by(Alert.severity)
    ).all()
    by_severity = {severity: count for severity, count in severity_rows}

    last_assessed = db.scalar(
        select(func.max(HealthScore.timestamp))
    )

    return {
        "total_equipment": total,
        "assessed_equipment": assessed,
        "unassessed_equipment": total - assessed,
        "average_health_score": round(float(average), 1) if average is not None else None,
        "counts_by_status": {
            status: by_status.get(status, 0)
            for status in (
                HealthStatus.EXCELLENT.value,
                HealthStatus.HEALTHY.value,
                HealthStatus.WARNING.value,
                HealthStatus.POOR.value,
                HealthStatus.CRITICAL.value,
            )
        },
        "open_alerts": unread,
        "alerts_by_severity": {
            severity: by_severity.get(severity, 0)
            for severity in (
                AlertSeverity.CRITICAL.value,
                AlertSeverity.WARNING.value,
                AlertSeverity.INFO.value,
            )
        },
        "needs_attention": [
            {
                "equipment_id": row.equipment_id,
                "equipment_name": row.equipment_name,
                "equipment_type": row.equipment_type,
                "location": row.location,
                "health_score": row.health_score,
                "status": row.status,
                "risk_level": row.risk_level,
                "last_updated": row.last_updated,
            }
            for row in needs_attention
        ],
        "last_assessed_at": last_assessed,
        "generated_at": datetime.now(UTC),
    }
