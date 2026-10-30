"""Raw sensor telemetry."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.anomaly import Anomaly
    from app.models.equipment import Equipment


class SensorReading(Base, TimestampMixin):
    """One multi-parameter telemetry sample for a piece of equipment.

    Columns are nullable because real telemetry has dropouts. The scoring
    service imputes missing channels from the equipment's recent history rather
    than rejecting the whole sample.
    """

    __tablename__ = "sensor_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_db_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True, nullable=False
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    # Core sensor channels. Units are fixed across equipment types so the ML
    # feature vector stays consistent; per-type acceptability is handled by
    # baseline profiles, not by changing units.
    temperature: Mapped[float | None] = mapped_column(Float)  # deg C
    vibration: Mapped[float | None] = mapped_column(Float)  # mm/s RMS
    pressure: Mapped[float | None] = mapped_column(Float)  # bar
    rpm: Mapped[float | None] = mapped_column(Float)  # revolutions/min
    current: Mapped[float | None] = mapped_column(Float)  # amps
    voltage: Mapped[float | None] = mapped_column(Float)  # volts
    power: Mapped[float | None] = mapped_column(Float)  # kW
    load: Mapped[float | None] = mapped_column(Float)  # percent of rated

    data_quality: Mapped[str] = mapped_column(String(20), default="complete", nullable=False)

    equipment: Mapped["Equipment"] = relationship(back_populates="readings")
    anomalies: Mapped[list["Anomaly"]] = relationship(back_populates="reading")

    __table_args__ = (
        # The dominant query is "latest N readings for one machine".
        Index("ix_readings_equipment_timestamp", "equipment_db_id", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<SensorReading equip={self.equipment_db_id} at={self.timestamp:%Y-%m-%d %H:%M}>"
