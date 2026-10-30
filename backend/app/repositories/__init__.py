"""Data access layer.

No HTTP or business logic here — repositories translate between Pydantic
schemas/domain errors and ORM objects.
"""

from app.repositories import equipment

__all__ = ["equipment"]
