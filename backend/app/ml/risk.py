"""Failure risk estimation.

**A probability requires a horizon, and an absolute probability requires
calibration data.** Neither can be hand-waved, so this module is explicit
about what it does and does not claim:

* It estimates the probability that a machine enters a failed state *within a
  stated horizon*, not a timeless "chance of failure".
* With no labelled failure history, the absolute rate is **not calibrated**.
  Every estimate carries `calibrated=False` and names its method so a
  consumer can never mistake an index for a measured frequency.
* The *relative* ordering is meaningful and is what the model is actually good
  at: given two machines, the more degraded one carries more risk.

Method
------
Rather than blending signals with hand-chosen weights, this uses a
degradation-hazard model from accelerated-life theory:

    lambda(d) = lambda0 * exp(k * d)          hazard rate at degradation d
    P(fail within H) = 1 - exp(-integral of lambda dt)

The exponential term encodes **wear-out**: hazard grows slowly while a machine
is healthy and accelerates sharply as degradation approaches an alarm limit,
which matches how mechanical failure actually behaves. A linear blend cannot
express that asymmetry; it treats a reading at 90 % of the alarm limit as only
10 % worse than one at 80 %.

The aggregate hazard sums the per-parameter hazards, weighted by importance,
so several mildly degraded channels can accumulate into a serious risk.

Calibration
-----------
`base_hazard_per_hour` must be set from the fleet's observed failure rate:

    lambda0 = 1 / mean_time_to_failure_hours

The default is a conservative placeholder and `calibrated` stays False until
`calibrated=True` is set explicitly, after real outcome data has been used to
fit the rate. See ML_DOCUMENTATION.md.
"""

import math
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Settings, get_settings
from app.ml.degradation import Degradation
from app.models.base import RiskLevel

#: Horizons reported by default, in hours.
DEFAULT_HORIZONS: tuple[int, ...] = (24, 168, 720)  # 1 day, 1 week, 30 days

#: The horizon the *risk level* is judged on: 30 days.
#:
#: A probability must be judged over a horizon that matches the decision it
#: informs. A 24-hour probability at a realistic industrial MTBF is always
#: fractions of a percent, so classifying risk on it would report "Low" for a
#: machine sitting at its alarm limit and make the whole channel useless.
#: Thirty days is the horizon a maintenance planner actually schedules against.
PRIMARY_HORIZON_HOURS = 720

#: How sharply hazard accelerates with degradation.
#:
#: k = 4 means the hazard at full degradation is e^4 ~= 55x the base rate,
#: which at the default base rate models a machine at its alarm limits as
#: failing within weeks rather than years. This is the wear-out region of the
#: bathtub curve, where the gradient genuinely is that steep.
DEFAULT_HAZARD_SENSITIVITY = 4.0


@dataclass
class RiskEstimate:
    """Risk of entering a failed state, for one or more horizons."""

    probability: float
    risk_level: str
    horizon_hours: int
    method: str
    calibrated: bool
    drivers: list[dict] = field(default_factory=list)
    horizon_probabilities: dict[int, float] = field(default_factory=dict)
    dominant_driver: str | None = None
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "probability": round(self.probability, 4),
            "risk_level": self.risk_level,
            "horizon_hours": self.horizon_hours,
            "method": self.method,
            "calibrated": self.calibrated,
            "drivers": self.drivers,
            "horizon_probabilities": {
                str(hours): round(value, 4)
                for hours, value in self.horizon_probabilities.items()
            },
            "dominant_driver": self.dominant_driver,
            "notes": self.notes,
        }


class RiskEstimator(Protocol):
    """Interface every risk model must satisfy.

    Exists so a supervised model can replace the heuristic without the API, the
    health scorer, or the alerting layer changing.
    """

    method_name: str

    def estimate(
        self,
        degradations: dict[str, Degradation],
        weights: dict[str, float],
        anomaly_score: float,
        health_score: float,
        settings: Settings,
    ) -> RiskEstimate: ...


class DegradationHazardEstimator:
    """Wear-out hazard model over per-parameter degradation.

    Parameters
    ----------
    base_hazard_per_hour:
        Failure rate of a healthy machine. Placeholder unless calibrated.
    sensitivity:
        Acceleration exponent. Higher means hazard stays flat longer and then
        rises more sharply near the alarm limit.
    anomaly_multiplier:
        Extra hazard scaling applied when the detector reports an unusual
        pattern. Statistical evidence of abnormality is treated as evidence of
        a worse state, not merely a different one.
    calibrated:
        Whether the base rate was fitted to real failure history. ``None``
        defers to ``settings.risk_model_calibrated``, which is the right answer
        for a caller with no database. The service layer passes the *persisted*
        calibration instead, so an instance that has been fitted reports
        calibrated without needing the environment to be edited by hand.
    """

    method_name = "degradation_hazard"

    def __init__(
        self,
        base_hazard_per_hour: float | None = None,
        sensitivity: float = DEFAULT_HAZARD_SENSITIVITY,
        anomaly_multiplier: float = 2.0,
        calibrated: bool | None = None,
    ) -> None:
        self.base_hazard_per_hour = base_hazard_per_hour
        self.sensitivity = sensitivity
        self.anomaly_multiplier = anomaly_multiplier
        self.calibrated = calibrated

    def _channel_hazard(self, degradation: float) -> float:
        """Hazard rate for a single channel at a given degradation.

        Below the floor, wear-out is negligible and only a small baseline
        hazard applies, so ordinary healthy variation does not accumulate into
        a meaningful risk.
        """
        if degradation <= 0.0:
            return 0.0
        return math.exp(self.sensitivity * degradation)

    def estimate(
        self,
        degradations: dict[str, Degradation],
        weights: dict[str, float],
        anomaly_score: float,
        health_score: float,
        settings: Settings,
    ) -> RiskEstimate:
        """Estimate failure probability across the default horizons."""
        base_rate = (
            self.base_hazard_per_hour
            if self.base_hazard_per_hour is not None
            else settings.base_hazard_per_hour
        )
        calibrated = (
            settings.risk_model_calibrated
            if self.calibrated is None
            else self.calibrated
        )

        drivers: list[dict] = []
        total_weighted_exponent = 0.0
        wear_out_factor = 0.0

        for name, degradation in degradations.items():
            if degradation.normalized <= 0.0:
                continue
            weight = weights.get(name, 0.0)
            exponent = self._channel_hazard(degradation.normalized)
            # `exp(k*d) - 1` is zero at d=0, so a healthy channel adds nothing
            # and the aggregate starts from the baseline hazard.
            contribution = weight * (exponent - 1.0)
            total_weighted_exponent += weight * exponent
            wear_out_factor += contribution
            drivers.append(
                {
                    "parameter": name,
                    "degradation": round(degradation.normalized, 4),
                    "weight": round(weight, 4),
                    "hazard_multiplier": round(exponent, 4),
                    "share": 0.0,  # filled in below
                }
            )

        # The baseline hazard of a healthy machine always applies. Healthy
        # machines do fail, and returning exactly zero probability would be a
        # false reassurance rather than a precise answer.
        #
        # An unusual sensor pattern adds to it even when every channel is still
        # inside its band: the detector has seen something the engineering
        # limits do not explain, and that is evidence of a worse state, not
        # merely a different one.
        anomaly_factor = self.anomaly_multiplier * max(0.0, anomaly_score)
        effective_rate = base_rate * (1.0 + wear_out_factor + anomaly_factor)

        # Share attribution, so the operator sees which channel owns the risk.
        if drivers and total_weighted_exponent > 0:
            for driver in drivers:
                driver["share"] = round(
                    (driver["weight"] * driver["hazard_multiplier"])
                    / total_weighted_exponent,
                    4,
                )
            drivers.sort(key=lambda d: -d["share"])
        else:
            # Nothing degraded: the risk above baseline comes from the anomaly
            # evidence alone, and that is what should be named.
            if anomaly_factor > 0:
                drivers.append(
                    {
                        "parameter": "anomaly_pattern",
                        "degradation": None,
                        "weight": None,
                        "hazard_multiplier": round(1.0 + anomaly_factor, 4),
                        "share": 1.0,
                    }
                )

        horizon_probabilities = {
            hours: self._probability(effective_rate, hours)
            for hours in DEFAULT_HORIZONS
        }
        primary = PRIMARY_HORIZON_HOURS
        if primary not in horizon_probabilities:
            horizon_probabilities[primary] = self._probability(effective_rate, primary)
        probability = horizon_probabilities[primary]

        return RiskEstimate(
            probability=probability,
            risk_level=classify_risk_level(probability, settings),
            horizon_hours=primary,
            method=self.method_name,
            calibrated=calibrated,
            drivers=drivers,
            horizon_probabilities=horizon_probabilities,
            dominant_driver=drivers[0]["parameter"] if drivers else None,
            notes=self._note(calibrated, effective_rate),
        )

    @staticmethod
    def _probability(hazard_rate: float, horizon_hours: int) -> float:
        """Convert a constant hazard rate into a probability over a horizon."""
        if hazard_rate <= 0.0:
            return 0.0
        return 1.0 - math.exp(-hazard_rate * max(horizon_hours, 0))

    @staticmethod
    def _note(calibrated: bool, effective_rate: float) -> str:
        if not calibrated:
            return (
                "Base hazard rate is a placeholder, not fitted to failure data. "
                "Treat this as a relative risk index: the ranking between "
                "machines is meaningful, the absolute probability is not."
            )
        return f"Effective hazard rate {effective_rate:.3e} per hour."


def classify_risk_level(probability: float, settings: Settings | None = None) -> str:
    """Map a horizon-specific probability to a risk level.

    Uses the same configurable thresholds as the health scorer so the two never
    disagree about what "High" means.
    """
    settings = settings or get_settings()
    if probability >= settings.risk_severe_min:
        return RiskLevel.SEVERE.value
    if probability >= settings.risk_high_min:
        return RiskLevel.HIGH.value
    if probability >= settings.risk_moderate_min:
        return RiskLevel.MODERATE.value
    return RiskLevel.LOW.value


def healthy_probability(
    settings: Settings | None = None,
    base_hazard_per_hour: float | None = None,
) -> float:
    """Probability assigned to a machine with no degradation at all.

    Non-zero by design: healthy machines do fail. Reporting exactly zero would
    be a false reassurance, and would also make the risk channel useless for
    comparing two healthy machines.

    `base_hazard_per_hour` lets a caller pass the *effective* rate, so a
    calibrated instance reports a calibrated healthy baseline rather than
    quietly falling back to the placeholder.
    """
    settings = settings or get_settings()
    estimator = DegradationHazardEstimator()
    rate = (
        base_hazard_per_hour
        if base_hazard_per_hour is not None
        else settings.base_hazard_per_hour
    )
    return estimator._probability(rate, PRIMARY_HORIZON_HOURS)


__all__ = [
    "DEFAULT_HAZARD_SENSITIVITY",
    "DEFAULT_HORIZONS",
    "DegradationHazardEstimator",
    "RiskEstimate",
    "RiskEstimator",
    "classify_risk_level",
    "healthy_probability",
]
