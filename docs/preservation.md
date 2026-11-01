# Explicit season preservation

Normal application startup does not initialize, migrate, or repair season data.
Keep matched private backups of complete season folders. Never use the commands
below to replace an existing installation's archive or wallet.

`scripts/initialize_season_storage.py initialize-new` creates only a missing
2026–27 season with the configured $5,000 starting bankroll. It refuses existing
directories, including partially initialized ones.

The optional `archive-legacy` action copies a legacy 2025–26 input directory
byte-for-byte into a new archive. Its inputs must be explicit:

```powershell
python scripts/initialize_season_storage.py archive-legacy --legacy-dir <private-input-directory> --baseline <private-baseline.json> --seasons-root <new-destination-root>
```

The baseline is a JSON object mapping each input filename to its lowercase
SHA-256 checksum. It must include exactly `ledger.csv`, `wallet.json`, and all
`results*.csv` and `schedule_*.csv` files in the input directory. For example,
the shape is `{"ledger.csv": "<64 hexadecimal characters>", ...}`; this is a
format illustration, not an executable baseline.

Establish and review that baseline independently from a trusted private backup.
Do not generate a fresh baseline merely to bypass a mismatch. Checksums verify
consistency with the supplied baseline; they do not establish its authenticity.
Keep input data and baselines out of Git.

Before creating the target, the tool checks exact baseline coverage and hashes,
supported legacy statuses, and reconciliation of ledger cash flow against the
$5,000 starting bankroll and recorded wallet. It derives counts and pending
stakes from the inputs; no personal historical identifiers are built in.

The copy preserves duplicate and pending records without settlement or refunds.
It checks destination hashes and produces season metadata, an integrity manifest,
and notes containing the input's exceptions. Those outputs are private runtime
data, not publishable fixtures. Existing archives are never overwritten. If a
copy is interrupted, preserve the partial directory for inspection rather than
deleting it or retrying over it. The tool is not a destructive recovery command
and is never called by the running app.

Tests exercise this path solely with six invented records, including a duplicate
and an open pick. The original installation-specific recovery script and legacy
backfill script are retained locally and ignored by Git; neither is required by
the app or public tests.
