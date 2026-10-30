"""Failure-history I/O for calibrating the risk model.

The service layer owns everything `ml.calibration` must not do: reading the
failure table, writing the fitted rate, and resolving what the *effective*
calibration of a running instance actually is.

`resolve_calibration` is the single place the rest of the application asks
"is this instance calibrated, and at what rate?". Health scoring, prediction
and explanation all route through it, so they cannot drift apart and report
different calibration states for the same deployment.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.ml.calibration import CalibrationReport, FailureInterval, fit_base_hazard
from app.models.base import utcnow
from app.models.calibration import ModelCalibration
from app.models.failure import Failure
from app.repositories.equipment import EquipmentNotFoundError, get_by_equipment_id

logger = logging.getLogger(__name__)


def effective_calibration(db: Session | None, settings: Settings | None = None) -> tuple[float, bool]:
    """Return ``(base_hazard_per_hour, calibrated)`` for this instance.

    A persisted fit wins over the environment. That ordering is deliberate: the
    environment holds a placeholder, whereas a persisted rate was derived from
    evidence and would be meaningless if an unrelated `.env` edit overrode it.

    With no session the environment is authoritative, which keeps the pure
    ``ml/`` tests and any caller without a database on the old, honest
    path: uncalibrated.
    """
    settings = settings or get_settings()

    if db is None:
        return settings.base_hazard_per_hour, settings.risk_model_calibrated

    record = db.get(ModelCalibration, 1)
    if record is None:
        return settings.base_hazard_per_hour, settings.risk_model_calibrated

    return record.base_hazard_per_hour, True


def _to_intervals(db: Session) -> list[FailureInterval]:
    rows = db.scalars(select(Failure)).all()
    return [
        FailureInterval(hours=row.interval_hours, is_catalytic=row.is_catalytic)
        for row in rows
    ]


def assess(db: Session, settings: Settings | None = None) -> CalibrationReport:
    """Try to fit a rate, and say precisely why if it cannot be fitted.

    Always reports against the rate the instance is *currently* running at, so
    a caller can compare what it would get against what it has.
    """
    settings = settings or get_settings()
    current, _ = effective_calibration(db, settings)
    report = fit_base_hazard(_to_intervals(db), current)
    return report


def apply_calibration(db: Session, settings: Settings | None = None) -> CalibrationReport:
    """Fit the rate, then persist it.

    The fitted rate is stored in the database rather than the environment
    because a deployed instance must keep its calibration across restarts
    without an operator editing `.env`. A rate derived from a year of failure
    history is a genuine measurement, and a measurement belongs with the data
    that produced it.

    Refuses to write anything unless the fit succeeded -- `fit_base_hazard`
    sets `calibrated=False` when the sample is too small, and a refused fit must
    never leave a stale rate behind looking current.
    """
    report = assess(db, settings)

    if not report.calibrated:
        logger.info("Calibration not applied: %s", report.reason)
        return report

    record = db.get(ModelCalibration, 1)
    if record is None:
        record = ModelCalibration(id=1)
        db.add(record)

    record.base_hazard_per_hour = report.suggested_base_hazard_per_hour
    record.sample_size = report.usable
    record.mean_interval_hours = report.mean_interval_hours
    record.coefficient_of_variation = report.coi
    record.calibrated_at = utcnow()

    db.commit()
    db.refresh(record)

    logger.info(
        "Calibrated base hazard to %.4e/h from %d failures",
        record.base_hazard_per_hour,
        report.usable,
    )
    return report


def clear_calibration(db: Session) -> bool:
    """Discard the persisted fit and fall back to the placeholder.

    Exposed rather than hidden: recalibrating against a new batch of records
    should start from the evidence, not silently average with a stale fit.
    """
    record = db.get(ModelCalibration, 1)
    if record is None:
        return False
    db.delete(record)
    db.commit()
    logger.info("Calibration discarded; base hazard reverted to the placeholder")
    return True


def calibration_record(db: Session) -> ModelCalibration | None:
    return db.get(ModelCalibration, 1)


# --- Failure recording -----------------------------------------------------


def record_failure(
    db: Session,
    equipment_id: str,
    *,
    interval_start: datetime,
    failed_at: datetime,
    is_catalytic: bool = False,
    failure_mode: str | None = None,
    note: str | None = None,
) -> Failure:
    """Record one closed failure cycle for one machine.

    Validation lives here rather than in the schema for the rule that cannot be
    expressed as a field constraint: the machine must exist. The interval
    ordering is checked in the schema, where a violation can be reported as
    malformed input instead of surfacing later as a server error.
    """
    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise EquipmentNotFoundError(f"Equipment '{equipment_id}' not found")

    row = Failure(
        equipment_db_id=equipment.id,
        interval_start=interval_start,
        failed_at=failed_at,
        is_catalytic=is_catalytic,
        failure_mode=failure_mode,
        note=note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_failures(db: Session, equipment_id: str) -> list[Failure]:
    """Closed failure cycles for one machine, newest first.

    Raises `EquipmentNotFoundError` on an unknown machine so the caller can map
    it to a 404 consistently with the rest of the API.
    """
    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise EquipmentNotFoundError(f"Equipment '{equipment_id}' not found")

    return list(
        db.scalars(
            select(Failure)
            .where(Failure.equipment_db_id == equipment.id)
            .order_by(Failure.failed_at.desc())
        ).all()
    )


def delete_failure(db: Session, failure_id: int) -> bool:
    row = db.get(Failure, failure_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
