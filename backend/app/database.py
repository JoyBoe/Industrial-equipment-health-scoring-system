"""Database engine, session factory, and FastAPI dependency.

The engine and session factory are module-level singletons: creating an engine
per request would exhaust SQLite connections and leak resources.
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# SQLite needs check_same_thread disabled because FastAPI runs sync endpoints
# in a thread pool. A file-backed SQLite also needs this before PRAGMA use.
engine_kwargs: dict = {"echo": settings.debug, "future": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}


def _ensure_sqlite_directory(database_url: str) -> None:
    """Create the parent directory for a file-backed SQLite database."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    path = Path(database_url[len(prefix) :])
    path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_directory(settings.database_url)

engine: Engine = create_engine(settings.database_url, **engine_kwargs)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """Enable foreign keys and WAL journaling on SQLite connections.

    SQLite disables foreign key enforcement by default, which would let
    `sensor_readings` reference a deleted `equipment` row. WAL also lets the
    dashboard read while the sensor pipeline writes.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped database session.

    The session is always closed, including when the request raises, so a
    failed handler cannot leave a connection checked out.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables for any model that has not been imported yet.

    Model modules are imported inside the function to avoid a circular import
    between `database`, `models`, and `app.main`.
    """
    from app import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)
