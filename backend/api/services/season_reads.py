"""Local-only season views. No provider requests or storage writes."""

import csv
import json
import os
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from ..core.config import DEFAULT_SEASON
from ..core import config
from .season_store import DEFAULT_ROOT, SEASON_STATES, SeasonStorageError, SeasonStore


def get_store() -> SeasonStore:
    # Explicit override allows isolated test/preview storage; never falls back
    # to legacy files when an override or season file is missing.
    return SeasonStore(Path(os.environ.get("SHEBETS_SEASONS_ROOT", str(DEFAULT_ROOT))))


def require_read_only(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if config.MAINTENANCE_READ_ONLY:
        raise HTTPException(503, detail={
            "season_id": request.query_params.get("season", DEFAULT_SEASON),
            "code": "maintenance_read_only",
            "message": "Bet placement and settlement are paused for maintenance.",
        })
    season = request.query_params.get("season")
    if season not in SEASON_STATES:
        raise HTTPException(400, detail={"season_id": season, "message": "An explicit approved season is required."})
    if season != DEFAULT_SEASON:
        raise HTTPException(403, detail={"season_id": season, "message": "Archived seasons never accept betting or settlement."})
    allowed = config.SETTLEMENT_ENABLED if request.url.path.rstrip("/").endswith("/settle") else config.BETTING_ENABLED
    if not allowed:
        raise HTTPException(503, detail={"season_id": season, "message": "This action is not enabled."})


def resolve_season(season: str = DEFAULT_SEASON, store: SeasonStore = Depends(get_store)) -> str:
    if season not in SEASON_STATES:
        raise HTTPException(400, detail={"season_id": season, "message": "Unknown season ID"})
    return season


def local_read(season: str, operation):
    try:
        return operation()
    except FileNotFoundError as exc:
        raise HTTPException(404, detail={"season_id": season, "message": str(exc)}) from exc
    except (SeasonStorageError, json.JSONDecodeError, csv.Error, KeyError, TypeError, ArithmeticError) as exc:
        raise HTTPException(422, detail={"season_id": season, "message": "Invalid local season data: " + str(exc)}) from exc


def financial_snapshot(store: SeasonStore, season: str):
    from .betting import BettingService
    return BettingService(store).snapshot(season)


def statistics(store: SeasonStore, season: str) -> dict:
    rows, wallet = financial_snapshot(store, season)
    metadata = store.metadata(season)
    won = [r for r in rows if r["status"].lower() == "won"]
    lost = [r for r in rows if r["status"].lower() == "lost"]
    pending = [r for r in rows if r["status"].lower() == "open"]
    settled = won + lost
    stakes = sum((Decimal(r["stake"] or "0") for r in settled), Decimal(0))
    profit = sum((Decimal(r["payout"] or "0") - Decimal(r["stake"] or "0") for r in settled), Decimal(0))
    return {
        "season_id": season,
        "archived": store.is_archived(season),
        "starting_bankroll": metadata["starting_bankroll"],
        "available_bankroll": wallet["balance"],
        "total_bets": len(rows), "wins": len(won), "losses": len(lost),
        "pending_bets": len(pending),
        "pending_stakes": float(sum((Decimal(r["stake"] or "0") for r in pending), Decimal(0))),
        "profit_loss": float(profit),
        "win_percentage": len(won) / len(settled) * 100 if settled else None,
        "roi": float(profit / stakes * 100) if stakes > 0 else None,
    }


def snapshots(store: SeasonStore, season: str, area: str, filename: str | None = None,
              date: str | None = None, team: str | None = None, status: str | None = None) -> dict:
    store.metadata(season)
    resolver = store.results_path if area == "results" else store.schedule_path
    # Resolve a permitted sentinel name to validate directory existence/links.
    prefix = "results_" if area == "results" else "schedule_"
    folder = resolver(season, prefix + "probe.csv").parent
    paths = sorted(folder.glob(prefix + "*.csv"))
    available = [p.name for p in paths]
    if filename:
        selected = resolver(season, filename)
        if not selected.is_file():
            raise FileNotFoundError(f"Saved {area} snapshot unavailable: {filename}")
        paths = [selected]
    games = []
    dates = set()
    for path in paths:
        safe = resolver(season, path.name)
        with safe.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                row_date = (row.get("date") or "")[:10]
                if row_date:
                    dates.add(row_date)
                if date and row_date != date:
                    continue
                if team and team.upper() not in (row.get("home"), row.get("away")):
                    continue
                if status and (row.get("status") or "").lower() != status.lower():
                    continue
                games.append({**row, "source_file": path.name})
    return {
        "season_id": season, "archived": store.is_archived(season),
        "source": "local_snapshots", "snapshots": available,
        "selected_snapshot": filename, "available_dates": sorted(dates),
        "date": date, "count": len(games), "games": games,
        "message": "" if games else (
            "No archived snapshot is available for this selection." if store.is_archived(season)
            else "No current-season snapshots are saved locally. Live NBA data is disabled during Phase 3."
        ),
        "note": "Saved snapshots may overlap or contain incomplete historical rows; source filenames are retained.",
    }
