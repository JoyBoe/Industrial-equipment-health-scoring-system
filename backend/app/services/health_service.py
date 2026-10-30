"""Health scoring service.

Orchestrates the pipeline: resolve the machine's baseline profile, obtain the
anomaly score, estimate risk, compute the score, explain it, and persist the
result while refreshing the denormalised cache on `equipment`.

Risk estimation lives behind `estimate_risk` and is deliberately a documented
model rather than a learned one: with no failure history the base hazard is a
placeholder, so the result is a relative index and says so. The interface here
is what stays stable, not the implementation -- a supervised estimator can
replace it without anything above this layer changing.
"""

import logging
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES, get_settings
from app.ml.baselines import get_profile
from app.ml.detector import MODEL_VERSION, align_with_readings
from app.ml.risk import DegradationHazardEstimator, RiskEstimate
from app.ml.scoring import HealthAssessment, compute_health_score
from app.models.equipment import Equipment
from app.repositories import health_score as score_repo
from app.repositories.equipment import EquipmentNotFoundError, get_by_equipment_id
from app.schemas.health import HealthScoreRequest
from app.services import anomaly_service
from app.services.explainability import build_assessment

logger = logging.getLogger(__name__)

#: Number of recent readings used to assess a machine's current state.
ASSESSMENT_WINDOW = 60


def _require_equipment(db: Session, equipment_id: str) -> Equipment:
    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise EquipmentNotFoundError(f"Equipment '{equipment_id}' not found")
    return equipment


@lru_cache(maxsize=4)
def _cached_estimator(base_hazard: float, calibrated: bool) -> DegradationHazardEstimator:
    """Build an estimator for one resolved calibration.

    Cached on the two values that can actually change it: the estimator is
    stateless per call, so a recalibration is the only event that should
    produce a new one. Four slots is ample -- one per distinct rate seen.
    """
    settings = get_settings()
    return DegradationHazardEstimator(
        base_hazard_per_hour=base_hazard,
        sensitivity=settings.risk_hazard_sensitivity,
        anomaly_multiplier=settings.risk_anomaly_multiplier,
        calibrated=calibrated,
    )


def get_risk_estimator(db: Session | None = None) -> DegradationHazardEstimator:
    """Return the estimator configured for this instance's calibration.

    Resolution happens here rather than in `ml/`, which must not touch a
    database. Callers without a session get the environment's answer, which
    keeps pure model tests and ad-hoc scripts on the uncalibrated path.
    """
    from app.services import calibration_service

    base_hazard, calibrated = calibration_service.effective_calibration(db)
    return _cached_estimator(base_hazard, calibrated)


def estimate_risk_detail(
    values: dict[str, float | None],
    profile,
    anomaly_score: float = 0.0,
    health_score: float = 100.0,
    db: Session | None = None,
) -> RiskEstimate:
    """Estimate failure risk with a stated horizon and driver attribution.

    Wraps the wear-out hazard model in `ml.risk`. The result is always
    horizon-specific and always states whether the underlying rate has been
    calibrated, because an uncalibrated number presented as a failure
    frequency would be a fabrication.

    `db` is what lets a fitted rate reach the estimate. Omitting it is safe,
    not silently wrong: the estimate then reports the environment's state,
    which is honestly `calibrated=False` until something has been fitted.
    """
    from app.ml.degradation import evaluate_all
    from app.ml.scoring import normalise_weights
    from app.config import get_settings

    settings = get_settings()
    degradations = evaluate_all(values, profile)
    active = [n for n, d in degradations.items() if d.direction != "unknown"]
    weights = normalise_weights(settings.parameter_weights, active)

    return get_risk_estimator(db).estimate(
        degradations, weights, anomaly_score, health_score, settings
    )


def estimate_risk(
    values: dict[str, float | None],
    health_score: float,
    anomaly_score: float,
    profile,
    db: Session | None = None,
) -> float:
    """Failure probability over the default horizon, for the health score.

    Thin wrapper over `estimate_risk_detail`; the scorer only needs the
    single-horizon number, and the horizon is always the default 24 h.
    """
    return estimate_risk_detail(
        values, profile, anomaly_score, health_score, db
    ).probability


def preview_assessment(
    values: dict[str, float | None],
    profile,
    anomaly_score: float = 0.0,
    db: Session | None = None,
) -> tuple[float, str, float]:
    """Return (health_score, status, failure_probability) without persisting.

    Used by the prediction endpoint, which needs the score for context but must
    not create an assessment record: a risk query is not a health assessment.
    """
    from app.ml.scoring import apply_status_caps, classify_status

    partial, _ = compute_health_score(
        values, profile, anomaly_score=anomaly_score, failure_probability=0.0
    )
    probability = estimate_risk(
        values, partial.health_score, anomaly_score, profile, db
    )
    breakdown, _ = compute_health_score(
        values, profile, anomaly_score=anomaly_score, failure_probability=probability
    )
    status = apply_status_caps(
        classify_status(breakdown.health_score), anomaly_score, breakdown.risk_level
    )
    return breakdown.health_score, status, breakdown.failure_probability


def score_reading(
    db: Session,
    equipment: Equipment,
    values: dict[str, float | None],
    *,
    anomaly_score: float = 0.0,
    anomaly_detected: bool = False,
    persist: bool = True,
    previous_score: float | None = None,
    source_reading_id: int | None = None,
    timestamp: datetime | None = None,
) -> HealthAssessment:
    """Score one set of sensor values for a machine and optionally persist it."""
    settings = get_settings()
    profile = get_profile(equipment.equipment_type)

    if previous_score is None and persist:
        previous = score_repo.latest(db, equipment)
        previous_score = previous.health_score if previous else None

    # The score depends on the failure probability, and the probability depends
    # on the score: break the cycle by computing the sensor/anomaly part first.
    partial_breakdown, factors = compute_health_score(
        values,
        profile,
        anomaly_score=anomaly_score,
        failure_probability=0.0,
        settings=settings,
    )
    failure_probability = estimate_risk(
        values, partial_breakdown.health_score, anomaly_score, profile, db
    )

    breakdown, factors = compute_health_score(
        values,
        profile,
        anomaly_score=anomaly_score,
        failure_probability=failure_probability,
        settings=settings,
    )

    assessment = build_assessment(
        breakdown,
        factors,
        anomaly_detected=anomaly_detected,
        previous_score=previous_score,
    )

    if persist:
        row = score_repo.create(
            db,
            equipment,
            health_score=assessment.health_score,
            status=assessment.status,
            risk_level=assessment.risk_level,
            anomaly_score=assessment.anomaly_score,
            failure_probability=assessment.failure_probability,
            score_breakdown=breakdown.as_dict(),
            contributing_factors=factors,
            explanation=assessment.explanation,
            recommendation=assessment.recommendation,
            model_version=MODEL_VERSION,
            source_reading_id=source_reading_id,
            timestamp=timestamp,
        )
        _refresh_equipment_cache(db, equipment, assessment, row.timestamp)
        db.commit()

    return assessment


def score_adhoc(
    db: Session, payload: HealthScoreRequest
) -> HealthAssessment:
    """Score values supplied in the request body.

    Nothing is persisted unless the caller names a real machine, matching the
    behaviour of the anomaly endpoint.
    """
    values = payload.channel_values()
    persist = False

    if payload.equipment_id:
        equipment = _require_equipment(db, payload.equipment_id)
        equipment_type = equipment.equipment_type
        persist = payload.persist
    else:
        equipment_type = payload.equipment_type  # type: ignore[assignment]
        equipment = None

    anomaly_score = 0.0
    anomaly_detected = False
    if payload.equipment_id:
        result, _ = anomaly_service.detect(
            db,
            anomaly_service.AnomalyDetectRequest(
                equipment_id=payload.equipment_id, **values
            ),
        )
        anomaly_score = result.anomaly_score
        anomaly_detected = result.anomaly_detected

    if equipment is None:
        # No machine to persist against: score without saving.
        settings = get_settings()
        profile = get_profile(equipment_type)
        partial, _ = compute_health_score(
            values, profile, anomaly_score=anomaly_score, failure_probability=0.0
        )
        probability = estimate_risk(
            values, partial.health_score, anomaly_score, profile, db
        )
        breakdown, factors = compute_health_score(
            values, profile, anomaly_score=anomaly_score, failure_probability=probability
        )
        return build_assessment(
            breakdown, factors, anomaly_detected=anomaly_detected
        )

    return score_reading(
        db,
        equipment,
        values,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_detected,
        persist=persist,
    )


def _refresh_equipment_cache(
    db: Session, equipment: Equipment, assessment: HealthAssessment, timestamp: datetime
) -> None:
    """Update the denormalised health columns on `equipment`.

    These exist so the dashboard table renders without a correlated subquery
    per row. They are a cache of the latest score, never the source of truth.
    """
    equipment.health_score = assessment.health_score
    equipment.status = assessment.status
    equipment.risk_level = assessment.risk_level
    equipment.failure_probability = assessment.failure_probability
    equipment.last_assessed_at = timestamp
    equipment.last_updated = timestamp


def assess_equipment(
    db: Session, equipment_id: str, window: int = ASSESSMENT_WINDOW
) -> HealthAssessment | None:
    """Score a machine from its most recent readings.

    Uses history rather than a single sample so the drift and anomaly detectors
    contribute. Returns None when the machine has no telemetry.
    """
    from app.services import sensor_service

    equipment = _require_equipment(db, equipment_id)
    readings = sensor_service.get_recent(db, equipment_id, count=window)
    if not readings:
        return None

    latest_reading = readings[-1]
    values = {c: getattr(latest_reading, c) for c in SENSOR_FEATURES}

    anomaly_score = 0.0
    anomaly_detected = False
    detector = anomaly_service.get_detector()
    if detector is not None:
        results = detector.detect_series(readings, equipment.equipment_type)
        pairs = align_with_readings(readings, results)
        if pairs:
            _, final = pairs[-1]
            anomaly_score = final.anomaly_score
            anomaly_detected = final.anomaly_detected

    return score_reading(
        db,
        equipment,
        values,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_detected,
        source_reading_id=latest_reading.id,
        timestamp=latest_reading.timestamp,
    )


def worst_reading_in_window(
    db: Session, equipment_id: str, window: int = ASSESSMENT_WINDOW
) -> tuple:
    """The reading in the window with the lowest health score.

    Returned as ``(reading, values, anomaly_score, anomaly_detected)``.

    Shared by the alert engine, the explainability service, and the detail
    view's reassess action. Those three previously each chose their own reading
    -- the alert engine used the worst, the others used the newest -- and the
    result was a machine with an open Critical vibration alert whose detail page
    read "Excellent, no action required". Sharing one function is what stops
    them diverging again.
    """
    from app.services import sensor_service

    equipment = _require_equipment(db, equipment_id)
    readings = sensor_service.get_recent(db, equipment_id, count=window)
    if not readings:
        return None

    profile = get_profile(equipment.equipment_type)

    # Anomaly scores need the ordered history for the drift detector, so the
    # series is scored once and the results attached per reading.
    anomaly_by_id: dict[int, tuple[float, bool]] = {}
    detector = anomaly_service.get_detector()
    if detector is not None:
        results = detector.detect_series(readings, equipment.equipment_type)
        for reading, result in align_with_readings(readings, results):
            anomaly_by_id[reading.id] = (result.anomaly_score, result.anomaly_detected)

    worst = None
    worst_score = None
    for reading in readings:
        values = {c: getattr(reading, c) for c in SENSOR_FEATURES}
        anomaly_score, anomaly_detected = anomaly_by_id.get(reading.id, (0.0, False))
        score, _, _ = preview_assessment(values, profile, anomaly_score, db)
        if worst_score is None or score < worst_score:
            worst_score = score
            worst = (reading, values, anomaly_score, anomaly_detected)

    return worst


def get_current_health(db: Session, equipment_id: str) -> dict | None:
    """Latest stored assessment, without recomputing."""
    equipment = _require_equipment(db, equipment_id)
    row = score_repo.latest(db, equipment)
    if row is None:
        return None

    previous = score_repo.previous(db, equipment)
    return {
        "equipment_id": equipment.equipment_id,
        "health_score": row.health_score,
        "status": row.status,
        "risk_level": row.risk_level,
        "anomaly_score": row.anomaly_score,
        "failure_probability": row.failure_probability,
        "contributing_factors": row.contributing_factors or [],
        "explanation": row.explanation,
        "recommendation": row.recommendation,
        "timestamp": row.timestamp,
        "previous_score": previous.health_score if previous else None,
        "delta": (
            round(row.health_score - previous.health_score, 2) if previous else None
        ),
    }


def get_trend(db: Session, equipment_id: str, limit: int = 200) -> list[dict]:
    """Score history for charting, oldest first."""
    _require_equipment(db, equipment_id)
    rows = score_repo.history(db, _require_equipment(db, equipment_id), limit=limit)
    return [
        {
            "timestamp": row.timestamp,
            "health_score": row.health_score,
            "status": row.status,
            "risk_level": row.risk_level,
            "anomaly_score": row.anomaly_score,
        }
        for row in rows
    ]


def score_all(
    db: Session,
    window: int = ASSESSMENT_WINDOW,
    strategy: str = "latest",
) -> dict[str, int]:
    """Assess every active machine, for a batch refresh.

    `strategy` selects what the fleet view reports:

    ``latest``
        Score the newest reading only. This is the instantaneous condition and
        the simplest definition of "how is it now".
    ``recent_worst``
        Score across the window and keep the worst result. It does not let a
        degradation hide behind a recovered latest reading, and is the right
        choice for ranking a fleet: measured on the seeded fleet at 1440 minutes
        it spans 27.05 points, versus 0.59 for ``latest`` (where every machine
        is genuinely nominal at its newest sample).

    Assessments from a ``recent_worst`` pass are not persisted, so the stored
    history remains a record of what the machine's instantaneous condition
    actually was at each point in time.

    Each machine is assessed independently, so this issues several queries per
    unit. The batched helpers in the repositories (``fleet_latest``,
    ``fleet_counts``, ``latest_timestamps``) exist to remove that N+1 but are
    not wired up; at the current fleet sizes the cost is not worth the
    regression risk.
    """
    from app.repositories.equipment import list_equipment

    if strategy not in {"latest", "recent_worst"}:
        raise ValueError(f"Unknown scoring strategy '{strategy}'")

    rows, _ = list_equipment(db, active_only=True, limit=500)
    scored = failed = 0
    for equipment in rows:
        try:
            if strategy == "recent_worst":
                if assess_recent_worst(db, equipment.equipment_id, window=window) is not None:
                    scored += 1
                else:
                    failed += 1
            elif assess_equipment(db, equipment.equipment_id, window=window) is not None:
                scored += 1
            else:
                failed += 1
        except Exception:  # noqa: BLE001 - one machine must not stop the batch
            failed += 1
            logger.exception("Failed to assess %s", equipment.equipment_id)
    return {"scored": scored, "skipped": failed}


def assess_recent_worst(
    db: Session, equipment_id: str, window: int = ASSESSMENT_WINDOW
) -> HealthAssessment | None:
    """Return the worst assessment across a recent window.

    Scores every reading in the window and keeps the lowest. Anomaly scoring
    runs once over the series so the drift detector still sees the history,
    and each reading is then scored against its own anomaly result.
    """
    from app.services import sensor_service

    equipment = _require_equipment(db, equipment_id)
    readings = sensor_service.get_recent(db, equipment_id, count=window)
    if not readings:
        return None

    anomaly_by_reading: dict[int, tuple[float, bool]] = {}
    detector = anomaly_service.get_detector()
    if detector is not None:
        results = detector.detect_series(readings, equipment.equipment_type)
        for reading, result in align_with_readings(readings, results):
            anomaly_by_reading[reading.id] = (result.anomaly_score, result.anomaly_detected)
    else:
        logger.warning("No trained detector; recent-worst scoring uses bands only")

    profile = get_profile(equipment.equipment_type)
    settings = get_settings()
    worst: HealthAssessment | None = None

    for reading in readings:
        values = {c: getattr(reading, c) for c in SENSOR_FEATURES}
        anomaly_score, anomaly_detected = anomaly_by_reading.get(reading.id, (0.0, False))

        partial, _ = compute_health_score(
            values, profile, anomaly_score=anomaly_score, failure_probability=0.0
        )
        probability = estimate_risk(
            values, partial.health_score, anomaly_score, profile, db
        )
        breakdown, factors = compute_health_score(
            values, profile, anomaly_score=anomaly_score, failure_probability=probability
        )
        assessment = build_assessment(
            breakdown, factors, anomaly_detected=anomaly_detected
        )
        if worst is None or assessment.health_score < worst.health_score:
            worst = assessment

    if worst is not None:
        # Refresh the cached columns so the dashboard shows the fleet view,
        # without appending a duplicate row to the assessment history.
        _refresh_equipment_cache(db, equipment, worst, datetime.now(UTC))
        db.commit()

    return worst
