"""Counterfactual attribution tests.

Attribution is only trustworthy if it is consistent with the published score,
so the central assertions compare counterfactual figures against the score
arithmetic rather than checking them in isolation.
"""

import pytest

from app.config import Settings
from app.ml.attribution import (
    UNITS,
    attribute_parameters,
    suppressed_by_policy,
)
from app.ml.baselines import get_profile
from app.ml.scoring import compute_health_score

MOTOR = get_profile("Industrial Motor")

NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}


def by_name(attribution) -> dict:
    return {item.parameter: item for item in attribution}


# --- Consistency with the score ---

def test_marginals_reconcile_with_deduction() -> None:
    """With no policy caps active, marginals must sum to the actual deduction."""
    values = {**NOMINAL, "vibration": 12.0, "temperature": 90.0, "current": 45.0}
    breakdown, _ = compute_health_score(values, MOTOR)
    attribution = attribute_parameters(values, MOTOR)

    total = sum(item.marginal_contribution for item in attribution)

    assert total == pytest.approx(100.0 - breakdown.health_score, abs=0.05)


def test_counterfactual_equals_recomputed_score() -> None:
    """Attribution must agree with an independent scoring call.

    Guards against the attribution path drifting from the scoring function.
    """
    values = {**NOMINAL, "vibration": 12.0, "temperature": 90.0}
    attribution = by_name(attribute_parameters(values, MOTOR))

    for item in attribution.values():
        expected_values = dict(values)
        expected_values[item.parameter] = item.nominal
        expected, _ = compute_health_score(expected_values, MOTOR)
        assert item.counterfactual_penalty == pytest.approx(expected.health_score, abs=0.01)


def test_marginal_is_never_negative() -> None:
    """Returning a channel to nominal cannot make the machine worse."""
    values = {**NOMINAL, "vibration": 12.0, "temperature": 90.0}

    for item in attribute_parameters(values, MOTOR):
        assert item.marginal_contribution >= -0.01


# --- Ordering and dominance ---

def test_attribution_sorted_by_marginal() -> None:
    values = {**NOMINAL, "vibration": 12.0, "temperature": 90.0, "power": 24.0}
    attribution = attribute_parameters(values, MOTOR)

    marginals = [item.marginal_contribution for item in attribution]
    assert marginals == sorted(marginals, reverse=True)


def test_exactly_one_dominant() -> None:
    values = {**NOMINAL, "vibration": 12.0, "temperature": 90.0}
    attribution = attribute_parameters(values, MOTOR)

    assert sum(1 for item in attribution if item.is_dominant) == 1
    assert attribution[0].is_dominant is True


def test_dominant_is_the_worst_channel() -> None:
    values = {**NOMINAL, "vibration": 12.0, "temperature": 88.0}

    assert by_name(attribute_parameters(values, MOTOR))["vibration"].is_dominant


# -- Skipping behaviour ---

def test_healthy_parameters_are_skipped() -> None:
    """Channels inside their band cannot be improved by intervention."""
    attribution = attribute_parameters(dict(NOMINAL), MOTOR)

    assert attribution == []


def test_missing_channel_is_skipped() -> None:
    attribution = attribute_parameters({"temperature": 90.0}, MOTOR)

    assert [item.parameter for item in attribution] == ["temperature"]


def test_unknown_channel_is_skipped() -> None:
    attribution = attribute_parameters({**NOMINAL, "humidity": 50.0}, MOTOR)

    assert "humidity" not in by_name(attribution)


def test_empty_values_produce_no_attribution() -> None:
    assert attribute_parameters({}, MOTOR) == []


# -- Reported context ---

def test_each_item_carries_its_operating_band() -> None:
    """The operator needs the limit they are being compared against."""
    item = by_name(attribute_parameters({**NOMINAL, "vibration": 12.0}, MOTOR))["vibration"]
    band = MOTOR.band("vibration")

    assert item.nominal == band.nominal
    assert item.ok_high == band.ok_high
    assert item.alarm_high == band.alarm_high
    assert item.unit == UNITS["vibration"]


def test_as_dict_is_json_ready() -> None:
    item = attribute_parameters({**NOMINAL, "vibration": 12.0}, MOTOR)[0].as_dict()

    assert isinstance(item["expected_range"], list)
    assert isinstance(item["marginal_contribution"], float)
    assert "would_fix" in item


# -- Actionable advice ---

def test_high_value_advice_targets_the_upper_limit() -> None:
    item = by_name(attribute_parameters({**NOMINAL, "vibration": 12.0}, MOTOR))["vibration"]

    assert item.would_fix is not None
    assert "at or below" in item.would_fix


def test_low_value_advice_targets_nominal() -> None:
    values = {**NOMINAL, "rpm": 900.0}
    item = by_name(attribute_parameters(values, MOTOR))["rpm"]

    assert item.would_fix is not None
    assert "Raise" in item.would_fix


def test_in_band_value_is_skipped() -> None:
    """A reading away from nominal but inside its band needs no intervention.

    Running a counterfactual on it would burn a scoring pass to learn that
    fixing it changes nothing, and listing it would imply a problem that does
    not exist.
    """
    attribution = by_name(attribute_parameters({**NOMINAL, "vibration": 5.0}, MOTOR))

    assert "vibration" not in attribution
    assert attribution == {}


# -- Policy masking ---

def test_masked_channel_is_reported() -> None:
    """A channel costing points whose counterfactual recovers nothing must be flagged.

    Without this the operator reads the linear share as the true cost and
    deprioritises a channel that is genuinely failing.
    """
    values = {**NOMINAL, "vibration": 13.0, "temperature": 95.0}
    attribution = attribute_parameters(values, MOTOR)
    suppressed = suppressed_by_policy(values, MOTOR, attribution)

    names = [item["parameter"] for item in suppressed]
    assert "temperature" in names

    entry = next(i for i in suppressed if i["parameter"] == "temperature")
    assert "floor" in entry["reason"]


def test_masked_channel_has_zero_marginal_but_real_penalty() -> None:
    values = {**NOMINAL, "vibration": 13.0, "temperature": 95.0}
    item = by_name(attribute_parameters(values, MOTOR))["temperature"]

    assert item.direct_penalty > 1.0
    assert abs(item.marginal_contribution) < 0.5


def test_healthy_reading_reports_nothing_suppressed() -> None:
    values = {**NOMINAL, "vibration": 10.0}
    attribution = attribute_parameters(values, MOTOR)

    assert suppressed_by_policy(values, MOTOR, attribution) == []


def test_in_band_channel_is_never_suppressed() -> None:
    values = {**NOMINAL, "vibration": 13.0, "current": 44.0}
    attribution = attribute_parameters(values, MOTOR)
    suppressed = suppressed_by_policy(values, MOTOR, attribution)

    assert "current" not in [item["parameter"] for item in suppressed]


# -- Cross-type and configuration ---

def test_attribution_differs_by_equipment_type() -> None:
    values = {"temperature": 170.0, "vibration": 3.0, "pressure": 4.0,
              "rpm": 1450.0, "current": 35.0, "voltage": 400.0,
              "power": 21.0, "load": 70.0}
    motor = by_name(attribute_parameters(values, get_profile("Industrial Motor")))
    boiler = by_name(attribute_parameters(values, get_profile("Boiler")))

    assert motor["temperature"].marginal_contribution > 0
    assert "temperature" not in boiler or boiler["temperature"].marginal_contribution == 0


def test_every_profile_attributes_without_error() -> None:
    from app.ml.baselines import PROFILES

    for name, profile in PROFILES.items():
        values = {k: profile.bands[k].nominal for k in
                  ("temperature", "vibration", "pressure", "rpm", "current",
                   "voltage", "power")}
        values["load"] = 70.0
        # Push one channel out of range so attribution has work to do.
        values["vibration"] = profile.bands["vibration"].alarm_high
        result = attribute_parameters(values, profile)
        assert result, name
        assert all(item.marginal_contribution >= 0 for item in result), name
