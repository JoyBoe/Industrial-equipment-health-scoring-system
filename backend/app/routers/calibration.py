"""Failure recording and risk-model calibration routes.

Split from `predictions` because these are administrative: they change what the
model *is*, whereas `/predict` only asks it a question. Keeping them apart
makes it obvious which endpoints have side effects on the reported numbers.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.equipment import EquipmentNotFoundError
from app.schemas.calibration import (
    CalibrateResponse,
    CalibrationClearResponse,
    CalibrationStatusResponse,
    FailureListResponse,
    FailureRecordCreate,
    FailureRecordResponse,
)
from app.services import calibration_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["calibration"])

#: What still applies after a fit succeeds. Reported rather than left out: a
#: successful calibration removes the placeholder caveat but does not turn a
#: wear-out model into a supervised one, and an operator reading `calibrated:
#: true` should not infer more than that.
POST_CALIBRATION_CAVEATS = [
    "Calibration sets the base rate only. The degradation exponent, channel "
    "weights and anomaly multiplier are unchanged and remain engineering "
    "judgement, not fitted values.",
    "The fit is an unweighted mean of completed service intervals. Censoring is "
    "not modelled: machines still running contribute no interval, so observed "
    "MTBF is biased short.",
    "A fit inherits the quality of the failure records it was given; this "
    "deployment's telemetry is synthetic, so no real-plant validation backs it.",
]


def _status_payload(db: Session) -> CalibrationStatusResponse:
    """Build the calibration status from the live state.

    Reports the *effective* rate as the headline, because that is what the risk
    model is actually using; the suggested rate is shown alongside it so a
    caller can see the gap between what it has and what it could get.
    """
    report = calibration_service.assess(db)
    record = calibration_service.calibration_record(db)
    effective, calibrated = calibration_service.effective_calibration(db)

    payload = report.as_dict()
    payload["calibrated"] = calibrated
    payload["base_hazard_per_hour"] = effective
    payload["calibrated_at"] = record.calibrated_at if record else None
    payload["persisted_sample_size"] = record.sample_size if record else None
    payload["caveats"] = POST_CALIBRATION_CAVEATS if calibrated else []
    return CalibrationStatusResponse(**payload)


def _not_found(exc: EquipmentNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


# --- Calibration ------------------------------------------------------------


@router.get(
    "/predict/calibration",
    response_model=CalibrationStatusResponse,
    summary="Risk model calibration status",
)
def get_calibration(db: Session = Depends(get_db)) -> CalibrationStatusResponse:
    """Whether reported probabilities are fitted, to what, and why not.

    Always returns a reason. An uncalibrated instance is told the exact number
    of usable records it is short of, rather than only that it is uncalibrated.
    """
    return _status_payload(db)


@router.post(
    "/predict/calibrate",
    response_model=CalibrateResponse,
    summary="Fit the risk model to failure history",
)
def post_calibrate(db: Session = Depends(get_db)) -> CalibrateResponse:
    """Fit ``lambda_0 = 1/MTBF`` from closed failure records and persist it.

    Refuses when fewer than
    ``MIN_FAILURES_TO_CALIBRATE`` usable records exist, and applies nothing when
    it refuses: a rate fitted from a handful of intervals would be confident and
    wrong, which is worse than the honest placeholder. The response carries the
    reason either way, so a rejected fit is as informative as an accepted one.
    """
    report = calibration_service.apply_calibration(db)
    return CalibrateResponse(applied=report.calibrated, status=_status_payload(db))


@router.delete(
    "/predict/calibration",
    response_model=CalibrationClearResponse,
    summary="Discard the fitted calibration",
)
def delete_calibration(db: Session = Depends(get_db)) -> CalibrationClearResponse:
    """Drop the persisted fit and fall back to the placeholder rate.

    Exposed rather than hidden: recalibrating against a new batch of records
    should start from that evidence, not silently average with a stale fit.
    """
    cleared = calibration_service.clear_calibration(db)
    return CalibrationClearResponse(cleared=cleared, status=_status_payload(db))


# --- Failure records --------------------------------------------------------


def _to_response(row, equipment_id: str) -> FailureRecordResponse:
    return FailureRecordResponse(
        id=row.id,
        equipment_id=equipment_id,
        interval_start=row.interval_start,
        failed_at=row.failed_at,
        interval_hours=round(row.interval_hours or 0.0, 4),
        is_catalytic=row.is_catalytic,
        failure_mode=row.failure_mode,
        note=row.note,
    )


@router.get(
    "/equipment/{equipment_id}/failures",
    response_model=FailureListResponse,
    summary="List recorded failures",
)
def get_failures(
    equipment_id: str, db: Session = Depends(get_db)
) -> FailureListResponse:
    """Closed failure cycles for one machine, newest first."""
    try:
        rows = calibration_service.list_failures(db, equipment_id)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc
    return FailureListResponse(
        equipment_id=equipment_id,
        failures=[_to_response(r, equipment_id) for r in rows],
        total=len(rows),
    )


@router.post(
    "/equipment/{equipment_id}/failures",
    response_model=FailureRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a failure",
)
def post_failure(
    equipment_id: str,
    payload: FailureRecordCreate,
    db: Session = Depends(get_db),
) -> FailureRecordResponse:
    """Record one closed failure cycle.

    Recording does **not** silently recalibrate: `POST /predict/calibrate`
    fits explicitly, so a fit is always a deliberate act with a visible result
    rather than a side effect of data entry.
    """
    try:
        row = calibration_service.record_failure(
            db,
            equipment_id,
            interval_start=payload.interval_start,
            failed_at=payload.failed_at,
            is_catalytic=payload.is_catalytic,
            failure_mode=payload.failure_mode,
            note=payload.note,
        )
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc
    return _to_response(row, equipment_id)


@router.delete(
    "/equipment/{equipment_id}/failures/{failure_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a failure record",
)
def delete_failure(
    equipment_id: str, failure_id: int, db: Session = Depends(get_db)
) -> Response:
    """Remove one record. The machine must exist even if the record does not."""
    try:
        calibration_service.list_failures(db, equipment_id)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc

    if not calibration_service.delete_failure(db, failure_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Failure record '{failure_id}' not found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
