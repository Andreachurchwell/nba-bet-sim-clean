from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ..core.config import APP_TIMEZONE
from ..services.nba_client import fetch_games_for_dates

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
async def get_results(days: int = Query(7, ge=1, le=30)) -> Dict[str, Any]:
    try:
        dates = iso_days_ago(days)
        raw = await fetch_games_for_dates(dates)   # now returns list[dict]
        finals = [simplify_game(g) for g in raw if (g.get("status") or "").lower() == "final"]
        finals.sort(key=lambda g: g["date"], reverse=True)
        return {"range_days": days, "count": len(finals), "games": finals}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch results: {e}")

