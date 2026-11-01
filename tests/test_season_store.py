import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.api.services.season_store import (
    ArchivedSeasonError, LEDGER_FIELDS, SeasonStorageError, SeasonStore,
    season_metadata,
)
from scripts.initialize_season_storage import archive_legacy
from synthetic_fixtures import isolated_seasons, synthetic_legacy, baseline_for

PROJECT = Path(__file__).resolve().parents[1]


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns,
                                       hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*") if p.is_file()
    }


class SeasonStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "seasons"
        self.store = SeasonStore(self.root)
        self.store.initialize("2026-27")
        archived = self.root / "2025-26"
        archived.mkdir()
        (archived / "season.json").write_text(json.dumps(season_metadata("2025-26")))
        (archived / "ledger.csv").write_text(",".join(LEDGER_FIELDS) + "\n")
        (archived / "wallet.json").write_text('{"balance":5075.0}')
        (archived / "results").mkdir()
        (archived / "schedules").mkdir()

    def test_fresh_season(self):
        self.assertEqual(self.store.read_wallet("2026-27"), {"balance": 5000.0})
        self.assertEqual(self.store.read_ledger("2026-27"), [])
        meta = self.store.metadata("2026-27")
        self.assertEqual(meta["starting_bankroll"], 5000)
        self.assertFalse(meta["betting_enabled"])
        self.assertFalse(list((self.root / "2026-27/results").iterdir()))
        self.assertFalse(list((self.root / "2026-27/schedules").iterdir()))

    def test_reads_do_not_write(self):
        before = snapshot(self.root)
        for season in ("2025-26", "2026-27"):
            self.store.metadata(season)
            self.store.is_archived(season)
            self.store.read_wallet(season)
            self.store.read_ledger(season)
            self.store.results_path(season, "results_last_3d.csv")
            self.store.schedule_path(season, "schedule_2026-10-31.csv")
        self.assertEqual(snapshot(self.root), before)

    def test_archived_writes_all_rejected(self):
        before = snapshot(self.root)
        operations = [
            lambda: self.store.write_wallet("2025-26", {"balance": 0}),
            lambda: self.store.write_ledger("2025-26", []),
            lambda: self.store.write_results("2025-26", "results_test.csv", b"test"),
            lambda: self.store.write_schedule("2025-26", "schedule_test.csv", b"test"),
            lambda: self.store.initialize("2025-26"),
        ]
        for operation in operations:
            with self.assertRaises(ArchivedSeasonError):
                operation()
        self.assertEqual(snapshot(self.root), before)

    def test_metadata_cannot_unlock_archive(self):
        path = self.root / "2025-26/season.json"
        meta = json.loads(path.read_text())
        meta["state"] = "active"
        path.write_text(json.dumps(meta))
        with self.assertRaises(ArchivedSeasonError):
            self.store.write_wallet("2025-26", {"balance": 0})
        with self.assertRaises(SeasonStorageError):
            self.store.metadata("2025-26")

    def test_initialization_never_overwrites(self):
        self.store.write_wallet("2026-27", {"balance": 4995})
        before = snapshot(self.root)
        with self.assertRaises(FileExistsError):
            self.store.initialize("2026-27")
        self.assertEqual(snapshot(self.root), before)

    def test_partial_initialization_is_not_reset(self):
        root = Path(self.temp.name) / "partial"
        (root / "2026-27").mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            SeasonStore(root).initialize("2026-27")
        self.assertEqual(list((root / "2026-27").iterdir()), [])

    def test_invalid_seasons(self):
        before = snapshot(self.root)
        for season in ("../2025-26", "2027-28", "2026-27/../2025-26", "", "C:\\data", None):
            for operation in (self.store.metadata, self.store.initialize,
                              self.store.ledger_path, self.store.is_archived):
                with self.subTest(season=season, operation=operation.__name__):
                    with self.assertRaises(SeasonStorageError):
                        operation(season)
        self.assertEqual(snapshot(self.root), before)

    def test_unsafe_export_paths(self):
        before = snapshot(self.root)
        for name in ("../ledger.csv", "C:\\wallet.csv", "results_..csv",
                     "results_x.csv:secret", "results_x\\evil.csv", "CON.csv"):
            with self.assertRaises(SeasonStorageError):
                self.store.write_results("2026-27", name, b"bad")
        self.assertEqual(snapshot(self.root), before)

    def test_missing_files_fail_without_repairs(self):
        wallet = self.root / "2026-27/wallet.json"
        wallet.unlink()
        before = snapshot(self.root)
        with self.assertRaises(FileNotFoundError):
            self.store.read_wallet("2026-27")
        with self.assertRaises(FileNotFoundError):
            self.store.write_wallet("2026-27", {"balance": 0})
        missing = Path(self.temp.name) / "absent"
        with self.assertRaises(FileNotFoundError):
            SeasonStore(missing).read_ledger("2026-27")
        self.assertFalse(missing.exists())
        self.assertEqual(snapshot(self.root), before)

    def test_active_writes_are_isolated(self):
        historical = snapshot(self.root / "2025-26")
        self.store.write_wallet("2026-27", {"balance": 4995})
        self.store.write_ledger("2026-27", [])
        self.store.write_results("2026-27", "results_test.csv", b"date,status\n")
        self.store.write_schedule("2026-27", "schedule_test.csv", b"date,status\n")
        self.assertEqual(self.store.read_wallet("2026-27")["balance"], 4995)
        self.assertEqual(snapshot(self.root / "2025-26"), historical)

    def test_linked_storage_rejected(self):
        target = Path(self.temp.name) / "elsewhere"
        target.mkdir()
        link = Path(self.temp.name) / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Windows account cannot create symlinks")
        with self.assertRaises(SeasonStorageError):
            SeasonStore(link)

    def test_link_rejection_checks_without_link_creation_privilege(self):
        before = snapshot(self.root)
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(SeasonStorageError):
                self.store.write_wallet("2026-27", {"balance": 0})
        if hasattr(Path, "is_junction"):
            with patch.object(Path, "is_junction", return_value=True):
                with self.assertRaises(SeasonStorageError):
                    self.store.read_wallet("2026-27")
        self.assertEqual(snapshot(self.root), before)


class LegacyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.legacy = self.base / "data"
        synthetic_legacy(self.legacy)
        self.baseline = baseline_for(self.legacy)

    def test_archive_is_exact_and_keeps_exceptions(self):
        before = snapshot(self.legacy)
        target = archive_legacy(self.legacy, self.base / "seasons", self.baseline)
        self.assertEqual(snapshot(self.legacy), before)
        manifest = json.loads((target / "archive_manifest.json").read_text())
        self.assertEqual(manifest["record_count"], 6)
        self.assertEqual(manifest["status_counts"], {"won": 2, "lost": 3, "open": 1})
        self.assertEqual(manifest["pending_stakes"], 25)
        self.assertEqual(manifest["duplicate_bet_ids"], {"synthetic-duplicate": 2})
        self.assertEqual(json.loads((target / "wallet.json").read_text())["balance"], 5075)
        for rel, expected in manifest["files"].items():
            self.assertEqual(hashlib.sha256((target / rel).read_bytes()).hexdigest(), expected["sha256"])
            if "legacy_filename" in expected:
                self.assertEqual((target / rel).read_bytes(), (self.legacy / expected["legacy_filename"]).read_bytes())
        with (target / "ledger.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(sum(r["bet_id"] == "synthetic-duplicate" for r in rows), 2)
        self.assertEqual(sum(r["status"] == "open" for r in rows), 1)
        archived_before = snapshot(target)
        with self.assertRaises(FileExistsError):
            archive_legacy(self.legacy, self.base / "seasons", self.baseline)
        self.assertEqual(snapshot(target), archived_before)

    def test_changed_baseline_refused_before_creation(self):
        (self.legacy / "wallet.json").write_text('{"balance":0}')
        with self.assertRaises(ValueError):
            archive_legacy(self.legacy, self.base / "seasons", self.baseline)
        self.assertFalse((self.base / "seasons").exists())

    def test_unreviewed_snapshot_refused_before_creation(self):
        (self.legacy / "results_unreviewed.csv").write_text("date,status\n")
        with self.assertRaises(ValueError):
            archive_legacy(self.legacy, self.base / "seasons", self.baseline)
        self.assertFalse((self.base / "seasons").exists())

    def test_invalid_or_incomplete_baseline_refused(self):
        for baseline in (None, {}, {**self.baseline, "wallet.json": "not-a-hash"}):
            with self.subTest(baseline=baseline), self.assertRaises(ValueError):
                archive_legacy(self.legacy, self.base / "seasons", baseline)
        self.assertFalse((self.base / "seasons").exists())

    def test_matching_hashes_do_not_bypass_bankroll_reconciliation(self):
        (self.legacy / "wallet.json").write_text('{"balance":9999}')
        with self.assertRaisesRegex(ValueError, "reconcile"):
            archive_legacy(self.legacy, self.base / "seasons", baseline_for(self.legacy))
        self.assertFalse((self.base / "seasons").exists())

    def test_archive_cli_requires_explicit_inputs(self):
        result = subprocess.run([sys.executable, "-B", str(PROJECT / "scripts/initialize_season_storage.py"),
                                 "archive-legacy", "--seasons-root", str(self.base / "seasons")],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --legacy-dir and --baseline", result.stderr)
        self.assertFalse((self.base / "seasons").exists())

    def test_archive_supports_inputs_without_pending_or_duplicate_records(self):
        with (self.legacy / "ledger.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))[:4]
        with (self.legacy / "ledger.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        (self.legacy / "wallet.json").write_text('{"balance":5110}')
        target = archive_legacy(self.legacy, self.base / "seasons", baseline_for(self.legacy))
        manifest = json.loads((target / "archive_manifest.json").read_text())
        self.assertEqual(manifest["record_count"], 4)
        self.assertEqual(manifest["recorded_balance"], 5110)
        self.assertEqual(manifest["pending_stakes"], 0)
        self.assertEqual(manifest["duplicate_bet_ids"], {})
        self.assertEqual(json.loads((target / "historical_notes.json").read_text())["unresolved_bets"], [])

    def test_new_season_cli_needs_no_legacy_data(self):
        target = self.base / "new-installation"
        result = subprocess.run([sys.executable, "-B", str(PROJECT / "scripts/initialize_season_storage.py"),
                                 "initialize-new", "--seasons-root", str(target)],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(SeasonStore(target).read_wallet("2026-27"), {"balance": 5000.0})
        self.assertFalse((target / "2025-26").exists())

    def test_app_import_and_missing_season_reads_never_modify_legacy(self):
        shutil.copytree(PROJECT / "backend/api", self.base / "backend/api",
                        ignore=shutil.ignore_patterns("__pycache__"))
        original = snapshot(self.legacy)
        code = '''
import sys, socket
original_connect = socket.socket.connect
def blocked(*args, **kwargs):
    raise AssertionError("Network access forbidden in Phase 2 tests")
def local_only(sock, address):
    if address[0] not in ("127.0.0.1", "::1"):
        return blocked()
    return original_connect(sock, address)
socket.socket.connect = local_only
socket.create_connection = blocked
from backend.api.main import app
from backend.api.routers import bets
from pathlib import Path
from fastapi.testclient import TestClient
with TestClient(app) as client:
    assert client.get("/seasons").json()["default_season"] == "2026-27"
    assert client.get("/bets/wallet").status_code == 404
    assert client.get("/bets/").status_code == 404
assert "backend.api.services.nba_client" not in sys.modules
assert not (Path.cwd() / "data/seasons").exists()
'''
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=self.base,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Phase 3 reads neither fall back to legacy files nor rewrite wallets.
        for rel, expected in original.items():
            self.assertEqual(hashlib.sha256((self.legacy / rel).read_bytes()).hexdigest(), expected[2])


if __name__ == "__main__":
    unittest.main()
