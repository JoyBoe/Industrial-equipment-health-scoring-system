"""Failure risk model tests.

The assertions here focus on the properties that make a risk number honest:
monotonicity, a stated horizon, an explicit calibration flag, and no false
reassurance for machines that are abnormal or silently unhealthy.
"""

import math

import pytest

from app.config import Settings, get_settings
from app.ml.baselines import get_profile
from app.ml.degradation import evaluate_all
from app.ml.risk import (
    DEFAULT_HAZARD_SENSITIVITY,
    DEFAULT_HORIZONS,
    PRIMARY_HORIZON_HOURS,
    DegradationHazardEstimator,
    classify_risk_level,
    healthy_probability,
)
from app.ml.scoring import normalise_weights

MOTOR = get_profile("Industrial Motor")
NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}


def estimate(values: dict, anomaly_score: float = 0.0, **settings_kwargs):
    settings = Settings(**settings_kwargs) if settings_kwargs else get_settings()
    estimator = DegradationHazardEstimator(
        base_hazard_per_hour=settings.base_hazard_per_hour,
        sensitivity=settings.risk_hazard_sensitivity,
        anomaly_multiplier=settings.risk_anomaly_multiplier,
    )
    degradations = evaluate_all(values, MOTOR)
    active = [n for n, d in degradations.items() if d.direction != "unknown"]
    weights = normalise_weights(settings.parameter_weights, active)
    return estimator.estimate(degradations, weights, anomaly_score, 100.0, settings)


# --- Horizons ---

def test_every_horizon_is_reported() -> None:
    result = estimate(NOMINAL)

    for hours in DEFAULT_HORIZONS:
        assert hours in result.horizon_probabilities


def test_horizon_probabilities_increase_with_time() -> None:
    """More time must never mean less risk."""
    result = estimate({**NOMINAL, "vibration": 12.0})
    ordered = [result.horizon_probabilities[h] for h in sorted(result.horizon_probabilities)]

    assert ordered == sorted(ordered)


def test_probabilities_within_bounds() -> None:
    for vibration in (3.0, 8.0, 12.0, 20.0):
        result = estimate({**NOMINAL, "vibration": vibration})
        for probability in result.horizon_probabilities.values():
            assert 0.0 <= probability <= 1.0


def test_horizon_is_explicitly_stated() -> None:
    """A probability without a horizon is not interpretable."""
    result = estimate({**NOMINAL, "vibration": 10.0})

    assert result.horizon_hours == PRIMARY_HORIZON_HOURS


# --- Calibration honesty ---

def test_uncalibrated_model_is_flagged() -> None:
    """The default model must not claim to be fitted to failure data."""
    result = estimate({**NOMINAL, "vibration": 11.0})

    assert result.calibrated is False
    assert "not fitted" in result.notes or "placeholder" in result.notes


def test_calibration_flag_reflects_configuration() -> None:
    result = estimate({**NOMINAL, "vibration": 11.0}, risk_model_calibrated=True)

    assert result.calibrated is True


def test_method_is_named() -> None:
    assert estimate(NOMINAL).method == "degradation_hazard"


# --- No false reassurance ---

def test_healthy_machine_has_nonzero_risk() -> None:
    """Exactly zero would be a false claim, not a precise one."""
    result = estimate(NOMINAL)

    assert 0.0 < result.probability < 0.05


def test_healthy_probability_helper_is_positive() -> None:
    assert healthy_probability() > 0.0


def test_anomaly_alone_increases_risk() -> None:
    """An unusual pattern with every channel in band is still evidence."""
    baseline = estimate(NOMINAL, anomaly_score=0.0)
    elevated = estimate(NOMINAL, anomaly_score=0.9)

    assert elevated.probability > baseline.probability


def test_anomaly_only_risk_names_the_pattern() -> None:
    result = estimate(NOMINAL, anomaly_score=0.8)

    assert result.dominant_driver == "anomaly_pattern"


# --- Monotonicity ---

def test_risk_increases_with_vibration() -> None:
    probabilities = [
        estimate({**NOMINAL, "vibration": v}).probability
        for v in (4.0, 7.0, 9.0, 11.0, 12.5)
    ]

    assert probabilities == sorted(probabilities)


def test_risk_increases_with_temperature() -> None:
    probabilities = [
        estimate({**NOMINAL, "temperature": t}).probability
        for t in (70.0, 85.0, 95.0, 105.0)
    ]

    assert probabilities == sorted(probabilities)


def test_wear_out_is_superlinear() -> None:
    """The hazard must accelerate near the alarm limit.

    This is the property a linear blend cannot express, and the reason for
    using an exponential wear-out curve: the last stretch before failure is
    far more dangerous than an equal-sized step earlier on.
    """
    band = MOTOR.band("vibration")
    span = band.clamp_span()

    early = estimate({**NOMINAL, "vibration": band.ok_high + 0.2 * span}).probability
    late = estimate({**NOMINAL, "vibration": band.ok_high + 0.8 * span}).probability
    baseline = estimate(NOMINAL).probability

    early_excess = early - baseline
    late_excess = late - baseline

    # Equal degradation steps, but the later one must cost far more risk.
    assert late_excess > early_excess * 2


def test_multiple_degraded_channels_accumulate() -> None:
    """Several mildly degraded channels must beat one of the same size."""
    two = estimate(
        {**NOMINAL, "vibration": 9.5, "temperature": 90.0}
    ).probability
    one = estimate({**NOMINAL, "vibration": 9.5}).probability

    assert two > one


# --- Sensitivity ---

def test_higher_sensitivity_raises_risk() -> None:
    low = estimate({**NOMINAL, "vibration": 12.0}, risk_hazard_sensitivity=1.0)
    high = estimate({**NOMINAL, "vibration": 12.0}, risk_hazard_sensitivity=6.0)

    assert high.probability > low.probability


def test_higher_base_hazard_raises_risk() -> None:
    low = estimate({**NOMINAL, "vibration": 12.0}, base_hazard_per_hour=1e-5)
    high = estimate({**NOMINAL, "vibration": 12.0}, base_hazard_per_hour=1e-4)

    assert high.probability > low.probability


def test_zero_degradation_contributes_nothing() -> None:
    """A healthy channel must not add wear-out hazard."""
    with_vibration = evaluate_all({**NOMINAL, "vibration": 2.0}, MOTOR)
    assert with_vibration["vibration"].normalized == 0.0


def test_sensitivity_default_is_steep_enough_to_matter() -> None:
    """At full degradation the hazard must be materially amplified.

    A sensitivity that leaves the multiplier small would make the whole model
    indistinguishable from a linear blend.
    """
    multiplier = math.exp(DEFAULT_HAZARD_SENSITIVITY)

    assert multiplier > 20.0


# --- Risk levels ---

def test_risk_level_escalates_with_degradation() -> None:
    levels = [
        estimate({**NOMINAL, "vibration": v}).risk_level
        for v in (3.0, 10.0, 12.0, 12.5)
    ]
    order = {"Low": 0, "Moderate": 1, "High": 2, "Severe": 3}

    assert [order[level] for level in levels] == sorted(order[level] for level in levels)


def test_fully_degraded_machine_is_not_low_risk() -> None:
    """The risk channel must actually be able to say something.

    An earlier version classified risk on a 24-hour probability, which is
    always a fraction of a percent at a realistic MTBF, so it reported "Low"
    for a machine sitting at its alarm limit and was useless.
    """
    result = estimate({**NOMINAL, "vibration": 12.5})

    assert result.risk_level != "Low"


def test_risk_level_thresholds_configurable() -> None:
    settings = Settings(risk_moderate_min=0.001, risk_high_min=0.002, risk_severe_min=0.003)

    assert classify_risk_level(0.0015, settings) == "Moderate"


# --- Driver attribution ---

def test_dominant_driver_is_the_worst_channel() -> None:
    result = estimate({**NOMINAL, "vibration": 12.0, "temperature": 88.0})

    assert result.dominant_driver == "vibration"


def test_driver_shares_sum_to_one() -> None:
    result = estimate({**NOMINAL, "vibration": 11.0, "temperature": 92.0, "current": 45.0})

    assert sum(d["share"] for d in result.drivers) == pytest.approx(1.0, abs=0.01)


def test_healthy_machine_has_no_drivers() -> None:
    result = estimate(NOMINAL)

    assert result.drivers == []


def test_every_driver_is_ordered_by_share() -> None:
    result = estimate({**NOMINAL, "vibration": 12.0, "temperature": 90.0, "power": 24.0})

    shares = [d["share"] for d in result.drivers]
    assert shares == sorted(shares, reverse=True)


# --- Robustness ---

def test_empty_degradations_do_not_crash() -> None:
    settings = get_settings()
    estimator = DegradationHazardEstimator()

    result = estimator.estimate({}, {}, 0.0, 100.0, settings)

    assert result.probability > 0.0
    assert result.horizon_probabilities


def test_zero_anomaly_and_zero_degradation_is_baseline_only() -> None:
    result = estimate({**NOMINAL, "vibration": 1.0}, anomaly_score=0.0)

    assert result.probability == pytest.approx(
        estimate(NOMINAL, anomaly_score=0.0).probability, abs=1e-6
    )


def test_probability_conversion_matches_exponential_survival() -> None:
    """P = 1 - exp(-lambda*H), checked against the closed form."""
    settings = get_settings()
    estimator = DegradationHazardEstimator()
    rate = 1e-3

    assert estimator._probability(rate, 720) == pytest.approx(
        1 - math.exp(-rate * 720), rel=1e-9
    )


def test_zero_rate_gives_zero_probability() -> None:
    assert DegradationHazardEstimator()._probability(0.0, 720) == 0.0


def test_all_equipment_types_produce_a_usable_estimate() -> None:
    from app.ml.baselines import PROFILES

    for name, profile in PROFILES.items():
        values = {k: profile.bands[k].nominal for k in
                  ("temperature", "vibration", "pressure", "rpm", "current",
                   "voltage", "power")}
        values["load"] = 70.0
        degradations = evaluate_all(values, profile)
        settings = get_settings()
        weights = normalise_weights(settings.parameter_weights, list(degradations))
        result = DegradationHazardEstimator().estimate(
            degradations, weights, 0.0, 100.0, settings
        )
        assert 0.0 <= result.probability <= 1.0, name
        assert result.risk_level in {"Low", "Moderate", "High", "Severe"}, name
