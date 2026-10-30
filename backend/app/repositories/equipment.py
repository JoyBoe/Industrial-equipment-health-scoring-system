"""Equipment data access.

All SQLAlchemy queries for equipment live here. Routers and services must not
build queries themselves, which keeps the query surface auditable and makes the
services straightforward to unit test.
"""

from collections.abc import Sequence

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.equipment import Equipment
from app.schemas.equipment import EquipmentCreate, EquipmentUpdate


class EquipmentNotFoundError(LookupError):
    """Raised when no equipment matches the supplied identifier."""


class DuplicateEquipmentError(ValueError):
    """Raised when an equipment_id is already registered."""


def get_by_equipment_id(db: Session, equipment_id: str) -> Equipment | None:
    """Fetch one unit by its external identifier, case-insensitively."""
    return db.scalar(
        select(Equipment).where(func.upper(Equipment.equipment_id) == equipment_id.strip().upper())
    )


def get_or_404(db: Session, equipment_id: str) -> Equipment:
    """Fetch one unit or raise, for routes that must return 404."""
    equipment = get_by_equipment_id(db, equipment_id)
    if equipment is None:
        raise EquipmentNotFoundError(f"Equipment '{equipment_id}' not found")
    return equipment


def _apply_filters(
    stmt: Select,
    *,
    status: str | None,
    equipment_type: str | None,
    location: str | None,
    search: str | None,
    active_only: bool,
    min_health: float | None,
    max_health: float | None,
) -> Select:
    """Apply dashboard filters to an equipment query.

    `search` matches name, id, or location so one box covers the likely
    operator intent.
    """
    if active_only:
        stmt = stmt.where(Equipment.is_active.is_(True))
    if status:
        stmt = stmt.where(Equipment.status == status)
    if equipment_type:
        stmt = stmt.where(Equipment.equipment_type == equipment_type)
    if location:
        stmt = stmt.where(Equipment.location == location)
    if min_health is not None:
        stmt = stmt.where(Equipment.health_score >= min_health)
    if max_health is not None:
        stmt = stmt.where(Equipment.health_score <= max_health)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Equipment.equipment_name.ilike(pattern),
                Equipment.equipment_id.ilike(pattern),
                Equipment.location.ilike(pattern),
            )
        )
    return stmt


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
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Equipment], int]:
    """Return a page of equipment plus the unpaginated total for that filter.

    The total is computed from the same filters so the frontend can render
    "showing 20 of 137" correctly.
    """
    filters = {
        "status": status,
        "equipment_type": equipment_type,
        "location": location,
        "search": search,
        "active_only": active_only,
        "min_health": min_health,
        "max_health": max_health,
    }

    base = _apply_filters(select(Equipment), **filters)
    total = db.scalar(
        _apply_filters(select(func.count(Equipment.id)), **filters)
    ) or 0

    sort_columns = {
        "equipment_id": Equipment.equipment_id,
        "equipment_name": Equipment.equipment_name,
        "health_score": Equipment.health_score,
        "last_updated": Equipment.last_updated,
        "operating_hours": Equipment.operating_hours,
    }
    column = sort_columns.get(sort_by, Equipment.equipment_id)

    # Nulls last on descending health sort, so unassessed equipment does not
    # masquerade as the worst performer.
    stmt = base.order_by(column.desc() if sort_order == "desc" else column.asc())
    stmt = stmt.limit(limit).offset(offset)

    return db.scalars(stmt).all(), total


def create_equipment(db: Session, payload: EquipmentCreate) -> Equipment:
    """Insert new equipment, converting a unique-key clash to a domain error."""
    if get_by_equipment_id(db, payload.equipment_id) is not None:
        raise DuplicateEquipmentError(f"Equipment '{payload.equipment_id}' already exists")

    equipment = Equipment(**payload.model_dump())
    db.add(equipment)
    try:
        db.commit()
    except IntegrityError as exc:
        # Guards the race where two requests pass the existence check together.
        db.rollback()
        raise DuplicateEquipmentError(f"Equipment '{payload.equipment_id}' already exists") from exc
    db.refresh(equipment)
    return equipment


def update_equipment(
    db: Session, equipment: Equipment, payload: EquipmentUpdate
) -> Equipment:
    """Apply a partial update, ignoring fields the caller did not supply."""
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return equipment

    for field, value in changes.items():
        setattr(equipment, field, value)

    db.commit()
    db.refresh(equipment)
    return equipment


def delete_equipment(db: Session, equipment: Equipment) -> None:
    """Delete a unit and its dependent rows.

    Readings, scores, anomalies, and alerts cascade at the database level via
    `ondelete="CASCADE"`.
    """
    db.delete(equipment)
    db.commit()


def deactivate_equipment(db: Session, equipment: Equipment) -> Equipment:
    """Retire a unit while preserving its history.

    Preferred over deletion when audit history must be retained, and it is the
    default behaviour of the DELETE route.
    """
    equipment.is_active = False
    db.commit()
    db.refresh(equipment)
    return equipment


def list_equipment_types(db: Session) -> list[str]:
    """Distinct equipment types in use, for populating filter dropdowns."""
    rows = db.scalars(
        select(Equipment.equipment_type)
        .where(Equipment.is_active.is_(True))
        .distinct()
        .order_by(Equipment.equipment_type)
    ).all()
    return list(rows)


def list_locations(db: Session) -> list[str]:
    """Distinct locations in use, for populating filter dropdowns."""
    rows = db.scalars(
        select(Equipment.location)
        .where(Equipment.is_active.is_(True))
        .distinct()
        .order_by(Equipment.location)
    ).all()
    return list(rows)
