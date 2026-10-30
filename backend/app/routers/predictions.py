"""Risk prediction routes."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES, get_settings
from app.database import get_db
from app.ml.baselines import get_profile
from app.ml.risk import DEFAULT_HORIZONS
from app.repositories.equipment import EquipmentNotFoundError, get_by_equipment_id
from app.schemas.risk import EquipmentRiskResponse, PredictionRequest, PredictionResponse
from app.services import anomaly_service, calibration_service, health_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["prediction"])


def _not_found(exc: EquipmentNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post(
    "/predict",
    response_model=PredictionResponse,
    summary="Estimate failure risk",
)
def predict(
    payload: PredictionRequest, db: Session = Depends(get_db)
) -> PredictionResponse:
    """Estimate the probability of failure within a stated horizon.

    The response always states the horizon and whether the underlying hazard
    rate has been calibrated against real failure data. An uncalibrated number
    is still useful for ranking machines, but must not be read as a measured
    failure frequency.
    """
    values = payload.channel_values()
    equipment = None

    if payload.equipment_id:
        try:
            equipment = get_by_equipment_id(db, payload.equipment_id)
        except Exception:  # noqa: BLE001 - normalised below
            equipment = None
        if equipment is None:
            raise _not_found(
                EquipmentNotFoundError(f"Equipment '{payload.equipment_id}' not found")
            )
        equipment_type = equipment.equipment_type
    else:
        equipment_type = payload.equipment_type

    anomaly_score = payload.anomaly_score
    if anomaly_score is None and payload.equipment_id:
        result, _ = anomaly_service.detect(
            db,
            anomaly_service.AnomalyDetectRequest(
                equipment_id=payload.equipment_id, **values
            ),
        )
        anomaly_score = result.anomaly_score

    profile = get_profile(equipment_type)
    estimate = health_service.estimate_risk_detail(
        values, profile, anomaly_score or 0.0, db=db
    )
    partial_score, partial_status, _ = health_service.preview_assessment(
        values, profile, anomaly_score or 0.0, db
    )

    return PredictionResponse(
        equipment_id=payload.equipment_id,
        equipment_type=equipment_type,
        health_score=partial_score,
        status=partial_status,
        failure_probability=estimate.probability,
        risk_level=estimate.risk_level,
        horizon_hours=payload.horizon_hours,
        horizon_probabilities={
            str(hours): value for hours, value in estimate.horizon_probabilities.items()
        },
        dominant_driver=estimate.dominant_driver,
        drivers=estimate.drivers,
        method=estimate.method,
        calibrated=estimate.calibrated,
        notes=estimate.notes,
        generated_at=datetime.now(UTC),
    )


@router.get(
    "/equipment/{equipment_id}/risk",
    response_model=EquipmentRiskResponse,
    responses={404: {"description": "Equipment not found or has no telemetry"}},
    summary="Failure risk from stored telemetry",
)
def equipment_risk(
    equipment_id: str,
    window: int = Query(60, ge=20, le=2000),
    db: Session = Depends(get_db),
) -> EquipmentRiskResponse:
    """Risk for a machine, assessed from its most recent readings."""
    from app.services import sensor_service

    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise _not_found(EquipmentNotFoundError(f"Equipment '{equipment_id}' not found"))

    readings = sensor_service.get_recent(db, equipment_id, count=window)
    if not readings:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No telemetry recorded for '{equipment_id}'"
        )

    latest = readings[-1]
    values = {c: getattr(latest, c) for c in SENSOR_FEATURES}

    anomaly_score = 0.0
    detector = anomaly_service.get_detector()
    if detector is not None:
        from app.ml.detector import align_with_readings

        results = detector.detect_series(readings, equipment.equipment_type)
        pairs = align_with_readings(readings, results)
        if pairs:
            anomaly_score = pairs[-1][1].anomaly_score

    profile = get_profile(equipment.equipment_type)
    estimate = health_service.estimate_risk_detail(
        values, profile, anomaly_score, db=db
    )

    from app.repositories import health_score as score_repo

    stored = score_repo.latest(db, equipment)

    return EquipmentRiskResponse(
        equipment_id=equipment.equipment_id,
        equipment_type=equipment.equipment_type,
        health_score=stored.health_score if stored else None,
        status=stored.status if stored else None,
        failure_probability=estimate.probability,
        risk_level=estimate.risk_level,
        horizon_hours=estimate.horizon_hours,
        horizon_probabilities={
            str(hours): value for hours, value in estimate.horizon_probabilities.items()
        },
        dominant_driver=estimate.dominant_driver,
        drivers=estimate.drivers,
        method=estimate.method,
        calibrated=estimate.calibrated,
        notes=estimate.notes,
        assessed_at=stored.timestamp if stored else latest.timestamp,
    )


@router.get("/predict/model-info", summary="Risk model provenance")
def model_info(db: Session = Depends(get_db)) -> dict:
    """The risk model's configuration and calibration state.

    Exposed so an operator can see at a glance whether a reported probability
    is fitted to real failure data or an uncalibrated index.
    """
    settings = get_settings()
    horizons = list(DEFAULT_HORIZONS)
    # Resolved from the persisted calibration, not read from settings, so this
    # endpoint cannot claim a state that disagrees with `/predict/calibration`.
    effective_rate, calibrated = calibration_service.effective_calibration(db)
    return {
        "method": "degradation_hazard",
        "description": (
            "Wear-out hazard model. Per-channel hazard is lambda0 * exp(k * "
            "degradation), aggregated by importance weight, converted to a "
            "probability over the stated horizon."
        ),
        "calibrated": calibrated,
        "base_hazard_per_hour": effective_rate,
        "implied_mtbf_hours": round(1.0 / effective_rate, 1),
        "hazard_sensitivity": settings.risk_hazard_sensitivity,
        "anomaly_multiplier": settings.risk_anomaly_multiplier,
        "horizons_hours": horizons,
        "horizon_labels": {
            str(24): "1 day",
            str(168): "1 week",
            str(720): "30 days",
        },
        "risk_thresholds": {
            "moderate_min": settings.risk_moderate_min,
            "high_min": settings.risk_high_min,
            "severe_min": settings.risk_severe_min,
        },
        "caveat": (
            "The base hazard rate is a placeholder, not fitted to failure data. "
            "Probabilities are a relative index until calibrated."
        )
        if not calibrated
        else "Base hazard rate fitted to fleet failure history.",
    }
