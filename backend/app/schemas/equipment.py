"""Equipment request/response schemas."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, computed_field, field_validator

from app.models.base import HealthStatus, RiskLevel
from app.schemas.common import (
    ORMModel,
    OperatingHours,
    field_validator,
    normalise_equipment_id,
    validate_not_before_installation,
)

EQUIPMENT_TYPES: tuple[str, ...] = (
    "Hydraulic Pump",
    "Industrial Motor",
    "Air Compressor",
    "CNC Machine",
    "Conveyor Drive",
    "Gearbox",
    "Boiler",
    "Chiller",
    "Transformer",
    "Turbine",
)


class EquipmentBase(ORMModel):
    """Fields shared by create and update payloads."""

    equipment_id: Annotated[str, Field(min_length=2, max_length=32, examples=["EQ-001"])]
    equipment_name: Annotated[str, Field(min_length=2, max_length=120)]
    equipment_type: Annotated[str, Field(min_length=2, max_length=60)]
    location: Annotated[str, Field(min_length=2, max_length=120)]
    manufacturer: Annotated[str | None, Field(max_length=120)] = None
    model_number: Annotated[str | None, Field(max_length=120)] = None
    installation_date: date | None = None
    operating_hours: OperatingHours = 0.0
    rated_power_kw: Annotated[float | None, Field(gt=0, le=10000)] = None
    is_active: bool = True

    @field_validator("equipment_id")
    @classmethod
    def _canonicalise_id(cls, value: str) -> str:
        return normalise_equipment_id(value)

    @field_validator("installation_date")
    @classmethod
    def _check_installation_date(cls, value: date | None) -> date | None:
        return None if value is None else validate_not_before_installation(value)

    @field_validator("equipment_name", "location", "equipment_type")
    @classmethod
    def _strip_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class EquipmentCreate(EquipmentBase):
    """Payload for registering new equipment."""


class EquipmentUpdate(ORMModel):
    """Partial update payload.

    Every field is optional; omitted fields are left unchanged. `equipment_id`
    is intentionally absent because it is the stable external key and changing
    it would orphan historical readings and alerts.

    `extra="forbid"` turns an attempt to send `equipment_id` into a loud 422
    instead of silently ignoring it, so a client bug surfaces immediately
    rather than appearing to succeed.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    equipment_name: Annotated[str | None, Field(min_length=2, max_length=120)] = None
    equipment_type: Annotated[str | None, Field(min_length=2, max_length=60)] = None
    location: Annotated[str | None, Field(min_length=2, max_length=120)] = None
    manufacturer: Annotated[str | None, Field(max_length=120)] = None
    model_number: Annotated[str | None, Field(max_length=120)] = None
    installation_date: date | None = None
    operating_hours: OperatingHours | None = None
    rated_power_kw: Annotated[float | None, Field(gt=0, le=10000)] = None
    is_active: bool | None = None

    @field_validator("installation_date")
    @classmethod
    def _check_installation_date(cls, value: date | None) -> date | None:
        return None if value is None else validate_not_before_installation(value)

    @field_validator("equipment_name", "location", "equipment_type")
    @classmethod
    def _strip_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class EquipmentRead(ORMModel):
    """Equipment representation returned by the API.

    `health_status` is exposed in addition to `status` so the frontend never
    has to hard-code the mapping.
    """

    id: int
    equipment_id: str
    equipment_name: str
    equipment_type: str
    location: str
    manufacturer: str | None
    model_number: str | None
    installation_date: date | None
    operating_hours: float
    rated_power_kw: float | None
    is_active: bool
    health_score: float | None
    status: str | None
    risk_level: str | None
    failure_probability: float | None
    last_assessed_at: datetime | None
    last_updated: datetime
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def health_status(self) -> str | None:
        """Status coerced to the canonical enum, or None if never assessed."""
        if self.status is None:
            return None
        try:
            return HealthStatus(self.status).value
        except ValueError:
            # Tolerate legacy or manually-inserted values rather than erroring
            # the whole dashboard because of one bad row.
            return self.status

    @computed_field  # type: ignore[prop-decorator]
    @property
    def risk_severity(self) -> str | None:
        if self.risk_level is None:
            return None
        try:
            return RiskLevel(self.risk_level).value
        except ValueError:
            return self.risk_level


class EquipmentSummary(ORMModel):
    """Compact row for the dashboard equipment table."""

    equipment_id: str
    equipment_name: str
    equipment_type: str
    location: str
    health_score: float | None
    status: str | None
    risk_level: str | None
    operating_hours: float
    last_updated: datetime


class EquipmentListResponse(ORMModel):
    """Paginated equipment collection."""

    items: list[EquipmentRead]
    total: int
    limit: int
    offset: int


class EquipmentDeleteResponse(ORMModel):
    """Result of a delete request."""

    equipment_id: str
    deleted: bool
    message: str
    kind: Literal["equipment", "deactivated"] = "equipment"
