"""FastAPI application factory and process entrypoint."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import engine, init_db
from app.routers import router as api_router

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    """Configure root logging once, at startup."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create the schema on startup and dispose the pool on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    init_db()
    logger.info("Database ready at %s", settings.database_url)
    yield
    engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Sensor telemetry analysis, anomaly detection, failure risk "
            "estimation, and explainable equipment health scoring."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        """Return a generic 500 rather than leaking a traceback to clients.

        The full exception is logged server-side for diagnosis.
        """
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """Point clients at the docs and the API root."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    return app


app = create_app()
