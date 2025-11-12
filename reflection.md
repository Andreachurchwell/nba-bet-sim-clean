

Live data ingestion (from an external API)

Normalized data model (consistent JSON structure)

Persistent storage layer (CSV)

Frontend client (Streamlit UI)

Separation of concerns (routers, services, core)

That’s basically a full-stack microservice, just simplified and class-appropriate.
This is exactly how you’d feed data into ML models or a betting ledger later.
---------------------------------------------------------------------------------------------------


How app works (flow of data)

Streamlit UI (client)

File: streamlit_app.py

You interact with widgets (date, team, days slider).

UI calls your backend via HTTP:

/games?date=YYYY-MM-DD&team=LAL&status=Scheduled|Final

/results?days=N

@st.cache_data(ttl=60) keeps Streamlit from spamming the backend.

FastAPI Router (thin controller)

Files: backend/api/routers/games.py, backend/api/routers/results.py

Responsibility:

Parse query params from the request.

Call a service function (no business logic here).

Shape a clean JSON response for the UI:

/games → {"date","count","games":[{...}]} with tipoff_local, status, etc.

/results → {"range_days","count","games":[{...}]} with winner,scores.

Service layer (integration with external API)

File: backend/api/services/nba_client.py

Responsibility:

Talk to balldontlie SDK (BalldontlieAPI).

Normalize SDK objects (like PaginatedListResponse, NBAGame) into plain dicts your app understands.

Cache results:

Single date cache (_CACHE_SINGLE)

Multi-date cache (_CACHE_MULTI)

Batch + retry for multi-day queries to avoid 429 rate limits.

Return a Python list of plain dict games back to the router.

Core config (environment + timezone)

File: backend/api/core/config.py

Responsibility:

Load API key, base URL, APP_TIMEZONE.

Routers/services read from here (single source of truth).

Persistence (optional, user-driven)

File: streamlit_app.py (CSV save buttons)

Responsibility:

Convert the response payload to a pandas.DataFrame.

Save to data/:

schedule_YYYY-MM-DD.csv

results_last_Nd.csv

Error handling & resilience

If the external API rate-limits or hiccups:

Service returns cached data when possible.

Router returns 502 only when there’s truly no fallback.

Streamlit shows a friendly message (“No final games found… try 2–3 days”).

Why this is “production-ish”

Separation of concerns: UI ↔ Router ↔ Service ↔ External API

Anti-corruption layer: SDK objects → plain dicts immediately

Resilience: caching, batching, retries, graceful UI messaging

Contracts: stable JSON shapes the frontend relies on

Observability: clear logs in Uvicorn showing each call