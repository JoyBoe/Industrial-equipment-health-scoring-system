"""Health scoring routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.equipment import EquipmentNotFoundError
from app.schemas.health import (
    BatchScoreResponse,
    HealthHistoryResponse,
    HealthScoreRequest,
    HealthScoreResponse,
    HealthTrendResponse,
    TrendPoint,
)
from app.services import calibration_service, health_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def _not_found(exc: EquipmentNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


def _to_response(
    assessment, equipment_id: str | None, equipment_type: str, db: Session | None = None
) -> HealthScoreResponse:
    # Resolved from the live calibration record rather than the schema default,
    # so a persisted fit is reflected immediately and an operator cannot see a
    # "calibrated" claim that disagrees with what the risk number was computed
    # from.
    _, calibrated = calibration_service.effective_calibration(db)
    return HealthScoreResponse(
        equipment_id=equipment_id,
        equipment_type=equipment_type,
        health_score=assessment.health_score,
        status=assessment.status,
        risk_level=assessment.risk_level,
        anomaly_score=assessment.anomaly_score,
        anomaly_detected=assessment.anomaly_detected,
        failure_probability=assessment.failure_probability,
        calibrated=calibrated,
        contributing_factors=assessment.contributing_factors,
        explanation=assessment.explanation,
        recommendation=assessment.recommendation,
        score_breakdown=assessment.breakdown.as_dict(),
    )


@router.post(
    "/health/score",
    response_model=HealthScoreResponse,
    summary="Score sensor values",
)
def score_health(
    payload: HealthScoreRequest, db: Session = Depends(get_db)
) -> HealthScoreResponse:
    """Compute a health score from sensor values.

    Supply `equipment_id` to use that machine's baseline and record the
    assessment, or `equipment_type` to score without persisting.
    """
    try:
        assessment = health_service.score_adhoc(db, payload)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc

    equipment_type = payload.equipment_type
    if payload.equipment_id:
        from app.repositories.equipment import get_by_equipment_id

        equipment = get_by_equipment_id(db, payload.equipment_id)
        equipment_type = equipment.equipment_type if equipment else equipment_type

    return _to_response(assessment, payload.equipment_id, equipment_type or "Generic", db)


@router.get(
    "/equipment/{equipment_id}/health",
    response_model=HealthHistoryResponse,
    responses={404: {"description": "Equipment not found or never assessed"}},
    summary="Latest health assessment",
)
def get_health(
    equipment_id: str, db: Session = Depends(get_db)
) -> HealthHistoryResponse:
    """The most recent stored assessment, with the change since the previous one."""
    try:
        body = health_service.get_current_health(db, equipment_id)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc
    if body is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No health assessment recorded for '{equipment_id}'",
        )
    return HealthHistoryResponse(
        **body,
        calibrated=calibration_service.effective_calibration(db)[1],
    )


@router.get(
    "/equipment/{equipment_id}/health/trend",
    response_model=HealthTrendResponse,
    responses={404: {"description": "Equipment not found"}},
    summary="Health score history",
)
def get_health_trend(
    equipment_id: str,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> HealthTrendResponse:
    """Score history for charting, oldest first."""
    try:
        points = health_service.get_trend(db, equipment_id, limit=limit)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc

    scores = [p["health_score"] for p in points]
    return HealthTrendResponse(
        equipment_id=equipment_id.upper(),
        points=[TrendPoint(**p) for p in points],
        count=len(points),
        latest=scores[-1] if scores else None,
        first=scores[0] if scores else None,
        min_score=min(scores) if scores else None,
        max_score=max(scores) if scores else None,
        average_score=round(sum(scores) / len(scores), 2) if scores else None,
    )


@router.post(
    "/equipment/{equipment_id}/health/assess",
    response_model=HealthScoreResponse,
    responses={404: {"description": "Equipment not found or has no telemetry"}},
    summary="Recompute health from stored telemetry",
)
def assess_equipment(
    equipment_id: str,
    window: int = Query(60, ge=20, le=2000, description="Recent readings to consider"),
    db: Session = Depends(get_db),
) -> HealthScoreResponse:
    """Score a machine from its recent readings and store the result.

    Unlike `/health/score`, this uses history, so the drift and anomaly
    detectors contribute.
    """
    try:
        assessment = health_service.assess_equipment(db, equipment_id, window=window)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc
    if assessment is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No telemetry recorded for '{equipment_id}'",
        )

    from app.repositories.equipment import get_by_equipment_id

    equipment = get_by_equipment_id(db, equipment_id)
    equipment_type = equipment.equipment_type if equipment else "Generic"
    return _to_response(assessment, equipment.equipment_id, equipment_type, db)


@router.post(
    "/health/score-all",
    response_model=BatchScoreResponse,
    summary="Score the whole fleet",
)
def score_all(
    window: int = Query(60, ge=20, le=2000),
    strategy: str = Query(
        "latest",
        pattern="^(latest|recent_worst)$",
        description=(
            "'latest' scores the newest reading; 'recent_worst' scores the whole "
            "window and keeps the worst result, which is what a fleet summary "
            "should show"
        ),
    ),
    db: Session = Depends(get_db),
) -> BatchScoreResponse:
    """Assess every active machine from its telemetry.

    A machine that fails to assess is counted and skipped rather than aborting
    the batch; one bad unit must not leave the fleet unrefreshed.
    """
    return BatchScoreResponse(
        **health_service.score_all(db, window=window, strategy=strategy)
    )


@router.get("/health/thresholds", summary="Configured scoring thresholds")
def get_thresholds() -> dict:
    """The thresholds and weights in force, so a score can be reproduced."""
    from app.config import get_settings

    settings = get_settings()
    return {
        "status_thresholds": {
            "excellent_min": settings.excellent_min,
            "healthy_min": settings.healthy_min,
            "warning_min": settings.warning_min,
            "poor_min": settings.poor_min,
        },
        "risk_thresholds": {
            "moderate_min": settings.risk_moderate_min,
            "high_min": settings.risk_high_min,
            "severe_min": settings.risk_severe_min,
        },
        "point_budgets": {
            "sensor": settings.sensor_points_budget,
            "anomaly": settings.anomaly_points_budget,
            "risk": settings.risk_points_budget,
        },
        "weights": {
            **settings.parameter_weights,
            "anomaly": settings.anomaly_weight,
            "risk": settings.risk_weight,
        },
    }
