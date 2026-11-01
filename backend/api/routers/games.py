from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Any, Dict, Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo
from ..core.config import APP_TIMEZONE
from ..services.season_store import SeasonStore
from ..services.season_reads import get_store, local_read, resolve_season, snapshots
from datetime import date as calendar_date
from ..services.live_games import CENTRAL, FeedUnavailable, LiveNBAClient, get_live_client

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/live")
def get_live_games(
    season: str = Depends(resolve_season),
    store: SeasonStore = Depends(get_store),
    client: LiveNBAClient = Depends(get_live_client),
    date: calendar_date | None = None,
    days_back: int = Query(0, ge=0, le=7),
    days_ahead: int = Query(0, ge=0, le=7),
):
    local_read(season, lambda: store.metadata(season))
    if store.is_archived(season):
        return {"season_id": season, "source": "archive", "verified": False,
                "games": [], "today": [], "upcoming": [], "recent_finals": [],
                "message": "Archived seasons use saved snapshots only; no live request was made."}
    try:
        return client.read(season, date or datetime.now(CENTRAL).date(), days_back, days_ahead)
    except FeedUnavailable as exc:
        return {"season_id": season, "source": "BALLDONTLIE", "verified": False,
                "stale": False, "games": [], "today": [], "upcoming": [], "recent_finals": [],
                "timezone": "America/Chicago", "message": str(exc), "retry_after": exc.retry_after}

def today_local() -> str:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).strftime("%Y-%m-%d")

def to_local_tip(dt_str: str, tz: str) -> Dict[str, Any]:
    """
    balldontlie often provides midnight (00:00) instead of real tipoff time.
    If time == 00:00, show date with 'Time TBD'.
    """
    if not dt_str:
        return {"text": "", "tbd": True}
    try:
        dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        local = dt_utc.astimezone(ZoneInfo(tz))
        if local.hour == 0 and local.minute == 0:
            return {"text": local.strftime("%Y-%m-%d"), "tbd": True}
        return {"text": local.strftime("%Y-%m-%d %I:%M %p"), "tbd": False}
    except Exception:
        return {"text": dt_str, "tbd": True}

def normalize_status(g: Dict[str, Any]) -> str:
    raw = (g.get("status") or "").strip()
    if raw.lower() == "final":
        return "Final"
    period = g.get("period") or 0
    try:
        period = int(period)
    except Exception:
        period = 0
    return "In Progress" if (period and raw.lower() != "final") else "Scheduled"

def simplify(g: Dict[str, Any], tz: str) -> Dict[str, Any]:
    home = g.get("home_team", {})
    away = g.get("visitor_team", {})
    tip = to_local_tip(g.get("date", ""), tz)
    return {
        "id": g.get("id"),
        "matchup": f"{away.get('abbreviation','')} @ {home.get('abbreviation','')}",
        "teams": f"{away.get('full_name','')} vs {home.get('full_name','')}",
        "home": home.get("abbreviation"),
        "away": away.get("abbreviation"),
        "home_score": g.get("home_team_score"),
        "away_score": g.get("visitor_team_score"),
        "status": normalize_status(g),
        "tipoff_local": tip["text"] + (" (Time TBD)" if tip["tbd"] else ""),
        "raw_date": g.get("date"),
    }

@router.get("")
def get_games(
    season: str = Depends(resolve_season),
    store: SeasonStore = Depends(get_store),
    date: Optional[str] = Query(None, description="Saved game date, YYYY-MM-DD"),
    team: Optional[str] = None,
    status: Optional[str] = None,
    snapshot: Optional[str] = None,
) -> Dict[str, Any]:
    return local_read(season, lambda: snapshots(store, season, "schedules", snapshot, date, team, status))
