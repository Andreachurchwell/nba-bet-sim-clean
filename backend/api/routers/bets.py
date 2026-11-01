"""Season-scoped Classic placement and settlement; no legacy write handlers."""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict

from ..core import config
from ..services.betting import BettingService, BetError
from ..services.live_games import FeedUnavailable, LiveNBAClient, get_live_client
from ..services.season_store import ArchivedSeasonError, SeasonStore
from ..services.season_reads import get_store, local_read, resolve_season, require_read_only, financial_snapshot

router = APIRouter(prefix="/bets", tags=["bets"], dependencies=[Depends(require_read_only)])


class PlaceBet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    season_id: str
    game_id: int = Field(strict=True, gt=0)
    pick: str = Field(pattern=r"^[A-Z]{2,4}$")
    stake: Decimal = Field(gt=0, le=5000, max_digits=10, decimal_places=2, allow_inf_nan=False)
    idempotency_key: UUID


def perform(season, operation):
    try:
        return local_read(season, operation)
    except ArchivedSeasonError as exc:
        raise HTTPException(403, detail={"season_id": season, "message": str(exc)}) from exc
    except BetError as exc:
        raise HTTPException(exc.status, detail={"season_id": season, "message": str(exc)}) from exc
    except FeedUnavailable as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(503, detail={"season_id": season, "message": str(exc)}, headers=headers) from exc
    except OSError:
        raise HTTPException(503, detail={"season_id": season, "message": "Storage update could not complete. Retry the same confirmation; do not start a replacement pick until the result is known."}) from None


@router.get("/wallet")
def get_wallet(season: str = Depends(resolve_season), store: SeasonStore = Depends(get_store)):
    return perform(season, lambda: {**financial_snapshot(store, season)[1], "season_id": season})


@router.get("/")
def list_bets(season: str = Depends(resolve_season), store: SeasonStore = Depends(get_store)):
    return perform(season, lambda: {"season_id": season, "bets": financial_snapshot(store, season)[0]})


@router.post("/")
def place_bet(b: PlaceBet, season: str = Depends(resolve_season), store: SeasonStore = Depends(get_store),
              provider: LiveNBAClient = Depends(get_live_client)):
    if b.season_id != season:
        raise HTTPException(400, detail={"season_id": season, "message": "The selected season changed. Review your pick again."})
    return perform(season, lambda: BettingService(store, provider).place(season, b.game_id, b.pick, b.stake, str(b.idempotency_key)))


@router.post("/settle")
def settle_bets(season: str = Depends(resolve_season), store: SeasonStore = Depends(get_store),
                provider: LiveNBAClient = Depends(get_live_client), after_game_id: int = Query(0, ge=0)):
    return perform(season, lambda: BettingService(store, provider).settle(season, after_game_id))
