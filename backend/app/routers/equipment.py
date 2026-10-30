"""Equipment routes.

This module only handles HTTP concerns: dependency injection, query
parameter parsing, and mapping domain errors to status codes.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.equipment import DuplicateEquipmentError, EquipmentNotFoundError
from app.schemas.equipment import (
    EquipmentCreate,
    EquipmentDeleteResponse,
    EquipmentRead,
    EquipmentUpdate,
)
from app.services import equipment_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/equipment", tags=["equipment"])


@router.get("", response_model=list[EquipmentRead], summary="List equipment")
def list_equipment(  # noqa: PLR0913 - mirrors the dashboard's filter surface
    db: Session = Depends(get_db),
    status_filter: str | None = Query(None, alias="status", description="Health status filter"),
    equipment_type: str | None = Query(None, max_length=60),
    location: str | None = Query(None, max_length=120),
    search: str | None = Query(None, max_length=120, description="Match ID, name, or location"),
    active_only: bool = Query(True),
    min_health: float | None = Query(None, ge=0, le=100),
    max_health: float | None = Query(None, ge=0, le=100),
    sort_by: str = Query("equipment_id"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[EquipmentRead]:
    """Return active equipment, newest assessments included.

    Accepts either a `limit` for the dashboard table or a bare call, which
    returns the first page. Use `/api/equipment/filter-options` for the
    distinct filter values.
    """
    page = equipment_service.list_equipment(
        db,
        status=status_filter,
        equipment_type=equipment_type,
        location=location,
        search=search,
        active_only=active_only,
        min_health=min_health,
        max_health=max_health,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    return list(page.items)


@router.get("/filter-options", summary="Distinct filter values")
def filter_options(db: Session = Depends(get_db)) -> dict[str, list[str]]:
    """Equipment types and locations present in the fleet."""
    return equipment_service.list_filter_options(db)


@router.get(
    "/{equipment_id}",
    response_model=EquipmentRead,
    responses={404: {"description": "Equipment not found"}},
    summary="Get equipment detail",
)
def get_equipment(equipment_id: str, db: Session = Depends(get_db)) -> EquipmentRead:
    """Fetch one unit by its external identifier."""
    try:
        return equipment_service.get_equipment(db, equipment_id)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post(
    "",
    response_model=EquipmentRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Equipment ID already exists"}},
    summary="Register equipment",
)
def create_equipment(
    payload: EquipmentCreate, db: Session = Depends(get_db)
) -> EquipmentRead:
    """Create a new unit.

    IDs are canonicalised to upper case, so `eq-001` and `EQ-001` cannot both
    be registered.
    """
    try:
        return equipment_service.create_equipment(db, payload)
    except DuplicateEquipmentError as exc:
        logger.info("Duplicate equipment rejected: %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.patch(
    "/{equipment_id}",
    response_model=EquipmentRead,
    responses={404: {"description": "Equipment not found"}},
    summary="Update equipment",
)
def update_equipment(
    equipment_id: str, payload: EquipmentUpdate, db: Session = Depends(get_db)
) -> EquipmentRead:
    """Partially update a unit. Omitted fields are left unchanged."""
    try:
        return equipment_service.update_equipment(db, equipment_id, payload)
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc


@router.delete(
    "/{equipment_id}",
    response_model=EquipmentDeleteResponse,
    responses={404: {"description": "Equipment not found"}},
    summary="Retire or delete equipment",
)
def delete_equipment(
    equipment_id: str,
    hard: bool = Query(False, description="Permanently delete, including history"),
    db: Session = Depends(get_db),
) -> EquipmentDeleteResponse:
    """Retire a unit by default; pass `?hard=true` to delete it and its history.

    Deactivation is the default because deleting a unit destroys the telemetry
    and assessment history that makes the audit trail useful.
    """
    try:
        if hard:
            equipment_service.delete_equipment(db, equipment_id)
            return EquipmentDeleteResponse(
                equipment_id=equipment_id.upper(),
                deleted=True,
                message="Equipment and all associated records deleted",
                kind="equipment",
            )
        equipment_service.deactivate_equipment(db, equipment_id)
        return EquipmentDeleteResponse(
            equipment_id=equipment_id.upper(),
            deleted=True,
            message="Equipment deactivated; history retained",
            kind="deactivated",
        )
    except EquipmentNotFoundError as exc:
        raise _not_found(exc) from exc


def _not_found(exc: EquipmentNotFoundError) -> HTTPException:
    """Build a 404 for an unknown equipment identifier."""
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
