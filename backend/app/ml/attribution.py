"""Counterfactual attribution: what each parameter actually costs.

The score breakdown already says *how many* points a parameter cost. That
answers "what happened" but not "what would it be worth to fix", which is the
question an operator actually has.

This module answers it by recomputing the score with one parameter replaced by
its nominal value, holding everything else fixed:

    counterfactual(p) = score(values with p at nominal)

The difference is the parameter's true marginal contribution. This is
attribution, not correlation: it is a direct causal intervention on the
scoring function, so it is exactly consistent with the published score and
cannot disagree with it.

A single-pass attribution is also reported, which is the cost of the
parameter's degradation under the normal linear weighting. Comparing the two
reveals when a parameter is being suppressed by the critical floor or the
status cap, which is exactly the situation where a linear share alone would
mislead an operator into thinking a channel is harmless.
"""

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.ml.baselines import BaselineProfile
from app.ml.scoring import compute_health_score


@dataclass
class ParameterAttribution:
    """One parameter's contribution to a score."""

    parameter: str
    value: float | None
    nominal: float
    ok_low: float
    ok_high: float
    alarm_high: float
    unit: str
    direct_penalty: float
    counterfactual_penalty: float
    marginal_contribution: float
    is_dominant: bool
    would_fix: str | None

    def as_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "value": self.value,
            "nominal": self.nominal,
            "expected_range": [self.ok_low, self.ok_high],
            "alarm_limit": self.alarm_high,
            "unit": self.unit,
            "direct_penalty": round(self.direct_penalty, 2),
            "counterfactual_penalty": round(self.counterfactual_penalty, 2),
            "marginal_contribution": round(self.marginal_contribution, 2),
            "is_dominant": self.is_dominant,
            "would_fix": self.would_fix,
        }


#: Display units, matching the units declared by the sensor API.
UNITS: dict[str, str] = {
    "temperature": "degC",
    "vibration": "mm/s RMS",
    "pressure": "bar",
    "rpm": "rev/min",
    "current": "A",
    "voltage": "V",
    "power": "kW",
    "load": "% of rated",
}


def attribute_parameters(
    values: dict[str, float | None],
    profile: BaselineProfile,
    *,
    anomaly_score: float = 0.0,
    failure_probability: float = 0.0,
    settings: Settings | None = None,
) -> list[ParameterAttribution]:
    """Compute per-parameter attribution for one set of readings.

    Each parameter is intervened on independently: only that one channel is
    moved to nominal, so the figures answer "if I fixed just this, how much
    would the score improve" rather than an unachievable joint counterfactual.
    """

    baseline_breakdown, _ = compute_health_score(
        values, profile, anomaly_score=anomaly_score,
        failure_probability=failure_probability, settings=settings,
    )

    attributions: list[ParameterAttribution] = []
    for parameter, value in values.items():
        band = profile.bands.get(parameter)
        if band is None or value is None:
            continue

        # Only channels that are actually out of band can be improved.
        # Comparing against `nominal` alone is not enough: a reading can sit
        # well away from nominal and still be perfectly healthy, and running a
        # counterfactual on it wastes a scoring pass to learn nothing.
        in_band = band.ok_low <= value <= band.ok_high or (
            not band.low_relevant and value <= band.ok_high
        )
        if in_band:
            continue

        counterfactual_values = dict(values)
        counterfactual_values[parameter] = band.nominal
        cf_breakdown, _ = compute_health_score(
            counterfactual_values, profile, anomaly_score=anomaly_score,
            failure_probability=failure_probability, settings=settings,
        )

        direct = baseline_breakdown.parameter_penalties.get(parameter, 0.0)
        marginal = cf_breakdown.health_score - baseline_breakdown.health_score

        attributions.append(
            ParameterAttribution(
                parameter=parameter,
                value=value,
                nominal=band.nominal,
                ok_low=round(band.ok_low, 3),
                ok_high=round(band.ok_high, 3),
                alarm_high=round(band.alarm_high, 3),
                unit=UNITS.get(parameter, ""),
                direct_penalty=direct,
                counterfactual_penalty=cf_breakdown.health_score,
                marginal_contribution=marginal,
                is_dominant=False,
                would_fix=_would_fix(parameter, value, band.nominal, band.ok_high),
            )
        )

    attributions.sort(key=lambda a: -a.marginal_contribution)
    if attributions:
        attributions[0].is_dominant = True
    return attributions


def _would_fix(parameter: str, value: float, nominal: float, ok_high: float) -> str | None:
    """Describe the change that would return a channel to normal.

    Phrased as an instruction an operator can act on, with the target value.
    Returns None when no single adjustment would help, so the UI does not
    offer empty advice.
    """
    if value > ok_high:
        return f"Reduce {parameter} from {value:g} to at or below {ok_high:g}"
    if value < nominal:
        return f"Raise {parameter} from {value:g} to about {nominal:g}"
    return None


def suppressed_by_policy(
    values: dict[str, float | None],
    profile: BaselineProfile,
    attribution: list[ParameterAttribution],
    *,
    settings: Settings | None = None,
) -> list[dict]:
    """Channels whose linear share understates how bad they really are.

    Two distinct cases, both of which make the factor list misleading on its
    own:

    ``not charged``
        The channel is out of range but carries no penalty at all, because the
        sensor budget is already saturated or it falls outside the active
        weight set.

    ``masked``
        The channel *is* charged points linearly, but fixing it on its own
        would not move the score, because a different channel's breach has
        pinned the result at the critical floor. The counterfactual marginal
        comes back as zero while the direct penalty is substantial, which
        looks like a contradiction unless it is explained.

    In both cases the operator must be told, because reading the linear share
    as the true cost would lead them to deprioritise a failing channel.
    """
    suppressed: list[dict] = []

    for item in attribution:
        band = profile.bands.get(item.parameter)
        if band is None:
            continue

        out_of_band = item.value > band.ok_high or (
            band.low_relevant and item.value < band.ok_low
        )
        if not out_of_band:
            continue

        if item.direct_penalty <= 0.0:
            reason = (
                "Out of range but charged no points, because the sensor point "
                "budget is already fully consumed by other channels. Fixing "
                "this one alone will not change the score. Treat it as a "
                "compounding risk, not a safe reading."
            )
        elif abs(item.marginal_contribution) < 0.5:
            reason = (
                f"Charged {item.direct_penalty:.1f} points by the linear "
                "weighting, but another channel's confirmed excursion is "
                "pinning the score at its floor, so correcting this one alone "
                "would recover none of them. Both need addressing."
            )
        else:
            continue

        suppressed.append({"parameter": item.parameter, "reason": reason})

    return suppressed


__all__ = [
    "UNITS",
    "ParameterAttribution",
    "attribute_parameters",
    "suppressed_by_policy",
]
