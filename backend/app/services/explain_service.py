"""Explainability service.

Composes everything needed to answer "why did this machine receive this
score?" into a single payload:

* the **score arithmetic**, so the number can be reconciled
* **per-parameter attribution** with counterfactuals, so the operator can see
  what fixing each channel would be worth
* **drift**, the slow-movement signal that a point-in-time score cannot show
* **trend**, comparing the machine against its own recent history
* a generated **explanation** and **recommendation**, both derived from the
  numbers above rather than asserted independently

Every field is computed. Nothing here is templated text with the numbers
bolted on afterwards, and no field can contradict the score it explains.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES, get_settings
from app.ml.attribution import attribute_parameters, suppressed_by_policy
from app.ml.baselines import get_profile
from app.ml.detector import align_with_readings
from app.ml.scoring import classify_status, apply_status_caps
from app.services import anomaly_service, health_service
from app.services.explainability import build_assessment

logger = logging.getLogger(__name__)

#: Drift level at which a parameter is reported as trending. Matches the
#: detector's naming threshold so the two never disagree about what counts as
#: a trend.
DRIFT_REPORT_THRESHOLD = 2.0

#: Reading count used for the trend comparison.
TREND_WINDOW = 60


def _drift_signals(
    db: Session, equipment, readings
) -> tuple[dict[str, float], float, bool]:
    """Extract per-parameter drift and the final anomaly result from history.

    Returns an empty mapping when no detector is available, so the explanation
    degrades to the band evidence rather than failing.
    """
    detector = anomaly_service.get_detector()
    if detector is None or not readings:
        return {}, 0.0, False

    results = detector.detect_series(readings, equipment.equipment_type)
    pairs = align_with_readings(readings, results)
    if not pairs:
        return {}, 0.0, False

    final = pairs[-1][1]
    drift = {
        name: detail.get("drift", 0.0)
        for name, detail in final.parameter_scores.items()
    }
    return drift, final.anomaly_score, final.anomaly_detected


def _trend_summary(db: Session, equipment) -> dict:
    """Compare the current score against the machine's recent assessments."""
    from app.repositories import health_score as score_repo

    rows = score_repo.history(db, equipment, limit=TREND_WINDOW)
    scores = [row.health_score for row in rows]

    if not scores:
        return {
            "available": False,
            "reason": "No previous assessment stored for this equipment",
        }

    return {
        "available": True,
        "samples": len(scores),
        "first_score": round(scores[0], 2),
        "latest_score": round(scores[-1], 2),
        "min_score": round(min(scores), 2),
        "max_score": round(max(scores), 2),
        "average_score": round(sum(scores) / len(scores), 2),
        "change_over_window": round(scores[-1] - scores[0], 2),
        "direction": (
            "stable"
            if abs(scores[-1] - scores[0]) < 2.0
            else ("declining" if scores[-1] < scores[0] else "improving")
        ),
    }


def explain_equipment(
    db: Session, equipment_id: str, window: int = TREND_WINDOW
) -> dict:
    """Full explanation for a machine, from its stored telemetry."""
    from app.repositories.equipment import get_by_equipment_id
    from app.services import sensor_service

    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        from app.repositories.equipment import EquipmentNotFoundError

        raise EquipmentNotFoundError(f"Equipment '{equipment_id}' not found")

    readings = sensor_service.get_recent(db, equipment_id, count=window)
    if not readings:
        from fastapi import HTTPException
        from starlette import status as http_status

        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"No telemetry recorded for '{equipment_id}'",
        )

    latest = readings[-1]
    values = {c: getattr(latest, c) for c in SENSOR_FEATURES}
    drift, anomaly_score, anomaly_detected = _drift_signals(db, equipment, readings)

    # Prefer the worst reading in the window, matching the alert engine and the
    # fleet dashboard. Explaining the newest sample made a machine with an open
    # Critical vibration alert read "Excellent, no action required" on its own
    # detail page.
    worst = health_service.worst_reading_in_window(db, equipment_id, window=window)
    if worst is not None:
        worst_reading, values, worst_anomaly, worst_detected = worst
        latest = worst_reading
        if worst_detected or worst_anomaly > anomaly_score:
            anomaly_score, anomaly_detected = worst_anomaly, worst_detected

    return _build_explanation(
        values,
        equipment.equipment_type,
        equipment.equipment_id,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_detected,
        drift=drift,
        db=db,
        equipment=equipment,
        observed_at=latest.timestamp,
    )


def explain_values(
    values: dict[str, float],
    equipment_type: str,
    *,
    equipment_id: str | None = None,
    anomaly_score: float = 0.0,
    anomaly_detected: bool = False,
) -> dict:
    """Explain a set of sensor values without touching the database."""
    return _build_explanation(
        values,
        equipment_type,
        equipment_id,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_detected,
        drift={},
    )


def _build_explanation(
    values: dict[str, float | None],
    equipment_type: str,
    equipment_id: str | None,
    *,
    anomaly_score: float,
    anomaly_detected: bool,
    drift: dict[str, float],
    db: Session | None = None,
    equipment=None,
    observed_at: datetime | None = None,
) -> dict:
    """Assemble the explanation payload from computed values."""
    settings = get_settings()
    profile = get_profile(equipment_type)

    score, status, probability = health_service.preview_assessment(
        values, profile, anomaly_score, db
    )
    estimate = health_service.estimate_risk_detail(
        values, profile, anomaly_score, score, db
    )

    # Recompute the full breakdown so the arithmetic and the narrative come
    # from one pass, as they do for the persisted assessment.
    from app.ml.scoring import compute_health_score

    breakdown, factors = compute_health_score(
        values,
        profile,
        anomaly_score=anomaly_score,
        failure_probability=estimate.probability,
        settings=settings,
    )
    status = apply_status_caps(
        classify_status(breakdown.health_score, settings),
        anomaly_score,
        breakdown.risk_level,
        settings,
    )

    from app.repositories import health_score as score_repo

    previous_score = None
    if db is not None and equipment is not None:
        previous = score_repo.latest(db, equipment)
        previous_score = previous.health_score if previous else None

    assessment = build_assessment(
        breakdown, factors, anomaly_detected=anomaly_detected,
        previous_score=previous_score,
    )

    attribution = attribute_parameters(
        values,
        profile,
        anomaly_score=anomaly_score,
        failure_probability=estimate.probability,
        settings=settings,
    )
    suppressed = suppressed_by_policy(values, profile, attribution, settings=settings)

    trending = sorted(
        (
            {"parameter": name, "drift": round(value, 4)}
            for name, value in drift.items()
            if value >= DRIFT_REPORT_THRESHOLD
        ),
        key=lambda item: -item["drift"],
    )

    return {
        "equipment_id": equipment_id,
        "equipment_type": equipment_type,
        "observed_at": observed_at,
        "generated_at": datetime.now(UTC),
        "health_score": assessment.health_score,
        "status": status,
        "risk_level": assessment.risk_level,
        "failure_probability": estimate.probability,
        "anomaly_score": anomaly_score,
        "anomaly_detected": anomaly_detected,
        "explanation": assessment.explanation,
        "recommendation": assessment.recommendation,
        "score_breakdown": breakdown.as_dict(),
        "contributing_factors": factors,
        "attribution": [item.as_dict() for item in attribution],
        "operating_bands": _operating_bands(profile),
        "trending_parameters": trending,
        "suppressed_by_policy": suppressed,
        "risk": estimate.as_dict(),
        "trend": (
            _trend_summary(db, equipment)
            if (db is not None and equipment is not None)
            else {
                "available": False,
                "reason": (
                    "No stored assessment history for this request; a trend "
                    "requires ordered history and cannot be derived from a "
                    "single sample"
                ),
            }
        ),
    }


def _operating_bands(profile) -> list[dict]:
    """Acceptable and alarm limits for every channel of this equipment type.

    Attribution only reports channels that are out of band, so it cannot supply
    limits for a healthy machine. The detail view needs them to draw alarm
    reference lines, and an operator needs them to judge whether a reading is
    normal in absolute terms rather than only relative to the current fault.
    """
    from app.ml.attribution import UNITS

    bands = []
    for parameter, band in profile.bands.items():
        bands.append(
            {
                "parameter": parameter,
                "unit": UNITS.get(parameter, ""),
                "nominal": round(band.nominal, 3),
                "ok_low": round(band.ok_low, 3),
                "ok_high": round(band.ok_high, 3),
                "alarm_low": round(band.alarm_low, 3),
                "alarm_high": round(band.alarm_high, 3),
                "low_relevant": band.low_relevant,
            }
        )
    return bands
