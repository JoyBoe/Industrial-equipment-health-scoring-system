"""Health score engine tests.

The central invariant asserted throughout is **decomposability**: the points
listed as contributing factors must sum to the points actually deducted. A score
whose explanation does not add up is worse than no score, because it invites
operators to act on a cause that was not the cause.
"""

import pytest

from app.config import Settings
from app.ml.baselines import get_profile
from app.ml.scoring import (
    classify_risk,
    classify_status,
    clamp01,
    compute_health_score,
    impact_label,
    normalise_weights,
)
from app.models.base import HealthStatus, RiskLevel
from app.services.explainability import (
    build_assessment,
    generate_explanation,
    generate_recommendation,
)

MOTOR = get_profile("Industrial Motor")

HEALTHY = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}


def score(values: dict, profile=None, **kwargs):
    profile = profile or MOTOR
    breakdown, factors = compute_health_score(values, profile, **kwargs)
    return breakdown, factors, build_assessment(
        breakdown, factors, anomaly_detected=breakdown.anomaly_score >= 0.5
    )


# --- Status classification ---

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100.0, HealthStatus.EXCELLENT.value),
        (90.0, HealthStatus.EXCELLENT.value),
        (89.99, HealthStatus.HEALTHY.value),
        (75.0, HealthStatus.HEALTHY.value),
        (74.99, HealthStatus.WARNING.value),
        (50.0, HealthStatus.WARNING.value),
        (49.99, HealthStatus.POOR.value),
        (25.0, HealthStatus.POOR.value),
        (24.99, HealthStatus.CRITICAL.value),
        (0.0, HealthStatus.CRITICAL.value),
    ],
)
def test_status_boundaries(value: float, expected: str) -> None:
    assert classify_status(value) == expected


def test_status_thresholds_are_configurable() -> None:
    settings = Settings(healthy_min=95.0, excellent_min=99.0)

    assert classify_status(96.0, settings) == HealthStatus.HEALTHY.value
    assert classify_status(96.0) == HealthStatus.EXCELLENT.value


# --- Risk classification ---

@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.0, RiskLevel.LOW.value),
        (0.09, RiskLevel.LOW.value),
        (0.10, RiskLevel.MODERATE.value),
        (0.24, RiskLevel.MODERATE.value),
        (0.25, RiskLevel.HIGH.value),
        (0.49, RiskLevel.HIGH.value),
        (0.50, RiskLevel.SEVERE.value),
        (1.0, RiskLevel.SEVERE.value),
    ],
)
def test_risk_boundaries(probability: float, expected: str) -> None:
    """Thresholds are applied to the 30-day horizon probability."""
    assert classify_risk(probability) == expected


def test_risk_thresholds_match_configured_values() -> None:
    from app.config import get_settings

    settings = get_settings()
    assert settings.risk_moderate_min == 0.10
    assert settings.risk_high_min == 0.25
    assert settings.risk_severe_min == 0.50


def test_risk_thresholds_are_configurable() -> None:
    settings = Settings(risk_moderate_min=0.05)

    assert classify_risk(0.1, settings) == RiskLevel.MODERATE.value


@pytest.mark.parametrize("value", [-1.0, 2.0, None, float("nan")])
def test_clamp01_handles_out_of_range(value) -> None:
    result = clamp01(value)

    assert 0.0 <= result <= 1.0


# --- Score bounds and sanity ---

def test_healthy_machine_scores_100() -> None:
    breakdown, factors, assessment = score(HEALTHY)

    assert breakdown.health_score == 100.0
    assert assessment.status == HealthStatus.EXCELLENT.value
    assert factors == []


def test_score_always_within_bounds() -> None:
    extreme = {
        "temperature": 200.0, "vibration": 30.0, "pressure": 20.0, "rpm": 100.0,
        "current": 70.0, "voltage": 300.0, "power": 40.0, "load": 150.0,
    }
    breakdown, _, _ = score(extreme)

    assert 0.0 <= breakdown.health_score <= 100.0


def test_score_decreases_monotonically_with_vibration() -> None:
    scores = [
        score({**HEALTHY, "vibration": v})[0].health_score
        for v in (3.0, 6.0, 9.0, 12.0)
    ]

    assert scores == sorted(scores, reverse=True)


def test_score_cannot_go_below_zero() -> None:
    """Even a total budget cannot produce a negative score."""
    settings = Settings(sensor_points_budget=100.0, anomaly_points_budget=0.0, risk_points_budget=0.0)
    breakdown, _ = compute_health_score(
        {**HEALTHY, "vibration": 30.0, "temperature": 200.0}, MOTOR, settings=settings
    )

    assert breakdown.health_score >= 0.0


def test_budgets_over_100_rejected() -> None:
    settings = Settings(sensor_points_budget=80.0, anomaly_points_budget=50.0, risk_points_budget=50.0)

    with pytest.raises(ValueError, match="exceeds"):
        settings.validate_budgets()


def test_budgets_over_100_rejected_at_score_time() -> None:
    settings = Settings(sensor_points_budget=90.0, anomaly_points_budget=20.0, risk_points_budget=20.0)

    with pytest.raises(ValueError, match="exceeds"):
        compute_health_score(HEALTHY, MOTOR, settings=settings)


# --- Decomposability: the core invariant ---

def test_factor_penalties_sum_to_total_deduction() -> None:
    """The explanation's arithmetic must reconcile with the score."""
    values = {
        "temperature": 95.0, "vibration": 11.0, "pressure": 6.0, "rpm": 1200.0,
        "current": 44.0, "voltage": 402.0, "power": 24.0, "load": 85.0,
    }
    breakdown, factors, _ = score(values, anomaly_score=0.4, failure_probability=0.3)

    total_deducted = breakdown.sensor_penalty + breakdown.anomaly_penalty + breakdown.risk_penalty
    factor_total = sum(f["penalty"] for f in factors)

    assert factor_total == pytest.approx(total_deducted, abs=0.05)
    assert breakdown.health_score == pytest.approx(100.0 - total_deducted, abs=0.05)


def test_factors_sorted_by_penalty() -> None:
    values = {
        "temperature": 100.0, "vibration": 12.0, "pressure": 5.9, "current": 46.0,
    }
    _, factors, _ = score(values, anomaly_score=0.5, failure_probability=0.5)

    penalties = [f["penalty"] for f in factors]
    assert penalties == sorted(penalties, reverse=True)


def test_no_zero_penalty_factors() -> None:
    """A factor costing nothing is not a cause."""
    values = {**HEALTHY, "vibration": 9.0}
    _, factors, _ = score(values)

    assert all(f["penalty"] > 0 for f in factors)


def test_contribution_shares_sum_to_one() -> None:
    values = {**HEALTHY, "temperature": 95.0, "vibration": 11.0}
    _, factors, _ = score(values, anomaly_score=0.4)

    assert sum(f["contribution_share"] for f in factors) == pytest.approx(1.0, abs=0.01)


# --- Missing data ---

def test_missing_channel_does_not_penalise() -> None:
    """A dropped channel is a telemetry problem, not a health problem."""
    full, _, _ = score(HEALTHY)
    partial, factors, _ = score({"temperature": 68.0, "vibration": 2.8})

    assert partial.health_score == full.health_score
    assert factors == []


def test_partial_reading_is_scored_against_reported_channels_only() -> None:
    """Weights renormalise so absent channels are not silently zeroed."""
    weights = normalise_weights(
        {"temperature": 0.22, "vibration": 0.28}, ["temperature", "vibration"]
    )

    assert sum(weights.values()) == pytest.approx(1.0)


def test_unknown_channel_weight_is_shared_evenly() -> None:
    weights = normalise_weights({"temperature": 0.22}, ["humidity", "flow"])

    assert weights["humidity"] == pytest.approx(0.5)


def test_empty_channel_set_yields_zero_weights() -> None:
    assert normalise_weights({"temperature": 0.22}, []) == {}


# --- Configurability ---

def test_weight_changes_the_score() -> None:
    """Concentrating all weight on vibration must cost more than the default mix."""
    vibration_heavy = Settings(
        vibration_weight=1.0, temperature_weight=0.0, power_weight=0.0,
        current_weight=0.0, pressure_weight=0.0, rpm_weight=0.0,
        voltage_weight=0.0, load_weight=0.0,
    )
    values = {**HEALTHY, "vibration": 8.0}

    default_score, _, _ = score(values)
    heavy, _ = compute_health_score(values, MOTOR, settings=vibration_heavy)

    assert heavy.health_score < default_score.health_score


def test_budget_reallocation_changes_score() -> None:
    """Shifting the budget from sensors to the anomaly term must be observable.

    The direction of the score change depends on which term dominates the
    input, so the assertion is that the configuration matters, not that the
    score necessarily falls.
    """
    lean_sensor = Settings(
        sensor_points_budget=40.0, anomaly_points_budget=40.0, risk_points_budget=20.0
    )
    values = {**HEALTHY, "vibration": 9.0}

    default, _, _ = score(values, anomaly_score=0.5)
    adjusted, _ = compute_health_score(values, MOTOR, anomaly_score=0.5, settings=lean_sensor)

    assert adjusted.health_score != default.health_score
    # The anomaly term was given a larger budget, so it must now cost more.
    assert adjusted.anomaly_penalty > default.anomaly_penalty


# --- Critical policy ---

def test_alarm_breach_never_reports_healthy() -> None:
    """A channel past its alarm limit guarantees at most a Warning."""
    band = MOTOR.band("vibration")
    breakdown, _, assessment = score({**HEALTHY, "vibration": band.alarm_high + 1})

    assert breakdown.critical_parameter is True
    assert assessment.status == HealthStatus.WARNING.value


def test_critical_floor_derived_from_configured_thresholds() -> None:
    """The floor follows the thresholds, so retuning them retunes the policy.

    A lightly weighted parameter at its alarm limit is used, because its linear
    penalty falls *below* the floor -- which is exactly the case the floor
    exists to cover.
    """
    strict = Settings(healthy_min=90.0, excellent_min=98.0)
    band = MOTOR.band("voltage")
    # Voltage carries only 0.06 of the weight, so a total excursion there costs
    # roughly 3.7 points linearly: well short of the 10.01 floor.
    breakdown, _ = compute_health_score(
        {**HEALTHY, "voltage": band.alarm_low}, MOTOR, settings=strict
    )

    assert breakdown.critical_parameter is True
    assert breakdown.sensor_penalty == pytest.approx(10.01, abs=0.01)
    assert breakdown.health_score == pytest.approx(89.99, abs=0.01)


def test_critical_floor_never_lowers_a_large_penalty() -> None:
    """The floor is a minimum, not a replacement for the real penalty."""
    breakdown, _ = compute_health_score(
        {**HEALTHY, "vibration": MOTOR.band("vibration").alarm_high}, MOTOR
    )

    assert breakdown.sensor_penalty > 10.01


def test_below_alarm_limit_is_not_critical() -> None:
    breakdown, _, _ = score({**HEALTHY, "vibration": 9.0})

    assert breakdown.critical_parameter is False


# --- Equipment-type differentiation ---

def test_same_values_score_differently_by_type() -> None:
    """170 C is an excursion on a motor and normal on a boiler.

    Other channels are held at each machine's own nominal so temperature is the
    only thing that differs; otherwise the comparison is confounded and any
    difference could come from a different channel tripping the critical floor.
    """
    motor_profile = get_profile("Industrial Motor")
    boiler_profile = get_profile("Boiler")

    common = {"vibration": 3.0, "voltage": 400.0, "load": 70.0}
    motor_values = {
        "temperature": 170.0, "pressure": 4.2, "rpm": 1450.0, "current": 35.0,
        "power": 21.0, **common,
    }
    boiler_values = {
        "temperature": 170.0, "pressure": 11.0, "rpm": 1800.0, "current": 280.0,
        "power": 160.0, **common,
    }

    motor, _, _ = score(motor_values, profile=motor_profile)
    boiler, _, _ = score(boiler_values, profile=boiler_profile)

    assert boiler.health_score == 100.0
    assert motor.health_score < boiler.health_score
    assert motor.critical_parameter is True
    assert boiler.critical_parameter is False


def test_every_profile_can_score_every_channel() -> None:
    """No profile may raise or produce NaN for a full reading.

    Nominal is not a perfect 100: a healthy machine still carries a baseline
    failure risk, so it scores marginally below. Reporting exactly 100 would
    imply zero chance of failure, which is a false reassurance.
    """
    from app.ml.baselines import PROFILES

    for name, profile in PROFILES.items():
        values = {
            "temperature": profile.bands["temperature"].nominal,
            "vibration": profile.bands["vibration"].nominal,
            "pressure": profile.bands["pressure"].nominal,
            "rpm": profile.bands["rpm"].nominal,
            "current": profile.bands["current"].nominal,
            "voltage": profile.bands["voltage"].nominal,
            "power": profile.bands["power"].nominal,
            "load": 70.0,
        }
        breakdown, _ = compute_health_score(values, profile)
        assert breakdown.health_score >= 99.0, f"{name} not near-perfect at nominal"
        assert breakdown.health_score <= 100.0


# --- Explanation ---

def test_explanation_of_healthy_machine() -> None:
    breakdown, factors, assessment = score(HEALTHY)

    assert "100" in assessment.explanation
    assert "within" in assessment.explanation.lower()


def test_explanation_names_the_dominant_cause() -> None:
    breakdown, factors, assessment = score({**HEALTHY, "vibration": 12.5})

    assert "vibration" in assessment.explanation.lower()


def test_explanation_mentions_score_change() -> None:
    breakdown, factors = compute_health_score({**HEALTHY, "vibration": 11.0}, MOTOR)
    text = generate_explanation(breakdown, factors, previous_score=90.0)

    assert "decreased" in text.lower()


def test_explanation_reports_stable_score() -> None:
    breakdown, factors = compute_health_score({**HEALTHY, "vibration": 11.0}, MOTOR)
    text = generate_explanation(breakdown, factors, previous_score=breakdown.health_score)

    assert "stable" in text.lower()


def test_explanation_lists_healthy_parameters() -> None:
    breakdown, factors, assessment = score({**HEALTHY, "vibration": 12.0})

    assert "within limits" in assessment.explanation.lower()


def test_recommendation_reflects_urgency() -> None:
    """Recommendation wording must match the computed status band."""
    _, _, healthy = score(HEALTHY)
    _, _, terrible = score(
        {"temperature": 200.0, "vibration": 30.0, "pressure": 20.0, "rpm": 100.0,
         "current": 70.0, "voltage": 300.0, "power": 40.0, "load": 150.0}
    )

    assert "no action" in healthy.recommendation.lower()
    assert terrible.status == "Poor"
    assert "promptly" in terrible.recommendation.lower()


def test_critical_machine_is_never_told_to_continue_monitoring() -> None:
    """The most damaging possible output is a calm message on a failed machine.

    Driven by the anomaly and risk terms alone, so no individual parameter is
    named -- the recommendation must still escalate.
    """
    _, _, assessment = score(
        {**HEALTHY, "vibration": 8.0},
        anomaly_score=1.0,
        failure_probability=0.95,
    )

    assert assessment.status in {"Warning", "Poor", "Critical"}
    assert "no action" not in assessment.recommendation.lower()
    assert "continue monitoring" not in assessment.recommendation.lower()


def test_recommendation_is_specific_to_the_cause() -> None:
    _, _, vibration = score({**HEALTHY, "vibration": 12.0})
    _, _, thermal = score({**HEALTHY, "temperature": 100.0})

    assert "bearing" in vibration.recommendation.lower()
    assert "cooling" in thermal.recommendation.lower()


def test_recommendation_mentions_multiple_causes() -> None:
    _, _, assessment = score({**HEALTHY, "vibration": 11.0, "temperature": 90.0})

    assert "also review" in assessment.recommendation.lower()


def test_explanation_never_invents_a_cause() -> None:
    """Every parameter named in the text must be a real factor or a cleared one."""
    values = {**HEALTHY, "vibration": 11.0}
    breakdown, factors, assessment = score(values)
    text = assessment.explanation.lower()

    for factor in factors:
        assert factor["parameter"] in text or factor["source"] != "sensor"

    # Pressure was never mentioned as a problem.
    assert "pressure has moved" not in text


@pytest.mark.parametrize(
    ("share", "expected"),
    [(0.0, "negligible"), (0.02, "minor"), (0.10, "moderate"), (0.30, "significant"), (0.9, "severe")],
)
def test_impact_labels(share: float, expected: str) -> None:
    assert impact_label(share) == expected
