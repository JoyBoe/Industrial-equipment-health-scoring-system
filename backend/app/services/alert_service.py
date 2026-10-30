"""Alert rules engine.

Rules are declared as data, not scattered conditionals, so the set of things
that can raise an alert is readable in one place and every rule is testable in
isolation.

Two design points carry most of the value:

**Thresholds are band degradations, not raw values.** A vibration threshold of
0.5 means "half way to the alarm limit", which is meaningful for a 2.8 mm/s
motor and a 0.9 mm/s turbine alike. A raw threshold would be silently wrong for
every machine but the one it was written for.

**Severity escalates with magnitude.** A channel 55 % of the way to its alarm
limit is a Warning; one past the limit is Critical. A fixed severity per rule
would make an alarm-limit breach and a marginal one indistinguishable, and
would train operators to ignore the alert.

Alerts deduplicate on (equipment, type) while open, enforced by a partial
unique index, and are auto-resolved when the condition clears.
"""

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.ml.baselines import BaselineProfile
from app.ml.degradation import evaluate_all
from app.models.alert import Alert
from app.models.base import AlertSeverity, AlertType
from app.models.equipment import Equipment
from app.repositories import alert as alert_repo

logger = logging.getLogger(__name__)

#: Severity ladder used for magnitude-based escalation.
SEVERITY_LADDER: tuple[str, ...] = (
    AlertSeverity.INFO.value,
    AlertSeverity.WARNING.value,
    AlertSeverity.CRITICAL.value,
)

#: Display units, matching the units declared by the sensor API.
PARAMETER_UNITS: dict[str, str] = {
    "temperature": "degC",
    "vibration": "mm/s RMS",
    "pressure": "bar",
    "rpm": "rev/min",
    "current": "A",
    "voltage": "V",
    "power": "kW",
    "load": "%",
}


@dataclass
class AlertContext:
    """Everything a rule may inspect.

    Assembled once per evaluation and passed to every rule, so rules stay pure
    and testable.
    """

    equipment: Equipment
    profile: BaselineProfile
    values: dict[str, float | None]
    health_score: float
    status: str
    failure_probability: float
    anomaly_score: float
    anomaly_detected: bool
    degradations: dict = field(default_factory=dict)


@dataclass
class AlertCandidate:
    """A rule firing, before deduplication."""

    alert_type: AlertType
    severity: str
    description: str
    recommended_action: str
    observed_value: str
    threshold_value: str


def escalate(value: float, warn_at: float, critical_at: float) -> str:
    """Map a magnitude to a severity.

    `warn_at` and `critical_at` are fractions of the way to the alarm limit, so
    the ladder is defined relative to the equipment's own limits.
    """
    if value >= critical_at:
        return AlertSeverity.CRITICAL.value
    if value >= warn_at:
        return AlertSeverity.WARNING.value
    return AlertSeverity.INFO.value


# --- Rule implementations -------------------------------------------------
# Each returns an AlertCandidate or None. Pure functions of the context.


def rule_low_health_score(ctx: AlertContext, settings: Settings) -> AlertCandidate | None:
    """Fire when the health score falls into a degraded band."""
    if ctx.health_score >= settings.alert_health_threshold:
        return None
    severity = (
        AlertSeverity.CRITICAL.value
        if ctx.health_score < 25.0
        else AlertSeverity.WARNING.value
    )
    return AlertCandidate(
        alert_type=AlertType.LOW_HEALTH_SCORE,
        severity=severity,
        description=(
            f"Health score is {ctx.health_score:.1f} ({ctx.status}), below the "
            f"alert threshold of {settings.alert_health_threshold:.0f}."
        ),
        recommended_action=(
            "Review the equipment's contributing factors and schedule corrective "
            "maintenance. Inspect the parameters named in the health explanation."
        ),
        observed_value=f"{ctx.health_score:.1f}",
        threshold_value=f"< {settings.alert_health_threshold:.0f}",
    )


def _parameter_rule(
    ctx: AlertContext,
    settings: Settings,
    channel: str,
    threshold: float,
    alert_type: AlertType,
    action: str,
    noun: str,
) -> AlertCandidate | None:
    """Shared implementation for the per-parameter rules."""
    degradation = ctx.degradations.get(channel)
    if degradation is None or degradation.normalized < threshold:
        return None

    # Critical once the reading passes the alarm limit (degradation 1.0),
    # Warning from the configured threshold.
    severity = escalate(degradation.normalized, threshold, 1.0)
    band = degradation.band
    direction = "above" if degradation.direction == "high" else "below"
    unit = PARAMETER_UNITS.get(channel, "").strip()
    unit = f" {unit}" if unit else ""

    return AlertCandidate(
        alert_type=alert_type,
        severity=severity,
        description=(
            f"{noun} is {degradation.value:g}{unit} {direction} its normal "
            f"operating range (acceptable up to {band.ok_high:g}{unit}, alarm "
            f"limit {band.alarm_high:g}{unit}). It is "
            f"{degradation.normalized * 100:.0f}% of the way to the alarm limit."
        ),
        recommended_action=action,
        observed_value=f"{degradation.value:g}{unit}",
        threshold_value=f"degradation >= {threshold:.2f}",
    )


def rule_high_temperature(ctx: AlertContext, settings: Settings) -> AlertCandidate | None:
    return _parameter_rule(
        ctx,
        settings,
        "temperature",
        settings.alert_temperature_threshold,
        AlertType.HIGH_TEMPERATURE,
        "Check the cooling system, verify airflow and filter condition, and "
        "confirm the lubrication is correct for the operating temperature.",
        "Operating temperature",
    )


def rule_excessive_vibration(ctx: AlertContext, settings: Settings) -> AlertCandidate | None:
    return _parameter_rule(
        ctx,
        settings,
        "vibration",
        settings.alert_vibration_threshold,
        AlertType.EXCESSIVE_VIBRATION,
        "Inspect bearings and coupling alignment, and check mounting fasteners "
        "for looseness. Schedule vibration analysis if the trend persists.",
        "Vibration",
    )


def rule_pressure_instability(ctx: AlertContext, settings: Settings) -> AlertCandidate | None:
    return _parameter_rule(
        ctx,
        settings,
        "pressure",
        settings.alert_pressure_threshold,
        AlertType.PRESSURE_INSTABILITY,
        "Check for leaks, verify the pressure sensor calibration, and inspect "
        "the relief valve and any strainer for blockage.",
        "Pressure",
    )


def rule_anomaly_detected(ctx: AlertContext, settings: Settings) -> AlertCandidate | None:
    """Fire on the detector's verdict, not merely on a high score."""
    if not ctx.anomaly_detected and ctx.anomaly_score < settings.alert_anomaly_threshold:
        return None
    severity = escalate(ctx.anomaly_score, settings.alert_anomaly_threshold, 0.9)
    return AlertCandidate(
        alert_type=AlertType.ANOMALY_DETECTED,
        severity=severity,
        description=(
            f"The sensor pattern was flagged as anomalous (anomaly score "
            f"{ctx.anomaly_score:.2f}). The combination of readings is unusual "
            "for healthy operation of this equipment type."
        ),
        recommended_action=(
            "Compare against recent operating history and check whether the "
            "process duty has changed. Correlate with the parameters named in "
            "the anomaly assessment."
        ),
        observed_value=f"{ctx.anomaly_score:.2f}",
        threshold_value=f">= {settings.alert_anomaly_threshold:.2f}",
    )


def rule_high_failure_risk(ctx: AlertContext, settings: Settings) -> AlertCandidate | None:
    if ctx.failure_probability < settings.alert_risk_threshold:
        return None
    severity = escalate(
        ctx.failure_probability, settings.alert_risk_threshold, 0.5
    )
    return AlertCandidate(
        alert_type=AlertType.HIGH_FAILURE_RISK,
        severity=severity,
        description=(
            f"Estimated 30-day failure probability is "
            f"{ctx.failure_probability:.1%}, above the alert threshold of "
            f"{settings.alert_risk_threshold:.0%}."
        ),
        recommended_action=(
            "Plan maintenance within the current planning cycle. Note that this "
            "risk figure is a relative index rather than a measured failure "
            "rate unless the model has been fitted to this fleet's failure "
            "history -- see GET /api/predict/calibration."
        ),
        observed_value=f"{ctx.failure_probability:.3f}",
        threshold_value=f">= {settings.alert_risk_threshold:.2f}",
    )


#: All rules, evaluated in this order. Deterministic ordering keeps alert
#: creation predictable and makes the evaluation reproducible.
RULES = (
    rule_low_health_score,
    rule_high_temperature,
    rule_excessive_vibration,
    rule_pressure_instability,
    rule_anomaly_detected,
    rule_high_failure_risk,
)


def build_context(
    equipment: Equipment,
    values: dict[str, float | None],
    *,
    health_score: float,
    status: str,
    failure_probability: float,
    anomaly_score: float,
    anomaly_detected: bool,
    profile: BaselineProfile | None = None,
) -> AlertContext:
    """Assemble the evaluation context for a machine."""
    from app.ml.baselines import get_profile

    resolved_profile = profile or get_profile(equipment.equipment_type)
    return AlertContext(
        equipment=equipment,
        profile=resolved_profile,
        values=values,
        health_score=health_score,
        status=status,
        failure_probability=failure_probability,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_detected,
        degradations=evaluate_all(values, resolved_profile),
    )


def evaluate_rules(ctx: AlertContext, settings: Settings | None = None) -> list[AlertCandidate]:
    """Run every rule and return the candidates that fired.

    Pure: performs no database work, so rule behaviour can be asserted
    directly.
    """
    settings = settings or get_settings()
    fired: list[AlertCandidate] = []
    for rule in RULES:
        candidate = rule(ctx, settings)
        if candidate is not None:
            fired.append(candidate)
    return fired


def apply_alerts(
    db: Session,
    ctx: AlertContext,
    settings: Settings | None = None,
) -> tuple[list[Alert], list[Alert]]:
    """Reconcile a machine's alerts against the current state.

    Returns ``(opened, auto_resolved)``.

    Open only what newly fired, resolve what has cleared. A condition that
    fires repeatedly while already open does not create a second alert, which
    is what stops a machine oscillating across a threshold from producing
    thousands of rows.
    """
    settings = settings or get_settings()
    fired = evaluate_rules(ctx, settings)
    fired_types = {candidate.alert_type.value for candidate in fired}

    opened: list[Alert] = []
    for candidate in fired:
        if alert_repo.get_active_by_type(db, ctx.equipment, candidate.alert_type.value):
            continue
        try:
            alert = alert_repo.create(
                db,
                ctx.equipment,
                alert_id=uuid.uuid4().hex,
                alert_type=candidate.alert_type.value,
                severity=candidate.severity,
                description=candidate.description,
                recommended_action=candidate.recommended_action,
                observed_value=candidate.observed_value,
                threshold_value=candidate.threshold_value,
            )
            opened.append(alert)
        except IntegrityError:
            # The partial unique index rejected a concurrent duplicate. The
            # other writer's row is equally correct, so this is not an error.
            db.rollback()
            logger.info("Alert already open for %s %s", ctx.equipment.equipment_id,
                        candidate.alert_type.value)

    resolved: list[Alert] = []
    if settings.auto_resolve_alerts:
        for existing in alert_repo.get_active(db, ctx.equipment):
            if existing.alert_type in fired_types:
                continue
            resolved.append(
                alert_repo.resolve(
                    db, existing, "condition cleared on re-evaluation"
                )
            )

    if opened or resolved:
        db.commit()

    return opened, resolved


def evaluate_equipment(
    db: Session, equipment: Equipment, window: int = 60
) -> tuple[list[Alert], list[Alert]]:
    """Assess a machine and reconcile its alerts.

    The context is built from the **worst reading in the window**, not the most
    recent one. An alert is a statement about a machine's condition, not about
    an instant, and a machine that was overheating an hour ago and has since
    recovered is still a machine an operator needs to know about. Using only the
    newest reading makes alerting useless: readings land in normal operating
    gaps most of the time, so a genuine excursion passes unnoticed and the
    alert clears before anyone sees it.
    """
    from app.services import health_service
    from app.services import sensor_service

    readings = sensor_service.get_recent(db, equipment.equipment_id, count=window)
    if not readings:
        return [], []

    # Shared with the explainability service and the detail view's reassess
    # action, so the alert set and the displayed condition cannot contradict
    # each other.
    worst = health_service.worst_reading_in_window(
        db, equipment.equipment_id, window=window
    )
    if worst is None:
        return [], []

    worst_reading, values, anomaly_score, anomaly_detected = worst
    profile = equipment_profile(equipment)
    score, status, probability = health_service.preview_assessment(
        values, profile, anomaly_score, db
    )
    ctx = build_context(
        equipment,
        values,
        health_score=score,
        status=status,
        failure_probability=probability,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_detected,
        profile=profile,
    )
    return apply_alerts(db, ctx)


def _with_anomalies(db: Session, equipment: Equipment, readings):
    """Pair each reading with its anomaly result.

    Anomaly scores are computed once across the series so the drift detector
    sees the ordered history, then attached per reading.
    """
    from app.ml.detector import align_with_readings
    from app.services import anomaly_service

    detector = anomaly_service.get_detector()
    if detector is None:
        return readings, {}

    results = detector.detect_series(readings, equipment.equipment_type)
    pairs = align_with_readings(readings, results)
    anomaly_by_id = {
        reading.id: (result.anomaly_score, result.anomaly_detected)
        for reading, result in pairs
    }
    return readings, anomaly_by_id


def equipment_profile(equipment: Equipment):
    from app.ml.baselines import get_profile

    return get_profile(equipment.equipment_type)


def _anomaly_for(db: Session, equipment: Equipment, readings) -> tuple[float, bool]:
    """Anomaly verdict from the most recent assessed reading."""
    _, anomaly_by_id = _with_anomalies(db, equipment, readings)
    if not readings:
        return 0.0, False
    return anomaly_by_id.get(readings[-1].id, (0.0, False))


def evaluate_fleet(db: Session, window: int = 60) -> dict[str, int]:
    """Reconcile alerts for every active machine.

    A machine that cannot be evaluated is counted and skipped; one bad unit
    must not leave the whole fleet un-evaluated.
    """
    from app.repositories.equipment import list_equipment

    settings = get_settings()
    rows, _ = list_equipment(db, active_only=True, limit=500)
    opened = resolved = skipped = 0
    for equipment in rows:
        try:
            new_alerts, cleared = evaluate_equipment(db, equipment, window=window)
            opened += len(new_alerts)
            resolved += len(cleared)
        except Exception:  # noqa: BLE001
            skipped += 1
            logger.exception("Alert evaluation failed for %s", equipment.equipment_id)
    return {"opened": opened, "resolved": resolved, "skipped": skipped}
