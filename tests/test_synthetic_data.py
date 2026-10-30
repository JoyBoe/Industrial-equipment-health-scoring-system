"""Tests for baseline profiles and the synthetic generator.

These matter more than typical unit tests: if the generator emits iid noise or
the profiles are inconsistent, every downstream score is meaningless and the
tests will still pass. So the assertions here are about *structure* —
correlation, regime separation, and mix fidelity — not just "it runs".
"""

import numpy as np
import pytest

from app.config import SENSOR_FEATURES
from app.ml.baselines import (
    DEFAULT_PROFILE,
    PROFILES,
    ParamBand,
    get_profile,
    has_specific_profile,
    validate_profiles,
)
from app.ml.synthetic import (
    FAULT_REGIMES,
    REGIME_CAVITATION,
    REGIME_DEGRADED,
    REGIME_NOMINAL,
    REGIME_OVERHEAT,
    SyntheticGenerator,
    SeriesConfig,
    generate_healthy_series,
    generate_multi_equipment,
    generate_series,
)


# --- Profiles ---

def test_all_profiles_are_self_consistent() -> None:
    validate_profiles()  # raises on any malformed band


def test_every_profile_covers_every_feature() -> None:
    for name, profile in PROFILES.items():
        for feature in SENSOR_FEATURES:
            assert feature in profile.bands, f"{name} missing {feature}"


def test_unknown_type_falls_back_to_generic() -> None:
    profile = get_profile("Self-Propelled Flux Capacitor")

    assert profile is DEFAULT_PROFILE
    assert has_specific_profile("Self-Propelled Flux Capacitor") is False


def test_profile_lookup_is_case_insensitive() -> None:
    assert get_profile("hydraulic pump") is get_profile("Hydraulic Pump")


@pytest.mark.parametrize("equipment_type", sorted(PROFILES))
def test_alarm_limits_exceed_ok_limits(equipment_type: str) -> None:
    profile = get_profile(equipment_type)
    for name, band in profile.bands.items():
        assert band.alarm_high > band.ok_high, f"{equipment_type}/{name}"
        assert band.sigma > 0, f"{equipment_type}/{name}"


def test_chiller_runs_cold_and_boiler_runs_hot() -> None:
    # A chiller reporting 150 C would be nonsense; a boiler reporting 10 C
    # likewise. The profiles must not be interchangeable.
    assert get_profile("Chiller").band("temperature").nominal < 30
    assert get_profile("Boiler").band("temperature").nominal > 100


def test_transformer_is_stationary() -> None:
    profile = get_profile("Transformer")

    assert profile.nominal_rpm == 0
    assert profile.band("rpm").ok_high < 10


def test_stationary_asset_band_has_headroom() -> None:
    # A zero-speed band would make the scorer's denominator zero.
    validate_profiles()


def test_unknown_parameter_raises_clear_error() -> None:
    with pytest.raises(KeyError, match="No baseline band"):
        get_profile("Hydraulic Pump").band("humidity")


# --- Generator structure ---

def test_generator_produces_requested_length() -> None:
    assert len(generate_series(samples=120, seed=1)) == 120


def test_all_regimes_appear_across_seeds() -> None:
    for seed in (42, 7, 99):
        regimes = {r.regime for r in generate_series(samples=4320, seed=seed)}
        assert regimes == {REGIME_NOMINAL, *FAULT_REGIMES}, f"seed {seed}: {regimes}"


def test_regime_mix_is_close_to_requested() -> None:
    requested = {
        REGIME_NOMINAL: 0.70,
        REGIME_DEGRADED: 0.15,
        "bearing_fault": 0.05,
        REGIME_OVERHEAT: 0.04,
        REGIME_CAVITATION: 0.03,
        "overload": 0.03,
    }
    readings = generate_series(samples=4320, seed=42)
    total = len(readings)

    for regime, target in requested.items():
        actual = sum(1 for r in readings if r.regime == regime) / total
        assert abs(actual - target) < 0.06, f"{regime}: {actual:.3f} vs {target}"


def test_generated_channels_are_correlated_not_independent() -> None:
    """Power must track current and voltage, not vary independently."""
    readings = generate_series(samples=2000, seed=42)
    current = np.array([r.current for r in readings])
    power = np.array([r.power for r in readings])
    load = np.array([r.load for r in readings])
    temperature = np.array([r.temperature for r in readings])

    assert np.corrcoef(current, power)[0, 1] > 0.85
    assert np.corrcoef(load, power)[0, 1] > 0.4
    assert np.corrcoef(load, temperature)[0, 1] > 0.3


def test_power_follows_three_phase_identity() -> None:
    """Power must be derived from V and I, not sampled independently.

    The power factor degrades slightly with load, so the check is that power
    is consistent with P = sqrt(3)*V*I*PF for a physically plausible PF.
    """
    nominal_pf = get_profile("Hydraulic Pump").power_factor
    for reading in generate_series(samples=300, seed=5):
        implied_pf = reading.power * 1000 / (np.sqrt(3) * reading.voltage * reading.current)
        assert 0.6 <= implied_pf <= nominal_pf
        assert implied_pf == pytest.approx(nominal_pf, rel=0.35)


def test_each_fault_has_a_distinct_signature() -> None:
    """Every injected fault must move a different channel decisively.

    A fault that leaves the data statistically unchanged is undetectable, and
    would make detection accuracy meaningless.
    """
    readings = generate_series(samples=8640, seed=42)
    nominal = [r for r in readings if r.regime == REGIME_NOMINAL]

    def mean(regime: str, attr: str) -> float:
        subset = [getattr(r, attr) for r in readings if r.regime == regime]
        return float(np.mean(subset))

    def base(attr: str) -> float:
        return float(np.mean([getattr(r, attr) for r in nominal]))

    # Overtemperature must actually be hotter.
    assert mean(REGIME_OVERHEAT, "temperature") > base("temperature") + 15
    # Bearing fault must be materially more vibratory.
    assert mean("bearing_fault", "vibration") > base("vibration") * 1.8
    # Cavitation must lose pressure.
    assert mean(REGIME_CAVITATION, "pressure") < base("pressure") * 0.8
    # Overload must draw more current at lower speed.
    assert mean("overload", "current") > base("current") * 1.15
    assert mean("overload", "rpm") < base("rpm") * 0.85


def test_gradual_wear_is_subtle_not_obvious() -> None:
    """Degraded wear should be detectable but not as blatant as an acute fault.

    If gradual wear were as extreme as a bearing failure, the system could not
    distinguish "drifting toward failure" from "failed", which is the whole
    point of the Warning band.
    """
    readings = generate_series(samples=8640, seed=42)
    nominal = [r for r in readings if r.regime == REGIME_NOMINAL]
    degraded = [r for r in readings if r.regime == REGIME_DEGRADED]

    vib_ratio = np.mean([r.vibration for r in degraded]) / np.mean(
        [r.vibration for r in nominal]
    )
    assert 1.1 < vib_ratio < 1.8


def test_healthy_series_contains_no_faults() -> None:
    """Baselines must be fitted on fault-free data only."""
    readings = generate_healthy_series(samples=1000, seed=7)

    assert {r.regime for r in readings} == {REGIME_NOMINAL}
    assert all(r.fault_type is None for r in readings)


def test_healthy_series_stays_within_alarm_limits() -> None:
    profile = get_profile("Hydraulic Pump")
    readings = generate_healthy_series(samples=1000, seed=7)

    for reading in readings:
        assert reading.temperature < profile.band("temperature").ok_high
        assert reading.vibration < profile.band("vibration").ok_high
        assert reading.pressure > profile.band("pressure").ok_low


def test_generation_is_deterministic_for_a_seed() -> None:
    a = generate_series(samples=200, seed=123)
    b = generate_series(samples=200, seed=123)
    c = generate_series(samples=200, seed=124)

    assert [r.as_dict() for r in a] == [r.as_dict() for r in b]
    assert [r.as_dict() for r in a] != [r.as_dict() for r in c]


def test_no_negative_or_nan_values() -> None:
    for reading in generate_series(samples=2000, seed=11):
        for feature in SENSOR_FEATURES:
            value = getattr(reading, feature)
            assert value is not None
            assert not np.isnan(value)
            if feature not in ("temperature", "pressure", "voltage"):
                assert value >= 0, f"{feature} was {value}"


def test_timestamps_are_monotonic_and_evenly_spaced() -> None:
    readings = generate_series(samples=100, seed=2, interval_minutes=1)
    deltas = {
        (b.timestamp - a.timestamp).total_seconds() for a, b in zip(readings, readings[1:])
    }

    assert deltas == {60.0}


def test_thermal_state_persists_across_calls() -> None:
    """Streaming in chunks must equal generating in one pass.

    Otherwise the thermal state resets at chunk boundaries and produces an
    implausible temperature jump.
    """
    one_pass = generate_series(samples=200, seed=9)

    generator = SyntheticGenerator(SeriesConfig(samples=200, seed=9))
    streamed = []
    remaining = 200
    while remaining > 0:
        chunk = min(37, remaining)
        streamed.extend(generator.generate(chunk))
        remaining -= chunk

    assert len(streamed) == 200
    assert [r.temperature for r in streamed] == pytest.approx(
        [r.temperature for r in one_pass]
    )


def test_zero_sample_series_is_empty() -> None:
    assert generate_series(samples=0, seed=1) == []


def test_fault_types_are_labelled_only_on_faults() -> None:
    for reading in generate_series(samples=2000, seed=4):
        if reading.regime == REGIME_NOMINAL:
            assert reading.fault_type is None
        else:
            assert reading.fault_type == reading.regime


def test_multi_equipment_series_are_distinct() -> None:
    fleet = [
        {"equipment_id": "EQ-001", "equipment_type": "Hydraulic Pump"},
        {"equipment_id": "EQ-002", "equipment_type": "Boiler"},
        {"equipment_id": "EQ-003", "equipment_type": "Chiller"},
    ]
    series = generate_multi_equipment(fleet, samples=500, seed_base=100)

    assert set(series) == {"EQ-001", "EQ-002", "EQ-003"}
    # Different equipment types must not share a temperature range.
    pump = np.mean([r.temperature for r in series["EQ-001"]])
    boiler = np.mean([r.temperature for r in series["EQ-002"]])
    chiller = np.mean([r.temperature for r in series["EQ-003"]])

    assert chiller < pump < boiler
    # No two units are identical.
    assert not np.allclose(
        [r.temperature for r in series["EQ-001"]],
        [r.temperature for r in series["EQ-002"]],
    )


def test_custom_regime_mix_is_respected() -> None:
    config = SeriesConfig(
        samples=1000,
        seed=3,
        regime_mix={REGIME_NOMINAL: 1.0},
    )
    readings = SyntheticGenerator(config).generate()

    assert {r.regime for r in readings} == {REGIME_NOMINAL}
