"""Historical health assessment results."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import HealthStatus, RiskLevel, TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.equipment import Equipment


class HealthScore(Base, TimestampMixin):
    """An immutable record of one health assessment.

    Rows are append-only so the trend chart always reflects what the system
    actually concluded at the time, including scores later produced by a
    different model version or weight configuration.
    """

    __tablename__ = "health_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_db_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True, nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    health_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)

    anomaly_score: Mapped[float | None] = mapped_column(Float)
    failure_probability: Mapped[float | None] = mapped_column(Float)

    # Per-parameter penalty breakdown that produced the score. Persisted so
    # explainability is reproducible after the fact rather than recomputed
    # from settings that may since have changed.
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    contributing_factors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    explanation: Mapped[str | None] = mapped_column(String(1000))
    recommendation: Mapped[str | None] = mapped_column(String(1000))

    # Which config/model produced this row, for auditability.
    model_version: Mapped[str | None] = mapped_column(String(40))
    source_reading_id: Mapped[int | None] = mapped_column(
        ForeignKey("sensor_readings.id", ondelete="SET NULL")
    )

    equipment: Mapped["Equipment"] = relationship(back_populates="health_scores")

    __table_args__ = (
        # Trend queries fetch one machine's history in time order.
        Index("ix_health_equipment_timestamp", "equipment_db_id", "timestamp"),
    )

    @property
    def status_enum(self) -> HealthStatus:
        return HealthStatus(self.status)

    @property
    def risk_enum(self) -> RiskLevel:
        return RiskLevel(self.risk_level)

    def __repr__(self) -> str:
        return f"<HealthScore equip={self.equipment_db_id} score={self.health_score:.1f}>"
