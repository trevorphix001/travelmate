from app.core.config import get_settings
from app.db import repo
from app.schemas.data import CostRow
from app.schemas.place import GeoPoint

DEFAULT_TIER = 2


async def load_cost_rows(dest: GeoPoint) -> tuple[list[CostRow], int]:
    tier = dest.tier or DEFAULT_TIER
    return await repo.get_cost_rows(tier, get_settings().cost_table_version), tier
