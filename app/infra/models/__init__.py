"""ORM table mappings (persistence only). Import from here so Alembic sees the full metadata.

Rows never leave app.infra: repositories map them to domain entities.
"""

from ..db import Base
from .users import UserRow

__all__ = ["Base", "UserRow"]
