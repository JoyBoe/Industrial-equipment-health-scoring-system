"""Operational alerts raised by the rules engine."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import AlertSeverity, AlertType, TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.equipment import Equipment


class Alert(Base, TimestampMixin):
    """A condition requiring operator attention.

    Deduplication is enforced by the unique index on
    (equipment_db_id, alert_type) for unresolved alerts, so a machine
    oscillating across a threshold raises one alert rather than thousands.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    equipment_db_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True, nullable=False
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
    alert_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), index=True, nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(Text)

    # Snapshot of the value that tripped the rule, so the alert explains itself
    # even after later readings change.
    observed_value: Mapped[str | None] = mapped_column(String(120))
    threshold_value: Mapped[str | None] = mapped_column(String(120))

    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    equipment: Mapped["Equipment"] = relationship(back_populates="alerts")

    __table_args__ = (
        # Partial unique index: only unresolved alerts dedupe, so a resolved
        # alert does not block the same condition from firing again later.
        Index(
            "uq_alert_active_per_type",
            "equipment_db_id",
            "alert_type",
            unique=True,
            sqlite_where=resolved.is_(False),
            postgresql_where=resolved.is_(False),
        ),
        Index("ix_alerts_equipment_resolved", "equipment_db_id", "resolved"),
    )

    @property
    def severity_enum(self) -> AlertSeverity:
        return AlertSeverity(self.severity)

    @property
    def type_enum(self) -> AlertType:
        return AlertType(self.alert_type)

    def __repr__(self) -> str:
        return f"<Alert {self.alert_id} {self.alert_type} {self.severity}>"
