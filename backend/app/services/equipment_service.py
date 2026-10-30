"""Equipment business logic.

Framework-agnostic so it can be unit tested without HTTP. Raises domain errors
that the router maps to status codes.
"""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models.equipment import Equipment
from app.repositories import equipment as equipment_repo
from app.repositories.equipment import (
    DuplicateEquipmentError,
    EquipmentNotFoundError,
)
from app.schemas.equipment import (
    EquipmentCreate,
    EquipmentListResponse,
    EquipmentRead,
    EquipmentUpdate,
)

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50


def list_equipment(
    db: Session,
    *,
    status: str | None = None,
    equipment_type: str | None = None,
    location: str | None = None,
    search: str | None = None,
    active_only: bool = True,
    min_health: float | None = None,
    max_health: float | None = None,
    sort_by: str = "equipment_id",
    sort_order: str = "asc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> EquipmentListResponse:
    """Return a validated page of equipment."""
    limit = min(max(limit, 1), MAX_PAGE_SIZE)
    offset = max(offset, 0)

    rows, total = equipment_repo.list_equipment(
        db,
        status=status,
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

    return EquipmentListResponse(
        items=[EquipmentRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_equipment(db: Session, equipment_id: str) -> EquipmentRead:
    """Fetch a single unit or raise `EquipmentNotFoundError`."""
    return EquipmentRead.model_validate(equipment_repo.get_or_404(db, equipment_id))


def create_equipment(db: Session, payload: EquipmentCreate) -> EquipmentRead:
    """Register new equipment, rejecting duplicate identifiers."""
    created = equipment_repo.create_equipment(db, payload)
    return EquipmentRead.model_validate(created)


def update_equipment(
    db: Session, equipment_id: str, payload: EquipmentUpdate
) -> EquipmentRead:
    """Apply a partial update to an existing unit."""
    equipment = equipment_repo.get_or_404(db, equipment_id)
    updated = equipment_repo.update_equipment(db, equipment, payload)
    return EquipmentRead.model_validate(updated)


def deactivate_equipment(db: Session, equipment_id: str) -> EquipmentRead:
    """Retire a unit, keeping its telemetry and assessment history."""
    equipment = equipment_repo.get_or_404(db, equipment_id)
    return EquipmentRead.model_validate(equipment_repo.deactivate_equipment(db, equipment))


def delete_equipment(db: Session, equipment_id: str) -> None:
    """Permanently remove a unit and all dependent records."""
    equipment = equipment_repo.get_or_404(db, equipment_id)
    equipment_repo.delete_equipment(db, equipment)


def list_filter_options(db: Session) -> dict[str, list[str]]:
    """Distinct types and locations, for dashboard filter controls."""
    return {
        "equipment_types": equipment_repo.list_equipment_types(db),
        "locations": equipment_repo.list_locations(db),
    }


def get_equipment_entity(db: Session, equipment_id: str) -> Equipment:
    """Return the ORM object for internal callers such as the scoring engine."""
    return equipment_repo.get_or_404(db, equipment_id)


def existing_equipment_ids(db: Session) -> Sequence[str]:
    """All active equipment identifiers, used to validate bulk ingest."""
    rows = equipment_repo.list_equipment(db, active_only=True, limit=MAX_PAGE_SIZE, offset=0)
    return [row.equipment_id for row in rows[0]]


__all__ = [
    "DuplicateEquipmentError",
    "EquipmentNotFoundError",
    "create_equipment",
    "deactivate_equipment",
    "delete_equipment",
    "existing_equipment_ids",
    "get_equipment",
    "get_equipment_entity",
    "list_equipment",
    "list_filter_options",
    "update_equipment",
]
