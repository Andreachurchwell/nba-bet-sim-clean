# Provider request audit and rate-limit fix

## Findings from code, history, and measured mocked calls

The committed 2025 client (`git show HEAD:backend/api/services/nba_client.py`)
cached schedules for 300 seconds; its Streamlit HTTP cache lasted 60 seconds.
The recent daily-games version cached both layers for only 15 seconds. Script
reruns do not inherently create provider calls, but after 15 seconds an expired
frontend read reached an expired backend entry. Team/wager edits, review,
confirmation and page returns could therefore keep refetching schedules.

Daily cache keys previously used a different overlapping three-day provider
window for each selected date. Adjacent dates missed even though much of the
returned schedule overlapped. Successful placement also cleared the same frontend
cache used for schedules, so the success rerun could trigger another fetch.

The single-game verifier is a separate, necessary provider call for accepting a
bet, and up to three distinct verifications occur on manual settlement. It had
no shared cooldown with schedules. Schedule failures were cached by exact date
range only, so changing dates or trying a bet could immediately hit the provider
again after a 429. Neither path honored Retry-After. These issues reduce the
available request budget without requiring any subscription change.

Home, My Picks, Performance and the archive already avoid live schedule reads
following the keyed-navigation fix. There is no hidden-tab provider request to
remove. `services/nba_client.py` remains used by the explicit historical backfill
script, not by the current app's daily feed or financial handlers.

## Call trace and counts

Counts below are external provider requests, not local FastAPI GETs. They assume
a complete one-page schedule response; pagination can add calls.

| Action | Before this fix | After |
|---|---|---|
| Open Home/My Picks/Performance/archive | 0 | 0 |
| Open NBA Games with cold schedule cache | 1 per uncached date range | 1 per uncached buffered calendar week |
| Make My Pick, edit team/wager, review, checkbox | 0 if still within 15s; otherwise a schedule refetch | 0 while the five-minute schedule cache is valid |
| Browse next/previous date in a warm week | Typically 1 for the new range | 0 |
| Confirm a new valid bet | 1 uncached verification, plus possible schedule refetch on rerun | 1 uncached verification; financial refresh does not evict schedules |
| Repeat an already accepted idempotency key | 0 verifications | 0 verifications |
| Manual settlement | Up to 3 distinct-game verifications | One fresh lookup per unique pending game; stops at the first provider error or cooldown |
| Retry/other date during provider cooldown | Other ranges/verifications could still call | 0 provider requests until retry is permitted |

## One-click settlement follow-up

The original three-game cap limited verification requests per click. Settlement
now checks all unique pending game IDs in one pass, without automatically
retrying or waiting through provider failures. Each game's verified results are
committed immediately under the existing financial lock and journal, so earlier
verifications do not expire while later games are fetched. Payout and refund
rules are unchanged; only open rows can receive a payout.

The first provider error stops the pass. Already committed results stay saved;
the response reports settled/won/lost/refunded counts, remaining open picks,
the recorded balance, successful checks, interruption status, and Retry-After
when available. A cursor resumes at unchecked games first, then wraps to recheck
earlier postponed/unfinished games. Each ID is fetched at most once per pass.
The shared provider cooldown still blocks requests from settlement, schedules,
and placement until retry is allowed. No stale schedule approves settlement.

The Streamlit button sends one settlement request. Its connection timeout stays
bounded, while its response timeout allows a complete multi-game pass; individual
provider lookups retain their existing bounded timeouts. Financial display caches
refresh after the response, without clearing schedule caches. A lost response
can be retried safely because committed rows are no longer open.

A measured comparison used the private pre-fix source and a mocked transport,
not live traffic. Schedule reads at t=0/16/32/48, one verification, and the success
read at t=64 produced **6 calls before versus 2 after**. This is a controlled
example, not a claim about historical account usage. No provider telemetry was
available to attribute the user's individual past 429 responses.

## Implementation and safety

Browsing schedules are cached for five minutes. Daily requests reuse a buffered
Monday-through-Sunday provider window; response filtering and the UI still show
only the selected America/Chicago date. Concurrent reads share the backend lock
and cache. The separate frontend schedule cache survives wallet/history refreshes.

Opening a form can use a verified cached schedule up to five minutes old. This
does NOT authorize a debit. The financial service remains byte-for-byte unchanged:
it requests the single-game endpoint uncached, validates fresh identity/status/
tipoff, and uses the original 30-second financial verification limit. Past-tipoff
checks still use the current clock. No successful verification is cached or
reused to accept another bet. Cached or unverifiable financial status fails closed.

One in-memory provider-wide cooldown is shared by schedule and verification
requests. A 429 honors Retry-After seconds or HTTP-date; absent/invalid headers
fall back to 60 seconds. Server errors honor Retry-After or use a short 10-second
backoff; network failures also back off. No automatic retry loop sleeps or hammers
the provider. Betting failures return a friendly message and Retry-After header;
the existing confirmation/idempotency key is retained for retry.

Reference: [HTTP Retry-After semantics, RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3).
Provider limits are documented in [BALLDONTLIE NBA documentation](https://nba.balldontlie.io/#account-tiers).
The account's subscription was not queried, changed, or assumed. Legitimate fresh
bet/settlement requests still consume quota. Other apps using the same key and
backend restarts/reload can also consume quota; these caches/cooldowns are local
to the running backend process, not a distributed account-wide rate limiter.

## Files changed

- `backend/api/services/live_games.py`: weekly browsing cache and shared request cooldown.
- `backend/api/services/game_eligibility.py`: separate five-minute form-preview age from unchanged default financial freshness.
- `backend/api/routers/games.py`: surface retry delay in unavailable feed responses.
- `backend/api/routers/bets.py`: forward Retry-After on failed fresh verification.
- `streamlit_app.py`: separate schedule cache and accurate freshness captions.
- `tests/test_provider_request_counts.py`: transport call counts, concurrency, retry parsing/cooldowns and freshness separation.
- `tests/test_betting.py`: end-to-end form counts, safe rate-limited placement and settlement.
- `tests/test_live_games.py`, `tests/test_daily_eligibility.py`: cache-policy expectations.
- `tests/test_season_store.py`, `tests/test_season_dashboard.py`: fresh temporary active-season fixtures independent of real user picks.

Financial rules, transaction code, stored records, navigation, and styling were
not changed. Tests initialize a new season only in temporary directories rather
than assuming the user's current saved season is empty. No live provider calls,
real picks, data resets, subscription changes, commits, or pushes were made.
