"""Seed the equipment master table.

Idempotent: existing rows are updated rather than duplicated, so the script is
safe to re-run after changing the fleet definition.

Usage:
    python -m app.seed
    python -m app.seed --reset   # delete inactive units first
"""

import argparse
import logging
from datetime import date

from app.database import SessionLocal, init_db
from app.repositories import equipment as equipment_repo
from app.schemas.equipment import EquipmentCreate

logger = logging.getLogger(__name__)

#: Fleet definition. `rated_power_kw` and baseline characteristics are used
#: later to build per-type operating profiles.
SEED_EQUIPMENT: list[dict] = [
    {
        "equipment_id": "EQ-001",
        "equipment_name": "Hydraulic Pump A",
        "equipment_type": "Hydraulic Pump",
        "location": "Production Line 1",
        "manufacturer": "Bosch Rexroth",
        "model_number": "A10VSO-031",
        "installation_date": date(2021, 3, 15),
        "operating_hours": 12480.0,
        "rated_power_kw": 15.0,
    },
    {
        "equipment_id": "EQ-002",
        "equipment_name": "Industrial Motor B",
        "equipment_type": "Industrial Motor",
        "location": "Production Line 2",
        "manufacturer": "Siemens",
        "model_number": "SIMOTICS 1LE2",
        "installation_date": date(2020, 8, 1),
        "operating_hours": 20150.5,
        "rated_power_kw": 22.0,
    },
    {
        "equipment_id": "EQ-003",
        "equipment_name": "Air Compressor C",
        "equipment_type": "Air Compressor",
        "location": "Utilities Bay",
        "manufacturer": "Atlas Copco",
        "model_number": "GA-75",
        "installation_date": date(2022, 1, 20),
        "operating_hours": 8930.0,
        "rated_power_kw": 75.0,
    },
    {
        "equipment_id": "EQ-004",
        "equipment_name": "CNC Spindle D",
        "equipment_type": "CNC Machine",
        "location": "Machining Cell 3",
        "manufacturer": "DMG Mori",
        "model_number": "DMU-50",
        "installation_date": date(2023, 5, 10),
        "operating_hours": 4560.0,
        "rated_power_kw": 25.0,
    },
    {
        "equipment_id": "EQ-005",
        "equipment_name": "Conveyor Drive E",
        "equipment_type": "Conveyor Drive",
        "location": "Warehouse Aisle 2",
        "manufacturer": "SEW-Eurodrive",
        "model_number": "R47-DRS71M4",
        "installation_date": date(2019, 11, 5),
        "operating_hours": 31220.0,
        "rated_power_kw": 5.5,
    },
    {
        "equipment_id": "EQ-006",
        "equipment_name": "Main Gearbox F",
        "equipment_type": "Gearbox",
        "location": "Production Line 1",
        "manufacturer": "Flender",
        "model_number": "K128-MF13",
        "installation_date": date(2018, 6, 30),
        "operating_hours": 44100.0,
        "rated_power_kw": 30.0,
    },
    {
        "equipment_id": "EQ-007",
        "equipment_name": "Process Boiler G",
        "equipment_type": "Boiler",
        "location": "Utilities Bay",
        "manufacturer": "Bosch",
        "model_number": "Superheated 3000",
        "installation_date": date(2017, 2, 14),
        "operating_hours": 58700.0,
        "rated_power_kw": 180.0,
    },
    {
        "equipment_id": "EQ-008",
        "equipment_name": "Cooling Chiller H",
        "equipment_type": "Chiller",
        "location": "Roof Plant",
        "manufacturer": "Carrier",
        "model_number": "30XA-802",
        "installation_date": date(2023, 9, 1),
        "operating_hours": 3210.0,
        "rated_power_kw": 110.0,
    },
]


def seed(db, reset: bool = False) -> tuple[int, int]:
    """Insert or update every unit in `SEED_EQUIPMENT`.

    Returns (created, updated).
    """
    created = updated = 0

    for row in SEED_EQUIPMENT:
        existing = equipment_repo.get_by_equipment_id(db, row["equipment_id"])
        if existing is None:
            equipment_repo.create_equipment(db, EquipmentCreate(**row))
            created += 1
            continue

        if reset:
            # Reactivating a retired unit is intentional under --reset.
            row = {**row, "is_active": True}
        changed = {
            key: value for key, value in row.items() if getattr(existing, key) != value
        }
        if changed:
            for key, value in changed.items():
                setattr(existing, key, value)
            db.commit()
            updated += 1

    return created, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed equipment master data")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reactivate and overwrite units that were previously retired",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    init_db()

    with SessionLocal() as db:
        created, updated = seed(db, reset=args.reset)

    logger.info("Seed complete: %d created, %d updated", created, updated)


if __name__ == "__main__":
    main()
