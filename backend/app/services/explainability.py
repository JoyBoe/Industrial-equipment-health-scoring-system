"""Explanation and recommendation generation.

Every sentence emitted here is derived from a number that was actually
computed. Nothing is inferred or embellished: if a sentence cannot be traced to
a factor in the breakdown, it is not said.

The output answers the operator's question directly -- "why did this machine
receive this score?" -- with the points attributable to each cause, and states
the parameters that are *fine* too, so absence of a cause is informative.
"""

from app.ml.scoring import (
    PARAMETER_PHRASES,
    DIRECTION_PHRASES,
    HealthAssessment,
    ScoreBreakdown,
    build_factors,
    impact_label,
)
from app.models.base import HealthStatus

#: Actionable advice per parameter, ordered by urgency. The first matching
#: parameter's advice is used as the primary recommendation.
PARAMETER_ACTIONS: dict[str, str] = {
    "temperature": (
        "Inspect the cooling system, verify airflow and filter condition, and "
        "confirm the lubrication is correct for the operating temperature."
    ),
    "vibration": (
        "Inspect bearings and coupling alignment, then check mounting "
        "fasteners for looseness. Schedule vibration analysis if the trend persists."
    ),
    "pressure": (
        "Check for leaks, verify the pressure sensor calibration, and inspect "
        "the relief valve and any strainer for blockage."
    ),
    "power": (
        "Check for mechanical drag, verify belt or coupling condition, and "
        "confirm the load is within the machine's continuous rating."
    ),
    "current": (
        "Check for a mechanical obstruction or a failing winding, and verify "
        "the supply voltage is within tolerance."
    ),
    "rpm": (
        "Verify the drive is not overloaded or stalling, and check the drive "
        "settings and belt tension."
    ),
    "voltage": (
        "Check the supply, connections, and any transformer tap position. An "
        "out-of-tolerance supply affects every other measurement."
    ),
    "load": (
        "Confirm the process demand matches the machine's duty, and check for "
        "a change in upstream conditions."
    ),
}

STATUS_ACTIONS: dict[str, str] = {
    HealthStatus.EXCELLENT.value: (
        "No action required. Continue routine monitoring."
    ),
    HealthStatus.HEALTHY.value: (
        "No immediate action required. Continue routine monitoring."
    ),
    HealthStatus.WARNING.value: (
        "Plan maintenance within the next scheduled window and re-check the "
        "affected parameters after any adjustment."
    ),
    HealthStatus.POOR.value: (
        "Schedule corrective maintenance promptly. Reduce load if the condition "
        "worsens, and re-assess after the work is completed."
    ),
    HealthStatus.CRITICAL.value: (
        "Immediate intervention required. Take the machine out of service or "
        "reduce its load to a safe level, and inspect before restarting."
    ),
}

#: Below this deduction the machine is healthy enough to say so plainly.
HEALTHY_TOTAL_DEDUCTION = 8.0


def format_points(points: float) -> str:
    """Render a penalty as a readable figure.

    Rounded to whole points because a claim of "2.37 points" implies a precision
    the underlying measurement does not have.
    """
    return f"{round(points)}"


def describe_parameter(factor: dict) -> str:
    """One clause describing a single contributing factor."""
    name = factor["parameter"]
    phrase = PARAMETER_PHRASES.get(name, f"{name.replace('_', ' ')} is out of range")

    if factor.get("direction") == "low" and name in DIRECTION_PHRASES:
        # Distinguish "too low" from "too high" where the meaning differs.
        return f"{name.replace('_', ' ')} {DIRECTION_PHRASES['low']}"

    value = factor.get("value")
    if value is None:
        return phrase
    return f"{phrase} (now {value:g})"


def generate_explanation(
    breakdown: ScoreBreakdown,
    factors: list[dict],
    *,
    previous_score: float | None = None,
) -> str:
    """Explain a score using the actual deductions.

    States the score, names the dominant cause with its point cost, and
    confirms healthy parameters so the operator knows what was checked.
    """
    score = breakdown.health_score
    sensor_factors = [f for f in factors if f["source"] == "sensor"]
    total_deducted = (
        breakdown.sensor_penalty + breakdown.anomaly_penalty + breakdown.risk_penalty
    )

    parts: list[str] = [f"Health score {score:g}."]

    if not sensor_factors and breakdown.anomaly_penalty < 0.01 and not breakdown.critical_parameter:
        if total_deducted < HEALTHY_TOTAL_DEDUCTION:
            parts.append(
                "All monitored parameters are within their normal operating "
                "ranges for this equipment type."
            )
        else:
            parts.append("No individual parameter has left its normal range.")
        return " ".join(parts)

    if sensor_factors:
        leading = sensor_factors[0]
        lead_points = format_points(leading["penalty"])
        lead_phrase = describe_parameter(leading)

        if len(sensor_factors) == 1:
            parts.append(
                f"The score is reduced mainly because {lead_phrase}, "
                f"costing about {lead_points} points."
            )
        else:
            secondary = sensor_factors[1]
            parts.append(
                f"The score is reduced primarily because {lead_phrase} "
                f"(-{lead_points} points), followed by "
                f"{describe_parameter(secondary)} "
                f"(-{format_points(secondary['penalty'])} points)."
            )

    if breakdown.critical_parameter and breakdown.critical_floor_points > 0.01:
        parts.append(
            f"A confirmed excursion past the alarm limit applies a minimum "
            f"deduction of {format_points(breakdown.critical_floor_points)} points, "
            "so the machine cannot be reported as healthy."
        )

    if breakdown.anomaly_penalty >= 1.0:
        parts.append(
            f"The detected sensor pattern is unusual for healthy operation, "
            f"deducting a further {format_points(breakdown.anomaly_penalty)} points."
        )
    if breakdown.risk_penalty >= 1.0:
        parts.append(
            f"Estimated failure risk contributes "
            f"{format_points(breakdown.risk_penalty)} points."
        )

    # Name the parameters that are fine, so a short factor list reads as
    # "these were checked and cleared" rather than "nothing else was measured".
    healthy = _healthy_parameters(breakdown, sensor_factors)
    if healthy:
        parts.append(f"{healthy} {'is' if len(healthy) == 1 else 'are'} within limits.")

    if previous_score is not None:
        delta = score - previous_score
        if abs(delta) >= 2.0:
            direction = "decreased" if delta < 0 else "increased"
            parts.append(
                f"The score has {direction} by {abs(delta):.0f} points since the "
                f"previous assessment ({previous_score:g})."
            )
        else:
            parts.append("The score is stable compared with the previous assessment.")

    return " ".join(parts)


def _healthy_parameters(breakdown: ScoreBreakdown, degraded: list[dict]) -> str:
    """List the reported parameters that incurred no penalty."""
    degraded_names = {f["parameter"] for f in degraded}
    checked = [name for name in breakdown.parameter_degradation if name in degraded_names]
    # Only parameters that were actually measured and found clean.
    scored = set(breakdown.weights) - degraded_names
    if not scored:
        return ""
    names = ", ".join(sorted(scored))
    return names.capitalize() if len(scored) == 1 else f"{names[0].upper()}{names[1:]}"


def generate_recommendation(
    breakdown: ScoreBreakdown, factors: list[dict]
) -> str:
    """Produce the recommended action.

    Led by the highest-penalty factor, because advice must match the actual
    cause; a generic "service the machine" would not tell an operator what to
    look at.
    """
    sensor_factors = [f for f in factors if f["source"] == "sensor"]
    status = breakdown.status
    urgency = STATUS_ACTIONS.get(status, STATUS_ACTIONS[HealthStatus.WARNING.value])

    if not sensor_factors:
        if breakdown.status in (HealthStatus.EXCELLENT.value, HealthStatus.HEALTHY.value):
            return STATUS_ACTIONS[status]
        # Degraded without a named parameter: the pattern, not a channel, is
        # the signal. Urgency must still lead -- telling an operator to
        # "continue monitoring" on a machine scored Critical would be the most
        # damaging output this system can produce.
        return (
            f"{urgency} No single parameter has left its normal range, so "
            "investigate the overall operating pattern and any recent changes "
            "in duty or process conditions."
        )

    leading = sensor_factors[0]
    action = PARAMETER_ACTIONS.get(leading["parameter"])

    if action is None:
        return urgency

    parameter = leading["parameter"]
    if len(sensor_factors) > 1:
        also = ", ".join(sorted(f["parameter"] for f in sensor_factors[1:4]))
        return f"{urgency} {action} Also review {also}."

    return f"{urgency} {action}"


def build_assessment(
    breakdown: ScoreBreakdown,
    factors: list[dict],
    *,
    anomaly_detected: bool,
    previous_score: float | None = None,
) -> HealthAssessment:
    """Assemble the full assessment from a breakdown and its factors."""
    return HealthAssessment(
        health_score=breakdown.health_score,
        status=breakdown.status,
        risk_level=breakdown.risk_level,
        anomaly_score=breakdown.anomaly_score,
        anomaly_detected=anomaly_detected,
        failure_probability=breakdown.failure_probability,
        contributing_factors=factors,
        explanation=generate_explanation(breakdown, factors, previous_score=previous_score),
        recommendation=generate_recommendation(breakdown, factors),
        breakdown=breakdown,
    )
