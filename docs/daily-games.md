# Daily schedule and eligibility explanations

NBA Games defaults to today's America/Chicago date. Previous Day, Today, Next
Day and the native date picker navigate one day at a time. Only cards whose
Central calendar date matches the selection are displayed. Upcoming Matchups
and Recent Finals are no longer separate sections in the live view. Archives
continue to use their own saved snapshots and stay permanently read-only.

`/games/live` defaults to zero days back/ahead. Daily browsing reuses a buffered
calendar-week provider response to handle UTC/Central boundaries and adjacent
dates; filtering occurs after timestamp conversion. Explicit multi-day API queries remain
available to existing callers, but the daily UI never displays their other days.

The backend adds `pick_eligible`, `pick_reason`, and a verification timestamp to
live game cards. It checks explicit lifecycle, aware future tipoff, identity,
status consistency and freshness. Display and submission share the scheduled/
future-tipoff/verification-age rules in `services/game_eligibility.py`. The
frontend requires backend approval and rechecks the deadline before showing a
button. Actual submission still performs a new uncached single-game lookup.
No betting amount, payout, transaction, cancellation or settlement rules changed.

Schedule display caches last five minutes; financial reads use a separate
15-second cache. Form-preview freshness is separate from the stricter financial
verification window. A new pick always requires a fresh, uncached backend game
lookup. Time passing can close a game before a display cache expires. Rate limits
or failures cannot authorize placement. See [provider-request-audit.md](provider-request-audit.md).

Cards explain final/live/postponed/canceled/delayed states, unavailable tipoffs,
elapsed tipoffs and unavailable verification. Archive cards explicitly say that
they are permanently read-only. Date navigation clears only an unsent UI draft;
it never changes saved picks or balances.

## Philadelphia finding

At **5:03:54 PM CDT on October 31, 2026**, fresh provider game **21718002** was
Philadelphia 76ers at Charlotte Hornets, with tipoff
`2026-10-31T22:00:00+00:00` (**5:00 PM CDT**). The provider still reported an
explicit scheduled state and had a valid timestamp and team IDs. Its scheduled
tipoff had already passed. Both existing frontend and backend time checks
correctly denied betting. This finding does not assert that the provider had
already updated its live status, or establish what was visible at an earlier
unrecorded review time. The card now explains: "Picks closed — game already
started", meaning picks close at the scheduled tipoff even if status lags.

Tests cover daily navigation, cross-day response isolation, Central midnight and
DST, backend/frontend rule agreement, explicit ineligibility explanations, and
stale provider responses. Tests use isolated data; no production test bets or
settlement were performed. Historical and current financial files are compared
against a checksum baseline captured before this change.
