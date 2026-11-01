"""Single-user Classic bets: cross-process lock + durable redo journal.

Each financial read/write takes the same lock. A durable journal is the commit
point; interrupted materialization is rolled forward, never guessed or reset.
"""
import csv
import ctypes
import hashlib
import io
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4, UUID
import re

from .live_games import FeedUnavailable
from .game_eligibility import pick_reason
from .season_store import BET_FIELDS, ArchivedSeasonError, SeasonStorageError, SeasonStore, _reject_links

ACTIVE = "2026-27"
MAX_STAKE = Decimal("5000.00")
_threads = {}
_threads_guard = threading.Lock()


class BetError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def money(value):
    if isinstance(value, bool):
        raise BetError("Enter a valid virtual amount.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
            raise BetError("Use a finite amount with no more than two decimal places.")
        return amount
    except (InvalidOperation, ValueError):
        raise BetError("Enter a valid virtual amount.") from None


def digest(content):
    return hashlib.sha256(content).hexdigest()


def atomic_write(path: Path, content: bytes):
    """Same-volume replacement; flush data and request durable rename metadata."""
    _reject_links(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "nt":
            move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
            move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            move.restype = ctypes.c_int
            if not move(str(temporary), str(path), 0x1 | 0x8):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.replace(temporary, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


@contextmanager
def season_lock(store: SeasonStore, season: str):
    store._require_writable(season)
    if season != ACTIVE:
        raise ArchivedSeasonError("Only the active season can use financial transactions")
    root = store.ledger_path(season).parent
    if os.name == "nt":
        drive = ctypes.WinDLL("kernel32").GetDriveTypeW
        drive.argtypes = [ctypes.c_wchar_p]
        if drive(root.anchor) == 4:
            raise BetError("Betting storage must be on a local drive.", 503)
    path = store._path(season, ".financial.lock")
    with _threads_guard:
        thread_lock = _threads.setdefault(str(root.resolve()), threading.Lock())
    if not thread_lock.acquire(timeout=3):
        raise BetError("Your bankroll is busy. Please retry shortly with the same pick confirmation.", 503)
    handle, locked = None, False
    try:
        handle = path.open("a+b")
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + 3
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise BetError("Another request is updating your bankroll. Retry shortly.", 503)
                time.sleep(0.02)
        yield
    finally:
        if handle:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        thread_lock.release()


class BettingService:
    def __init__(self, store, provider=None, now=None):
        self.store, self.provider = store, provider
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _paths(self):
        return {"ledger": self.store.ledger_path(ACTIVE), "wallet": self.store.wallet_path(ACTIVE),
                "journal": self.store._path(ACTIVE, "transaction.json")}

    def _recover(self):
        paths = self._paths()
        if not paths["journal"].exists():
            return
        try:
            txn = json.loads(paths["journal"].read_text(encoding="utf-8"))
            if txn["season_id"] != ACTIVE or txn["version"] != 1 or txn["state"] not in ("pending", "committed"):
                raise ValueError()
            for key in ("ledger", "wallet"):
                item = txn[key]
                after = item["content"].encode("utf-8")
                if digest(after) != item["after"]:
                    raise ValueError()
                current = digest(paths[key].read_bytes())
                if current not in (item["before"], item["after"]):
                    raise ValueError()
                if txn["state"] == "committed" and current != item["after"]:
                    raise ValueError()
            if txn["state"] == "pending":
                for key in ("ledger", "wallet"):
                    atomic_write(paths[key], txn[key]["content"].encode("utf-8"))
                txn["state"] = "committed"
                atomic_write(paths["journal"], json.dumps(txn, allow_nan=False).encode("utf-8"))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BetError("Transaction recovery needs review. Betting is blocked; no balance was guessed or reset.", 503) from None

    def _read(self):
        self._recover()
        rows = self.store.read_ledger(ACTIVE)
        wallet = self.store.read_wallet(ACTIVE)
        balance = money(wallet["balance"])
        expected = money(self.store.metadata(ACTIVE)["starting_bankroll"])
        ids, keys = set(), set()
        for row in rows:
            if not row.get("idempotency_key") or row["bet_id"] in ids or row["idempotency_key"] in keys:
                raise BetError("Current-season ledger integrity needs review.", 503)
            ids.add(row["bet_id"]); keys.add(row["idempotency_key"])
            try:
                UUID(row["idempotency_key"])
                if min(int(row["game_id"]), int(row["home_team_id"]), int(row["away_team_id"])) <= 0 or row["home_team_id"] == row["away_team_id"]:
                    raise ValueError()
                away, separator, home = row["matchup"].partition(" @ ")
                if not separator or home == away or not all(re.fullmatch(r"[A-Z]{2,4}", team) for team in (home, away)) or row["pick"] not in (home, away):
                    raise ValueError()
                for field in ("placed_at", "tipoff_utc", "placed_verified_at"):
                    if datetime.fromisoformat(row[field]).tzinfo is None:
                        raise ValueError()
                if row["status"] != "open":
                    for field in ("settled_at", "settled_verified_at"):
                        if datetime.fromisoformat(row[field]).tzinfo is None:
                            raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise BetError("Current-season bet identity or timestamp integrity needs review.", 503) from None
            stake, payout = money(row["stake"]), money(row["payout"] or 0)
            status = row["status"]
            correct = {"open": Decimal(0), "won": stake * 2, "lost": Decimal(0), "void": stake}
            if stake <= 0 or status not in correct or payout != correct[status]:
                raise BetError("Current-season financial records need review.", 503)
            expected += payout - stake
        if balance != expected or balance < 0:
            raise BetError("Wallet and ledger do not agree. Betting is blocked without resetting either file.", 503)
        return rows, {"balance": float(balance)}

    def snapshot(self, season):
        if self.store.is_archived(season):
            return self.store.read_ledger(season), self.store.read_wallet(season)
        with season_lock(self.store, season):
            return self._read()

    def _commit(self, rows, balance):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=BET_FIELDS)
        writer.writeheader(); writer.writerows(rows)
        contents = {"ledger": stream.getvalue(), "wallet": json.dumps({"balance": float(balance)}, allow_nan=False)}
        paths = self._paths()
        txn = {"version": 1, "season_id": ACTIVE, "state": "pending", "transaction_id": str(uuid4())}
        for key, content in contents.items():
            txn[key] = {"before": digest(paths[key].read_bytes()), "after": digest(content.encode("utf-8")), "content": content}
        # After this durable commit point, retries roll the same transaction
        # forward. They never add the debit or payout a second time.
        atomic_write(paths["journal"], json.dumps(txn, allow_nan=False).encode("utf-8"))
        self._recover()

    def _duplicate(self, rows, game_id, pick, stake, request_key):
        existing = next((row for row in rows if row["idempotency_key"] == request_key), None)
        if existing and (int(existing["game_id"]) != game_id or existing["pick"] != pick or money(existing["stake"]) != stake):
            raise BetError("This confirmation was already used for a different pick. Start a new pick.", 409)
        return existing

    def _fresh(self, game_id):
        game = self.provider.verify_game(game_id)
        try:
            verified = datetime.fromisoformat(game["verified_at"])
            age = self.now() - verified
            if verified.tzinfo is None or not timedelta(0) <= age <= timedelta(seconds=30):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise BetError("Game verification is stale or incomplete. Please refresh.", 503) from None
        if game.get("id") != game_id or game.get("provider_season") != 2026:
            raise BetError("Game identity or season could not be verified.", 503)
        return game

    def _eligible(self, game):
        reason = pick_reason(game, self.now())
        if reason:
            raise BetError(reason)

    def place(self, season, game_id, pick, stake, request_key):
        self.store._require_writable(season)
        if season != ACTIVE:
            raise ArchivedSeasonError("Archive betting is forbidden")
        stake = money(stake)
        if stake <= 0 or stake > MAX_STAKE:
            raise BetError("Choose a virtual wager from $0.01 to $5,000.00.")
        def reply(row, wallet, repeated):
            return {"season_id": season, "bet_id": row["bet_id"], "balance": wallet["balance"],
                    "status": row["status"], "repeated": repeated}
        with season_lock(self.store, season):
            rows, wallet = self._read()
            duplicate = self._duplicate(rows, game_id, pick, stake, request_key)
            if duplicate:
                return reply(duplicate, wallet, True)
        game = self._fresh(game_id)
        if pick not in (game["home"], game["away"]):
            raise BetError("Choose one of the two verified teams in this game.")
        with season_lock(self.store, season):
            rows, wallet = self._read()
            duplicate = self._duplicate(rows, game_id, pick, stake, request_key)
            if duplicate:
                return reply(duplicate, wallet, True)
            self._eligible(game)
            balance = money(wallet["balance"])
            if stake > balance:
                raise BetError("Your available virtual bankroll is too low for this wager.")
            row = dict.fromkeys(BET_FIELDS, "")
            row.update(placed_at=self.now().isoformat(), bet_id="bet-" + str(uuid4()), date=game["date"],
                       game_id=str(game_id), matchup=game["away"] + " @ " + game["home"], pick=pick,
                       stake=str(stake), status="open", payout="", idempotency_key=request_key,
                       home_team_id=str(game["home_team_id"]), away_team_id=str(game["away_team_id"]),
                       home_name=game["home_name"], away_name=game["away_name"], tipoff_utc=game["datetime_utc"],
                       placed_verified_at=game["verified_at"])
            rows.append(row)
            self._commit(rows, balance - stake)
            return reply(row, {"balance": float(balance - stake)}, False)

    def settle(self, season, after_game_id=0):
        self.store._require_writable(season)
        if season != ACTIVE:
            raise ArchivedSeasonError("Archived bets are never settled")
        rows, _ = self.snapshot(season)
        pending_ids = sorted({int(r["game_id"]) for r in rows if r["status"] == "open"})
        ids = ([value for value in pending_ids if value > after_game_id] or pending_ids)[:3]
        verified, warnings = {}, []
        for game_id in ids:
            try:
                verified[game_id] = self._fresh(game_id)
            except (FeedUnavailable, BetError) as exc:
                warnings.append(str(exc))
        changed = 0
        with season_lock(self.store, season):
            rows, wallet = self._read()
            balance = money(wallet["balance"])
            for row in rows:
                if row["status"] != "open":
                    continue
                game = verified.get(int(row["game_id"]))
                if not game:
                    continue
                if (str(game.get("home_team_id")), str(game.get("away_team_id"))) != (row["home_team_id"], row["away_team_id"]) or game["away"] + " @ " + game["home"] != row["matchup"]:
                    warnings.append("A game's participating teams changed; its pick remains pending.")
                    continue
                if self.now() - datetime.fromisoformat(game["verified_at"]) > timedelta(seconds=30):
                    warnings.append("A result verification expired; retry settlement.")
                    continue
                stake = money(row["stake"])
                state = game["status_state"]
                if state in ("canceled", "cancelled"):
                    status, payout, reason = "void", stake, "verified_cancellation_refund"
                elif state == "final":
                    home, away = game["home_score"], game["away_score"]
                    if home is None or away is None or home == away:
                        warnings.append("A final score is incomplete or tied; its pick remains pending.")
                        continue
                    winner = game["home"] if home > away else game["away"]
                    status = "won" if row["pick"] == winner else "lost"
                    payout, reason = (stake * 2 if status == "won" else Decimal(0)), "verified_final"
                else:
                    continue
                row.update(status=status, payout=str(payout), settled_at=self.now().isoformat(),
                           settled_verified_at=game["verified_at"], settlement_reason=reason)
                balance += payout; changed += 1
            if changed:
                self._commit(rows, balance)
            return {"season_id": season, "settled": changed, "balance": float(balance), "warnings": warnings,
                    "pending": sum(r["status"] == "open" for r in rows), "checked_games": len(ids),
                    "next_after_game_id": ids[-1] if ids else 0}
