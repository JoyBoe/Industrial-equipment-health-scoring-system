"""Schemas for failure records and risk-model calibration."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel


def _as_utc_naive(value: datetime) -> datetime:
    """Normalise a timestamp to UTC-naive.

    The API accepts machine-commissioning dates typed by operators, who have no
    reason to append `+00:00`; naive input is read as UTC, matching the rest of
    the API. Aware input is converted first, so `+05:30` does not silently shift
    the interval it describes.

    The result is deliberately naive because that is how every timestamp is
    stored here (SQLite has no timezone type) — see ARCHITECTURE.md §3. Dropping
    the offset *after* conversion, rather than letting SQLite discard it, keeps
    the rule visible instead of accidental.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class FailureRecordCreate(BaseModel):
    """One closed failure cycle, as an operator would enter it."""

    model_config = {"str_strip_whitespace": True}

    #: Start of the service interval: commissioning, or the previous return to
    #: service. Naive input is read as UTC.
    interval_start: datetime
    #: When the machine actually failed. Must not precede `interval_start`.
    failed_at: datetime
    #: False for wear-out failures -- the ones the hazard model describes.
    #: Exogenous events (operator error, supply surge) are excluded from the fit
    #: because they say nothing about wear.
    is_catalytic: bool = False
    failure_mode: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("interval_start", "failed_at")
    @classmethod
    def _normalise(cls, value: datetime) -> datetime:
        return _as_utc_naive(value)

    @field_validator("failure_mode")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _interval_must_be_forward(self) -> "FailureRecordCreate":
        # Checked here rather than in the service so a backwards interval is
        # rejected as malformed input (422) instead of surfacing as a server
        # error or, worse, quietly producing a negative MTBF.
        if self.failed_at < self.interval_start:
            raise ValueError(
                f"failed_at ({self.failed_at.isoformat()}) precedes "
                f"interval_start ({self.interval_start.isoformat()})"
            )
        return self


class FailureRecordResponse(ORMModel):
    """A stored failure record."""

    id: int
    equipment_id: str
    interval_start: datetime
    failed_at: datetime
    #: Hours of service before the failure. The quantity the fit actually uses.
    interval_hours: float
    is_catalytic: bool
    failure_mode: str | None
    note: str | None


class FailureListResponse(BaseModel):
    """Every recorded failure for one machine, newest first."""

    equipment_id: str
    failures: list[FailureRecordResponse]
    total: int


class CalibrationStatusResponse(BaseModel):
    """Whether the reported probabilities are fitted, and to what.

    Returned whether or not calibration has succeeded: the reason it has *not*
    succeeded is the more useful half of the payload for an operator holding a
    `calibrated: false` response elsewhere.
    """

    calibrated: bool
    #: The rate the risk model is running at right now, fitted or placeholder.
    base_hazard_per_hour: float
    #: What a fit from the current records would produce, if it succeeded.
    suggested_base_hazard_per_hour: float | None
    implied_mtbf_hours: float | None
    implied_mtbf_years: float | None
    candidate_failures: int
    usable: int
    excluded_catalytic: int
    excluded_outliers: int
    mean_interval_hours: float | None
    stdev_interval_hours: float | None
    coefficient_of_variation: float | None
    #: True when the count passed but the spread of intervals is wide enough
    #: that the fitted rate rests on thin evidence.
    thin_sample: bool
    reason: str
    minimum_required: int
    #: When the persisted fit was made; null if no fit has been applied.
    calibrated_at: datetime | None = None
    #: Sample size that produced the persisted fit.
    persisted_sample_size: int | None = None
    #: Every caveat that still applies after a successful fit.
    caveats: list[str] = Field(default_factory=list)


class CalibrateResponse(BaseModel):
    """The outcome of attempting to fit and apply a base hazard."""

    applied: bool
    status: CalibrationStatusResponse


class CalibrationClearResponse(BaseModel):
    """Whether discarding the persisted fit changed anything."""

    cleared: bool
    status: CalibrationStatusResponse
