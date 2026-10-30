"""Health score request/response schemas."""

from datetime import datetime
from typing import Annotated, Any

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

assert SENSOR_FEATURES == (
    "temperature", "vibration", "pressure", "rpm", "current", "voltage", "power", "load"
), "health scoring channels diverged from the ML feature contract"


class HealthScoreRequest(ORMModel):
    """Sensor values to score.

    Either `equipment_id` (resolves the machine's own equipment type and, by
    default, persists the result) or `equipment_type` (scores against a type
    envelope without touching the database).
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

    persist: bool = Field(
        default=True,
        description="Store the assessment when an equipment_id is supplied",
    )

    @field_validator("equipment_id")
    @classmethod
    def _canonicalise(cls, value: str | None) -> str | None:
        return None if value is None else normalise_equipment_id(value)

    @model_validator(mode="after")
    def _require_target_and_values(self) -> "HealthScoreRequest":
        if not self.equipment_id and not self.equipment_type:
            raise ValueError("either equipment_id or equipment_type must be provided")
        if all(getattr(self, channel) is None for channel in SENSOR_FEATURES):
            raise ValueError("at least one sensor channel must be provided")
        return self

    def channel_values(self) -> dict[str, float]:
        """Provided channels as a plain mapping."""
        return {
            channel: float(getattr(self, channel))
            for channel in SENSOR_FEATURES
            if getattr(self, channel) is not None
        }


class ContributingFactor(ORMModel):
    """One reason the score was reduced.

    `penalty` is the number of points this factor actually cost, so the figures
    in an explanation add up to the score.
    """

    parameter: str
    value: float | None
    impact: str
    penalty: float
    contribution_share: float
    degradation: float | None
    direction: str
    source: str


class ScoreBreakdownRead(ORMModel):
    """Full arithmetic behind a score, for auditing."""

    health_score: float
    status: str
    risk_level: str
    sensor_penalty: float
    anomaly_penalty: float
    risk_penalty: float
    anomaly_score: float
    failure_probability: float
    parameter_penalties: dict[str, float]
    parameter_degradation: dict[str, float]
    total_weight: float
    budgets: dict[str, float]
    weights: dict[str, float]


class HealthScoreResponse(ORMModel):
    """A health assessment."""

    equipment_id: str | None = None
    equipment_type: str
    health_score: float = Field(ge=0, le=100)
    status: str
    risk_level: str
    anomaly_score: float
    anomaly_detected: bool
    failure_probability: float
    #: Always reported alongside `failure_probability`. This response reports a
    #: risk number, so it must also report whether that number is calibrated --
    #: otherwise a client has no way to know it is reading a relative index.
    calibrated: bool = False
    contributing_factors: list[ContributingFactor]
    explanation: str
    recommendation: str
    score_breakdown: ScoreBreakdownRead


class HealthHistoryResponse(ORMModel):
    """Latest stored assessment for a machine."""

    equipment_id: str
    health_score: float
    status: str
    risk_level: str
    anomaly_score: float | None
    failure_probability: float | None
    #: As in HealthScoreResponse: a stored probability is still an uncalibrated
    #: index unless the risk model has been fitted.
    calibrated: bool = False
    contributing_factors: list[dict[str, Any]]
    explanation: str | None
    recommendation: str | None
    timestamp: datetime
    previous_score: float | None
    delta: float | None


class TrendPoint(ORMModel):
    """One point on the health-score trend chart."""

    timestamp: datetime
    health_score: float
    status: str
    risk_level: str
    anomaly_score: float | None


class HealthTrendResponse(ORMModel):
    """Health score history, oldest first."""

    equipment_id: str
    points: list[TrendPoint]
    count: int
    latest: float | None
    first: float | None
    min_score: float | None
    max_score: float | None
    average_score: float | None


class BatchScoreResponse(ORMModel):
    """Result of scoring the whole fleet."""

    scored: int
    skipped: int
