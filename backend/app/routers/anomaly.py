"""Anomaly detection routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.equipment import EquipmentNotFoundError
from app.schemas.anomaly import (
    AnomalyDetectRequest,
    AnomalyRecordRead,
    AnomalyResponse,
    AnomalySummary,
)
from app.services import anomaly_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/anomaly", tags=["anomaly"])


def _not_found(exc: EquipmentNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post(
    "/detect",
    response_model=AnomalyResponse,
    summary="Assess a single sensor sample for anomalies",
)
def detect(
    payload: AnomalyDetectRequest, db: Session = Depends(get_db)
) -> AnomalyResponse:
    """Score one sample and report which parameters drove the result.

    Supply `equipment_id` to use that machine's own baseline, or
    `equipment_type` to score against a type envelope. Nothing is persisted
    unless `persist` is set, so this is safe to use for ad-hoc checks.
    """
    try:
        result, equipment_type = anomaly_service.detect(
            db,
            payload,
            # Ad-hoc checks must not pollute the assessment history, so only a
            # request naming a real machine is persisted.
            persist=payload.equipment_id is not None,
        )
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc

    detector = anomaly_service.get_detector()
    return AnomalyResponse(
        equipment_id=payload.equipment_id,
        equipment_type=equipment_type,
        timestamp=anomaly_service.now_utc(),
        anomaly_score=result.anomaly_score,
        anomaly_detected=result.anomaly_detected,
        threshold=detector.threshold if detector else 0.5,
        affected_parameters=result.affected_parameters,
        parameter_scores=result.parameter_scores,
        method=result.method,
        model_version=result.model_version,
        detail=result.detail,
    )


@router.post(
    "/detect/{equipment_id}",
    response_model=AnomalySummary,
    summary="Assess a machine's recent history",
)
def detect_history(
    equipment_id: str,
    limit: int = Query(300, ge=20, le=2000),
    db: Session = Depends(get_db),
) -> AnomalySummary:
    """Score the machine's recent telemetry and store the results.

    This is the path that uses the drift detector, since a sustained change can
    only be recognised from ordered history.
    """
    try:
        anomaly_service.detect_history(db, equipment_id, limit=limit)
        summary = anomaly_service.get_summary(db, equipment_id)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc
    return AnomalySummary(**summary)


@router.get(
    "/{equipment_id}",
    response_model=list[AnomalyRecordRead],
    responses={404: {"description": "Equipment not found"}},
    summary="Anomaly history",
)
def anomaly_history(
    equipment_id: str,
    detected_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[AnomalyRecordRead]:
    """Stored assessments for one machine, newest first."""
    from app.repositories import anomaly as anomaly_repo
    from app.repositories.equipment import get_by_equipment_id

    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise _not_found(EquipmentNotFoundError(f"Equipment '{equipment_id}' not found"))

    rows = anomaly_repo.list_for_equipment(
        db, equipment, detected_only=detected_only, limit=limit, offset=offset
    )
    return [
        AnomalyRecordRead(
            id=row.id,
            equipment_id=equipment.equipment_id,
            timestamp=row.timestamp,
            anomaly_score=row.anomaly_score,
            anomaly_detected=row.anomaly_detected,
            affected_parameters=row.affected_parameters or [],
            parameter_scores=row.parameter_scores,
            method=row.method,
            model_version=row.model_version,
        )
        for row in rows
    ]


@router.get(
    "/{equipment_id}/summary",
    response_model=AnomalySummary,
    responses={404: {"description": "Equipment not found"}},
    summary="Aggregate anomaly statistics",
)
def anomaly_summary(
    equipment_id: str, db: Session = Depends(get_db)
) -> AnomalySummary:
    """Detection rate, score range, and the most frequently implicated channel."""
    try:
        return AnomalySummary(**anomaly_service.get_summary(db, equipment_id))
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc
