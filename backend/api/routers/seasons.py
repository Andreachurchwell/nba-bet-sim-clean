from fastapi import APIRouter, Depends

from ..core.config import DEFAULT_SEASON
from ..core import config
from ..services.season_store import SEASON_STATES, SeasonStore
from ..services.season_reads import get_store, local_read, resolve_season, statistics

router = APIRouter(prefix="/seasons", tags=["seasons"])


@router.get("")
def list_seasons():
    return {
        "season_id": DEFAULT_SEASON, "default_season": DEFAULT_SEASON,
        "read_only": config.MAINTENANCE_READ_ONLY,
        "betting_enabled": config.BETTING_ENABLED and not config.MAINTENANCE_READ_ONLY,
        "settlement_enabled": config.SETTLEMENT_ENABLED and not config.MAINTENANCE_READ_ONLY,
        "seasons": [{"season_id": season, "archived": state == "archived",
                     "label": season + (" — Season Archive" if state == "archived" else " — Current Season")}
                    for season, state in reversed(tuple(SEASON_STATES.items()))],
    }


@router.get("/stats")
def get_statistics(season: str = Depends(resolve_season), store: SeasonStore = Depends(get_store)):
    return local_read(season, lambda: statistics(store, season))
