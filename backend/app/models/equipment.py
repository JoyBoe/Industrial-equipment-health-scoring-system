"""Equipment master record."""

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import HealthStatus, RiskLevel, TimestampMixin, utcnow
from app.database import Base

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.anomaly import Anomaly
    from app.models.health_score import HealthScore
    from app.models.sensor_reading import SensorReading


class Equipment(Base, TimestampMixin):
    """A monitored machine.

    `health_score`, `status`, and `risk_level` are denormalised from the most
    recent HealthScore row. They are cached here so the dashboard equipment
    table can be rendered without a correlated subquery per row, and are
    rewritten by the scoring service whenever a new score is produced.
    """

    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Human-facing identifier, e.g. "EQ-001". Unique and stable across renames.
    equipment_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    equipment_name: Mapped[str] = mapped_column(String(120), nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    location: Mapped[str] = mapped_column(String(120), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(120))
    model_number: Mapped[str | None] = mapped_column(String(120))
    installation_date: Mapped[date | None] = mapped_column(Date)

    operating_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rated_power_kw: Mapped[float | None] = mapped_column(Float)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Cached results from the latest health assessment.
    health_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(20), index=True)
    risk_level: Mapped[str | None] = mapped_column(String(20))
    failure_probability: Mapped[float | None] = mapped_column(Float)
    last_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    readings: Mapped[list["SensorReading"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan", passive_deletes=True
    )
    health_scores: Mapped[list["HealthScore"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan", passive_deletes=True
    )
    anomalies: Mapped[list["Anomaly"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan", passive_deletes=True
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # Dashboard filters by active + status, and sorts by health score.
        Index("ix_equipment_active_status", "is_active", "status"),
        Index("ix_equipment_health_score", "health_score"),
    )

    def __repr__(self) -> str:
        return f"<Equipment {self.equipment_id} {self.equipment_name!r}>"
