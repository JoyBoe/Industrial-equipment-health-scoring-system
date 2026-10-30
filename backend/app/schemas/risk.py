"""Failure prediction request/response schemas."""

from datetime import datetime
from typing import Annotated

from pydantic import Field, model_validator

from app.config import SENSOR_FEATURES
from app.ml.risk import DEFAULT_HORIZONS, PRIMARY_HORIZON_HOURS
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
), "prediction channels diverged from the ML feature contract"


class PredictionRequest(ORMModel):
    """Sensor values to estimate failure risk for.

    Either `equipment_id` (uses that machine's type and stored state) or
    `equipment_type`.
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

    horizon_hours: int = Field(
        default=PRIMARY_HORIZON_HOURS,
        ge=1,
        le=24 * 365,
        description=(
            "Horizon over which the probability is stated. Defaults to the "
            "30-day decision horizon used for the risk level."
        ),
    )
    anomaly_score: float | None = Field(
        None, ge=0.0, le=1.0, description="Override; otherwise derived from telemetry"
    )

    @field_validator("equipment_id")
    @classmethod
    def _canonicalise(cls, value: str | None) -> str | None:
        return None if value is None else normalise_equipment_id(value)

    @model_validator(mode="after")
    def _require_target(self) -> "PredictionRequest":
        if not self.equipment_id and not self.equipment_type:
            raise ValueError("either equipment_id or equipment_type must be provided")
        return self

    def channel_values(self) -> dict[str, float]:
        return {
            channel: float(getattr(self, channel))
            for channel in SENSOR_FEATURES
            if getattr(self, channel) is not None
        }


class RiskDriver(ORMModel):
    """One channel's contribution to the estimated risk."""

    parameter: str
    degradation: float | None
    weight: float | None
    hazard_multiplier: float
    share: float


class PredictionResponse(ORMModel):
    """A failure risk estimate.

    `calibrated` is always reported. A probability without a stated horizon and
    an honest calibration flag is not usable information.
    """

    equipment_id: str | None = None
    equipment_type: str
    health_score: float
    status: str
    failure_probability: float = Field(ge=0.0, le=1.0)
    risk_level: str
    horizon_hours: int
    horizon_probabilities: dict[str, float]
    dominant_driver: str | None
    drivers: list[RiskDriver]
    method: str
    calibrated: bool
    notes: str
    generated_at: datetime


class EquipmentRiskResponse(ORMModel):
    """Risk for a machine, from its stored history."""

    equipment_id: str
    equipment_type: str
    health_score: float | None
    status: str | None
    failure_probability: float
    risk_level: str
    horizon_hours: int
    horizon_probabilities: dict[str, float]
    dominant_driver: str | None
    drivers: list[RiskDriver]
    method: str
    calibrated: bool
    notes: str
    assessed_at: datetime | None
