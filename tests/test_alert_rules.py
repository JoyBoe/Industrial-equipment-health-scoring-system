"""Alert rules engine tests.

Rules are pure functions of a context, so most of these assert rule behaviour
directly without any database involved. Only deduplication and resolution need
persistence, and those are tested through the service.
"""

import pytest
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.ml.baselines import get_profile
from app.models.base import AlertSeverity, AlertType, HealthStatus
from app.models.equipment import Equipment
from app.services.alert_service import (
    RULES,
    AlertContext,
    build_context,
    escalate,
    evaluate_rules,
    rule_anomaly_detected,
    rule_excessive_vibration,
    rule_high_failure_risk,
    rule_high_temperature,
    rule_low_health_score,
    rule_pressure_instability,
)

MOTOR = get_profile("Industrial Motor")

NOMINAL = {
    "temperature": 68.0, "vibration": 2.8, "pressure": 4.2, "rpm": 1450.0,
    "current": 35.0, "voltage": 400.0, "power": 21.0, "load": 70.0,
}


def make_context(equipment: Equipment, **overrides) -> AlertContext:
    """Build a context, defaulting to a healthy machine."""
    values = {**NOMINAL, **overrides.pop("values", {})}
    params = {
        "health_score": 100.0,
        "status": HealthStatus.EXCELLENT.value,
        "failure_probability": 0.01,
        "anomaly_score": 0.0,
        "anomaly_detected": False,
    }
    params.update(overrides)
    return build_context(equipment, values, profile=MOTOR, **params)


@pytest.fixture
def equipment(db: Session) -> Equipment:
    """A machine for rule evaluation, removed afterwards.

    The test database is session-scoped, so without teardown this row would
    collide with the next test's fixture on the unique equipment_id.
    """
    row = Equipment(
        equipment_id="EQ-ALT",
        equipment_name="Alert Test Motor",
        equipment_type="Industrial Motor",
        location="Test Bay",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        yield row
    finally:
        db.delete(row)
        db.commit()


# --- Severity escalation ---

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, AlertSeverity.INFO.value),
        (0.49, AlertSeverity.INFO.value),
        (0.50, AlertSeverity.WARNING.value),
        (0.99, AlertSeverity.WARNING.value),
        (1.0, AlertSeverity.CRITICAL.value),
        (1.5, AlertSeverity.CRITICAL.value),
    ],
)
def test_escalate_ladder(value: float, expected: str) -> None:
    assert escalate(value, 0.5, 1.0) == expected


def test_alarm_breach_is_always_critical() -> None:
    """A confirmed excursion must not be reported as a mere warning."""
    assert escalate(1.0, 0.9, 1.0) == AlertSeverity.CRITICAL.value


# --- Healthy machine ---

def test_healthy_machine_raises_nothing(equipment: Equipment) -> None:
    assert evaluate_rules(make_context(equipment)) == []


# --- Individual rules ---

def test_low_health_rule(equipment: Equipment) -> None:
    fired = rule_low_health_score(make_context(equipment, health_score=30.0), get_settings())

    assert fired is not None
    assert fired.alert_type == AlertType.LOW_HEALTH_SCORE
    # 30 is in the "Poor" band, which is a warning-level condition.
    assert fired.severity == AlertSeverity.WARNING.value
    assert "30.0" in fired.description


def test_low_health_rule_critical_below_25(equipment: Equipment) -> None:
    """A machine in the Critical health band must raise a Critical alert."""
    fired = rule_low_health_score(make_context(equipment, health_score=15.0), get_settings())

    assert fired.severity == AlertSeverity.CRITICAL.value


def test_low_health_rule_boundary(equipment: Equipment) -> None:
    settings = get_settings()
    at_threshold = rule_low_health_score(
        make_context(equipment, health_score=settings.alert_health_threshold), settings
    )
    below = rule_low_health_score(
        make_context(equipment, health_score=settings.alert_health_threshold - 1), settings
    )

    assert at_threshold is None
    assert below is not None


def test_high_temperature_rule(equipment: Equipment) -> None:
    fired = rule_high_temperature(
        make_context(equipment, values={"temperature": 95.0}), get_settings()
    )

    assert fired is not None
    assert fired.alert_type == AlertType.HIGH_TEMPERATURE
    assert "95" in fired.description
    assert "degC" in fired.description


def test_excessive_vibration_rule(equipment: Equipment) -> None:
    fired = rule_excessive_vibration(
        make_context(equipment, values={"vibration": 12.0}), get_settings()
    )

    assert fired is not None
    assert fired.alert_type == AlertType.EXCESSIVE_VIBRATION
    assert "bearing" in fired.recommended_action.lower()


def test_pressure_rule(equipment: Equipment) -> None:
    fired = rule_pressure_instability(
        make_context(equipment, values={"pressure": 1.0}), get_settings()
    )

    assert fired is not None
    assert fired.alert_type == AlertType.PRESSURE_INSTABILITY


def test_anomaly_rule(equipment: Equipment) -> None:
    fired = rule_anomaly_detected(
        make_context(equipment, anomaly_score=0.9, anomaly_detected=True), get_settings()
    )

    assert fired is not None
    assert fired.alert_type == AlertType.ANOMALY_DETECTED
    assert fired.severity == AlertSeverity.CRITICAL.value


def test_high_risk_rule(equipment: Equipment) -> None:
    fired = rule_high_failure_risk(
        make_context(equipment, failure_probability=0.6), get_settings()
    )

    assert fired is not None
    assert fired.alert_type == AlertType.HIGH_FAILURE_RISK
    assert "60" in fired.description or "60.0%" in fired.description


def test_high_risk_alert_never_presents_an_index_as_a_measured_rate(
    equipment: Equipment,
) -> None:
    """The alert text must not present an index as a measured rate.

    The wording is stated as a rule rather than as a fixed state: the model can
    now be fitted to real failure history, so an alert that hard-coded
    "uncalibrated" would become false the moment an operator calibrated it.
    What must survive either way is the warning itself.
    """
    fired = rule_high_failure_risk(
        make_context(equipment, failure_probability=0.6), get_settings()
    )

    action = fired.recommended_action.lower()
    assert "relative index" in action, (
        "An alert quoting a probability must label it an index, not a rate"
    )
    assert "measured failure rate" in action
    assert "fitted to this fleet's failure history" in action


def test_missing_parameter_does_not_fire(equipment: Equipment) -> None:
    """A dropped channel is a telemetry problem, not an alert condition."""
    ctx = build_context(
        equipment,
        {"temperature": 95.0},  # vibration absent entirely
        health_score=100.0,
        status="Excellent",
        failure_probability=0.01,
        anomaly_score=0.0,
        anomaly_detected=False,
        profile=MOTOR,
    )

    assert "vibration" not in ctx.degradations
    assert rule_excessive_vibration(ctx, get_settings()) is None
    assert rule_high_temperature(ctx, get_settings()) is not None


# --- Thresholds are band-relative ---

def test_threshold_is_equivalent_across_equipment_types() -> None:
    """The same degradation must trip the rule on any machine type.

    A raw threshold would be silently wrong for every machine but the one it
    was written for.
    """
    settings = get_settings()
    motor = get_profile("Industrial Motor")
    turbine = get_profile("Turbine")

    # Half way to each machine's own vibration alarm limit.
    motor_v = motor.band("vibration").ok_high + 0.5 * motor.band("vibration").clamp_span()
    turbine_v = turbine.band("vibration").ok_high + 0.5 * turbine.band("vibration").clamp_span()

    motor_ctx = build_context(
        equipment=Equipment(equipment_id="X1", equipment_name="M", equipment_type="Industrial Motor", location="B"),
        values={**NOMINAL, "vibration": motor_v}, profile=motor, health_score=100.0,
        status="Excellent", failure_probability=0.01, anomaly_score=0.0, anomaly_detected=False,
    )
    turbine_ctx = build_context(
        equipment=Equipment(equipment_id="X2", equipment_name="T", equipment_type="Turbine", location="B"),
        values={**NOMINAL, "vibration": turbine_v, "temperature": 88.0}, profile=turbine,
        health_score=100.0, status="Excellent", failure_probability=0.01,
        anomaly_score=0.0, anomaly_detected=False,
    )

    assert rule_excessive_vibration(motor_ctx, settings) is not None
    assert rule_excessive_vibration(turbine_ctx, settings) is not None


def test_thresholds_are_configurable(equipment: Equipment) -> None:
    lenient = Settings(alert_vibration_threshold=0.95)
    strict = Settings(alert_vibration_threshold=0.10)
    ctx = make_context(equipment, values={"vibration": 8.0})

    assert rule_excessive_vibration(ctx, lenient) is None
    assert rule_excessive_vibration(ctx, strict) is not None


# --- Multi-rule ---

def test_multiple_rules_can_fire_together(equipment: Equipment) -> None:
    ctx = make_context(
        equipment,
        values={"temperature": 120.0, "vibration": 13.0},
        health_score=35.0,
        failure_probability=0.7,
        anomaly_score=0.9,
        anomaly_detected=True,
    )

    fired = {c.alert_type for c in evaluate_rules(ctx)}

    assert AlertType.HIGH_TEMPERATURE in fired
    assert AlertType.EXCESSIVE_VIBRATION in fired
    assert AlertType.LOW_HEALTH_SCORE in fired
    assert AlertType.ANOMALY_DETECTED in fired
    assert AlertType.HIGH_FAILURE_RISK in fired


def test_rules_never_raise(equipment: Equipment) -> None:
    """Rules must tolerate a context with almost nothing in it."""
    empty = AlertContext(
        equipment=equipment, profile=MOTOR, values={}, health_score=100.0,
        status="Excellent", failure_probability=0.0, anomaly_score=0.0,
        anomaly_detected=False, degradations={},
    )
    assert evaluate_rules(empty) == []


def test_all_rules_are_registered() -> None:
    assert len(RULES) == 6


def test_rule_order_is_deterministic() -> None:
    assert RULES == tuple(RULES)
