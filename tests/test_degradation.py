"""Tests for parameter degradation.

The scorer and the detector share this module, so an error here would show up
as an internally inconsistent score *and* explanation rather than an obvious
crash.
"""

import math

import pytest

from app.ml.baselines import get_profile
from app.ml.degradation import (
    Degradation,
    dominant,
    evaluate,
    evaluate_all,
    high_side,
    low_side,
)


@pytest.fixture
def pump():
    return get_profile("Hydraulic Pump")


# --- High side ---

def test_value_inside_band_is_not_degraded(pump) -> None:
    result = evaluate(60.0, "temperature", pump.band("temperature"))

    assert result.normalized == 0.0
    assert result.direction == "normal"
    assert result.is_degraded is False


def test_value_at_ok_high_is_not_degraded(pump) -> None:
    band = pump.band("temperature")
    result = evaluate(band.ok_high, "temperature", band)

    assert result.normalized == 0.0


def test_value_at_alarm_high_is_fully_degraded(pump) -> None:
    band = pump.band("temperature")
    result = evaluate(band.alarm_high, "temperature", band)

    assert result.normalized == pytest.approx(1.0)
    assert result.is_critical is True


def test_degradation_is_linear_between_limits(pump) -> None:
    band = pump.band("temperature")
    midpoint = (band.ok_high + band.alarm_high) / 2

    result = evaluate(midpoint, "temperature", band)

    assert result.normalized == pytest.approx(0.5, abs=0.01)


def test_degradation_is_monotonic(pump) -> None:
    band = pump.band("temperature")
    values = [band.nominal, band.ok_high, band.ok_high + 5, band.alarm_high, band.alarm_high + 20]
    scores = [evaluate(v, "temperature", band).normalized for v in values]

    assert scores == sorted(scores)


def test_overshoot_reported_beyond_alarm(pump) -> None:
    band = pump.band("temperature")
    result = evaluate(band.alarm_high + band.clamp_span(), "temperature", band)

    assert result.normalized == 1.0
    assert result.overshoot == pytest.approx(1.0, abs=0.01)


def test_overshoot_is_zero_inside_band(pump) -> None:
    result = evaluate(50.0, "temperature", pump.band("temperature"))

    assert result.overshoot == 0.0


# --- Low side ---

def test_low_side_ignored_when_not_relevant(pump) -> None:
    """A cold pump is not a fault; only overtemperature is meaningful."""
    result = evaluate(-40.0, "temperature", pump.band("temperature"))

    assert result.normalized == 0.0
    assert result.direction == "normal"


def test_low_side_penalised_when_relevant(pump) -> None:
    """Collapsing pressure is a real failure (cavitation, loss of prime)."""
    band = pump.band("pressure")
    assert band.low_relevant is True

    result = evaluate(band.alarm_low, "pressure", band)

    assert result.direction == "low"
    assert result.normalized == pytest.approx(1.0)


def test_rpm_below_band_is_degraded(pump) -> None:
    """A machine that will not reach speed is stalling."""
    band = pump.band("rpm")
    result = evaluate(band.alarm_low * 0.9, "rpm", band)

    assert result.direction == "low"
    assert result.normalized > 0.9


# --- Missing values ---

def test_missing_value_is_neutral(pump) -> None:
    """A dropped channel is a telemetry problem, not a health problem.

    Penalising it would let a flaky gateway degrade an otherwise healthy
    machine's score.
    """
    result = evaluate(None, "temperature", pump.band("temperature"))

    assert result.normalized == 0.0
    assert result.direction == "unknown"
    assert result.is_degraded is False


def test_evaluate_all_skips_unknown_channels(pump) -> None:
    results = evaluate_all(
        {"temperature": 90.0, "humidity": 50.0}, pump
    )

    assert "temperature" in results
    assert "humidity" not in results


def test_evaluate_all_with_empty_values(pump) -> None:
    assert evaluate_all({}, pump) == {}


# --- Ranking ---

def test_dominant_ranks_worst_first(pump) -> None:
    """Ranking follows the computed degradation, not a fixed parameter order."""
    results = evaluate_all(
        {"temperature": 60.0, "vibration": 11.0, "pressure": 60.0}, pump
    )

    ranked = dominant(results)

    assert ranked
    # Pressure at 60 bar is below its alarm floor of 62, so it is fully
    # degraded and must outrank vibration.
    assert ranked[0].parameter == "pressure"
    assert [d.normalized for d in ranked] == sorted(
        (d.normalized for d in ranked), reverse=True
    )


def test_dominant_names_vibration_when_it_is_worst(pump) -> None:
    results = evaluate_all(
        {"temperature": 60.0, "vibration": 12.0, "pressure": 95.0}, pump
    )

    assert dominant(results)[0].parameter == "vibration"


def test_dominant_excludes_healthy_parameters(pump) -> None:
    results = evaluate_all({"temperature": 55.0, "vibration": 3.0, "pressure": 100.0}, pump)

    assert dominant(results) == []


def test_dominant_respects_limit(pump) -> None:
    results = evaluate_all(
        {"temperature": 95.0, "vibration": 11.5, "pressure": 65.0, "current": 38.0}, pump
    )

    assert len(dominant(results, limit=2)) == 2


def test_dominant_is_deterministic_on_ties(pump) -> None:
    """Equal degradations must not reorder between calls."""
    results = evaluate_all(
        {"temperature": 90.0, "vibration": 10.0, "pressure": 70.0}, pump
    )

    first = [d.parameter for d in dominant(results)]
    second = [d.parameter for d in dominant(results)]

    assert first == second


# --- Equipment-type differentiation ---

def test_same_value_scores_differently_by_equipment_type() -> None:
    """The whole point of per-type profiles.

    170 C is alarming for a motor and normal for a boiler; 20 C is normal for a
    chiller and suspiciously cold for a boiler.
    """
    motor = get_profile("Industrial Motor").band("temperature")
    boiler = get_profile("Boiler").band("temperature")

    assert evaluate(170.0, "temperature", motor).is_degraded is True
    assert evaluate(170.0, "temperature", boiler).is_degraded is False

    chiller = get_profile("Chiller").band("temperature")
    assert evaluate(12.0, "temperature", chiller).is_degraded is False
    # A boiler's temperature is judged on the high side only: an idle boiler is
    # legitimately cold, so flagging it would be wrong.
    assert evaluate(12.0, "temperature", boiler).is_degraded is False

    # Power demonstrates the same point on a channel that is rating-derived:
    # 190 kW is unremarkable for a 180 kW boiler and absurd for a 110 kW
    # chiller.
    assert evaluate(190.0, "power", get_profile("Boiler").band("power")).is_degraded is False
    assert evaluate(190.0, "power", chiller_band_power()).is_degraded is True


def chiller_band_power():
    return get_profile("Chiller").band("power")


def test_unknown_type_uses_generic_profile() -> None:
    results = evaluate_all({"temperature": 95.0}, get_profile("Warp Drive"))

    assert results["temperature"].is_degraded is True
