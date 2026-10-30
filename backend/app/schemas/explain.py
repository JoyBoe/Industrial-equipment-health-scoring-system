"""Explainability request/response schemas."""

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from app.config import SENSOR_FEATURES

from app.schemas.common import (
    Amps,
    LoadPercent,
    ORMModel,
    PowerKw,
    PressureBar,
    Rpm,
    TemperatureC,
    VibrationMmS,
    Volts,
    field_validator,
    normalise_equipment_id,
)
from app.schemas.health import ContributingFactor, ScoreBreakdownRead

assert SENSOR_FEATURES == (
    "temperature", "vibration", "pressure", "rpm", "current", "voltage", "power", "load"
), "explainability channels diverged from the ML feature contract"


class ExplainRequest(ORMModel):
    """Sensor values to explain.

    Either `equipment_id` (uses the machine's type) or `equipment_type`.
    """

    equipment_id: str | None = Field(None, min_length=2, max_length=32)
    equipment_type: str | None = Field(None, min_length=2, max_length=60)

    temperature: TemperatureC | None = None
    vibration: VibrationMmS | None = None
    pressure: PressureBar | None = None
    rpm: Rpm | None = None
    current: Amps | None = None
    voltage: Volts | None = None
    power: PowerKw | None = None
    load: LoadPercent | None = None

    anomaly_score: float | None = Field(None, ge=0.0, le=1.0)

    @field_validator("equipment_id")
    @classmethod
    def _canonicalise(cls, value: str | None) -> str | None:
        return None if value is None else normalise_equipment_id(value)

    @model_validator(mode="after")
    def _require_target(self) -> "ExplainRequest":
        if not self.equipment_id and not self.equipment_type:
            raise ValueError("either equipment_id or equipment_type must be provided")
        if all(getattr(self, channel) is None for channel in SENSOR_FEATURES):
            raise ValueError("at least one sensor channel must be provided")
        return self

    def channel_values(self) -> dict[str, float]:
        return {
            channel: float(getattr(self, channel))
            for channel in SENSOR_FEATURES
            if getattr(self, channel) is not None
        }


class AttributionItem(ORMModel):
    """One channel's cost, with the counterfactual for fixing it.

    `expected_range` and `alarm_limit` are included so an operator can see the
    limit they are being judged against without a second lookup.
    """

    parameter: str
    value: float
    nominal: float
    expected_range: list[float]
    alarm_limit: float
    unit: str
    direct_penalty: float
    counterfactual_penalty: float
    marginal_contribution: float
    is_dominant: bool
    would_fix: str | None


class OperatingBand(ORMModel):
    """Acceptable and alarm limits for one channel of this equipment type."""

    parameter: str
    unit: str
    nominal: float
    ok_low: float
    ok_high: float
    alarm_low: float
    alarm_high: float
    low_relevant: bool


class TrendingParameter(ORMModel):
    """A channel moving persistently away from normal.

    Still inside its acceptable range, which is precisely why a point-in-time
    score would not show it.
    """

    parameter: str
    drift: float


class SuppressedFactor(ORMModel):
    """An out-of-range channel that is not being charged any points."""

    parameter: str
    reason: str


class TrendSummary(ORMModel):
    """Health over the machine's recent assessment history."""

    available: bool
    reason: str | None = None
    samples: int | None = None
    first_score: float | None = None
    latest_score: float | None = None
    min_score: float | None = None
    max_score: float | None = None
    average_score: float | None = None
    change_over_window: float | None = None
    direction: str | None = None


class ExplanationResponse(ORMModel):
    """Complete explanation of a health score."""

    equipment_id: str | None = None
    equipment_type: str
    observed_at: datetime | None = None
    generated_at: datetime

    health_score: float
    status: str
    risk_level: str
    failure_probability: float
    anomaly_score: float
    anomaly_detected: bool

    explanation: str
    recommendation: str

    score_breakdown: ScoreBreakdownRead
    contributing_factors: list[ContributingFactor]
    attribution: list[AttributionItem]
    operating_bands: list[OperatingBand] = []
    trending_parameters: list[TrendingParameter]
    suppressed_by_policy: list[SuppressedFactor]
    risk: dict[str, Any]
    trend: TrendSummary
