"""Alert routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.base import AlertSeverity
from app.repositories import alert as alert_repo
from app.repositories.equipment import get_by_equipment_id
from app.schemas.alert import (
    AlertListResponse,
    AlertRead,
    AlertSummary,
    EvaluateRequest,
    EvaluationResponse,
    ResolveRequest,
)
from app.services import alert_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _to_schema(row, equipment_id: str) -> AlertRead:
    return AlertRead(
        alert_id=row.alert_id,
        equipment_id=equipment_id,
        timestamp=row.timestamp,
        alert_type=row.alert_type,
        severity=row.severity,
        description=row.description,
        recommended_action=row.recommended_action,
        observed_value=row.observed_value,
        threshold_value=row.threshold_value,
        resolved=row.resolved,
        resolved_at=row.resolved_at,
        acknowledged=row.acknowledged,
    )


@router.get("", response_model=AlertListResponse, summary="List alerts")
def list_alerts(  # noqa: PLR0913 - mirrors the alert filter surface
    db: Session = Depends(get_db),
    equipment_id: str | None = Query(None, max_length=32),
    severity: str | None = Query(None, pattern="^(Info|Warning|Critical)$"),
    alert_type: str | None = Query(None, max_length=40),
    resolved: bool | None = Query(None, description="Filter by resolution state"),
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> AlertListResponse:
    """Return alerts, newest first.

    Without `equipment_id` this covers the whole fleet, which is the useful
    default for an operator opening the alerts view.
    """
    settings = get_settings()
    page_size = limit or settings.alert_page_size

    rows, total = alert_repo.list_alerts(
        db,
        equipment_id=equipment_id,
        severity=severity,
        alert_type=alert_type,
        resolved=resolved,
        limit=page_size,
        offset=offset,
    )

    # Resolve the external equipment_id for each row in one query rather than
    # per alert.
    from app.models.equipment import Equipment
    from sqlalchemy import select

    db_ids = {row.equipment_db_id for row in rows}
    names: dict[int, str] = {}
    if db_ids:
        names = {
            equipment.id: equipment.equipment_id
            for equipment in db.scalars(
                select(Equipment).where(Equipment.id.in_(db_ids))
            ).all()
        }

    return AlertListResponse(
        items=[_to_schema(row, names.get(row.equipment_db_id, "?")) for row in rows],
        total=total,
        limit=page_size,
        offset=offset,
        counts_by_severity=alert_repo.counts_by_severity(db, resolved=False),
        unresolved_total=alert_repo.unresolved_total(db),
    )


@router.get("/summary", response_model=AlertSummary, summary="Fleet alert counts")
def alert_summary(db: Session = Depends(get_db)) -> AlertSummary:
    """Counts for dashboard badges."""
    counts = alert_repo.counts_by_severity(db, resolved=False)
    return AlertSummary(
        unresolved_total=alert_repo.unresolved_total(db),
        counts_by_severity=counts,
        critical=counts.get(AlertSeverity.CRITICAL.value, 0),
        warning=counts.get(AlertSeverity.WARNING.value, 0),
        info=counts.get(AlertSeverity.INFO.value, 0),
    )


@router.post(
    "/evaluate",
    response_model=EvaluationResponse,
    summary="Evaluate alert rules",
)
def evaluate_alerts(
    payload: EvaluateRequest | None = None, db: Session = Depends(get_db)
) -> EvaluationResponse:
    """Run the rules engine and reconcile alerts with current state.

    Opens alerts for conditions that have newly fired and, when auto-resolution
    is enabled, closes alerts whose condition has cleared.
    """
    request = payload or EvaluateRequest()

    if request.equipment_id:
        equipment = get_by_equipment_id(db, request.equipment_id)
        if equipment is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Equipment '{request.equipment_id}' not found",
            )
        opened, resolved = alert_service.evaluate_equipment(
            db, equipment, window=request.window
        )
        return EvaluationResponse(
            equipment_id=equipment.equipment_id,
            opened=len(opened),
            resolved=len(resolved),
            skipped=0,
            opened_alerts=[_to_schema(row, equipment.equipment_id) for row in opened],
        )

    result = alert_service.evaluate_fleet(db, window=request.window)
    return EvaluationResponse(
        equipment_id=None, opened=result["opened"], resolved=result["resolved"],
        skipped=result["skipped"], opened_alerts=[],
    )


@router.post(
    "/{alert_id}/resolve",
    response_model=AlertRead,
    responses={404: {"description": "Alert not found"}},
    summary="Resolve an alert",
)
def resolve_alert(
    alert_id: str,
    payload: ResolveRequest | None = None,
    db: Session = Depends(get_db),
) -> AlertRead:
    """Mark an alert resolved, optionally recording why."""
    row = alert_repo.get_by_alert_id(db, alert_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert '{alert_id}' not found")

    note = payload.note if payload else None
    resolved = alert_repo.resolve(db, row, note)

    equipment = get_by_equipment_id(
        db, _equipment_id_for(db, resolved.equipment_db_id)
    )
    return _to_schema(resolved, equipment.equipment_id if equipment else "?")


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AlertRead,
    responses={404: {"description": "Alert not found"}},
    summary="Acknowledge an alert",
)
def acknowledge_alert(alert_id: str, db: Session = Depends(get_db)) -> AlertRead:
    """Mark an alert as seen without resolving it."""
    row = alert_repo.get_by_alert_id(db, alert_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert '{alert_id}' not found")

    acknowledged = alert_repo.acknowledge(db, row)
    equipment = get_by_equipment_id(db, _equipment_id_for(db, acknowledged.equipment_db_id))
    return _to_schema(acknowledged, equipment.equipment_id if equipment else "?")


def _equipment_id_for(db: Session, equipment_db_id: int) -> str:
    from app.models.equipment import Equipment

    equipment = db.get(Equipment, equipment_db_id)
    return equipment.equipment_id if equipment else ""
