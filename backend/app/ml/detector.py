"""Anomaly detection.

Three detectors run together, because each covers a different failure shape and
no single one is sufficient:

1. **Isolation Forest** (multivariate, learned) — catches unusual *combinations*
   of channels, such as high current at low speed, which no per-parameter
   threshold would flag.
2. **Baseline band deviation** (deterministic) — catches a single channel
   outside its engineering limits. This is what makes a finding attributable to
   a *named parameter*.
3. **EWMA drift** (temporal) — catches slow movement away from normal while
   every value is still inside its limits. A point-in-time detector is blind to
   this, and gradual wear is the failure mode that matters most.

The forest's raw `score_samples` output is not a probability and is not
comparable across retrains. It is calibrated against the score distribution of
the training data and mapped to a bounded 0..1 `anomaly_score`, so the number
means the same thing for every machine and after every retrain.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.ml.baselines import get_profile
from app.ml.degradation import FLOOR, evaluate_all
from app.ml.features import (
    DEFAULT_WINDOW,
    FeatureSpec,
    build_features,
    feature_importance_names,
    impute,
)

logger = logging.getLogger(__name__)

#: Bumped whenever the feature set or training procedure changes, so stored
#: scores remain interpretable.
MODEL_VERSION = "if-1.0.0"

#: Default forest size. 100 trees is ample for a few thousand training rows and
#: keeps inference fast enough to score on every ingested reading.
DEFAULT_N_ESTIMATORS = 100

#: Percentiles of the healthy training score distribution used to anchor the
#: calibrated scale, paired with the score each maps to.
#:
#: Healthy operation must land low on the scale. The worst 1 % of healthy data
#: reaching ~0.6 leaves room above the threshold for real faults without
#: flagging normal variation.
CALIBRATION_PERCENTILES = (0.0, 50.0, 90.0, 99.0, 99.9)
CALIBRATION_TARGETS = (0.0, 0.15, 0.35, 0.6, 0.8)


@dataclass
class AnomalyResult:
    """Outcome of assessing one sample."""

    anomaly_score: float
    anomaly_detected: bool
    affected_parameters: list[str]
    parameter_scores: dict[str, dict]
    method: str = "isolation_forest+band+ewma"
    model_version: str = MODEL_VERSION
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "anomaly_score": round(self.anomaly_score, 4),
            "anomaly_detected": self.anomaly_detected,
            "affected_parameters": self.affected_parameters,
            "parameter_scores": self.parameter_scores,
            "method": self.method,
            "model_version": self.model_version,
            **({"detail": self.detail} if self.detail else {}),
        }


class AnomalyDetector:
    """Trained anomaly detector, usable for batch and single-sample scoring.

    Fitted on healthy operation only. Training on data that already contains
    failures teaches the model that those failures are normal.
    """

    def __init__(
        self,
        spec: FeatureSpec | None = None,
        n_estimators: int = DEFAULT_N_ESTIMATORS,
        contamination: float | str = "auto",
        seed: int = 42,
        threshold: float = 0.5,
    ) -> None:
        self.spec = spec or FeatureSpec()
        self.n_estimators = n_estimators
        self.contamination = contamination
        self.seed = seed
        self.threshold = threshold

        self.forest: IsolationForest | None = None
        self.feature_names: list[str] = list(feature_importance_names(self.spec))
        # Calibration anchors from the healthy training distribution.
        self._calibration_x: list[float] = []
        self._calibration_y: list[float] = []
        # Per-channel mean/std of the training data, for the z-score features.
        self._channel_stats: dict[str, tuple[float, float]] = {}
        # EWMA state, used by `detect_series`.
        self._ewma: dict[str, float] = {}
        self._is_fitted = False

    # -- training ----------------------------------------------------------

    def fit(self, matrix: pd.DataFrame) -> "AnomalyDetector":
        """Fit the forest and calibration on healthy feature rows.

        `matrix` must contain the columns in `feature_names`.
        """
        if matrix.empty:
            raise ValueError("Cannot fit the detector on an empty feature matrix")
        missing = [name for name in self.feature_names if name not in matrix.columns]
        if missing:
            raise ValueError(f"Feature matrix is missing columns: {missing[:5]}")

        data = matrix[self.feature_names].astype(float)
        # Defensive: a non-finite value anywhere would propagate through the
        # forest into silent nonsense.
        data = data.replace([np.inf, -np.inf], np.nan).fillna(0.0)

        self.forest = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.seed,
            n_jobs=-1,
        )
        self.forest.fit(data.to_numpy())

        raw = -self.forest.score_samples(data.to_numpy())
        self._set_calibration(raw)

        # Store channel statistics so single-sample scoring can z-score a value
        # against its own history without refitting.
        for channel in self.spec.raw:
            values = data[channel].to_numpy()
            self._channel_stats[channel] = (float(np.mean(values)), float(np.std(values) or 1.0))

        self._is_fitted = True
        logger.info(
            "Detector fitted on %d rows, %d features (healthy score p50=%.2f p99=%.2f)",
            len(data),
            len(self.feature_names),
            self._calibration_y[1] if self._calibration_y else 0.0,
            self._calibration_y[3] if len(self._calibration_y) > 3 else 0.0,
        )
        return self

    def _set_calibration(self, raw_scores: np.ndarray) -> None:
        """Map raw forest scores onto a stable 0..1 scale.

        The mapping is anchored to percentiles of the *healthy* training
        distribution, not to its extremes. Anchoring the median to 0.5 would
        mean half of all healthy data sits at the anomaly threshold by
        construction, which is how a detector ends up crying wolf on a
        perfectly healthy fleet.

        The anchors below express the intended meaning of the scale:
        the median of healthy operation scores ~0.15, the worst 1 % ~0.6, and
        only genuinely unprecedented input approaches 1.0.
        """
        self._calibration_x = [float(v) for v in np.percentile(raw_scores, CALIBRATION_PERCENTILES)]
        self._calibration_y = list(CALIBRATION_TARGETS)
        # The final anchor is the observed maximum, mapped to 1.0, so anything
        # beyond the training data saturates rather than extrapolating wildly.
        self._calibration_x.append(float(np.max(raw_scores)))
        self._calibration_y.append(1.0)

    def _normalize(self, raw_score: float) -> float:
        """Calibrate a single raw forest score to 0..1."""
        if not self._calibration_x:
            return 0.0
        return float(
            np.clip(np.interp(raw_score, self._calibration_x, self._calibration_y), 0.0, 1.0)
        )

    # -- inference ---------------------------------------------------------

    def channel_z(self, channel: str, value: float) -> float:
        """Deviation of a single value from training, in training sigmas.

        Public accessor so callers assessing one isolated sample can use the
        statistics learned at fit time without touching private state.

        Returns 0.0 for a channel absent from the training set: without a
        learned mean and spread there is no meaningful deviation to report, and
        treating the missing mean as zero would invent a large z-score out of
        nothing.
        """
        stats = self._channel_stats.get(channel)
        if stats is None or value != value:  # unknown channel or NaN
            return 0.0
        mean, std = stats
        if not std:
            return 0.0
        return float((float(value) - mean) / std)

    def _require_fitted(self) -> None:
        if not self._is_fitted or self.forest is None:
            raise RuntimeError("AnomalyDetector must be fitted before use")

    def score_features(self, matrix: pd.DataFrame) -> np.ndarray:
        """Calibrated anomaly score for each row of a feature matrix."""
        self._require_fitted()
        if matrix.empty:
            return np.array([], dtype=float)
        data = matrix.reindex(columns=self.feature_names, fill_value=0.0).astype(float)
        data = data.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        raw = -self.forest.score_samples(data.to_numpy())
        return np.array([self._normalize(score) for score in raw])

    def detect_matrix(
        self, matrix: pd.DataFrame, equipment_type: str
    ) -> list[AnomalyResult]:
        """Assess every row of a feature matrix.

        Per-row band evaluation needs the raw channel values, which are the
        first columns of the matrix.
        """
        results: list[AnomalyResult] = []
        scores = self.score_features(matrix)
        profile = get_profile(equipment_type)

        for position, (_, row) in enumerate(matrix.iterrows()):
            values = {c: row.get(c) for c in self.spec.raw}
            band_results = evaluate_all(values, profile)
            results.append(
                self._combine(
                    float(scores[position]), band_results, profile, drift=None
                )
            )
        return results

    def detect_series(
        self,
        readings,
        equipment_type: str,
        spec: FeatureSpec | None = None,
    ) -> list[AnomalyResult]:
        """Assess a chronological series of readings.

        Unlike `detect_matrix` this also applies the EWMA drift detector, which
        needs the ordered history.

        **The result list is shorter than the input.** The feature pipeline
        discards the first `spec.window` samples because their rolling
        statistics are built from insufficient history. Use
        `align_with_readings` to map results back onto the original readings;
        silently zipping the two lists misattributes scores to the wrong
        samples.
        """
        from app.ml.features import detection_matrix, to_frame

        spec = spec or self.spec
        frame = to_frame(readings)
        if frame.empty:
            return []

        features = build_features(frame, spec)
        matrix = detection_matrix(frame, spec)
        if matrix.empty:
            return []

        # The feature matrix drops the leading window, so align raw values with
        # the rows that were actually scored.
        raw_rows = features.iloc[self.spec.window :]
        scores = self.score_features(matrix)
        profile = get_profile(equipment_type)

        results: list[AnomalyResult] = []
        self._ewma = {}
        for position in range(len(matrix)):
            row = raw_rows.iloc[position]
            values = {c: row.get(c) for c in self.spec.raw}
            band_results = evaluate_all(values, profile)
            drift = self._update_ewma(values)
            results.append(self._combine(float(scores[position]), band_results, profile, drift))
        return results

    def _update_ewma(self, values: dict[str, float | None]) -> dict[str, float]:
        """Advance the EWMA of each channel's normalised deviation.

        Exponentially weighted, so a sustained slow rise is amplified while
        ordinary noise is not.
        """
        profile_deviations: dict[str, float] = {}
        for channel, value in values.items():
            if value is None:
                continue
            # Normalise by the channel's own scale before averaging, so
            # channels with different units are comparable.
            mean, std = self._channel_stats.get(channel, (0.0, 1.0))
            deviation = abs(float(value) - mean) / (std or 1.0)
            previous = self._ewma.get(channel, 0.0)
            updated = EWMA_ALPHA * deviation + (1 - EWMA_ALPHA) * previous
            self._ewma[channel] = updated
            profile_deviations[channel] = updated
        return profile_deviations

    def _combine(
        self,
        forest_score: float,
        band_results: dict,
        profile,
        drift: dict[str, float] | None,
    ) -> AnomalyResult:
        """Fuse the three detectors into one score and a named cause.

        The band deviation is given the greatest authority because it is
        grounded in engineering limits and is directly explainable; the forest
        score contributes where no channel has left its band, which is exactly
        the case it is good at.
        """
        band_scores = {k: v.normalized for k, v in band_results.items()}
        max_band = max(band_scores.values(), default=0.0)

        drift_scores = drift or {}
        max_drift = max(drift_scores.values(), default=0.0) if drift_scores else 0.0
        # Drift is only meaningful once there is enough history for the EWMA to
        # have converged; below that it just reflects the first sample.
        drift_component = float(np.clip((max_drift - EWMA_FLOOR) / max(1e-6, 1.0 - EWMA_FLOOR), 0, 1)) * 0.6

        # Weighted fusion. A parameter outside its band is a stronger claim of
        # abnormality than a statistical outlier in a combination of channels.
        combined = (
            BAND_AUTHORITY * max_band
            + FOREST_AUTHORITY * forest_score
            + DRIFT_AUTHORITY * drift_component
        )
        combined = float(np.clip(combined, 0.0, 1.0))

        # A parameter beyond its alarm limit is an anomaly regardless of what
        # the forest thinks, so the floor is applied after fusion.
        if max_band >= 1.0:
            combined = max(combined, 0.9)

        # Naming uses the higher floor so that "affected parameters" stays
        # selective. A drift of one standard deviation is ordinary variation
        # and must not be reported as a suspected cause.
        affected = sorted(
            [
                name
                for name, value in band_results.items()
                if value.is_degraded
                or drift_scores.get(name, 0.0) >= EWMA_AFFECTED_FLOOR
            ]
        )

        parameter_scores = {
            name: {
                "value": None if result.direction == "unknown" else round(result.value, 4),
                "degradation": round(result.normalized, 4),
                "direction": result.direction,
                "overshoot": round(result.overshoot, 4),
                "drift": round(drift_scores.get(name, 0.0), 4),
            }
            for name, result in band_results.items()
        }

        return AnomalyResult(
            anomaly_score=round(combined, 4),
            anomaly_detected=combined >= self.threshold,
            affected_parameters=affected,
            parameter_scores=parameter_scores,
        )


#: Smoothing factor for the drift detector. ~0.15 corresponds to a few samples
#: of memory: responsive to a real trend, insensitive to single spikes.
EWMA_ALPHA = 0.15


def align_with_readings(readings, results: list[AnomalyResult]) -> list[tuple]:
    """Pair a `detect_series` result list with the readings it actually scored.

    `detect_series` skips the first `spec.window` samples, so its output lines
    up with the *tail* of the input. Naively zipping the two lists
    misattributes every score to the wrong sample, which silently corrupts
    evaluation and any per-reading persistence.

    Returns `(reading, result)` pairs for the scored tail, oldest first.
    """
    offset = max(len(readings) - len(results), 0)
    return list(zip(readings[offset:], results, strict=True))

#: Deviation level at which drift begins contributing to the fused score.
#: Roughly one standard deviation of normal variation.
EWMA_FLOOR = 1.0

#: Deviation level at which a parameter is *named* as affected.
#:
#: Deliberately higher than the scoring floor. Reporting every channel that has
#: drifted by a single standard deviation listed ~2.3 "affected parameters" even
#: on healthy machines, which makes the field meaningless: if everything is
#: named, nothing is.
EWMA_AFFECTED_FLOOR = 2.0

#: Relative authority of each detector in the fused score.
BAND_AUTHORITY = 0.55
FOREST_AUTHORITY = 0.30
DRIFT_AUTHORITY = 0.15
