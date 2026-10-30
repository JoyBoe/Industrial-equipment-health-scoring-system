"""Anomaly detection results."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.equipment import Equipment
    from app.models.sensor_reading import SensorReading


class Anomaly(Base, TimestampMixin):
    """One anomaly assessment, recorded whether or not it was anomalous.

    Persisting the negative case is deliberate: a detector's false-negative
    rate can only be measured against rows where nothing was flagged.
    """

    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_db_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reading_id: Mapped[int | None] = mapped_column(
        ForeignKey("sensor_readings.id", ondelete="SET NULL"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    anomaly_detected: Mapped[bool] = mapped_column(Boolean, index=True, nullable=False)

    # Which parameters drove the score, and how far each strayed from its
    # baseline. This is the bridge between a numeric score and a human reason.
    affected_parameters: Mapped[list[str] | None] = mapped_column(JSON)
    parameter_scores: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    method: Mapped[str] = mapped_column(String(40), default="isolation_forest", nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(40))

    equipment: Mapped["Equipment"] = relationship(back_populates="anomalies")
    reading: Mapped["SensorReading"] = relationship(back_populates="anomalies")

    __table_args__ = (
        # Alerting scans recent anomalies per machine, most severe first.
        Index("ix_anomaly_equipment_detected", "equipment_db_id", "anomaly_detected", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<Anomaly equip={self.equipment_db_id} score={self.anomaly_score:.3f}>"
