"""Explainability routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.equipment import EquipmentNotFoundError
from app.schemas.explain import ExplainRequest, ExplanationResponse
from app.services import explain_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["explainability"])


@router.post(
    "/explain",
    response_model=ExplanationResponse,
    summary="Explain a health score for given sensor values",
)
def explain(payload: ExplainRequest, db: Session = Depends(get_db)) -> ExplanationResponse:
    """Explain a set of sensor values without reading history.

    For the full picture including drift and trend, use the equipment endpoint.
    """
    values = payload.channel_values()
    equipment_type = payload.equipment_type

    if payload.equipment_id:
        from app.repositories.equipment import get_by_equipment_id

        equipment = get_by_equipment_id(db, payload.equipment_id)
        if equipment is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Equipment '{payload.equipment_id}' not found",
            )
        equipment_type = equipment.equipment_type

    anomaly_score = payload.anomaly_score or 0.0
    body = explain_service.explain_values(
        values,
        equipment_type,
        equipment_id=payload.equipment_id,
        anomaly_score=anomaly_score,
        anomaly_detected=anomaly_score >= 0.5,
    )
    return ExplanationResponse(**body)


@router.get(
    "/equipment/{equipment_id}/explain",
    response_model=ExplanationResponse,
    responses={404: {"description": "Equipment not found or has no telemetry"}},
    summary="Explain a machine's health score",
)
def explain_equipment(
    equipment_id: str,
    window: int = Query(60, ge=20, le=2000, description="Readings to consider"),
    db: Session = Depends(get_db),
) -> ExplanationResponse:
    """Full explanation for a machine from its stored telemetry.

    Includes drift and trend, which require ordered history and so cannot be
    produced from a single sample.
    """
    try:
        body = explain_service.explain_equipment(db, equipment_id, window=window)
    except EquipmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ExplanationResponse(**body)
