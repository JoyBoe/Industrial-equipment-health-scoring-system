"""Sensor reading schemas."""

from datetime import UTC, datetime

from pydantic import Field, model_validator

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
    validate_not_future,
)

#: Channels the scoring engine and ML pipeline consume.
SENSOR_CHANNELS: tuple[str, ...] = (
    "temperature",
    "vibration",
    "pressure",
    "rpm",
    "current",
    "voltage",
    "power",
    "load",
)


class SensorReadingCreate(ORMModel):
    """A telemetry sample submitted by a gateway or the seed tooling.

    All channels are optional so a partial or dropouted sample is still
    recordable, but at least one must be present. Physical bounds are
    enforced per channel; engineering alarm limits are applied later against
    the equipment's baseline profile.
    """

    equipment_id: str = Field(min_length=2, max_length=32, examples=["EQ-001"])
    timestamp: datetime | None = None

    temperature: TemperatureC | None = None
    vibration: VibrationMmS | None = None
    pressure: PressureBar | None = None
    rpm: Rpm | None = None
    current: Amps | None = None
    voltage: Volts | None = None
    power: PowerKw | None = None
    load: LoadPercent | None = None

    @field_validator("equipment_id")
    @classmethod
    def _canonicalise_id(cls, value: str) -> str:
        return normalise_equipment_id(value)

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else validate_not_future(value)

    @model_validator(mode="after")
    def _require_at_least_one_channel(self) -> "SensorReadingCreate":
        if all(getattr(self, channel) is None for channel in SENSOR_CHANNELS):
            raise ValueError("at least one sensor channel must be provided")
        return self

    @model_validator(mode="after")
    def _check_physical_consistency(self) -> "SensorReadingCreate":
        """Reject combinations that cannot physically co-occur.

        A zero current with non-zero power, or zero voltage with non-zero
        current, indicates a faulty gateway rather than a real machine state.
        """
        if self.power and self.current == 0:
            raise ValueError("power cannot be non-zero while current is zero")
        if self.voltage == 0 and self.current:
            raise ValueError("current cannot be non-zero while voltage is zero")
        return self

    @property
    def provided_channels(self) -> list[str]:
        return [c for c in SENSOR_CHANNELS if getattr(self, c) is not None]

    @property
    def data_quality(self) -> str:
        """Classify the sample for downstream reporting."""
        count = len(self.provided_channels)
        if count == len(SENSOR_CHANNELS):
            return "complete"
        if count >= len(SENSOR_CHANNELS) - 2:
            return "partial"
        return "sparse"


class SensorReadingBatchCreate(ORMModel):
    """Bulk ingest, used by the seed script and gateway replay."""

    readings: list[SensorReadingCreate] = Field(min_length=1, max_length=5000)


class SensorReadingRead(ORMModel):
    """A stored reading."""

    id: int
    equipment_id: str
    timestamp: datetime
    temperature: float | None
    vibration: float | None
    pressure: float | None
    rpm: float | None
    current: float | None
    voltage: float | None
    power: float | None
    load: float | None
    data_quality: str


class SensorReadingBatchResponse(ORMModel):
    """Result of a bulk ingest."""

    inserted: int
    rejected: int
    equipment_ids: list[str]
