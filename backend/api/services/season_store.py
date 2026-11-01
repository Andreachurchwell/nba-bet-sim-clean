"""Season-scoped files, connected to read-only HTTP views in Phase 3.

Reads never initialize or repair storage. All writes must use this service's
write methods; returned read paths are not a filesystem permission boundary.
"""

import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType


LEDGER_FIELDS = (
    "placed_at", "bet_id", "date", "game_id", "matchup", "pick", "stake",
    "status", "payout",
)
BET_FIELDS = LEDGER_FIELDS + (
    "idempotency_key", "home_team_id", "away_team_id", "home_name", "away_name",
    "tipoff_utc", "placed_verified_at", "settled_at", "settled_verified_at", "settlement_reason",
)
# Immutable policy, independent of editable season metadata.
SEASON_STATES = MappingProxyType({"2025-26": "archived", "2026-27": "active"})
DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "seasons"


class SeasonStorageError(ValueError):
    """Invalid, missing or inconsistent season storage."""


class ArchivedSeasonError(PermissionError):
    """Archived data cannot be changed through the storage service."""


def _reject_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise SeasonStorageError("Linked storage paths are not supported")


def _new_season_directory(root: Path, season: str) -> Path:
    """Explicit setup only; an existing or partially initialized season is an error."""
    _reject_links(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / season
    _reject_links(target)
    target.mkdir(exist_ok=False)
    return target


class SeasonStore:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root).absolute()
        _reject_links(self.root)

    def _check_id(self, season: str) -> None:
        if not isinstance(season, str) or season not in SEASON_STATES:
            raise SeasonStorageError("Unknown season ID")

    def _path(self, season: str, *parts: str) -> Path:
        self._check_id(season)
        candidate = self.root.joinpath(season, *parts)
        _reject_links(candidate)
        if not candidate.resolve().is_relative_to((self.root / season).resolve()):
            raise SeasonStorageError("Path escapes season storage")
        return candidate

    def metadata(self, season: str) -> dict:
        path = self._path(season, "season.json")
        if not path.is_file():
            raise FileNotFoundError(f"Missing season metadata for {season}; run explicit setup")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            valid = (
                isinstance(value, dict)
                and value.get("season_id") == season
                and value.get("state") == SEASON_STATES[season]
                and value.get("starting_bankroll") == 5000
                and value.get("provider_season") == int(season[:4])
                and value.get("betting_enabled") is False
            )
        except (ValueError, TypeError) as exc:
            raise SeasonStorageError(f"Invalid metadata for {season}") from exc
        if not valid:
            raise SeasonStorageError(f"Inconsistent metadata for {season}")
        return value

    def is_archived(self, season: str) -> bool:
        self._check_id(season)
        return SEASON_STATES[season] == "archived"

    def _require_writable(self, season: str) -> None:
        self._check_id(season)
        if self.is_archived(season):
            raise ArchivedSeasonError(f"Season {season} is read-only")
        self.metadata(season)

    def ledger_path(self, season: str) -> Path:
        self.metadata(season)
        return self._required_file(season, "ledger.csv")

    def wallet_path(self, season: str) -> Path:
        self.metadata(season)
        return self._required_file(season, "wallet.json")

    def _required_file(self, season: str, name: str) -> Path:
        path = self._path(season, name)
        if not path.is_file():
            raise FileNotFoundError(f"Missing {name} for season {season}")
        return path

    def _csv_path(self, season: str, area: str, filename: str) -> Path:
        self.metadata(season)
        # Excludes separators, alternate streams, device names and traversal.
        prefix = "results_" if area == "results" else "schedule_"
        if not isinstance(filename, str) or not re.fullmatch(
            re.escape(prefix) + r"[A-Za-z0-9_-]+\.csv", filename
        ):
            raise SeasonStorageError("Invalid season CSV filename")
        folder = self._path(season, area)
        if not folder.is_dir():
            raise FileNotFoundError(f"Missing {area} directory for season {season}")
        return self._path(season, area, filename)

    def results_path(self, season: str, filename: str) -> Path:
        return self._csv_path(season, "results", filename)

    def schedule_path(self, season: str, filename: str) -> Path:
        return self._csv_path(season, "schedules", filename)

    def read_ledger(self, season: str) -> list[dict]:
        with self.ledger_path(season).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            accepted = [list(LEDGER_FIELDS)]
            if not self.is_archived(season):
                accepted.append(list(BET_FIELDS))
            if reader.fieldnames not in accepted:
                raise SeasonStorageError(f"Invalid ledger headers for {season}")
            return list(reader)

    def read_wallet(self, season: str) -> dict:
        return json.loads(self.wallet_path(season).read_text(encoding="utf-8"))

    def _replace(self, path: Path, content: bytes) -> None:
        """Replace a single file atomically. Multi-file betting transactions come later."""
        _reject_links(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _reject_links(path)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def write_wallet(self, season: str, value: dict) -> None:
        self._require_writable(season)
        self._replace(self.wallet_path(season), json.dumps(value, allow_nan=False).encode())

    def write_ledger(self, season: str, rows: list[dict]) -> None:
        self._require_writable(season)
        path = self.ledger_path(season)
        content = io.StringIO(newline="")
        writer = csv.DictWriter(content, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        self._replace(path, content.getvalue().encode("utf-8"))

    def write_results(self, season: str, filename: str, content: bytes) -> None:
        self._require_writable(season)
        self._replace(self.results_path(season, filename), content)

    def write_schedule(self, season: str, filename: str, content: bytes) -> None:
        self._require_writable(season)
        self._replace(self.schedule_path(season, filename), content)

    def initialize(self, season: str) -> Path:
        """Explicit creation only. Never reset an existing season or legacy wallet."""
        self._check_id(season)
        if self.is_archived(season):
            raise ArchivedSeasonError("Archives require the explicit archival setup tool")
        target = _new_season_directory(self.root, season)
        # A failure leaves a partial folder for inspection; retries refuse overwrite.
        for area in ("results", "schedules"):
            (target / area).mkdir()
        metadata = season_metadata(season)
        with (target / "season.json").open("x", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)
        with (target / "ledger.csv").open("x", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(LEDGER_FIELDS)
        with (target / "wallet.json").open("x", encoding="utf-8") as handle:
            json.dump({"balance": 5000.0}, handle)
        return target


def season_metadata(season: str) -> dict:
    if season not in SEASON_STATES:
        raise SeasonStorageError("Unknown season ID")
    return {
        "schema_version": 1,
        "season_id": season,
        "provider_season": int(season[:4]),
        "state": SEASON_STATES[season],
        "starting_bankroll": 5000.0,
        "betting_enabled": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
