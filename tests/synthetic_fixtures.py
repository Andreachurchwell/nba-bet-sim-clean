"""Invented fixtures only. Never reads an installation's runtime data or secrets."""
import csv
import hashlib
import json
from pathlib import Path

from backend.api.services.season_store import LEDGER_FIELDS, SeasonStore
from scripts.initialize_season_storage import archive_legacy


def baseline_for(root):
    """Test-only baseline generation; production baselines must be reviewed separately."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(root).iterdir() if p.is_file()}


def synthetic_legacy(root):
    """Six rows: two wins, three losses (one duplicate pair), one pending pick.

    $100 realized profit minus $25 pending stake = $5,075 available.
    Settled stakes total $220, so ROI is 100 / 220 * 100.
    """
    root = Path(root)
    root.mkdir(parents=True)
    specs = [
        ("synthetic-win-a", "2025-11-04", "100", "won", "200"),
        ("synthetic-loss-a", "2025-11-05", "40", "lost", "0"),
        ("synthetic-win-b", "2025-11-06", "60", "won", "120"),
        ("synthetic-duplicate", "2025-11-07", "10", "lost", "0"),
        ("synthetic-duplicate", "2025-11-07", "10", "lost", "0"),
        ("synthetic-open", "2025-11-08", "25", "open", "0"),
    ]
    with (root / "ledger.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        for index, (bet_id, day, stake, status, payout) in enumerate(specs):
            writer.writerow(dict(placed_at=day + "T12:00:00+00:00", bet_id=bet_id,
                date=day, game_id=900001 + min(index, 3) if index < 5 else 900005,
                matchup="BOS @ LAL", pick="LAL", stake=stake, status=status, payout=payout))
    (root / "wallet.json").write_text(json.dumps({"balance": 5075.0}), encoding="utf-8")
    header = "date,matchup,away,home,away_score,home_score,winner,status\n"
    (root / "results_synthetic.csv").write_text(header +
        "2025-11-04,BOS @ LAL,BOS,LAL,100,110,LAL,Final\n" +
        "2025-11-08,BOS @ LAL,BOS,LAL,,,,Postponed\n", encoding="utf-8")
    (root / "schedule_synthetic.csv").write_text(header +
        "2025-11-08,BOS @ LAL,BOS,LAL,,,,Postponed\n", encoding="utf-8")
    return root


def isolated_seasons(root):
    """Build an invented immutable archive and empty active season in test storage."""
    root = Path(root)
    legacy = synthetic_legacy(root.parent / (root.name + "_synthetic_legacy"))
    archive_legacy(legacy, root, baseline_for(legacy))
    SeasonStore(root).initialize("2026-27")
    return root
