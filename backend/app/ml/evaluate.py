"""Detector evaluation against known ground truth.

The generator records `regime` and `fault_type`, which the detector never
sees. That makes precision and recall measurable, which is otherwise impossible
without labelled real failure data.

Two levels of recall are reported, because they answer different questions:

**Per-sample** — of all samples inside a fault, how many were flagged? This is
the strict measure and is always well below 100 %, because injected faults ramp
in over several samples by design. A developing fault is genuinely not yet
abnormal on its first sample.

**Per-event** — of all distinct fault episodes, how many were detected *at all*?
This is what an operator actually cares about: not how many samples during a
bearing failure raised an alarm, but whether the failure was noticed at all.

Reporting only one of these would be misleading in opposite directions.

Usage:
    python -m app.ml.evaluate
    python -m app.ml.evaluate --samples 3000 --types "Hydraulic Pump,Boiler"
"""

import argparse
import logging
from dataclasses import dataclass, field

import pandas as pd
from sklearn.metrics import precision_score, recall_score

from app.config import get_settings
from app.ml import registry
from app.ml.detector import align_with_readings
from app.ml.synthetic import generate_series

logger = logging.getLogger(__name__)

#: Contiguous samples sharing a regime form one episode. Faults are injected as
#: single unbroken runs, so a change in regime is an event boundary.
EPISODE_BREAK = "regime_change"

DEFAULT_TYPES = [
    "Hydraulic Pump",
    "Industrial Motor",
    "Boiler",
    "Chiller",
    "CNC Machine",
    "Air Compressor",
]


@dataclass
class EvaluationReport:
    """Detection performance, overall and broken down by regime."""

    threshold: float
    per_sample: dict[str, float] = field(default_factory=dict)
    per_regime: pd.DataFrame | None = None
    per_event: dict[str, float] = field(default_factory=dict)
    samples_evaluated: int = 0

    def summary(self) -> str:
        lines = [
            f"Threshold: {self.threshold:.2f}   Samples: {self.samples_evaluated}",
            "",
            "Per-sample detection",
            f"  precision {self.per_sample.get('precision', 0):.3f}"
            f"   recall {self.per_sample.get('recall', 0):.3f}"
            f"   fpr {self.per_sample.get('fpr', 0):.3f}",
            "",
            "Per-event detection (episodes noticed at all)",
            f"  episodes {int(self.per_event.get('episodes', 0))}"
            f"   detected {self.per_event.get('recall', 0):.3f}",
        ]
        if self.per_regime is not None:
            lines += ["", "Per regime", self.per_regime.to_string()]
        return "\n".join(lines)


def _label_events(regimes: list[str]) -> list[int]:
    """Assign an episode id to each sample, incrementing on regime change."""
    ids: list[int] = []
    current = -1
    previous = None
    for regime in regimes:
        if regime != previous:
            current += 1
            previous = regime
        ids.append(current)
    return ids


def evaluate_detector(
    detector,
    equipment_types: list[str] | None = None,
    samples: int = 3000,
    seed: int = 11,
) -> EvaluationReport:
    """Score held-out generated series and measure detection performance.

    Seeds start at 11, well past the training seeds (0..7), so evaluation uses
    data the detector has never seen.
    """
    types = equipment_types or DEFAULT_TYPES
    records: list[dict] = []

    for index, equipment_type in enumerate(types):
        readings = generate_series(
            equipment_type=equipment_type, samples=samples, seed=seed + index
        )
        results = detector.detect_series(readings, equipment_type)
        for reading, result in align_with_readings(readings, results):
            records.append(
                {
                    "equipment_type": equipment_type,
                    "regime": reading.regime,
                    "detected": result.anomaly_detected,
                    "score": result.anomaly_score,
                    "affected": len(result.affected_parameters),
                }
            )

    frame = pd.DataFrame(records)
    is_fault = (frame["regime"] != "nominal").to_numpy()
    detected = frame["detected"].to_numpy()

    report = EvaluationReport(threshold=detector.threshold, samples_evaluated=len(frame))
    report.per_sample = {
        "precision": float(precision_score(is_fault, detected, zero_division=0)),
        "recall": float(recall_score(is_fault, detected, zero_division=0)),
        "fpr": float(detected[~is_fault].mean()) if (~is_fault).any() else 0.0,
    }

    grouped = frame.groupby("regime").agg(
        samples=("detected", "size"),
        flag_rate=("detected", "mean"),
        mean_score=("score", "mean"),
        max_score=("score", "max"),
        mean_affected=("affected", "mean"),
    )
    report.per_regime = grouped.round(3)

    report.per_event = _event_recall(frame, detector.threshold)
    return report


def _event_recall(frame: pd.DataFrame, threshold: float) -> dict[str, float]:
    """Fraction of distinct fault episodes that were flagged at least once.

    A machine that alarms briefly during a two-hour failure has still detected
    the failure; counting only the fraction of samples that tripped would
    understate detection badly for long events.
    """
    frame = frame.copy()
    frame["episode"] = _label_events(frame["regime"].tolist())

    fault_episodes = frame[frame["regime"] != "nominal"]
    if fault_episodes.empty:
        return {"episodes": 0, "recall": 0.0}

    detected_any = fault_episodes.groupby("episode")["detected"].any()
    return {
        "episodes": int(len(detected_any)),
        "recall": float(detected_any.mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the anomaly detector")
    parser.add_argument("--types", help="Comma-separated equipment types")
    parser.add_argument("--samples", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--models-dir", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")
    settings = get_settings()
    models_dir = args.models_dir or settings.models_dir

    detector = registry.load(models_dir)
    types = (
        [t.strip() for t in args.types.split(",") if t.strip()] if args.types else None
    )

    report = evaluate_detector(detector, types, args.samples, args.seed)
    print(report.summary())


if __name__ == "__main__":
    main()
