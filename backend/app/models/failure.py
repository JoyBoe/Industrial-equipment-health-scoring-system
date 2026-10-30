"""Closed failure records, the raw material for calibrating the risk model.

Until this table holds real history the risk model stays uncalibrated: the base
hazard is a placeholder, so every probability is a relative index rather than a
failure frequency. `ml.calibration` fits a new base rate from these rows and
refuses to do so when the sample is too small to be meaningful.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class Failure(Base, TimestampMixin):
    """One closed failure record for one machine.

    Deliberately records the *cycle*, not just the failure instant: MTBF is the
    mean of the intervals between failures, so each row needs a start and an
    end. The start is when the machine was commissioned or last returned to
    service; the end is when it failed.
    """

    __tablename__ = "failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_db_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: When the interval began: commissioning, or the previous return to service.
    interval_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: When the machine actually failed.
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: False for failures caused by something other than wear -- an operator
    #: error, a supply surge, an external impact. Those should be excluded from
    #: a wear-out hazard estimate, because the model describes wear.
    is_catalytic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Free-text description retained for auditing. Never read by the model.
    note: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    failure_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def interval_hours(self) -> float | None:
        """Hours of service between the interval start and the failure."""
        if self.interval_start is None or self.failed_at is None:
            return None
        delta = self.failed_at - self.interval_start
        return max(delta.total_seconds() / 3600.0, 0.0)
