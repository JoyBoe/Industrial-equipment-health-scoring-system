"""Fit the risk model's base hazard rate from real failure history.

`ml.risk` estimates failure probability from

    lambda(d) = lambda_0 * exp(k * d)

and every probability it reports inherits the credibility of `lambda_0`. With
no failure history `lambda_0` is a placeholder chosen for a plausible order of
magnitude, which is why the API reports `calibrated: false` and the probability
must be read as a relative index rather than a frequency.

This module fits `lambda_0` from closed failure records:

    lambda_0 = 1 / MTBF

where MTBF is the mean service interval between failures across the fleet.
Fitting it is what makes `calibrated: true` honest rather than aspirational.

Refusing to calibrate on a small sample matters more than calibrating at all.
Five failures produce an MTBF with enormous variance, and a confidently wrong
rate is worse than an obviously-unfitted one: it would convert an honest
"relative index" into a false measurement. Calibration is deliberately
conservative for that reason.

Pure computation. Reading and persisting records is the service layer's job
(`services.calibration_service`), because `ml/` must never perform I/O.
"""

import statistics
from dataclasses import dataclass

#: Minimum closed failures before calibration is offered at all. Below this the
#: MTBF has too much variance to be worth trusting, so the system keeps the
#: placeholder and says why, rather than pretending to a precision it does not
#: have.
MIN_FAILURES_TO_CALIBRATE: int = 5

#: Service intervals shorter than this are almost certainly data-entry errors
#: (an end time before its start, a wrong date) and would drag the fitted MTBF
#: down far enough to make every machine look about to fail.
MIN_SANE_INTERVAL_HOURS: float = 1.0

#: Guards against a single absurd record -- a machine commissioned in 1970 --
#: making the whole fleet look immortal.
MAX_SANE_INTERVAL_HOURS: float = 1_000_000.0

#: Coefficient of variation above which a fit is reported as resting on thin
#: evidence even though the sample size passed. The rate is still applied; this
#: only changes the warning the operator sees.
THIN_SAMPLE_COV: float = 0.75


@dataclass(frozen=True)
class FailureInterval:
    """One closed failure cycle, reduced to what the fit needs.

    Takes the place of an ORM row so this module can be exercised without a
    database.
    """

    #: Hours of service between the interval start and the failure.
    hours: float | None
    #: False for failures caused by something other than wear -- operator error,
    #: a supply surge, an external impact. Those say nothing about wear-out.
    is_catalytic: bool = False


@dataclass(frozen=True)
class CalibrationReport:
    """The outcome of trying to fit the base hazard, and why."""

    candidate_failures: int
    excluded_catalytic: int
    excluded_outliers: int
    usable: int
    mean_interval_hours: float | None
    stdev_interval_hours: float | None
    suggested_base_hazard_per_hour: float | None
    current_base_hazard_per_hour: float
    calibrated: bool
    reason: str

    @property
    def implied_mtbf_hours(self) -> float | None:
        if not self.suggested_base_hazard_per_hour:
            return None
        return 1.0 / self.suggested_base_hazard_per_hour

    @property
    def implied_mtbf_years(self) -> float | None:
        hours = self.implied_mtbf_hours
        return None if hours is None else hours / (24.0 * 365.25)

    @property
    def coi(self) -> float | None:
        """Coefficient of variation of the sample. Above ~0.5 the fit is thin."""
        if self.stdev_interval_hours is None or not self.mean_interval_hours:
            return None
        return self.stdev_interval_hours / self.mean_interval_hours

    @property
    def thin_sample(self) -> bool:
        """True when the count passed but the spread is wide enough to warn on."""
        cov = self.coi
        return bool(self.calibrated and cov is not None and cov > THIN_SAMPLE_COV)

    def as_dict(self) -> dict:
        return {
            "candidate_failures": self.candidate_failures,
            "excluded_catalytic": self.excluded_catalytic,
            "excluded_outliers": self.excluded_outliers,
            "usable": self.usable,
            "mean_interval_hours": _round(self.mean_interval_hours, 2),
            "stdev_interval_hours": _round(self.stdev_interval_hours, 2),
            "coefficient_of_variation": _round(self.coi, 4),
            "implied_mtbf_hours": _round(self.implied_mtbf_hours, 2),
            "implied_mtbf_years": _round(self.implied_mtbf_years, 2),
            "suggested_base_hazard_per_hour": _round(
                self.suggested_base_hazard_per_hour, 10
            ),
            "current_base_hazard_per_hour": _round(
                self.current_base_hazard_per_hour, 10
            ),
            "calibrated": self.calibrated,
            "thin_sample": self.thin_sample,
            "reason": self.reason,
            "minimum_required": MIN_FAILURES_TO_CALIBRATE,
        }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def clean_intervals(
    records: list[FailureInterval],
) -> tuple[list[float], int, int]:
    """Usable service intervals in hours, plus what was excluded and why.

    Two exclusions, kept separate because they mean opposite things: a catalytic
    failure is *correctly* excluded (it is not wear), while an out-of-range
    interval is *suspiciously* excluded (the record is probably wrong).
    """
    intervals: list[float] = []
    excluded_catalytic = 0
    excluded_outliers = 0

    for record in records:
        if record.is_catalytic:
            excluded_catalytic += 1
            continue
        hours = record.hours
        if hours is None or not MIN_SANE_INTERVAL_HOURS <= hours <= MAX_SANE_INTERVAL_HOURS:
            excluded_outliers += 1
            continue
        intervals.append(hours)

    return intervals, excluded_catalytic, excluded_outliers


def _insufficient(
    candidate: int,
    usable: int,
    excluded_catalytic: int,
    excluded_outliers: int,
    current: float,
) -> CalibrationReport:
    detail = (
        f" (from {candidate} total, excluding {excluded_catalytic} catalytic "
        f"and {excluded_outliers} outliers)"
        if candidate
        else ". Record failures with POST /api/equipment/{equipment_id}/failures"
    )
    return CalibrationReport(
        candidate_failures=candidate,
        excluded_catalytic=excluded_catalytic,
        excluded_outliers=excluded_outliers,
        usable=usable,
        mean_interval_hours=None,
        stdev_interval_hours=None,
        suggested_base_hazard_per_hour=None,
        current_base_hazard_per_hour=current,
        calibrated=False,
        reason=(
            f"Need at least {MIN_FAILURES_TO_CALIBRATE} usable closed failure "
            f"records to calibrate; found {usable}{detail}"
        ),
    )


def fit_base_hazard(
    records: list[FailureInterval],
    current_base_hazard_per_hour: float,
) -> CalibrationReport:
    """Fit ``lambda_0 = 1 / MTBF`` from closed failure cycles.

    Returns a report either way. A report with ``calibrated=False`` carries the
    reason, which the API surfaces verbatim so an operator knows exactly what
    is missing rather than being told only that calibration has not happened.
    """
    intervals, excluded_catalytic, excluded_outliers = clean_intervals(records)
    candidate = len(records)

    if len(intervals) < MIN_FAILURES_TO_CALIBRATE:
        return _insufficient(
            candidate,
            len(intervals),
            excluded_catalytic,
            excluded_outliers,
            current_base_hazard_per_hour,
        )

    mean_hours = statistics.fmean(intervals)
    stdev_hours = statistics.stdev(intervals) if len(intervals) > 1 else 0.0

    if mean_hours <= 0.0:
        return CalibrationReport(
            candidate_failures=candidate,
            excluded_catalytic=excluded_catalytic,
            excluded_outliers=excluded_outliers,
            usable=len(intervals),
            mean_interval_hours=mean_hours,
            stdev_interval_hours=stdev_hours,
            suggested_base_hazard_per_hour=None,
            current_base_hazard_per_hour=current_base_hazard_per_hour,
            calibrated=False,
            reason="Mean service interval is not positive; check the failure records",
        )

    suggested = 1.0 / mean_hours

    return CalibrationReport(
        candidate_failures=candidate,
        excluded_catalytic=excluded_catalytic,
        excluded_outliers=excluded_outliers,
        usable=len(intervals),
        mean_interval_hours=mean_hours,
        stdev_interval_hours=stdev_hours,
        suggested_base_hazard_per_hour=suggested,
        current_base_hazard_per_hour=current_base_hazard_per_hour,
        calibrated=True,
        reason=(
            f"Fitted from {len(intervals)} closed failures "
            f"(MTBF {mean_hours:,.0f} h / {mean_hours / 8760:.1f} y)"
        ),
    )
