"""Tests for the critical-floor policy.

History: the floor used to be a *constant* (``100 - healthy_min + eps``),
applied to the sensor term whenever any channel was at its alarm limit. That
was correct for one machine but destroyed fleet-level discrimination: over a
24-hour window every breaching machine was pinned to an identical penalty, so
the seeded 8-unit fleet landed within 0.32 points of each other.

The floor now interpolates on excursion severity (``ml.scoring``), and the
un-floored penalty is published alongside it. These tests pin both the original
guarantee -- a confirmed excursion never reports as "Healthy" -- and the new
one: machines past their limits at different depths must separate.
"""

import pytest

from app.config import Settings
from app.ml.baselines import get_profile
from app.ml.scoring import (
    CRITICAL_FLOOR_MARGIN,
    FLOOR_SEVERITY_SATURATION,
    compute_health_score,
)


def readings(profile_name: str, **overrides: dict) -> dict:
    """Baseline nominal readings for a profile, with named overrides."""
    profile = get_profile(profile_name)
    values = {
        name: band.nominal
        for name, band in profile.bands.items()
    }
    # `alarm_high` is the cleanest way to express "exactly at the limit" and
    # "twice the limit" in units the profile actually owns.
    values.update(overrides)
    return values


def sensor_penalty_for(profile_name: str, **overrides: float) -> tuple[float, object]:
    values = readings(profile_name, **overrides)
    breakdown, _factors = compute_health_score(
        values, profile=get_profile(profile_name), anomaly_score=0.0, failure_probability=0.0
    )
    return breakdown.sensor_penalty, breakdown


class TestFloorStillEnforcesThePolicy:
    """The original reason the floor exists must survive the fix."""

    def test_a_machine_at_its_alarm_limit_never_reports_healthy(self) -> None:
        profile = get_profile("Industrial Motor")
        alarm = profile.bands["vibration"].alarm_high
        values = readings("Industrial Motor", vibration=alarm)
        breakdown, _ = compute_health_score(
            values, profile=profile, anomaly_score=0.0, failure_probability=0.0
        )
        assert breakdown.critical_parameter is True
        assert breakdown.status != "Excellent", (
            "A confirmed excursion reported as 'Excellent' is the exact failure "
            "the floor was introduced to prevent."
        )
        assert breakdown.status not in {"Excellent", "Healthy"}, (
            f"Status at an alarm limit was {breakdown.status}, expected Warning or worse."
        )

    def test_the_floor_derives_from_the_configured_healthy_threshold(self) -> None:
        """Retuning healthy_min must retune the floor, not desync from it."""
        settings = Settings()
        boundary = 100.0 - settings.healthy_min + CRITICAL_FLOOR_MARGIN
        # The boundary is the floor at severity 1.0, so it must sit inside the
        # sensor budget for the policy to ever bind.
        assert 0.0 < boundary <= settings.sensor_points_budget

    def test_an_healthy_machine_is_not_floored(self) -> None:
        """Guards the opposite failure: a floor applied unconditionally."""
        profile = get_profile("Industrial Motor")
        values = readings("Industrial Motor")
        breakdown, _ = compute_health_score(
            values, profile=profile, anomaly_score=0.0, failure_probability=0.0
        )
        assert breakdown.critical_parameter is False
        assert breakdown.critical_floor_points == 0.0
        assert breakdown.health_score == pytest.approx(100.0, abs=0.01)


class TestFloorPreservesDiscrimination:
    """The defect that was fixed."""

    def test_machines_at_different_depths_past_the_limit_separate(self) -> None:
        profile = get_profile("Industrial Motor")
        alarm = profile.bands["vibration"].alarm_high

        shallow, shallow_b = sensor_penalty_for(
            "Industrial Motor", vibration=alarm * 1.05
        )
        deep, deep_b = sensor_penalty_for(
            "Industrial Motor", vibration=alarm * 1.9
        )

        assert shallow_b.critical_parameter and deep_b.critical_parameter
        assert deep > shallow, (
            f"Depth past the limit must still separate machines: "
            f"1.9x gave {deep} which is not more than 1.05x's {shallow}. "
            "The floor has become constant again."
        )
        assert deep_b.worst_excursion_severity > shallow_b.worst_excursion_severity

    def test_severity_is_monotonic_in_the_excursion(self) -> None:
        profile = get_profile("Industrial Motor")
        alarm = profile.bands["vibration"].alarm_high

        severities = [
            sensor_penalty_for(
                "Industrial Motor", vibration=alarm * factor
            )[1].worst_excursion_severity
            for factor in (1.0, 1.2, 1.4, 1.6)
        ]
        assert severities == sorted(severities), (
            f"Severity must rise monotonically with the excursion, got {severities}"
        )

    def test_two_breaching_machines_do_not_collide(self) -> None:
        """The concrete version of the fleet defect.

        Two machines that only differ in how far past their limit they are must
        not produce the same score, which is what the constant floor did.
        """
        profile = get_profile("Industrial Motor")
        alarm = profile.bands["vibration"].alarm_high
        a, _ = compute_health_score(
            readings("Industrial Motor", vibration=alarm * 1.1),
            profile=profile, anomaly_score=0.0, failure_probability=0.0,
        )
        b, _ = compute_health_score(
            readings("Industrial Motor", vibration=alarm * 1.8),
            profile=profile, anomaly_score=0.0, failure_probability=0.0,
        )
        assert a.health_score != b.health_score

    def test_health_score_falls_monotonically_with_excursion_depth(self) -> None:
        """The property fleet ranking depends on.

        Every step deeper past the alarm limit must cost points. This is the
        invariant that failed when the floor was constant: beyond the Healthy
        boundary every depth was worth exactly the same penalty, so the score
        was flat and the fleet could not be ranked.
        """
        profile = get_profile("Industrial Motor")
        alarm = profile.bands["vibration"].alarm_high

        scores = [
            compute_health_score(
                readings("Industrial Motor", vibration=alarm * factor),
                profile=profile, anomaly_score=0.0, failure_probability=0.0,
            )[0].health_score
            for factor in (1.0, 1.2, 1.5, 1.8, 2.2)
        ]

        assert scores == sorted(scores, reverse=True), (
            f"Health score must fall as the excursion deepens, got {scores}. "
            "A flat run here means the critical floor has become constant again."
        )
        # And the steps must be real, not rounding noise.
        assert scores[0] - scores[-1] > 1.0, (
            f"Total separation of {scores[0] - scores[-1]:.2f} points is too small "
            "for a fleet view to rank machines on."
        )

    def test_floor_saturates_at_the_sensor_budget(self) -> None:
        """Past saturation, the floor must not exceed the sensor budget."""
        profile = get_profile("Industrial Motor")
        settings = Settings()
        alarm = profile.bands["vibration"].alarm_high
        breakdown, _ = compute_health_score(
            readings("Industrial Motor", vibration=alarm * 50.0),
            profile=profile, anomaly_score=0.0, failure_probability=0.0,
        )
        assert breakdown.sensor_penalty <= settings.sensor_points_budget + 0.01
        assert FLOOR_SEVERITY_SATURATION > 0


class TestFloorIsNonDestructive:
    """The information the floor previously discarded must now survive."""

    def test_the_unfloored_penalty_is_published(self) -> None:
        profile = get_profile("Industrial Motor")
        alarm = profile.bands["vibration"].alarm_high
        breakdown, _ = compute_health_score(
            readings("Industrial Motor", vibration=alarm * 2.0),
            profile=profile, anomaly_score=0.0, failure_probability=0.0,
        )
        assert breakdown.critical_floor_points > 0.0
        assert breakdown.unfloored_sensor_penalty < breakdown.sensor_penalty, (
            "unfloored_sensor_penalty must be the pre-policy value, otherwise "
            "publishing it is pointless."
        )

    def test_unfloored_penalty_equals_the_floored_one_when_no_floor_applies(self) -> None:
        profile = get_profile("Industrial Motor")
        breakdown, _ = compute_health_score(
            readings("Industrial Motor", vibration=profile.bands["vibration"].ok_high),
            profile=profile, anomaly_score=0.0, failure_probability=0.0,
        )
        assert breakdown.unfloored_sensor_penalty == pytest.approx(
            breakdown.sensor_penalty, abs=0.001
        )

    def test_factor_penalties_still_reconcile_with_the_score(self) -> None:
        """The floor may add points, but the published list must still add up."""
        profile = get_profile("Industrial Motor")
        alarm = profile.bands["vibration"].alarm_high
        breakdown, factors = compute_health_score(
            readings("Industrial Motor", vibration=alarm * 1.7),
            profile=profile, anomaly_score=0.4, failure_probability=0.2,
        )
        total = sum(f["penalty"] for f in factors)
        deducted = (
            breakdown.sensor_penalty
            + breakdown.anomaly_penalty
            + breakdown.risk_penalty
        )
        assert total == pytest.approx(deducted, abs=0.05)
