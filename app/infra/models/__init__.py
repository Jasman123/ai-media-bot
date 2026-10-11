"""ORM table mappings (persistence only). Import from here so Alembic sees the full metadata.

Rows never leave app.infra: repositories map them to domain entities.
"""

from ..db import Base
from .billing import LedgerEntryRow, ReservationRow
from .catalog import CatalogRow
from .users import UserRow

__all__ = ["Base", "CatalogRow", "LedgerEntryRow", "ReservationRow", "UserRow"]
