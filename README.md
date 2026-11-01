<p align="center">
  <img src="assets/she_bets_logo.png" alt="SheBETS logo" width="180" />
</p>

# SheBETS Classic

SheBETS Classic is a local NBA winner-pick simulator with a Streamlit frontend,
FastAPI backend, and CSV/JSON storage. It uses virtual money only: no deposits,
payments, or real-money wagering.

The active season is **2026-27** (BALLDONTLIE season identifier `2026`). The
**2025-26 archive** has separate storage and is permanently read-only through
the application. Each season has a **$5,000 starting virtual bankroll**. Startup
never resets an existing wallet.

## Features

- Daily NBA games, defaulting to today in America/Chicago, with Previous Day,
  Today, Next Day, and a date picker. Tipoff times are labeled CST/CDT.
- Select Game → choose a winner → enter a virtual wager → **Place My Pick**.
  No extra review screen. Success displays the team, wager, and updated bankroll.
  Retries reuse an idempotency key to prevent a second debit.
- Fresh server-side game verification and bankroll validation before accepting
  new picks. Started games and uncertain eligibility cannot accept picks.
- Manual **Refresh Results / Settle Eligible Bets** in My Picks. Each request
  checks up to three distinct pending games; further clicks advance through the queue.
- Home / Dashboard, NBA Games, My Picks, Performance, and Season Archive, with
  season-specific statistics, charts, and CSV downloads.
- Lavender, lilac, and champagne-gold styling with team-abbreviation badges.
  Official NBA team logos are not currently included.

## Virtual-money rules

Picks use fixed even-money returns, not sportsbook odds. The stake is deducted
on placement. A win credits twice the stake, including the original stake;
a loss credits nothing. Wagers must be positive cent amounts, at most $5,000
per pick, and within the available bankroll.

Only verified finals with complete, unequal scores settle wins/losses. Verified
cancellations refund the stake; postponed or incomplete games remain pending.
Settled picks are not paid again. There is no automatic background settlement.

## NBA data and caching

The active feed uses BALLDONTLIE through the backend. Schedule responses are
cached for five minutes using season-specific buffered calendar-week queries,
so adjacent dates can reuse requests. The frontend displays only the selected
Central calendar day and caches schedules separately from financial refreshes.

Provider rate limits trigger a shared backend cooldown honoring `Retry-After`
when supplied. Older schedules may remain visible with a warning. They never
authorize a financial action: new picks and settlement require fresh single-game
verification and fail safely when the provider cannot verify the game.

Caches/cooldowns are process-local and reset on backend restart. Pagination,
new picks, and manual settlement still consume quota. Available fields and access
depend on the API account; missing times and scores are not invented.

## Local setup

Use Python 3.12. Run these PowerShell commands from the repository root:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS/Linux, activate with `source venv/bin/activate` instead.
Create a private `.env` in the repository root:

```dotenv
NBA_API_KEY=replace_with_your_own_key
NBA_API_BASE_URL=https://api.balldontlie.io/v1/games
APP_TIMEZONE=America/Chicago
```

Do not commit `.env`. Run the backend from the repository root so environment
file discovery is consistent:

```powershell
python -m uvicorn backend.api.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, activate the same environment and run:

```powershell
python -m streamlit run streamlit_app.py --server.address 127.0.0.1
```

Open `http://127.0.0.1:8501`. Backend health is at
`http://127.0.0.1:8000/health`; API documentation is at
`http://127.0.0.1:8000/docs`.

### Season storage

Runtime data is private and excluded from Git:

```text
data/seasons/
  2025-26/       # Preserved archive and integrity manifest
  2026-27/       # Active wallet, ledger, metadata, schedules, and results
```

For an existing installation, preserve or restore these folders from a private
backup. Do not reinitialize, replace, or delete existing season data. Startup
neither creates missing seasons nor falls back to legacy shared files.

For a genuinely new installation with no active-season folder, explicitly create
an empty 2026-27 season:

```powershell
python scripts/initialize_season_storage.py initialize-new
```

This creates the $5,000 wallet and refuses to overwrite an existing folder.
It does not supply the historical archive; without a privately restored archive,
the archive view reports missing data. The `archive-legacy` action is an explicit
preservation tool requiring a reviewed checksum baseline, not normal setup.
See [preservation inputs](docs/preservation.md). Installation-specific recovery
and legacy backfill tools remain local and are not distributed.

### Environment variables

| Variable | Used by | Purpose |
| --- | --- | --- |
| `NBA_API_KEY` | Backend | BALLDONTLIE credential for live verification. |
| `NBA_API_BASE_URL` | Backend | Games endpoint; defaults to the URL above. |
| `APP_TIMEZONE` | Backend | Compatibility helpers; defaults to America/Chicago. The current daily feed/UI explicitly use America/Chicago. |
| `SHEBETS_API_URL` | Frontend | Backend address; defaults to `http://127.0.0.1:8000`. |
| `SHEBETS_SEASONS_ROOT` | Backend | Optional alternate season-storage root for isolated installations/previews. |

Set `SHEBETS_API_URL` in the frontend terminal and `SHEBETS_SEASONS_ROOT` in the
backend terminal when needed; Streamlit does not load `.env` itself. Runtime
betting/settlement kill switches are Python constants in
`backend/api/core/config.py`, not environment variables. Archive writes are
blocked independently of those switches.

## Project structure

```text
backend/api/
  main.py                # FastAPI application
  core/config.py         # Provider configuration and runtime switches
  routers/               # Games, picks/wallet, results, and seasons
  services/              # Provider client, eligibility, storage, transactions
streamlit_app.py         # Navigation, daily schedule, and pick workflow
ui_components.py         # Cards, branding, and abbreviation fallbacks
dashboard_views.py       # Performance chart data
assets/                  # Original SheBETS logo and styles
scripts/                 # Explicit initialization and preservation tools
tests/                   # Storage, API, provider, and Streamlit regressions
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

Provider calls are mocked; all wallets, ledgers, and archives are generated in
temporary storage from invented fixtures in `tests/synthetic_fixtures.py`.
The complete suite runs without `.env`, an API key, private backups, or a `data/`
directory. It covers financial calculations, idempotency, settlement, season
isolation, archive integrity, daily navigation, and provider caching/cooldowns.
Windows may skip the symlink test when the account lacks permission to create symlinks.

## Limitations and future work

- Single-user local app without authentication. Keep both servers bound to
  loopback; public hosting needs a separate security/deployment review.
- Only the two configured seasons are supported. Persistent records require
  private file backups; Git does not protect ignored runtime data.
- Provider outages, quotas, and incomplete data can block picks or leave results
  pending. Manual refresh is required.
- Historical charts attribute eventual payouts to placement dates; original
  settlement timestamps were unavailable. Archive exceptions are preserved.
- Follow-up work includes automated CI, dependency review, and team logos only
  if appropriate reuse permission is established.
- Accounts, leaderboards, spreads/totals, and an AI analyst remain proposals for
  a separate SheBETS 2.0 project, not current Classic features.

Keep wallets, ledgers, schedules/results, secrets, and private recovery notes
out of Git. Some implementation reports describe earlier phases and are retained
locally rather than serving as current setup instructions.

Created by Andrea Churchwell.
