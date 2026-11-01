"""Explicit season setup with checksum-verified archive inputs; never run at startup."""

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api.services.season_store import (
    DEFAULT_ROOT, SeasonStore, _new_season_directory, _reject_links, season_metadata,
)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_legacy(legacy: Path, seasons_root: Path, expected_hashes: dict[str, str]) -> Path:
    """Copy legacy bytes unchanged after checking an independently reviewed baseline."""
    legacy, seasons_root = Path(legacy).absolute(), Path(seasons_root).absolute()
    _reject_links(legacy)
    originals = [legacy / "ledger.csv", legacy / "wallet.json"]
    originals += sorted(legacy.glob("results*.csv"))
    originals += sorted(legacy.glob("schedule_*.csv"))
    for path in originals:
        _reject_links(path)
        if not path.is_file():
            raise FileNotFoundError(f"Missing legacy file: {path.name}")
    before = {path.name: checksum(path) for path in originals}
    if not isinstance(expected_hashes, dict) or set(expected_hashes) != set(before):
        raise ValueError("Baseline must cover exactly the input ledger, wallet, results and schedules")
    if any(not isinstance(value, str) or len(value) != 64
           or any(c not in "0123456789abcdef" for c in value) for value in expected_hashes.values()):
        raise ValueError("Baseline must contain lowercase SHA-256 checksums")
    if before != expected_hashes:
        raise ValueError("Source differs from the reviewed checksum baseline; no archive created")
    with (legacy / "ledger.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    counts = dict(Counter(row["status"] for row in rows))
    wallet = json.loads((legacy / "wallet.json").read_text())
    net = sum((Decimal(r["payout"] or "0") - Decimal(r["stake"] or "0") for r in rows), Decimal(0))
    balance = Decimal(str(wallet["balance"]))
    if not balance.is_finite() or not net.is_finite() or Decimal("5000") + net != balance:
        raise ValueError("Ledger and wallet do not reconcile with the $5,000 starting bankroll")
    if any(status not in {"won", "lost", "open"} for status in counts):
        raise ValueError("Unsupported legacy status; review inputs before archiving")
    duplicates = Counter(row["bet_id"] for row in rows)
    duplicate_ids = [key for key, count in duplicates.items() if count > 1]
    open_rows = [row for row in rows if row["status"] == "open"]
    pending_stakes = sum((Decimal(row["stake"]) for row in open_rows), Decimal(0))
    target = _new_season_directory(seasons_root, "2025-26")
    (target / "results").mkdir()
    (target / "schedules").mkdir()
    files = {}
    for source in originals:
        area = "results" if source.name.startswith("results") else (
            "schedules" if source.name.startswith("schedule_") else ""
        )
        destination = target / area / source.name
        shutil.copy2(source, destination)
        if checksum(source) != before[source.name] or checksum(destination) != before[source.name]:
            raise RuntimeError("Source changed or archive copy failed; inspect partial archive")
        files[destination.relative_to(target).as_posix()] = {
            "legacy_filename": source.name,
            "size": source.stat().st_size,
            "sha256": before[source.name],
        }
    notes = {
        "unresolved_bets": open_rows,
        "postponement_note": "Open records are preserved without live verification, settlement or refund.",
        "duplicate_bet_ids": {key: duplicates[key] for key in duplicate_ids},
        "duplicate_note": "Duplicate identifiers are preserved without deduplication or balance adjustment.",
        "wallet_history_note": "The original wallet is a snapshot, not a full timestamped transaction history. No settlement timestamps were reconstructed.",
        "amendment_policy": "Future approved corrections must be separate amendments; never overwrite this snapshot.",
    }
    for name, value in [("season.json", season_metadata("2025-26")), ("historical_notes.json", notes)]:
        with (target / name).open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
        files[name] = {"size": (target / name).stat().st_size, "sha256": checksum(target / name)}
    manifest = {
        "schema_version": 1,
        "season_id": "2025-26",
        "starting_bankroll": 5000.0,
        "starting_bankroll_evidence": "Ledger and recorded wallet reconcile against the configured $5,000 starting bankroll.",
        "recorded_balance": float(balance),
        "record_count": len(rows),
        "status_counts": counts,
        "pending_stakes": float(pending_stakes),
        "duplicate_bet_ids": {key: duplicates[key] for key in duplicate_ids},
        "files": files,
        "scope": "Checksums cover original copied data and generated metadata/notes; manifest is excluded from its own checksums. The supplied checksum baseline must be reviewed independently before use.",
    }
    with (target / "archive_manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    if any(checksum(path) != before[path.name] for path in originals):
        raise RuntimeError("Legacy files changed during archive creation")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["archive-legacy", "initialize-new"])
    parser.add_argument("--legacy-dir", type=Path, help="Explicit legacy input directory (archive-legacy only)")
    parser.add_argument("--baseline", type=Path, help="Reviewed JSON mapping of input filenames to SHA-256 checksums")
    parser.add_argument("--seasons-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if args.action == "archive-legacy":
        if args.legacy_dir is None or args.baseline is None:
            parser.error("archive-legacy requires --legacy-dir and --baseline; no input is inferred")
        target = archive_legacy(args.legacy_dir, args.seasons_root, json.loads(args.baseline.read_text(encoding="utf-8")))
    else:
        target = SeasonStore(args.seasons_root).initialize("2026-27")
    print(f"Created {target}. Existing season directories are never overwritten.")


if __name__ == "__main__":
    main()
