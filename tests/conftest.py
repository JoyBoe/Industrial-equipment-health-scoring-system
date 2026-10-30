"""Shared pytest fixtures.

Tests must not touch the developer's real database, so every test runs against
a throwaway SQLite file and the engine is rebuilt per test session.
"""

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Point the app at a scratch database before any app module reads settings.
os.environ["APP_DATABASE_URL"] = f"sqlite:///{BACKEND_DIR / 'test_staging.db'}"
os.environ["APP_LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_database() -> None:
    """Create a clean schema for the whole test session."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def client() -> TestClient:
    """HTTP client wired to the test database."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """Direct database session for service and model tests."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
