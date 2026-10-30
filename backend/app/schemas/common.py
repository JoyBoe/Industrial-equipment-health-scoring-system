"""Shared schema fields and validators.

Keeping common constraints here prevents the same rule from drifting between
the equipment and reading schemas.
"""

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Physical plausibility ceilings. These are deliberately far above any legal
# operating value: they exist to reject corrupt or spoofed data (e.g. a
# temperature of 1e9), not to encode engineering alarm limits. Real alarm
# thresholds live in the per-type baseline profiles.
TemperatureC = Annotated[float, Field(ge=-50, le=300)]
VibrationMmS = Annotated[float, Field(ge=0, le=500)]
PressureBar = Annotated[float, Field(ge=0, le=1000)]
Rpm = Annotated[float, Field(ge=0, le=60000)]
Amps = Annotated[float, Field(ge=0, le=10000)]
Volts = Annotated[float, Field(ge=0, le=1000)]
PowerKw = Annotated[float, Field(ge=0, le=10000)]
LoadPercent = Annotated[float, Field(ge=0, le=200)]
OperatingHours = Annotated[float, Field(ge=0, le=200000)]


class ORMModel(BaseModel):
    """Base for schemas populated directly from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


def normalise_equipment_id(value: str) -> str:
    """Trim whitespace and upper-case the identifier.

    Equipment IDs are matched case-insensitively everywhere else, so storing
    them canonicalised avoids duplicate rows from "eq-001" vs "EQ-001".
    """
    cleaned = value.strip().upper()
    if not cleaned:
        raise ValueError("equipment_id must not be empty")
    return cleaned


def validate_not_future(value: datetime) -> datetime:
    """Reject timestamps meaningfully in the future.

    A small tolerance absorbs clock skew between the sensor gateway and the
    API host.

    A timezone-naive timestamp is interpreted as UTC rather than rejected.
    Gateways commonly send local time with no offset, and storage is UTC
    throughout, so that is the only defensible assumption. Comparing a naive
    value against an aware `now()` raises TypeError, which surfaced as a 500 on
    an otherwise perfectly reasonable payload.
    """
    from datetime import UTC, timedelta

    comparable = value.replace(tzinfo=UTC) if value.tzinfo is None else value

    if comparable > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("timestamp must not be in the future")
    return value


def validate_not_before_installation(installation_date: date) -> date:
    """Reject installation dates in the future."""
    if installation_date > date.today():
        raise ValueError("installation_date must not be in the future")
    return installation_date


__all__ = [
    "Amps",
    "Any",
    "ORMModel",
    "OperatingHours",
    "PowerKw",
    "PressureBar",
    "Rpm",
    "TemperatureC",
    "VibrationMmS",
    "Volts",
    "LoadPercent",
    "field_validator",
    "normalise_equipment_id",
    "validate_not_future",
    "validate_not_before_installation",
]
