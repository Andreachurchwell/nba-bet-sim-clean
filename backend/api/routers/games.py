from fastapi import APIRouter, HTTPException, Query
from typing import Any, Dict, Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo
from ..core.config import APP_TIMEZONE
from ..services.nba_client import fetch_games_for_date

router = APIRouter(prefix="/games", tags=["games"])

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
async def get_games(
    date: Optional[str] = Query(None, description="YYYY-MM-DD in local time"),
    team: Optional[str] = Query(None, description="Filter by team abbreviation, e.g., LAL"),
    status: Optional[str] = Query(None, description="Scheduled | In Progress | Final"),
) -> Dict[str, Any]:
    target = date or today_local()
    try:
        raw_games: List[Dict[str, Any]] = await fetch_games_for_date(target)
        games = [simplify(g, APP_TIMEZONE) for g in raw_games]

        if team:
            t = team.upper()
            games = [g for g in games if g["home"] == t or g["away"] == t]
        if status:
            s = status.lower()
            games = [g for g in games if g["status"].lower() == s]

        # Sort by tipoff text (best-effort)
        def sort_key(g):
            try:
                return datetime.strptime(g["tipoff_local"].replace(" (Time TBD)", ""), "%Y-%m-%d %I:%M %p")
            except Exception:
                try:
                    return datetime.strptime(g["tipoff_local"].replace(" (Time TBD)", ""), "%Y-%m-%d")
                except Exception:
                    return datetime.max
        games.sort(key=sort_key)

        return {"date": target, "count": len(games), "games": games}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed for {target}: {e}")

