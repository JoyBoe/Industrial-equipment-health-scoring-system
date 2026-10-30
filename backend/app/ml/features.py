"""Preprocessing and feature engineering for the ML pipeline.

Raw telemetry is a poor model input. Each channel is a noisy point sample, and
a point sample cannot distinguish "this machine is briefly off-nominal" from
"this machine is degrading". The features built here add the temporal context
that makes gradual failure visible:

* **rolling mean** — the smoothed level, far more stable than a single sample
* **rolling standard deviation** — rising variance is an early fault signature,
  often visible well before the mean moves
* **first difference** — rate of change between consecutive samples
* **rolling slope** — sustained trend, distinguishing a transient from wear
* **z-score vs baseline** — deviation from the machine's own nominal, in
  units of its normal spread
* **power consistency residual** — measured power versus the power implied by
  V and I; a growing residual indicates a mechanical load rise or a degrading
  winding, which no single channel reveals

All rolling operations are strictly backward-looking. `shift(0)` with a
trailing window is used deliberately so that the features for sample *t* never
contain information from *t+1*; using a centred window would leak the future
into training and make detection accuracy meaningless.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.config import SENSOR_FEATURES

#: Default trailing window, in samples. At the 1-minute ingest cadence this is
#: 15 minutes, long enough to suppress single-sample noise while still
#: responding inside a realistic maintenance interval.
DEFAULT_WINDOW = 15

#: Short window for responsiveness to sudden changes such as a trip or impact.
SHORT_WINDOW = 5


@dataclass(frozen=True)
class FeatureSpec:
    """Names of the engineered columns, for reuse by the detector and scorer."""

    raw: tuple[str, ...] = SENSOR_FEATURES
    rolling_mean: bool = True
    rolling_std: bool = True
    delta: bool = True
    slope: bool = True
    zscore: bool = True
    window: int = DEFAULT_WINDOW

    def names(self) -> list[str]:
        """Column names produced by `build_features`, excluding raw channels."""
        if not (self.rolling_mean or self.rolling_std or self.delta or self.slope or self.zscore):
            return []
        out: list[str] = []
        if self.rolling_mean:
            out += [f"{c}_mean" for c in self.raw]
        if self.rolling_std:
            out += [f"{c}_std" for c in self.raw]
        if self.delta:
            out += [f"{c}_delta" for c in self.raw]
        if self.slope:
            out += [f"{c}_slope" for c in self.raw]
        if self.zscore:
            out += [f"{c}_z" for c in self.raw]
        return out


def to_frame(readings) -> pd.DataFrame:
    """Convert readings (ORM objects or dicts) into a time-indexed frame.

    Returns only the sensor channels, sorted chronologically. An empty input
    yields an empty frame with the correct columns, so callers do not need to
    special-case a machine with no history.
    """
    columns = ["timestamp", *SENSOR_FEATURES]
    if not readings:
        return pd.DataFrame(columns=columns)

    rows = [
        {column: getattr(reading, column, None) for column in columns}
        for reading in readings
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    # NaT timestamps cannot be ordered meaningfully; drop them explicitly
    # rather than letting them sort arbitrarily.
    frame = frame.dropna(subset=["timestamp"])
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    for column in SENSOR_FEATURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def impute(frame: pd.DataFrame, window: int = DEFAULT_WINDOW) -> pd.DataFrame:
    """Fill missing channels from local history.

    Telemetry dropouts are normal. Each gap is filled with the trailing median
    of the same channel, then with the profile-independent series median, then
    with the column mean. Restricting the fill to *trailing* data avoids
    leaking future values into earlier rows.
    """
    result = frame.copy()
    for column in SENSOR_FEATURES:
        if column not in result:
            continue
        series = result[column]
        if series.isna().all():
            continue
        # Trailing median: `shift(1)` then rolling, so a value is never filled
        # from itself or from a later sample.
        filled = series.shift(1).rolling(window=window, min_periods=1).median()
        result[column] = series.fillna(filled)
        result[column] = result[column].fillna(result[column].median())
    return result


def add_rolling_features(
    frame: pd.DataFrame, spec: FeatureSpec | None = None
) -> pd.DataFrame:
    """Add rolling and differenced features for every raw channel."""
    spec = spec or FeatureSpec()
    result = frame.copy()
    window = max(spec.window, 2)

    for column in spec.raw:
        series = result[column]
        if spec.rolling_mean:
            result[f"{column}_mean"] = series.rolling(window, min_periods=1).mean()
        if spec.rolling_std:
            # A single sample has zero spread, which would read as "perfectly
            # steady"; min_periods=2 avoids a misleading leading value.
            result[f"{column}_std"] = series.rolling(window, min_periods=2).std()
        if spec.delta:
            result[f"{column}_delta"] = series.diff()
        if spec.slope:
            # Linear fit over the window, per sample. Computed with a rolling
            # covariance so it stays vectorised.
            rolling_mean = series.rolling(window, min_periods=2).mean()
            index = pd.Series(np.arange(len(series), dtype=float), index=series.index)
            rolling_index_mean = index.rolling(window, min_periods=2).mean()
            cov = (series - rolling_mean).mul(index - rolling_index_mean).rolling(
                window, min_periods=2
            ).sum()
            var = (index - rolling_index_mean).pow(2).rolling(window, min_periods=2).sum()
            result[f"{column}_slope"] = cov.div(var.replace(0, np.nan))
        if spec.zscore:
            mean = result.get(f"{column}_mean", series.rolling(window, min_periods=1).mean())
            std = result.get(f"{column}_std", series.rolling(window, min_periods=2).std())
            # Guard the zero-variance case: a constant channel (a stationary
            # transformer reporting 0 RPM) is not infinitely anomalous.
            result[f"{column}_z"] = (
                (series - mean) / std.replace(0, np.nan)
            ).clip(-10, 10).fillna(0.0)

    return result


def add_power_consistency(frame: pd.DataFrame, power_factor: float = 0.88) -> pd.DataFrame:
    """Add the residual between measured and electrically-implied power.

    Three-phase power is P = sqrt(3) * V * I * PF. Comparing it with the
    reported power yields a single scalar that rises when a machine is
    fighting an increasing mechanical load or when a winding degrades. No
    individual channel shows this.
    """
    result = frame.copy()
    voltage = result.get("voltage")
    current = result.get("current")
    power = result.get("power")
    if voltage is None or current is None or power is None:
        return result

    implied = np.sqrt(3) * voltage * current * power_factor / 1000.0
    residual = (power - implied) / implied.replace(0, np.nan)
    result["power_residual"] = residual.clip(-5, 5).fillna(0.0)
    return result


def add_load_derived(frame: pd.DataFrame) -> pd.DataFrame:
    """Add ratios that expose abnormal power draw or speed-load mismatch.

    A machine drawing full power while barely loaded is slipping its coupling
    or failing to start; the ratio makes that visible without knowing the
    machine's rated power.
    """
    result = frame.copy()
    power = result.get("power")
    load = result.get("load")
    if power is not None and load is not None:
        result["power_per_load"] = (
            power / load.replace(0, np.nan)
        ).clip(0, 50).fillna(0.0)
    rpm = result.get("rpm")
    current = result.get("current")
    if rpm is not None and current is not None:
        # Low speed with high current is the overload/stall signature.
        result["current_per_rpm"] = (
            current / rpm.replace(0, np.nan) * 1000.0
        ).clip(0, 1000).fillna(0.0)
    return result


def build_features(
    frame: pd.DataFrame,
    spec: FeatureSpec | None = None,
    power_factor: float = 0.88,
    do_impute: bool = True,
) -> pd.DataFrame:
    """Run the full preprocessing chain and return the feature frame.

    Ordering matters: imputation must precede the rolling statistics, or a
    dropout propagates NaN across an entire window.
    """
    spec = spec or FeatureSpec()
    working = impute(frame, window=spec.window) if do_impute else frame.copy()
    working = add_rolling_features(working, spec)
    working = add_power_consistency(working, power_factor)
    working = add_load_derived(working)
    return working


def detection_matrix(
    frame: pd.DataFrame,
    spec: FeatureSpec | None = None,
    power_factor: float = 0.88,
) -> pd.DataFrame:
    """Build features and return only the columns the detector consumes.

    The first `window` rows are dropped: their rolling statistics are computed
    from too little history to be meaningful, and training on them would teach
    the model that partial windows are normal.
    """
    spec = spec or FeatureSpec()
    features = build_features(frame, spec, power_factor)
    columns = [*spec.raw, *spec.names(), "power_residual", "power_per_load", "current_per_rpm"]
    available = [column for column in columns if column in features.columns]
    matrix = features[available].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if len(matrix) > spec.window:
        matrix = matrix.iloc[spec.window :]
    return matrix.reset_index(drop=True)


def feature_importance_names(spec: FeatureSpec | None = None) -> list[str]:
    """Column names in the order `detection_matrix` emits them."""
    spec = spec or FeatureSpec()
    return [
        *spec.raw,
        *spec.names(),
        "power_residual",
        "power_per_load",
        "current_per_rpm",
    ]
