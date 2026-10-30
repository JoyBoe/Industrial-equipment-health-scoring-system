"""Liveness and readiness endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import SENSOR_FEATURES, get_settings
from app.database import get_db

router = APIRouter()


@router.get("/health", summary="Service and database health")
def read_health(response: Response, db: Session = Depends(get_db)) -> dict:
    """Report service status and confirm the database is reachable.

    A database failure is reported as 503 rather than raised, so a monitoring
    system can distinguish "service down" from "service up, database down".
    """
    settings = get_settings()
    database_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any driver error means unreachable
        database_ok = False

    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if database_ok else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "database": "connected" if database_ok else "unavailable",
        "sensor_features": list(SENSOR_FEATURES),
        "timestamp": datetime.now(UTC).isoformat(),
    }
