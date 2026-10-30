"""Anomaly request/response schemas."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, model_validator

from app.config import SENSOR_FEATURES
from app.schemas.common import ORMModel, field_validator, normalise_equipment_id
from app.schemas.sensor_reading import SENSOR_CHANNELS

# The ML feature contract and the ingestion schema must agree on the channel
# set. A silent divergence would make the detector score columns that the API
# never accepts.
assert SENSOR_CHANNELS == SENSOR_FEATURES, "channel definitions diverged"


class AnomalyDetectRequest(ORMModel):
    """Ad-hoc anomaly check on a single sample of sensor values.

    Lets an operator or an integration test the detector without first writing
    a reading. When `equipment_id` is supplied the values are scored against
    that machine's own baseline profile; otherwise `equipment_type` is required
    so the right limits are used.

    Requests naming an existing machine are recorded in the assessment history,
    because the result then describes real equipment. Requests carrying only an
    `equipment_type` are exploratory and are never persisted.
    """

    equipment_id: str | None = Field(None, min_length=2, max_length=32)
    equipment_type: str | None = Field(None, min_length=2, max_length=60)

    temperature: float | None = Field(None, ge=-50, le=300)
    vibration: float | None = Field(None, ge=0, le=500)
    pressure: float | None = Field(None, ge=0, le=1000)
    rpm: float | None = Field(None, ge=0, le=60000)
    current: float | None = Field(None, ge=0, le=10000)
    voltage: float | None = Field(None, ge=0, le=1000)
    power: float | None = Field(None, ge=0, le=10000)
    load: float | None = Field(None, ge=0, le=200)

    @field_validator("equipment_id")
    @classmethod
    def _canonicalise(cls, value: str | None) -> str | None:
        return None if value is None else normalise_equipment_id(value)

    @model_validator(mode="after")
    def _require_target_and_values(self) -> "AnomalyDetectRequest":
        if not self.equipment_id and not self.equipment_type:
            raise ValueError("either equipment_id or equipment_type must be provided")
        if all(getattr(self, channel) is None for channel in SENSOR_CHANNELS):
            raise ValueError("at least one sensor channel must be provided")
        return self

    def channel_values(self) -> dict[str, float]:
        """The provided channels as a plain mapping."""
        return {
            channel: float(getattr(self, channel))
            for channel in SENSOR_CHANNELS
            if getattr(self, channel) is not None
        }


class ParameterScore(ORMModel):
    """Per-parameter detail behind an anomaly score."""

    value: float | None
    degradation: Annotated[float, Field(ge=0, le=1)]
    direction: str
    overshoot: float
    drift: float


class AnomalyResponse(ORMModel):
    """Result of a single anomaly assessment."""

    equipment_id: str | None = None
    equipment_type: str
    timestamp: datetime
    anomaly_score: Annotated[float, Field(ge=0, le=1)]
    anomaly_detected: bool
    threshold: float
    affected_parameters: list[str]
    parameter_scores: dict[str, ParameterScore]
    method: str
    model_version: str
    detail: dict[str, Any] = Field(default_factory=dict)


class AnomalyRecordRead(ORMModel):
    """A stored anomaly assessment, for the history view."""

    id: int
    equipment_id: str
    timestamp: datetime
    anomaly_score: float
    anomaly_detected: bool
    affected_parameters: list[str]
    parameter_scores: dict[str, Any] | None
    method: str
    model_version: str | None


class AnomalySummary(ORMModel):
    """Aggregate anomaly statistics for one machine."""

    equipment_id: str
    assessed: int
    detected: int
    detection_rate: float
    max_anomaly_score: float
    mean_anomaly_score: float
    most_common_parameters: list[str]
    last_detected_at: datetime | None
