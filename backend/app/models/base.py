"""Shared column mixins and enum types for ORM models."""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Adds creation and update timestamps to a model.

    `server_default`/`onupdate` are used rather than Python-side defaults so the
    database is the source of truth even for rows inserted outside the API.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def utcnow() -> datetime:
    """Timezone-aware current time.

    Used where a value must be known in Python (e.g. when computing a
    timestamp for a row not yet flushed).
    """
    return datetime.now(UTC)


class HealthStatus(str, enum.Enum):
    """Health classification bands defined by the scoring engine."""

    EXCELLENT = "Excellent"
    HEALTHY = "Healthy"
    WARNING = "Warning"
    POOR = "Poor"
    CRITICAL = "Critical"


class RiskLevel(str, enum.Enum):
    """Failure risk bands."""

    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"
    SEVERE = "Severe"


class AlertSeverity(str, enum.Enum):
    """Alert urgency."""

    INFO = "Info"
    WARNING = "Warning"
    CRITICAL = "Critical"


class AlertType(str, enum.Enum):
    """Conditions that can raise an alert."""

    LOW_HEALTH_SCORE = "low_health_score"
    HIGH_TEMPERATURE = "high_temperature"
    EXCESSIVE_VIBRATION = "excessive_vibration"
    ANOMALY_DETECTED = "anomaly_detected"
    HIGH_FAILURE_RISK = "high_failure_risk"
    PRESSURE_INSTABILITY = "pressure_instability"
