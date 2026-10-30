"""Model persistence.

Artifacts are versioned and stored with the training metadata needed to
interpret a score later: which feature set was used, how many rows it was
trained on, and when. Without that, a score from six months ago cannot be
reconciled with today's model.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import joblib

from app.ml.detector import MODEL_VERSION, AnomalyDetector
from app.ml.features import FeatureSpec

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "anomaly_detector.joblib"
METADATA_NAME = "anomaly_detector.meta.json"


@dataclass
class ModelMetadata:
    """Provenance for a trained detector."""

    model_version: str
    trained_at: str
    training_rows: int
    feature_count: int
    feature_names: list[str]
    equipment_types: list[str] = field(default_factory=list)
    threshold: float = 0.5
    contamination: float | str = "auto"
    n_estimators: int = 100
    notes: str = ""


def artifact_path(models_dir: Path) -> Path:
    return Path(models_dir) / ARTIFACT_NAME


def metadata_path(models_dir: Path) -> Path:
    return Path(models_dir) / METADATA_NAME


def save(detector: AnomalyDetector, models_dir: Path, metadata: ModelMetadata) -> Path:
    """Persist the detector and its metadata.

    The detector is saved as a single artifact so the feature list, scaler
    statistics, and calibration always travel together and cannot drift apart.
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    path = artifact_path(models_dir)
    joblib.dump(detector, path)

    metadata_path(models_dir).write_text(json.dumps(asdict(metadata), indent=2))
    logger.info("Saved detector to %s (%d features)", path, metadata.feature_count)
    return path


def load(models_dir: Path) -> AnomalyDetector:
    """Load a trained detector, with a clear error if none exists."""
    path = artifact_path(models_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No trained detector at {path}. Run `python -m app.ml.train` first."
        )
    detector = joblib.load(path)
    if not isinstance(detector, AnomalyDetector):
        raise TypeError(f"Artifact at {path} is a {type(detector)}, not an AnomalyDetector")
    return detector


def load_metadata(models_dir: Path) -> ModelMetadata | None:
    """Load training metadata, or None when absent."""
    path = metadata_path(models_dir)
    if not path.exists():
        return None
    return ModelMetadata(**json.loads(path.read_text()))


def is_trained(models_dir: Path) -> bool:
    """Whether a usable artifact is present."""
    return artifact_path(models_dir).exists()


def load_or_none(models_dir: Path) -> AnomalyDetector | None:
    """Load a detector, returning None when unavailable.

    Used by read paths that must degrade gracefully on a fresh install instead
    of failing the whole request because the model has not been trained yet.
    """
    try:
        return load(models_dir)
    except (FileNotFoundError, TypeError, Exception) as exc:  # noqa: BLE001
        logger.warning("Detector unavailable: %s", exc)
        return None


def new_metadata(
    detector: AnomalyDetector,
    training_rows: int,
    equipment_types: list[str],
    notes: str = "",
) -> ModelMetadata:
    """Build metadata for a freshly fitted detector."""
    return ModelMetadata(
        model_version=MODEL_VERSION,
        trained_at=datetime.now(UTC).isoformat(),
        training_rows=training_rows,
        feature_count=len(detector.feature_names),
        feature_names=list(detector.feature_names),
        equipment_types=equipment_types,
        threshold=detector.threshold,
        contamination=str(detector.contamination),
        n_estimators=detector.n_estimators,
        notes=notes,
    )


__all__ = [
    "FeatureSpec",
    "ModelMetadata",
    "artifact_path",
    "is_trained",
    "load",
    "load_metadata",
    "load_or_none",
    "new_metadata",
    "save",
]
