"""Parameter degradation: how far a reading sits outside its acceptable band.

This is the single source of truth for "how bad is this value", shared by the
anomaly detector (which uses it to attribute a score to a named parameter) and
the health scoring engine (which uses it to compute penalties). Keeping one
implementation means the explanation shown to a user and the score that number
is derived from can never disagree.

The mapping is deliberately bounded and monotonic:

* inside the acceptable band -> 0.0
* between `ok_high` and `alarm_high` -> linear 0..1
* at or beyond `alarm_high` -> 1.0

A separate `overshoot` value reports how far *past* the alarm limit the reading
sits, because "20% over the limit" and "200% over the limit" are both fully
degraded on this scale but are not equally urgent to an operator.
"""

from dataclasses import dataclass

from app.ml.baselines import ParamBand

#: Degradation below this is treated as nominal and not reported as a factor.
#: Without a floor, ordinary measurement noise produces a long tail of trivial
#: "contributing factors" and buries the real ones.
FLOOR = 0.05


@dataclass(frozen=True)
class Degradation:
    """Result of evaluating one parameter against its band."""

    parameter: str
    value: float
    normalized: float
    direction: str  # "normal", "high", "low", or "unknown"
    overshoot: float
    band: ParamBand | None = None

    @property
    def is_degraded(self) -> bool:
        return self.normalized >= FLOOR

    @property
    def is_critical(self) -> bool:
        return self.normalized >= 0.75


def high_side(value: float, band: ParamBand) -> float:
    """Degradation contributed by exceeding the upper limit."""
    if value <= band.ok_high:
        return 0.0
    if value >= band.alarm_high:
        return 1.0
    return (value - band.ok_high) / band.clamp_span()


def low_side(value: float, band: ParamBand) -> float:
    """Degradation contributed by falling below the lower limit.

    Only applied when `band.low_relevant` is set: a cold chiller or a low
    vibration reading is normal, while collapsing pressure is a fault.
    """
    if not band.low_relevant or value >= band.ok_low:
        return 0.0
    if value <= band.alarm_low:
        return 1.0
    return (band.ok_low - value) / band.low_span()


def evaluate(value: float | None, parameter: str, band: ParamBand) -> Degradation:
    """Evaluate one reading against its band.

    A missing value returns neutral degradation rather than a failure: a
    dropped channel is a telemetry problem, and must not silently penalise the
    machine's health.
    """
    if value is None:
        return Degradation(
            parameter=parameter, value=float("nan"), normalized=0.0, direction="unknown", overshoot=0.0, band=band
        )

    high = high_side(float(value), band)
    low = low_side(float(value), band)

    if high > 0 and high >= low:
        direction = "high"
        normalized = high
        overshoot = max(0.0, (float(value) - band.alarm_high) / band.clamp_span())
    elif low > 0:
        direction = "low"
        normalized = low
        overshoot = max(0.0, (band.alarm_low - float(value)) / band.low_span())
    else:
        direction = "normal"
        normalized = 0.0
        overshoot = 0.0

    return Degradation(
        parameter=parameter,
        value=float(value),
        normalized=min(normalized, 1.0),
        direction=direction,
        overshoot=overshoot,
        band=band,
    )


def evaluate_all(
    values: dict[str, float | None], profile
) -> dict[str, Degradation]:
    """Evaluate every channel present in `values` against `profile`'s bands.

    Channels the profile has no band for are skipped rather than raising, so an
    unexpected extra channel cannot break a scoring request.
    """
    results: dict[str, Degradation] = {}
    for parameter, value in values.items():
        band = profile.bands.get(parameter)
        if band is None:
            continue
        results[parameter] = evaluate(value, parameter, band)
    return results


def dominant(
    degradations: dict[str, Degradation], limit: int = 3
) -> list[Degradation]:
    """The parameters most responsible, worst first.

    This is what turns a single number into "vibration, then temperature": the
    ordering is the contribution, so the operator reads the cause directly.
    """
    ranked = [d for d in degradations.values() if d.is_degraded]
    ranked.sort(key=lambda d: (-d.normalized, d.parameter))
    return ranked[:limit]
