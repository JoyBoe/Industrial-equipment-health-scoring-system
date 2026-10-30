"""Alert request/response schemas."""

from datetime import datetime

from pydantic import Field

from app.schemas.common import ORMModel
from app.models.base import AlertSeverity, AlertType


class AlertRead(ORMModel):
    """One alert as returned by the API."""

    alert_id: str
    equipment_id: str
    timestamp: datetime
    alert_type: str
    severity: str
    description: str
    recommended_action: str | None
    observed_value: str | None
    threshold_value: str | None
    resolved: bool
    resolved_at: datetime | None
    acknowledged: bool


class AlertListResponse(ORMModel):
    """A filtered page of alerts."""

    items: list[AlertRead]
    total: int
    limit: int
    offset: int
    counts_by_severity: dict[str, int]
    unresolved_total: int


class ResolveRequest(ORMModel):
    """Body for resolving an alert."""

    note: str | None = Field(
        None, max_length=500, description="Reason for closure, retained on the record"
    )


class EvaluateRequest(ORMModel):
    """Body for triggering alert evaluation."""

    equipment_id: str | None = Field(
        None, min_length=2, max_length=32, description="Limit to one machine"
    )
    window: int = Field(60, ge=20, le=2000, description="Readings to consider")

    model_config = {"from_attributes": True, "str_strip_whitespace": True}


class EvaluationResponse(ORMModel):
    """Result of an evaluation run."""

    equipment_id: str | None = None
    opened: int
    resolved: int
    skipped: int
    opened_alerts: list[AlertRead]


class AlertSummary(ORMModel):
    """Fleet-level alert counts for dashboard badges."""

    unresolved_total: int
    counts_by_severity: dict[str, int]
    critical: int
    warning: int
    info: int


__all__ = [
    "AlertRead",
    "AlertListResponse",
    "AlertSeverity",
    "AlertType",
    "AlertSummary",
    "EvaluateRequest",
    "EvaluationResponse",
    "ResolveRequest",
]
