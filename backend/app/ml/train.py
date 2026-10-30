"""Train the anomaly detector on healthy operation.

**Training data must be fault-free.** A detector fitted on data that already
contains failures learns that those failures are normal, which is precisely
the error that makes an unsupervised detector useless in the field. The
default here trains on generated healthy series; with real telemetry, fit on
a period the plant confirmed was healthy.

Usage:
    python -m app.ml.train
    python -m app.ml.train --types "Hydraulic Pump,Industrial Motor" --trees 200
    python -m app.ml.train --threshold 0.55
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from app.config import get_settings
from app.ml.baselines import get_profile
from app.ml.detector import AnomalyDetector
from app.ml.features import FeatureSpec, build_features, detection_matrix
from app.ml.synthetic import generate_healthy_series
from app.ml import registry

logger = logging.getLogger(__name__)

#: Rows of healthy history per equipment type. Enough for the forest to see the
#: normal covariance structure without bloating the artifact.
SAMPLES_PER_TYPE = 1500

#: Equipment types the detector is trained to generalise over. Covering a range
#: of machine types is what lets one model serve the fleet.
DEFAULT_TRAINING_TYPES = [
    "Hydraulic Pump",
    "Industrial Motor",
    "Air Compressor",
    "CNC Machine",
    "Conveyor Drive",
    "Gearbox",
    "Boiler",
    "Chiller",
]


def build_training_matrix(
    equipment_types: list[str] | None = None,
    samples_per_type: int = SAMPLES_PER_TYPE,
    seed: int = 42,
) -> tuple[pd.DataFrame, list[str]]:
    """Assemble the healthy feature matrix.

    Each type is generated on a shared time axis so the matrix has a single
    feature schema, and the types are concatenated so the forest learns what is
    normal *across* machine types rather than one type at a time.
    """
    types = equipment_types or DEFAULT_TRAINING_TYPES
    spec = FeatureSpec()

    frames: list[pd.DataFrame] = []
    for index, equipment_type in enumerate(types):
        readings = generate_healthy_series(
            equipment_type=equipment_type,
            samples=samples_per_type,
            seed=seed + index,
        )
        raw = pd.DataFrame([r.as_dict() for r in readings])
        frame = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(raw["timestamp"], utc=True),
                **{c: raw[c] for c in spec.raw},
            }
        ).sort_values("timestamp").reset_index(drop=True)

        matrix = detection_matrix(frame, spec)
        if not matrix.empty:
            frames.append(matrix)

    if not frames:
        raise RuntimeError("No training data could be generated")

    return pd.concat(frames, ignore_index=True), types


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the anomaly detector")
    parser.add_argument("--types", help="Comma-separated equipment types")
    parser.add_argument("--samples", type=int, default=SAMPLES_PER_TYPE)
    parser.add_argument("--trees", type=int, default=100)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Fused score at which an anomaly is reported",
    )
    parser.add_argument("--contamination", default="auto")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--models-dir", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    settings = get_settings()
    models_dir = Path(args.models_dir) if args.models_dir else Path(settings.models_dir)

    types = (
        [t.strip() for t in args.types.split(",") if t.strip()]
        if args.types
        else DEFAULT_TRAINING_TYPES
    )

    logger.info("Generating healthy training data for %d equipment types", len(types))
    matrix, types = build_training_matrix(types, args.samples, args.seed)
    logger.info("Training matrix: %d rows x %d features", len(matrix), matrix.shape[1])

    detector = AnomalyDetector(
        n_estimators=args.trees,
        contamination=args.contamination,
        seed=args.seed,
        threshold=args.threshold,
    )
    detector.fit(matrix)

    # Sanity check before persisting: healthy data must not be flagged. A model
    # that alarms on its own training distribution is worse than no model.
    scores = detector.score_features(matrix)
    flagged = float((scores >= args.threshold).mean())
    logger.info("Healthy rows scored >= %.2f: %.2f%%", args.threshold, flagged * 100)
    if flagged > 0.05:
        logger.warning(
            "%.1f%% of healthy training rows exceed the threshold; the model may be "
            "too sensitive or the threshold too low",
            flagged * 100,
        )

    metadata = registry.new_metadata(
        detector,
        training_rows=len(matrix),
        equipment_types=types,
        notes=(
            "Trained on generated fault-free telemetry. Calibration anchors the "
            "score to this training distribution."
        ),
    )
    registry.save(detector, models_dir, metadata)
    logger.info("Done. Mean healthy score: %.4f, max: %.4f", scores.mean(), scores.max())


if __name__ == "__main__":
    main()
