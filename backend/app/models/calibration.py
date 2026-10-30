"""The persisted result of calibrating the risk model.

Kept in the database rather than the environment so a deployed instance retains
its calibration across restarts without an operator editing `.env`. A fitted rate
derived from a year of failure history is a genuine measurement, and a
measurement belongs with the data that produced it.
"""

from datetime import datetime

from sqlalchemy import Float, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampMixin, utcnow
from app.database import Base


class ModelCalibration(Base, TimestampMixin):
    """A single row (id=1) holding the fitted base hazard."""

    __tablename__ = "model_calibration"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: lambda_0, failures per hour of service while nominally healthy.
    base_hazard_per_hour: Mapped[float] = mapped_column(Float, nullable=False)

    #: How many closed failures supported the fit. Reported so a caller can
    #: judge how much to trust the number without re-deriving it.
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Mean service interval in hours that produced the rate.
    mean_interval_hours: Mapped[float] = mapped_column(Float, nullable=False)

    #: Spread of that sample, relative to its mean. A high value means the fit
    #: rests on inconsistent evidence even if the count is large.
    coefficient_of_variation: Mapped[float | None] = mapped_column(Float, nullable=True)

    calibrated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
