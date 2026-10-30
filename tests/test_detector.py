"""Tests for the anomaly detector, model persistence, and evaluation.

Performance is asserted, not assumed: a test that merely checks the detector
"returns a number" would pass just as happily on a detector that never fires.
"""

import numpy as np
import pandas as pd
import pytest

from app.ml import registry
from app.ml.detector import MODEL_VERSION, AnomalyDetector, align_with_readings
from app.ml.evaluate import evaluate_detector
from app.ml.features import FeatureSpec
from app.ml.synthetic import generate_healthy_series, generate_series
from app.ml.train import build_training_matrix


@pytest.fixture(scope="module")
def training_matrix() -> pd.DataFrame:
    matrix, _ = build_training_matrix(
        ["Hydraulic Pump", "Industrial Motor", "Boiler"], samples_per_type=600, seed=1
    )
    return matrix


@pytest.fixture(scope="module")
def detector(training_matrix: pd.DataFrame) -> AnomalyDetector:
    return AnomalyDetector(seed=1).fit(training_matrix)


@pytest.fixture(scope="module")
def pump_detector(detector: AnomalyDetector) -> AnomalyDetector:
    return detector


# --- Training ---

def test_fit_populates_feature_names(detector: AnomalyDetector) -> None:
    assert len(detector.feature_names) == 51
    assert "temperature" in detector.feature_names
    assert "power_residual" in detector.feature_names


def test_fit_on_empty_matrix_raises() -> None:
    with pytest.raises(ValueError, match="empty feature matrix"):
        AnomalyDetector().fit(pd.DataFrame())


def test_fit_with_missing_columns_raises(training_matrix: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="missing columns"):
        AnomalyDetector().fit(training_matrix[["temperature"]])


def test_predict_before_fit_raises() -> None:
    with pytest.raises(RuntimeError, match="must be fitted"):
        AnomalyDetector().score_features(pd.DataFrame({"temperature": [1.0]}))


def test_channel_z_is_zero_at_training_mean(detector: AnomalyDetector) -> None:
    mean, std = detector._channel_stats["temperature"]

    assert detector.channel_z("temperature", mean) == pytest.approx(0.0)


def test_channel_z_handles_unknown_channel(detector: AnomalyDetector) -> None:
    assert detector.channel_z("humidity", 5.0) == 0.0


# --- Calibration ---

def test_scores_are_bounded(detector: AnomalyDetector, training_matrix: pd.DataFrame) -> None:
    scores = detector.score_features(training_matrix)

    assert scores.min() >= 0.0
    assert scores.max() <= 1.0


def test_healthy_data_scores_low(detector: AnomalyDetector) -> None:
    """Healthy operation must land low on the calibrated scale.

    If the median of healthy data sat near the threshold, a quarter of a
    healthy fleet would be reported as anomalous.
    """
    readings = generate_healthy_series("Hydraulic Pump", 500, seed=3)
    results = detector.detect_series(readings, "Hydraulic Pump")
    scores = [r.anomaly_score for r in results]

    assert np.median(scores) < 0.35
    assert sum(r.anomaly_detected for r in results) / len(results) < 0.08


def test_calibration_is_monotonic(detector: AnomalyDetector) -> None:
    """A more extreme raw score must never produce a lower calibrated score."""
    raw = list(np.linspace(-0.6, 0.0, 200))
    calibrated = [detector._normalize(value) for value in raw]

    assert calibrated == sorted(calibrated)


def test_calibration_is_reproducible(detector: AnomalyDetector) -> None:
    assert detector._normalize(-0.5) == pytest.approx(detector._normalize(-0.5))


# --- Detection ---

def test_detects_overheating(detector: AnomalyDetector) -> None:
    readings = generate_series("Hydraulic Pump", 3000, seed=21)
    results = detector.detect_series(readings, "Hydraulic Pump")
    pairs = align_with_readings(readings, results)

    overheat = [res for read, res in pairs if read.regime == "overheat"]
    assert overheat
    assert any(r.anomaly_detected for r in overheat)


def test_detects_overload_via_stall(detector: AnomalyDetector) -> None:
    """Overload is a speed stall, not a high absolute current.

    An earlier version of the baseline profiles allowed 2.4x rated current and
    gave rpm no low-side limit, so this fault was completely invisible.
    """
    readings = generate_series("Hydraulic Pump", 3000, seed=21)
    results = detector.detect_series(readings, "Hydraulic Pump")
    pairs = align_with_readings(readings, results)

    overload = [res for read, res in pairs if read.regime == "overload"]
    assert overload
    assert sum(r.anomaly_detected for r in overload) / len(overload) > 0.5


def test_detects_bearing_fault(detector: AnomalyDetector) -> None:
    readings = generate_series("Hydraulic Pump", 3000, seed=21)
    results = detector.detect_series(readings, "Hydraulic Pump")
    pairs = align_with_readings(readings, results)

    bearing = [res for read, res in pairs if read.regime == "bearing_fault"]
    assert any(r.anomaly_detected for r in bearing)


def test_healthy_series_produces_few_alarms(detector: AnomalyDetector) -> None:
    readings = generate_healthy_series("Hydraulic Pump", 800, seed=5)
    results = detector.detect_series(readings, "Hydraulic Pump")

    rate = sum(r.anomaly_detected for r in results) / len(results)
    assert rate < 0.05, f"false-positive rate {rate:.3f} is too high"


def test_empty_series_returns_no_results(detector: AnomalyDetector) -> None:
    assert detector.detect_series([], "Hydraulic Pump") == []


def test_affected_parameters_are_specific(detector: AnomalyDetector) -> None:
    """A bearing fault must name vibration, not every channel.

    If everything is named, the field carries no information.
    """
    readings = generate_series("Hydraulic Pump", 3000, seed=21)
    results = detector.detect_series(readings, "Hydraulic Pump")

    during = [
        res for read, res in align_with_readings(readings, results)
        if read.regime == "bearing_fault"
    ]
    assert during
    named = [r.affected_parameters for r in during]
    mean_named = sum(len(n) for n in named) / len(named)
    assert mean_named < 3.5
    assert any("vibration" in params for params in named)


def test_healthy_samples_name_few_parameters(detector: AnomalyDetector) -> None:
    readings = generate_healthy_series("Hydraulic Pump", 600, seed=5)
    results = detector.detect_series(readings, "Hydraulic Pump")

    mean_named = sum(len(r.affected_parameters) for r in results) / len(results)
    assert mean_named < 1.5


def test_parameter_scores_cover_all_channels(detector: AnomalyDetector) -> None:
    readings = generate_series("Hydraulic Pump", 100, seed=2)
    result = detector.detect_series(readings, "Hydraulic Pump")[-1]

    assert set(result.parameter_scores) == set(FeatureSpec().raw)
    for detail in result.parameter_scores.values():
        assert 0.0 <= detail["degradation"] <= 1.0
        assert detail["direction"] in {"normal", "high", "low", "unknown"}


def test_alarm_breach_forces_high_score(detector: AnomalyDetector) -> None:
    """A parameter past its alarm limit is an anomaly regardless of the forest."""
    from app.ml.baselines import get_profile
    from app.ml.degradation import evaluate_all

    band = get_profile("Hydraulic Pump").band("temperature")
    values = {"temperature": band.alarm_high * 1.2, "vibration": 3.0}
    result = detector._combine(0.0, evaluate_all(values, get_profile("Hydraulic Pump")), None, None)

    assert result.anomaly_score >= 0.9
    assert result.anomaly_detected is True


def test_detect_series_does_not_mutate_input(detector: AnomalyDetector) -> None:
    readings = generate_healthy_series("Hydraulic Pump", 100, seed=9)
    before = [r.as_dict() for r in readings]

    detector.detect_series(readings, "Hydraulic Pump")

    assert [r.as_dict() for r in readings] == before


def test_detection_is_deterministic(detector: AnomalyDetector) -> None:
    readings = generate_series("Hydraulic Pump", 400, seed=13)

    first = detector.detect_series(readings, "Hydraulic Pump")
    second = detector.detect_series(readings, "Hydraulic Pump")

    assert [r.anomaly_score for r in first] == [r.anomaly_score for r in second]


# --- Registry ---

def test_save_and_load_roundtrip(tmp_path, training_matrix) -> None:
    original = AnomalyDetector(seed=1).fit(training_matrix)
    metadata = registry.new_metadata(original, len(training_matrix), ["Hydraulic Pump"])

    registry.save(original, tmp_path, metadata)
    loaded = registry.load(tmp_path)

    assert loaded.feature_names == original.feature_names
    assert loaded._normalize(-0.5) == pytest.approx(original._normalize(-0.5))


def test_metadata_roundtrip(tmp_path, training_matrix) -> None:
    original = AnomalyDetector(seed=1).fit(training_matrix)
    metadata = registry.new_metadata(original, len(training_matrix), ["Boiler"])
    registry.save(original, tmp_path, metadata)

    loaded = registry.load_metadata(tmp_path)

    assert loaded is not None
    assert loaded.model_version == MODEL_VERSION
    assert loaded.training_rows == len(training_matrix)
    assert loaded.equipment_types == ["Boiler"]


def test_load_missing_model_raises_helpfully(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="app.ml.train"):
        registry.load(tmp_path)


def test_load_or_none_returns_none_when_missing(tmp_path) -> None:
    assert registry.load_or_none(tmp_path) is None


def test_is_trained(tmp_path, training_matrix) -> None:
    assert registry.is_trained(tmp_path) is False
    registry.save(AnomalyDetector(seed=1).fit(training_matrix), tmp_path,
                  registry.new_metadata(AnomalyDetector(), 1, []))
    assert registry.is_trained(tmp_path) is True


# --- Evaluation ---

def test_evaluation_reports_both_recall_levels(detector: AnomalyDetector) -> None:
    report = evaluate_detector(detector, ["Hydraulic Pump"], samples=1500, seed=31)

    assert report.samples_evaluated > 0
    assert "precision" in report.per_sample
    assert "recall" in report.per_event
    assert report.per_regime is not None


def test_evaluation_shows_low_false_positive_rate(detector: AnomalyDetector) -> None:
    report = evaluate_detector(detector, ["Hydraulic Pump"], samples=1500, seed=31)

    assert report.per_sample["fpr"] < 0.10


def test_evaluation_event_recall_is_reasonable(detector: AnomalyDetector) -> None:
    """Every injected fault episode should be noticed at least once."""
    report = evaluate_detector(detector, ["Hydraulic Pump"], samples=3000, seed=31)

    assert report.per_event["episodes"] > 0
    assert report.per_event["recall"] > 0.7
