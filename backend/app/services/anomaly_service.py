"""Anomaly detection service.

Owns the detector lifecycle. The trained artifact is loaded once and cached,
because deserialising a forest on every request would dominate response time.

If no model has been trained, detection degrades to the deterministic band and
drift detectors rather than failing. Those need no training data and still give
a defensible answer, which keeps the API useful on a fresh install.
"""

import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.ml import registry
from app.ml.baselines import get_profile
from app.ml.degradation import evaluate_all
from app.ml.detector import AnomalyDetector, AnomalyResult, align_with_readings
from app.repositories import anomaly as anomaly_repo
from app.repositories.equipment import EquipmentNotFoundError, get_by_equipment_id
from app.schemas.anomaly import AnomalyDetectRequest

logger = logging.getLogger(__name__)

#: Most recent readings used as context for the drift detector.
CONTEXT_SAMPLES = 60


@lru_cache(maxsize=4)
def _load_detector(models_dir: str) -> AnomalyDetector | None:
    """Load and cache the detector for a models directory.

    Cached by path so a retrain is picked up by restarting the process, and a
    missing model is logged once rather than on every request.
    """
    detector = registry.load_or_none(Path(models_dir))
    if detector is None:
        logger.warning(
            "No trained anomaly model in %s; falling back to band and drift "
            "detection only. Run `python -m app.ml.train`.",
            models_dir,
        )
    return detector


def reset_detector_cache() -> None:
    """Drop the cached detector, so a retrain takes effect without a restart."""
    _load_detector.cache_clear()


def get_detector() -> AnomalyDetector | None:
    """Return the cached detector, or None if untrained."""
    return _load_detector(str(get_settings().models_dir))


def _resolve_profile(db: Session, request: AnomalyDetectRequest) -> tuple[str, str]:
    """Determine which equipment type's limits apply.

    An explicit `equipment_id` wins, because a real machine's type is more
    specific than whatever the caller guessed.
    """
    if request.equipment_id:
        equipment = get_by_equipment_id(db, request.equipment_id)
        if equipment is None:
            raise EquipmentNotFoundError(f"Equipment '{request.equipment_id}' not found")
        return equipment.equipment_id, equipment.equipment_type

    return None, request.equipment_type  # type: ignore[return-value]


def detect(
    db: Session,
    request: AnomalyDetectRequest,
    persist: bool = False,
) -> tuple[AnomalyResult, str]:
    """Assess a single sample.

    With a trained model this uses all three detectors. Without one, the band
    and drift logic still runs, so the endpoint returns a meaningful answer
    rather than an error.

    Returns the result and the equipment type whose limits were applied, which
    the caller cannot infer on its own when only `equipment_id` was given.
    """
    resolved_id, equipment_type = _resolve_profile(db, request)
    values = request.channel_values()
    profile = get_profile(equipment_type)

    band_results = evaluate_all(values, profile)
    detector = get_detector()

    if detector is not None:
        # A single sample has no history, so rolling features are degenerate.
        # Fall back to the band/drift path for the attribution, and let the
        # forest contribute only via the per-channel statistics.
        result = _score_single_sample(detector, values, band_results)
    else:
        max_band = max((d.normalized for d in band_results.values()), default=0.0)
        result = AnomalyResult(
            anomaly_score=round(float(max_band), 4),
            anomaly_detected=max_band >= 0.5,
            affected_parameters=sorted(
                name for name, d in band_results.items() if d.is_degraded
            ),
            parameter_scores=_parameter_scores(band_results, {}),
            method="band_only",
        )

    if persist and resolved_id:
        equipment = get_by_equipment_id(db, resolved_id)
        if equipment is not None:
            anomaly_repo.create(
                db,
                equipment,
                anomaly_score=result.anomaly_score,
                anomaly_detected=result.anomaly_detected,
                affected_parameters=result.affected_parameters,
                parameter_scores=result.parameter_scores,
                method=result.method,
                model_version=result.model_version,
            )
            db.commit()

    return result, equipment_type


def _score_single_sample(
    detector: AnomalyDetector, values: dict[str, float], band_results: dict
) -> AnomalyResult:
    """Score one isolated sample.

    The forest needs a feature vector with rolling statistics, which a single
    reading cannot supply. Rather than fabricate a history, the band
    degradation is used as the multivariate signal and the forest is used only
    for its channel statistics to express the deviation as a z-score. The result
    is labelled accordingly so the provenance is not overstated.
    """
    max_band = max((d.normalized for d in band_results.values()), default=0.0)

    # Express the worst deviation in the units the forest learned.
    worst_z = 0.0
    for name, degradation in band_results.items():
        if degradation.value == degradation.value:  # not NaN
            worst_z = max(worst_z, abs(detector.channel_z(name, degradation.value)))

    forest_component = min(worst_z / 6.0, 1.0)

    combined = max(
        0.55 * max_band + 0.45 * forest_component,
        0.9 if max_band >= 1.0 else 0.0,
    )
    combined = float(min(combined, 1.0))

    affected = sorted(name for name, d in band_results.items() if d.is_degraded)
    return AnomalyResult(
        anomaly_score=round(combined, 4),
        anomaly_detected=combined >= detector.threshold,
        affected_parameters=affected,
        parameter_scores=_parameter_scores(band_results, {}),
        method="band+forest_prior",
    )


def _parameter_scores(
    band_results: dict, drift_scores: dict[str, float]
) -> dict[str, dict]:
    """Flatten degradation results into the API's per-parameter shape."""
    return {
        name: {
            "value": None if result.direction == "unknown" else round(result.value, 4),
            "degradation": round(result.normalized, 4),
            "direction": result.direction,
            "overshoot": round(result.overshoot, 4),
            "drift": round(drift_scores.get(name, 0.0), 4),
        }
        for name, result in band_results.items()
    }


def detect_history(
    db: Session, equipment_id: str, limit: int = 300
) -> list[AnomalyResult]:
    """Assess a machine's recent history and persist the results.

    This is the path used after ingesting readings: the drift detector needs
    ordered history, so single-sample assessment is not sufficient.
    """
    from app.services import sensor_service

    detector = get_detector()
    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise EquipmentNotFoundError(f"Equipment '{equipment_id}' not found")

    readings = sensor_service.get_recent(db, equipment_id, count=limit)
    if not readings:
        return []

    if detector is not None:
        results = detector.detect_series(readings, equipment.equipment_type)
    else:
        profile = get_profile(equipment.equipment_type)
        results = []
        for reading in readings:
            band_results = evaluate_all(
                {c: getattr(reading, c) for c in band_results_channels()}, profile
            )
            max_band = max((d.normalized for d in band_results.values()), default=0.0)
            results.append(
                AnomalyResult(
                    anomaly_score=round(float(max_band), 4),
                    anomaly_detected=max_band >= 0.5,
                    affected_parameters=sorted(
                        n for n, d in band_results.items() if d.is_degraded
                    ),
                    parameter_scores=_parameter_scores(band_results, {}),
                    method="band_only",
                )
            )

    for reading, result in align_with_readings(readings, results):
        anomaly_repo.create(
            db,
            equipment,
            anomaly_score=result.anomaly_score,
            anomaly_detected=result.anomaly_detected,
            affected_parameters=result.affected_parameters,
            parameter_scores=result.parameter_scores,
            reading_id=reading.id,
            method=result.method,
            model_version=result.model_version,
        )
    db.commit()
    return results


def band_results_channels() -> tuple[str, ...]:
    """The channel set evaluated by the band detector."""
    from app.config import SENSOR_FEATURES

    return SENSOR_FEATURES


def get_summary(db: Session, equipment_id: str) -> dict:
    """Aggregate anomaly statistics for one machine."""
    from app.services import sensor_service

    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise EquipmentNotFoundError(f"Equipment '{equipment_id}' not found")

    from app.repositories import sensor_reading as reading_repo

    total = reading_repo.reading_count(db, equipment)
    detected = anomaly_repo.unresolved_count(db, equipment)
    latest_row = anomaly_repo.latest(db, equipment)
    recent = anomaly_repo.list_for_equipment(db, equipment, limit=1000)

    scores = [row.anomaly_score for row in recent]
    return {
        "equipment_id": equipment.equipment_id,
        "assessed": total,
        "detected": detected,
        "detection_rate": round(detected / total, 4) if total else 0.0,
        "max_anomaly_score": round(max(scores), 4) if scores else 0.0,
        "mean_anomaly_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "most_common_parameters": [
            name for name, _ in anomaly_repo.parameter_frequency(db, equipment)
        ],
        "last_detected_at": (
            anomaly_repo.latest_detected(db, equipment).timestamp
            if anomaly_repo.latest_detected(db, equipment)
            else None
        ),
    }


def now_utc() -> datetime:
    """Current UTC time, for response timestamps."""
    return datetime.now(UTC)
