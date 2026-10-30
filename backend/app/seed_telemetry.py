"""Generate and persist synthetic telemetry for the seeded fleet.

**The data this writes is synthetic and is for development and testing only.**
No real plant data is available to this project. See
`data/SYNTHETIC_DATA.md` for what is modelled and why.

Usage:
    python -m app.seed_telemetry                 # 48 h for every unit
    python -m app.seed_telemetry --hours 12
    python -m app.seed_telemetry --healthy      # fault-free, for baselining
    python -m app.seed_telemetry --replace      # wipe existing readings first
"""

import argparse
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.database import SessionLocal, init_db
from app.ml.synthetic import generate_healthy_series, generate_series
from app.repositories import sensor_reading as reading_repo
from app.repositories.equipment import get_by_equipment_id

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 48


def _equipment_rows(db):
    """Active equipment, newest identifiers first for stable output."""
    from app.repositories.equipment import list_equipment

    rows, _ = list_equipment(
        db, active_only=False, sort_by="equipment_id", limit=500, offset=0
    )
    return list(rows)


def seed_telemetry(
    db, hours: int = DEFAULT_HOURS, healthy: bool = False, replace: bool = False
) -> dict[str, int]:
    """Write telemetry for every equipment unit.

    Each unit gets a distinct series, derived from its own equipment type, so
    a boiler and a chiller are not scored against each other's envelopes.
    """
    if replace:
        # Clearing first makes repeated runs reproducible instead of appending
        # a second, overlapping history that would corrupt rolling features.
        deleted = db.execute(delete(reading_repo.SensorReading)).rowcount
        db.commit()
        logger.info("Removed %d existing readings", deleted)

    samples = max(hours * 60, 60)
    # Anchor every series to the same wall-clock end so the fleet shares one
    # time axis and cross-machine charts line up.
    end_time = datetime.now(UTC).replace(second=0, microsecond=0)
    start_time = end_time - timedelta(minutes=samples)

    per_unit: dict[str, int] = {}
    units = _equipment_rows(db)

    for index, equipment in enumerate(units):
        # Deterministic per-unit seed: re-running with the same flags must
        # reproduce the same data, otherwise debugging is impossible.
        seed = 1000 + index
        if healthy:
            series = generate_healthy_series(
                equipment_type=equipment.equipment_type,
                samples=samples,
                seed=seed,
                start_time=start_time,
            )
        else:
            series = generate_series(
                equipment_type=equipment.equipment_type,
                samples=samples,
                seed=seed,
                base_load=55.0 + (index * 7.0) % 35.0,
                start_time=start_time,
            )

        for reading in series:
            values = {
                "temperature": reading.temperature,
                "vibration": reading.vibration,
                "pressure": reading.pressure,
                "rpm": reading.rpm,
                "current": reading.current,
                "voltage": reading.voltage,
                "power": reading.power,
                "load": reading.load,
            }
            quality = "complete" if all(v is not None for v in values.values()) else "partial"
            reading_repo.create_reading(
                db,
                equipment,
                values,
                timestamp=reading.timestamp,
                data_quality=quality,
            )
        per_unit[equipment.equipment_id] = len(series)
        logger.info("  %s (%s): %d readings", equipment.equipment_id, equipment.equipment_type, len(series))

    db.commit()
    return per_unit


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic sensor telemetry")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="Hours of history")
    parser.add_argument(
        "--healthy",
        action="store_true",
        help="Generate fault-free data, for fitting detection baselines",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing readings before generating",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    init_db()

    mode = "healthy (fault-free)" if args.healthy else "mixed (with injected faults)"
    logger.info("Generating %d h of %s telemetry per unit", args.hours, mode)

    with SessionLocal() as db:
        if not _equipment_rows(db):
            logger.error("No equipment found. Run `python -m app.seed` first.")
            return
        result = seed_telemetry(
            db, hours=args.hours, healthy=args.healthy, replace=args.replace
        )

    total = sum(result.values())
    logger.info("Done: %d readings across %d units", total, len(result))


if __name__ == "__main__":
    main()
