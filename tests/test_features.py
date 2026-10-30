"""Tests for feature engineering.

The critical property is causality: features for sample t must not depend on
samples after t. A centred window would leak the future and inflate every
accuracy metric the detector later reports.
"""

import numpy as np
import pandas as pd
import pytest

from app.config import SENSOR_FEATURES
from app.ml.features import (
    DEFAULT_WINDOW,
    FeatureSpec,
    add_load_derived,
    add_power_consistency,
    add_rolling_features,
    build_features,
    detection_matrix,
    feature_importance_names,
    impute,
    to_frame,
)
from app.ml.synthetic import generate_series


@pytest.fixture
def frame() -> pd.DataFrame:
    return to_frame(generate_series("Hydraulic Pump", 400, seed=42))


# --- to_frame ---

def test_to_frame_extracts_channels(frame: pd.DataFrame) -> None:
    assert list(frame.columns) == ["timestamp", *SENSOR_FEATURES]
    assert len(frame) == 400


def test_to_frame_sorts_chronologically() -> None:
    readings = generate_series(samples=50, seed=1)
    shuffled = list(reversed(readings))
    frame = to_frame(shuffled)

    assert frame["timestamp"].is_monotonic_increasing


def test_to_frame_handles_empty_input() -> None:
    empty = to_frame([])

    assert empty.empty
    assert list(empty.columns) == ["timestamp", *SENSOR_FEATURES]


def test_to_frame_coerces_bad_values_to_nan() -> None:
    from app.models.sensor_reading import SensorReading  # noqa: F401

    class Fake:
        timestamp = pd.Timestamp("2026-01-01T00:00:00Z")
        temperature = "not-a-number"
        vibration = 1.0
        pressure = 1.0
        rpm = 1.0
        current = 1.0
        voltage = 1.0
        power = 1.0
        load = 1.0

    frame = to_frame([Fake()])

    assert pd.isna(frame["temperature"].iloc[0])


# --- Imputation ---

def test_impute_fills_gaps_from_trailing_median() -> None:
    rows = []
    for i in range(20):
        row = {c: 10.0 for c in SENSOR_FEATURES}
        row["timestamp"] = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=i)
        rows.append(row)
    rows[10]["temperature"] = np.nan
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)

    result = impute(frame, window=5)

    assert not result["temperature"].isna().any()
    assert result["temperature"].iloc[10] == pytest.approx(10.0)


def test_impute_leaves_no_nan(frame: pd.DataFrame) -> None:
    holed = frame.copy()
    holed.loc[100:140, "vibration"] = np.nan
    holed.loc[200, "pressure"] = np.nan

    result = impute(holed)

    assert not result[list(SENSOR_FEATURES)].isna().any().any()


def test_impute_handles_fully_null_channel() -> None:
    rows = []
    for i in range(10):
        row = {c: 5.0 for c in SENSOR_FEATURES}
        row["timestamp"] = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=i)
        row["vibration"] = np.nan
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)

    result = impute(frame)

    # Cannot invent a value from no history; must not crash or emit NaN-driven
    # garbage into the rolling statistics.
    assert len(result) == 10


# --- Causality ---

def test_features_are_causal() -> None:
    """Features at time t must not change when later data changes.

    This is the single most important property of the whole feature pipeline.
    A centred or bidirectional window would leak the future and make every
    accuracy figure the detector later reports meaningless.

    The comparison truncates one *fixed* frame. Generating two series of
    different lengths would not test causality at all, because the generator
    plans a different fault timeline for each length.
    """
    frame = to_frame(generate_series(samples=300, seed=42))
    full = build_features(frame)
    truncated = build_features(frame.iloc[:200].copy())

    cutoff = 180
    full_slice = full.iloc[:cutoff].reset_index(drop=True)
    trunc_slice = truncated.iloc[:cutoff].reset_index(drop=True)

    for column in (
        "temperature_mean",
        "temperature_std",
        "temperature_slope",
        "temperature_z",
        "vibration_mean",
        "power_residual",
    ):
        assert np.allclose(
            full_slice[column].fillna(0).to_numpy(),
            trunc_slice[column].fillna(0).to_numpy(),
        ), f"{column} leaks future data"


def test_rolling_mean_uses_only_past_values() -> None:
    values = np.arange(50, dtype=float)
    frame = pd.DataFrame({c: values.copy() for c in SENSOR_FEATURES})
    frame["timestamp"] = pd.to_datetime("2026-01-01", utc=True) + pd.to_timedelta(
        np.arange(50), unit="m"
    )
    spec = FeatureSpec(window=5, rolling_std=False, delta=False, slope=False, zscore=False)

    result = add_rolling_features(frame, spec)

    # Trailing window of 5 at index 10 covers indices 6..10 and excludes 11+.
    assert result["temperature_mean"].iloc[10] == pytest.approx(np.mean(values[6:11]))


# --- Rolling statistics ---

def test_rolling_std_detects_variance_increase() -> None:
    """Rising variance is the early fault signal; the feature must capture it."""
    readings = generate_series("Hydraulic Pump", 4320, seed=42)
    features = build_features(to_frame(readings))
    regimes = np.array([r.regime for r in readings])

    nominal_std = features.loc[regimes == "nominal", "vibration_std"].mean()
    bearing_std = features.loc[regimes == "bearing_fault", "vibration_std"].mean()

    assert bearing_std > nominal_std * 2


def test_pressure_variance_rises_under_gradual_wear() -> None:
    """Degrading seals show as instability before the mean drifts."""
    readings = generate_series("Hydraulic Pump", 4320, seed=42)
    features = build_features(to_frame(readings))
    regimes = np.array([r.regime for r in readings])

    nominal = features.loc[regimes == "nominal", "pressure_std"].mean()
    degraded = features.loc[regimes == "degraded", "pressure_std"].mean()

    assert degraded > nominal * 2


def test_constant_channel_does_not_produce_infinite_z() -> None:
    frame = pd.DataFrame({c: [5.0] * 30 for c in SENSOR_FEATURES})
    frame["timestamp"] = pd.to_datetime("2026-01-01", utc=True) + pd.to_timedelta(
        np.arange(30), unit="m"
    )

    result = add_rolling_features(frame)

    assert np.isfinite(result["temperature_z"]).all()


# --- Derived features ---

def test_power_residual_detects_overload() -> None:
    readings = generate_series("Hydraulic Pump", 4320, seed=42)
    features = add_power_consistency(to_frame(readings))
    regimes = np.array([r.regime for r in readings])

    nominal = features.loc[regimes == "nominal", "power_residual"].abs().mean()
    overload = features.loc[regimes == "overload", "power_residual"].abs().mean()

    assert overload > nominal * 2


def test_current_per_rpm_detects_stall() -> None:
    readings = generate_series("Hydraulic Pump", 4320, seed=42)
    features = add_load_derived(to_frame(readings))
    regimes = np.array([r.regime for r in readings])

    nominal = features.loc[regimes == "nominal", "current_per_rpm"].mean()
    overload = features.loc[regimes == "overload", "current_per_rpm"].mean()

    assert overload > nominal * 1.4


# --- Matrix assembly ---

def test_detection_matrix_has_no_nan_or_inf(frame: pd.DataFrame) -> None:
    holed = frame.copy()
    holed.loc[50:80, "vibration"] = np.nan
    holed.loc[90:120, "pressure"] = np.nan

    matrix = detection_matrix(holed)
    values = matrix.to_numpy()

    assert not np.isnan(values).any()
    assert not np.isinf(values).any()


def test_detection_matrix_drops_unsafe_leading_rows(frame: pd.DataFrame) -> None:
    matrix = detection_matrix(frame)

    # Partial windows are not trustworthy training signal.
    assert len(matrix) == len(frame) - DEFAULT_WINDOW


def test_detection_matrix_column_order_matches_names(frame: pd.DataFrame) -> None:
    matrix = detection_matrix(frame)

    assert list(matrix.columns) == feature_importance_names()


def test_detection_matrix_on_empty_input() -> None:
    matrix = detection_matrix(to_frame([]))

    assert matrix.empty
    assert list(matrix.columns) == feature_importance_names()


def test_feature_names_are_unique() -> None:
    names = feature_importance_names()

    assert len(names) == len(set(names))


def test_spec_names_respect_disabled_features() -> None:
    spec = FeatureSpec(rolling_std=False, delta=False, slope=False, zscore=False)

    names = spec.names()

    assert all(name.endswith("_mean") for name in names)
