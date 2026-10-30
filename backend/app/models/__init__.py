"""ORM models.

Importing this package registers every mapper on `Base.metadata`, which is what
`init_db()` relies on to discover tables.
"""

from app.database import Base
from app.models.alert import Alert
from app.models.anomaly import Anomaly
from app.models.base import (
    AlertSeverity,
    AlertType,
    HealthStatus,
    RiskLevel,
    TimestampMixin,
    utcnow,
)
from app.models.calibration import ModelCalibration
from app.models.equipment import Equipment
from app.models.failure import Failure
from app.models.health_score import HealthScore
from app.models.sensor_reading import SensorReading

__all__ = [
    "Alert",
    "AlertSeverity",
    "AlertType",
    "Anomaly",
    "Base",
    "Equipment",
    "Failure",
    "HealthScore",
    "HealthStatus",
    "ModelCalibration",
    "RiskLevel",
    "SensorReading",
    "TimestampMixin",
    "utcnow",
]
