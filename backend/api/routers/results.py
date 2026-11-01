from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Dict, Any, List
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ..core.config import APP_TIMEZONE
from ..services.season_store import SeasonStore
from ..services.season_reads import get_store, local_read, resolve_season, snapshots

router = APIRouter(prefix="/results", tags=["results"])

def iso_days_ago(n: int) -> List[str]:
    tz = ZoneInfo(APP_TIMEZONE)
    today = datetime.now(tz).date()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, n)]

def simplify_game(g: Dict[str, Any]) -> Dict[str, Any]:
    home = g.get("home_team") or {}
    away = g.get("visitor_team") or {}
    return {
        "id": g.get("id"),
        "date": g.get("date"),
        "home": home.get("abbreviation"),
        "away": away.get("abbreviation"),
        "home_name": home.get("full_name"),
        "away_name": away.get("full_name"),
        "home_score": g.get("home_team_score"),
        "away_score": g.get("visitor_team_score"),
        "status": g.get("status"),
        "winner": (
            home.get("abbreviation")
            if (g.get("home_team_score", 0) > g.get("visitor_team_score", 0))
            else away.get("abbreviation")
        ) if (g.get("home_team_score") is not None and g.get("visitor_team_score") is not None) else None
    }

@router.get("")
def get_results(
    season: str = Depends(resolve_season),
    store: SeasonStore = Depends(get_store),
    snapshot: str | None = None,
    date: str | None = None,
    days: int = Query(7, ge=1, le=30),
) -> Dict[str, Any]:
    # Keep the old days argument compatible, but do not apply today's rolling
    # window to historical snapshots. The UI chooses saved files explicitly.
    return local_read(season, lambda: {
        **snapshots(store, season, "results", snapshot, date),
        "range_days": None,
        "window_note": "Local snapshots only; the legacy days parameter does not filter saved files.",
    })
