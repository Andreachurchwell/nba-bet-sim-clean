from balldontlie import BalldontlieAPI
from fastapi import HTTPException
from typing import Any, Dict, List, Tuple
from ..core.config import NBA_API_KEY
import time

# caches
_CACHE_SINGLE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_MULTI: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
CACHE_TTL = 300  # 5 minutes

api = BalldontlieAPI(api_key=NBA_API_KEY)

def _obj_to_dict(obj: Any) -> Dict[str, Any]:
    """Turn SDK model objects into plain dicts."""
    if obj is None:
        return {}
    # SDK models usually have .dict()
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    # Fallback to __dict__
    try:
        return dict(vars(obj))
    except Exception:
        return {}

def _as_games(result: Any) -> List[Dict[str, Any]]:
    """Extract a list of game dicts from SDK responses."""
    if result is None:
        return []
    # PaginatedListResponse has .data (list of NBAGame objects)
    if hasattr(result, "data"):
        return [_obj_to_dict(g) for g in (result.data or [])]
    # Raw dict shape (older code paths)
    if isinstance(result, dict):
        raw = result.get("data", []) or []
        return [g if isinstance(g, dict) else _obj_to_dict(g) for g in raw]
    # Last resort: try to treat it like a single game
    return [_obj_to_dict(result)]

def _key_for_dates(dates: List[str]) -> str:
    return ",".join(sorted(dates))

async def fetch_games_for_date(date_str: str) -> List[Dict[str, Any]]:
    now = time.time()
    hit = _CACHE_SINGLE.get(date_str)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        result = api.nba.games.list(dates=[date_str], per_page=100)
        games = _as_games(result)
        _CACHE_SINGLE[date_str] = (now, games)
        return games
    except Exception as e:
        if hit:
            return hit[1]
        raise HTTPException(status_code=502, detail=str(e))

async def fetch_games_for_dates(dates: List[str]) -> List[Dict[str, Any]]:
    """
    Fetch games for multiple dates with small batches to avoid rate limits.
    Uses in-memory cache, retries, and falls back gracefully.
    """
    now = time.time()
    key = _key_for_dates(dates)
    hit = _CACHE_MULTI.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    # --- helper: call with retry -------------------------------------------
    def _list_with_retry(batch: List[str]) -> List[Dict[str, Any]]:
        backoffs = [0.0, 0.4, 0.8]  # 3 tries, gentle backoff
        for wait in backoffs:
            if wait:
                time.sleep(wait)
            try:
                res = api.nba.games.list(dates=batch, per_page=100)
                return _as_games(res)
            except Exception:
                # try again; final exception handled below
                pass
        # last attempt (raise whatever the SDK throws)
        res = api.nba.games.list(dates=batch, per_page=100)
        return _as_games(res)
    # -----------------------------------------------------------------------

    # chunk dates (small batches keep the API happy)
    BATCH = 3
    combined: List[Dict[str, Any]] = []
    try:
        for i in range(0, len(dates), BATCH):
            batch = dates[i:i + BATCH]
            combined.extend(_list_with_retry(batch))
            time.sleep(0.25)  # tiny pause between batches
        _CACHE_MULTI[key] = (time.time(), combined)
        return combined
    except Exception:
        # fallback: day-by-day using the single-date path (already cached)
        combined = []
        for d in dates:
            try:
                combined.extend(await fetch_games_for_date(d))
                time.sleep(0.2)
            except Exception:
                # skip a failing day; continue others
                pass
        _CACHE_MULTI[key] = (time.time(), combined)
        return combined


