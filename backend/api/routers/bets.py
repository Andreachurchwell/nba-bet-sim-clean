from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from pathlib import Path
import csv
import json
from ..services.nba_client import fetch_games_for_date, fetch_games_for_dates
from ..core.config import APP_TIMEZONE
from datetime import datetime, timedelta  # <-- add timedelta

router = APIRouter(prefix="/bets", tags=["bets"])

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
LEDGER = DATA_DIR / "ledger.csv"
WALLET = DATA_DIR / "wallet.json"

# ensure ledger file exists with headers
if not LEDGER.exists():
    with LEDGER.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["placed_at","bet_id","date","game_id","matchup","pick","stake","status","payout"])

# ensure wallet exists
if not WALLET.exists():
    WALLET.write_text(json.dumps({"balance": 5000.0}))

def read_wallet() -> Dict[str,Any]:
    return json.loads(WALLET.read_text())

def write_wallet(obj: Dict[str,Any]):
    WALLET.write_text(json.dumps(obj))

def append_ledger(row: List[Any]):
    with LEDGER.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)

def read_ledger() -> List[Dict[str,Any]]:
    rows = []
    with LEDGER.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows

class PlaceBet(BaseModel):
    date: str        # YYYY-MM-DD (game date)
    game_id: int
    matchup: str
    pick: str        # team abbreviation
    stake: float

@router.get("/wallet")
def get_wallet():
    w = read_wallet()
    return w

@router.get("/")  # clearer than ""
def list_bets():
    return {"bets": read_ledger()}

@router.post("/")  # clearer than ""
def place_bet(b: PlaceBet):
    w = read_wallet()
    balance = float(w.get("balance", 0))
    stake = float(b.stake)
    if stake <= 0:
        raise HTTPException(status_code=400, detail="Stake must be > 0")
    if stake > balance:
        raise HTTPException(status_code=400, detail="Insufficient funds")

    # subtract stake immediately
    w["balance"] = balance - stake
    write_wallet(w)

    bet_id = f"bet-{int(datetime.utcnow().timestamp()*1000)}"
    placed_at = datetime.utcnow().isoformat()
    append_ledger([placed_at, bet_id, b.date, b.game_id, b.matchup, b.pick.upper(), stake, "open", ""])

    return {"status":"ok", "balance": w["balance"], "bet_id": bet_id}

@router.post("/settle")
def settle_bets(days: int = 3):
    """
    Settle open bets by looking up finished games in the last `days`.
    Call this after games are FINAL.
    """
    try:
        ledger = read_ledger()            # list[dict] from CSV
        wallet = read_wallet()
        changed = 0

        # dates to check (today back `days-1`)
        today = datetime.now().date()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(days)]

        # fetch games (sync client)
        try:
            raw_games = fetch_games_for_dates(dates) or []
        except Exception:
            raw_games = []

        # map by id for quick lookup
        game_map = {}
        for g in raw_games:
            try:
                game_map[int(g.get("id"))] = g
            except Exception:
                continue

        # update rows in memory
        new_rows = []
        for row in ledger:
            # ensure dict-like row
            if not isinstance(row, dict):
                new_rows.append(row)
                continue

            status_now = row.get("status", "")
            # already settled?
            if status_now and status_now != "open":
                new_rows.append(row)
                continue

            # parse required fields safely
            try:
                gid = int(row.get("game_id", 0))
                pick = (row.get("pick") or "").upper()
                stake = float(row.get("stake", 0))
            except Exception:
                # broken row, skip
                new_rows.append(row)
                continue

            g = game_map.get(gid)
            if not g:
                # no result yet
                new_rows.append(row)
                continue

            # only settle when FINAL
            status_api = (g.get("status") or "").lower()
            if status_api != "final":
                new_rows.append(row)
                continue

            home_score = g.get("home_team_score")
            away_score = g.get("visitor_team_score")
            if home_score is None or away_score is None:
                new_rows.append(row)
                continue

            # compute winner
            try:
                winner = (
                    g.get("home_team", {}).get("abbreviation")
                    if float(home_score) > float(away_score)
                    else g.get("visitor_team", {}).get("abbreviation")
                )
            except Exception:
                new_rows.append(row)
                continue

            if winner == pick:
                payout = stake * 2  # return stake + same amount as winnings
                wallet["balance"] = float(wallet.get("balance", 0)) + payout
                row["status"] = "won"
                row["payout"] = f"{payout}"
            else:
                row["status"] = "lost"
                row["payout"] = "0"

            changed += 1
            new_rows.append(row)

        # rewrite ledger safely (preserve header order)
        with LEDGER.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            header = ["placed_at","bet_id","date","game_id","matchup","pick","stake","status","payout"]
            writer.writerow(header)
            for r in new_rows:
                writer.writerow([
                    r.get("placed_at",""),
                    r.get("bet_id",""),
                    r.get("date",""),
                    r.get("game_id",""),
                    r.get("matchup",""),
                    r.get("pick",""),
                    r.get("stake",""),
                    r.get("status",""),
                    r.get("payout",""),
                ])

        write_wallet(wallet)
        return {"settled": changed, "new_balance": wallet.get("balance", 0)}
    except Exception as e:
        # never blow up the client—report a clean error
        return {"settled": 0, "error": f"{type(e).__name__}: {e}"}

