"""API route modules."""

from fastapi import APIRouter

from app.routers.alerts import router as alerts_router
from app.routers.anomaly import router as anomaly_router
from app.routers.calibration import router as calibration_router
from app.routers.dashboard import router as dashboard_router
from app.routers.equipment import router as equipment_router
from app.routers.explain import router as explain_router
from app.routers.health import router as system_health_router
from app.routers.health_scores import router as health_scores_router
from app.routers.predictions import router as predictions_router
from app.routers.sensors import router as sensors_router

router = APIRouter()
router.include_router(system_health_router, tags=["system"])
router.include_router(dashboard_router)
router.include_router(equipment_router)
router.include_router(sensors_router)
router.include_router(anomaly_router)
router.include_router(alerts_router)
router.include_router(health_scores_router)
router.include_router(predictions_router)
router.include_router(calibration_router)
router.include_router(explain_router)
