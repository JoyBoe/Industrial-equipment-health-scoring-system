"""Health score engine.

The score is deterministic, auditable, and fully decomposable:

    health_score = 100 - sensor_penalty - anomaly_penalty - risk_penalty

The 100 available points are partitioned into three budgets (see `Settings`).
Each point deducted can be traced to exactly one parameter or to one of the
two model-derived terms, so the explanation is a *formatting pass over the
arithmetic* rather than a separate guess that might disagree with the number.

Why not let a regressor output the score directly? Because a regressor cannot
answer "why is this machine at 63?". Every point here has a named owner, which
is the difference between a scoring system an operator can act on and a black
box that happens to emit a number.

The machine-learned terms (anomaly, risk) are deliberately capped at a minority
of the budget. The engineering bands carry the majority of the signal, so a
retrained or misbehaving model degrades the score gracefully instead of
controlling it.
"""

from dataclasses import asdict, dataclass, field

from app.config import Settings, get_settings
from app.ml.baselines import BaselineProfile
from app.ml.degradation import Degradation, evaluate_all
from app.models.base import HealthStatus, RiskLevel

#: Epsilon applied to the critical floor so a floored machine lands just below
#: the Healthy boundary rather than exactly on it. Floating-point equality with
#: the threshold would make the resulting status depend on rounding.
CRITICAL_FLOOR_MARGIN: float = 0.01

#: Depth, measured in band spans past the alarm limit, at which the critical
#: floor saturates at the full sensor budget. A value of 1.0 means "one full
#: band-width past the limit" -- e.g. an acceptable band of 0-6 with an alarm at
#: 12 saturates at 18.
#:
#: The floor interpolates between the Healthy boundary and the full budget over
#: this range. Making it a *constant* -- the previous behaviour -- pinned every
#: breaching machine to an identical score and destroyed fleet-level
#: discrimination, which is why the seeded fleet used to land within 0.32
#: points. See ML_DOCUMENTATION.md section 12.
FLOOR_SEVERITY_SATURATION: float = 1.0

#: Impact labels, in ascending order of severity. Derived from each factor's
#: share of the total deduction, so the label reflects contribution rather than
#: a fixed threshold that would misreport a severe-but-lightly-weighted factor.
IMPACT_LEVELS: tuple[tuple[float, str], ...] = (
    (0.01, "negligible"),
    (0.08, "minor"),
    (0.20, "moderate"),
    (0.40, "significant"),
    (1.01, "severe"),
)

#: Phrasing used when a parameter is the dominant cause. Keyed by parameter so
#: the wording is specific rather than generic.
PARAMETER_PHRASES: dict[str, str] = {
    "temperature": "operating temperature has risen above the normal range",
    "vibration": "vibration has increased above the normal operating range",
    "pressure": "pressure has moved outside its normal operating band",
    "power": "power consumption is higher than expected for the load",
    "current": "current draw indicates increased mechanical load",
    "rpm": "running speed is outside its normal band",
    "voltage": "supply voltage is outside its normal band",
    "load": "operating load is outside its expected range",
}

DIRECTION_PHRASES: dict[str, str] = {
    "high": "is running high",
    "low": "has fallen below its normal range",
}


@dataclass
class ScoreBreakdown:
    """The complete arithmetic behind one health score.

    Retained verbatim so an explanation can be regenerated later even after
    weights or model versions change.
    """

    health_score: float
    status: str
    risk_level: str
    sensor_penalty: float
    anomaly_penalty: float
    risk_penalty: float
    anomaly_score: float
    failure_probability: float
    parameter_penalties: dict[str, float] = field(default_factory=dict)
    parameter_degradation: dict[str, float] = field(default_factory=dict)
    critical_parameter: bool = False
    critical_floor_points: float = 0.0
    #: The sensor penalty before the critical floor was applied. Published so
    #: the floor is never destructive: a consumer can recover exactly how far
    #: past its limits a machine really is, even after the policy has raised the
    #: reported penalty to enforce a status.
    unfloored_sensor_penalty: float = 0.0
    #: Worst excursion severity across all channels, in band spans past the
    #: alarm limit. 0.0 is exactly at the limit; 1.0 is one band-width past it.
    #: Drives the interpolated floor, and is the value a fleet view should sort
    #: on when it needs to rank machines past their limits.
    worst_excursion_severity: float = 0.0
    total_weight: float = 0.0
    budgets: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class HealthAssessment:
    """A score plus everything needed to explain and act on it."""

    health_score: float
    status: str
    risk_level: str
    anomaly_score: float
    anomaly_detected: bool
    failure_probability: float
    contributing_factors: list[dict]
    explanation: str
    recommendation: str
    breakdown: ScoreBreakdown

    def as_dict(self) -> dict:
        return {
            "health_score": self.health_score,
            "status": self.status,
            "risk_level": self.risk_level,
            "anomaly_score": self.anomaly_score,
            "anomaly_detected": self.anomaly_detected,
            "failure_probability": self.failure_probability,
            "contributing_factors": self.contributing_factors,
            "explanation": self.explanation,
            "recommendation": self.recommendation,
            "score_breakdown": self.breakdown.as_dict(),
        }


def classify_status(score: float, settings: Settings | None = None) -> str:
    """Map a score to a health status using configurable thresholds."""
    settings = settings or get_settings()
    if score >= settings.excellent_min:
        return HealthStatus.EXCELLENT.value
    if score >= settings.healthy_min:
        return HealthStatus.HEALTHY.value
    if score >= settings.warning_min:
        return HealthStatus.WARNING.value
    if score >= settings.poor_min:
        return HealthStatus.POOR.value
    return HealthStatus.CRITICAL.value


def classify_risk(probability: float, settings: Settings | None = None) -> str:
    """Map a failure probability to a risk level using configurable thresholds."""
    settings = settings or get_settings()
    if probability >= settings.risk_severe_min:
        return RiskLevel.SEVERE.value
    if probability >= settings.risk_high_min:
        return RiskLevel.HIGH.value
    if probability >= settings.risk_moderate_min:
        return RiskLevel.MODERATE.value
    return RiskLevel.LOW.value


#: Status bands from worst to best, so "worse" is a max over this order.
STATUS_SEVERITY: dict[str, int] = {
    HealthStatus.CRITICAL.value: 0,
    HealthStatus.POOR.value: 1,
    HealthStatus.WARNING.value: 2,
    HealthStatus.HEALTHY.value: 3,
    HealthStatus.EXCELLENT.value: 4,
}


def worse_status(first: str, second: str) -> str:
    """Return whichever of two statuses is more severe."""
    return first if STATUS_SEVERITY.get(first, 0) <= STATUS_SEVERITY.get(second, 0) else second


def apply_status_caps(
    status: str,
    anomaly_score: float,
    risk_level: str,
    settings: Settings | None = None,
) -> str:
    """Prevent the status from being better than the models support.

    The score's sensor budget dominates by design, so a machine with pristine
    readings but a Severe failure risk would otherwise be reported "Healthy".
    That combination is the most dangerous output this system could produce, so
    the risk and anomaly terms are allowed to cap the status from above.
    """
    settings = settings or get_settings()

    if risk_level == RiskLevel.SEVERE.value:
        status = worse_status(status, settings.severe_risk_status_cap)
    elif risk_level == RiskLevel.HIGH.value:
        status = worse_status(status, settings.high_risk_status_cap)

    if clamp01(anomaly_score) >= settings.anomaly_status_cap_min:
        status = worse_status(status, HealthStatus.WARNING.value)

    return status


def impact_label(share: float) -> str:
    """Label a factor by its share of the total deduction."""
    for threshold, label in IMPACT_LEVELS:
        if share < threshold:
            return label
    return IMPACT_LEVELS[-1][1]


def normalise_weights(
    weights: dict[str, float], active: list[str]
) -> dict[str, float]:
    """Renormalise weights across the parameters actually present.

    A machine reporting only temperature and vibration must not be scored
    against a budget split for eight channels; it would be penalised for
    parameters it never measured.
    """
    selected = {name: weights.get(name, 0.0) for name in active}
    total = sum(selected.values())
    if total <= 0:
        # No configured weight for these parameters: share the budget evenly
        # rather than silently scoring everything as perfect.
        share = 1.0 / len(active) if active else 0.0
        return dict.fromkeys(active, share)
    return {name: value / total for name, value in selected.items()}


def compute_health_score(
    values: dict[str, float | None],
    profile: BaselineProfile,
    *,
    anomaly_score: float = 0.0,
    failure_probability: float = 0.0,
    settings: Settings | None = None,
) -> tuple[ScoreBreakdown, list[dict]]:
    """Compute the score and the factor list that explains it.

    Returns the arithmetic breakdown and the contributing factors, both derived
    from the same pass, so they cannot disagree.
    """
    settings = settings or get_settings()
    settings.validate_budgets()

    degradations: dict[str, Degradation] = evaluate_all(values, profile)

    # Weight only the parameters that were actually reported and have a band.
    active = [
        name
        for name, d in degradations.items()
        if d.direction != "unknown"
    ]
    weights = normalise_weights(settings.parameter_weights, active)

    sensor_penalty = 0.0
    parameter_penalties: dict[str, float] = {}
    parameter_degradation: dict[str, float] = {}

    for name, degradation in degradations.items():
        penalty = 0.0
        if degradation.direction != "unknown":
            penalty = settings.sensor_points_budget * weights.get(name, 0.0) * degradation.normalized
        sensor_penalty += penalty
        if degradation.normalized > 0:
            parameter_penalties[name] = round(penalty, 4)
            parameter_degradation[name] = round(degradation.normalized, 4)

    # The sensor budget is a ceiling on its own contribution, so clamping here
    # guarantees the sum can never exceed 100 even with several channels in
    # alarm at once.
    sensor_penalty = min(sensor_penalty, settings.sensor_points_budget)

    # A parameter at or beyond its alarm limit is a confirmed excursion, not a
    # trend. Linear weighting alone can leave such a machine in the "Healthy"
    # band, because a single channel only holds a fraction of the budget.
    #
    # The floor interpolates from "just cross the Healthy boundary" when a
    # channel is exactly at its alarm limit, to the full sensor budget as the
    # excursion grows. Interpolating on severity is what keeps the floor
    # discriminating: a constant floor assigns every breaching machine the same
    # penalty and makes the fleet indistinguishable.
    #
    # `normalized` cannot be used here because it is capped at 1.0 at the alarm
    # limit. `overshoot` is the depth past the limit, measured in band spans,
    # so it is the quantity that actually varies between breaching machines.
    breaches = [
        d for d in degradations.values() if d.direction != "unknown" and d.normalized >= 1.0
    ]
    critical = bool(breaches)
    # 0.0 means "exactly at the alarm limit"; FLOOR_SEVERITY_SATURATION is the
    # depth at which the floor is fully saturated.
    severity = max((d.overshoot for d in breaches), default=0.0)

    unfloored = sensor_penalty
    floor_added = 0.0
    if critical:
        healthy_boundary = 100.0 - settings.healthy_min + CRITICAL_FLOOR_MARGIN
        saturation = clamp01(severity / FLOOR_SEVERITY_SATURATION)
        floor = healthy_boundary + saturation * (settings.sensor_points_budget - healthy_boundary)
        if sensor_penalty < floor:
            floor_added = floor - sensor_penalty
            sensor_penalty = floor

    anomaly_penalty = settings.anomaly_points_budget * settings.anomaly_weight * clamp01(anomaly_score)
    risk_penalty = settings.risk_points_budget * settings.risk_weight * clamp01(failure_probability)

    raw_score = 100.0 - sensor_penalty - anomaly_penalty - risk_penalty
    score = round(min(max(raw_score, 0.0), 100.0), 2)

    risk_level = classify_risk(clamp01(failure_probability), settings)
    status = apply_status_caps(
        classify_status(score, settings), anomaly_score, risk_level, settings
    )

    breakdown = ScoreBreakdown(
        health_score=score,
        status=status,
        risk_level=risk_level,
        sensor_penalty=round(sensor_penalty, 4),
        anomaly_penalty=round(anomaly_penalty, 4),
        risk_penalty=round(risk_penalty, 4),
        anomaly_score=round(clamp01(anomaly_score), 4),
        failure_probability=round(clamp01(failure_probability), 4),
        parameter_penalties=parameter_penalties,
        parameter_degradation=parameter_degradation,
        critical_parameter=critical,
        critical_floor_points=round(floor_added, 4),
        unfloored_sensor_penalty=round(unfloored, 4),
        worst_excursion_severity=round(severity, 4),
        total_weight=round(sum(weights.values()), 4),
        budgets={
            "sensor": settings.sensor_points_budget,
            "anomaly": settings.anomaly_points_budget,
            "risk": settings.risk_points_budget,
        },
        weights={k: round(v, 4) for k, v in weights.items()},
    )

    return breakdown, build_factors(
        degradations,
        parameter_penalties,
        sensor_penalty,
        anomaly_penalty,
        risk_penalty,
        floor_added,
    )


def clamp01(value: float | None) -> float:
    """Clamp to [0, 1], treating None as 0."""
    if value is None:
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:  # NaN
        return 0.0
    return min(max(numeric, 0.0), 1.0)


def build_factors(
    degradations: dict[str, Degradation],
    penalties: dict[str, float],
    sensor_penalty: float,
    anomaly_penalty: float,
    risk_penalty: float,
    floor_added: float = 0.0,
) -> list[dict]:
    """Build the contributing-factor list from the computed penalties.

    Each factor reports the points it actually cost, so the figures in an
    explanation add up to the score rather than merely suggesting it.
    """
    total_deducted = sensor_penalty + anomaly_penalty + risk_penalty
    factors: list[dict] = []

    for name, penalty in penalties.items():
        # A parameter with no weight for the active set can be flagged as
        # out-of-band yet cost nothing. Listing it as a contributing factor
        # would report a cause with a zero-point penalty, which is
        # self-contradictory and misleading in the explanation.
        if penalty <= 0.0:
            continue
        degradation = degradations[name]
        share = penalty / total_deducted if total_deducted > 0 else 0.0
        factors.append(
            {
                "parameter": name,
                "value": None if degradation.direction == "unknown" else round(degradation.value, 3),
                "impact": impact_label(share),
                "penalty": round(penalty, 2),
                "contribution_share": round(share, 4),
                "degradation": round(degradation.normalized, 4),
                "direction": degradation.direction,
                "source": "sensor",
            }
        )

    if floor_added > 0.0:
        # Surfaced explicitly so the published penalties reconcile exactly with
        # the score. A hidden uplift would make the breakdown unauditable.
        share = floor_added / total_deducted if total_deducted > 0 else 0.0
        factors.append(
            {
                "parameter": "confirmed_excursion",
                "value": None,
                "impact": impact_label(share),
                "penalty": round(floor_added, 2),
                "contribution_share": round(share, 4),
                "degradation": None,
                "direction": "high",
                "source": "critical_policy",
            }
        )

    if anomaly_penalty > 0.01:
        share = anomaly_penalty / total_deducted if total_deducted > 0 else 0.0
        factors.append(
            {
                "parameter": "anomaly_pattern",
                "value": None,
                "impact": impact_label(share),
                "penalty": round(anomaly_penalty, 2),
                "contribution_share": round(share, 4),
                "degradation": None,
                "direction": "high",
                "source": "anomaly_detector",
            }
        )

    if risk_penalty > 0.01:
        share = risk_penalty / total_deducted if total_deducted > 0 else 0.0
        factors.append(
            {
                "parameter": "failure_risk",
                "value": None,
                "impact": impact_label(share),
                "penalty": round(risk_penalty, 2),
                "contribution_share": round(share, 4),
                "degradation": None,
                "direction": "high",
                "source": "risk_model",
            }
        )

    factors.sort(key=lambda factor: (-factor["penalty"], factor["parameter"]))
    return factors
