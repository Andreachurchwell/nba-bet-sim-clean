"""Read-only NBA feed. Raw JSON preserves fields omitted by the old SDK.

No disk cache, ledger, wallet, archive or settlement access exists here.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from math import isfinite
from math import ceil
from email.utils import parsedate_to_datetime
import re
from zoneinfo import ZoneInfo

import requests

from ..core.config import NBA_API_BASE_URL, NBA_API_KEY
from .game_eligibility import display_eligibility

CENTRAL = ZoneInfo("America/Chicago")
STATES = {
    "scheduled": "Scheduled", "in_progress": "In Progress", "final": "Final",
    "postponed": "Postponed", "canceled": "Canceled", "cancelled": "Canceled",
    "delayed": "Delayed", "suspended": "Suspended", "abandoned": "Abandoned",
    "unknown": "Status unavailable",
}


class FeedUnavailable(Exception):
    def __init__(self, message, retry_after=None, code="provider_unavailable"):
        super().__init__(message)
        self.retry_after = retry_after
        self.code = code


def normalize_game(raw: dict) -> dict:
    """Never infer a tipoff from midnight/date-only or scores from status."""
    state = str(raw.get("status_state") or "").lower()
    text = str(raw.get("status") or "").strip()
    if raw.get("postponed"):
        state = "postponed"
    if state not in STATES:
        lowered = text.lower()
        if lowered in STATES:
            state = lowered
        elif lowered.startswith("final"):
            state = "final"
        elif any(word in lowered for word in ("qtr", "halftime", "overtime")):
            state = "in_progress"
        else:
            state = "unknown"
    timestamp = raw.get("datetime")
    local = None
    if isinstance(timestamp, str) and "T" in timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                local = parsed.astimezone(CENTRAL)
        except ValueError:
            pass
    provider_date = str(raw.get("date") or "")[:10]
    home = raw.get("home_team") if isinstance(raw.get("home_team"), dict) else {}
    away = raw.get("visitor_team") if isinstance(raw.get("visitor_team"), dict) else {}
    def score(value):
        # Provider 0-0 on unstarted games is not an actual score.
        if state not in ("final", "in_progress", "suspended"):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0 and value == int(value):
            return int(value)
        return None
    return {
        "id": raw.get("id"), "provider_season": raw.get("season"),
        "provider_date": provider_date, "date": local.date().isoformat() if local else provider_date,
        "datetime_utc": local.astimezone(timezone.utc).isoformat() if local else None,
        "tipoff_local": local.strftime("%b %d Â· %I:%M %p %Z") if local else "Time TBD Â· America/Chicago",
        "timezone": "America/Chicago", "time_verified": local is not None,
        "home": home.get("abbreviation") or "TBD", "away": away.get("abbreviation") or "TBD",
        "home_name": home.get("full_name") or home.get("abbreviation") or "Team TBD",
        "away_name": away.get("full_name") or away.get("abbreviation") or "Team TBD",
        "home_score": score(raw.get("home_team_score")),
        "away_score": score(raw.get("visitor_team_score")),
        "status": STATES[state], "status_state": state, "provider_status": text,
        "eligibility_state": "postponed" if raw.get("postponed") else str(raw.get("status_state") or "unknown").lower(),
        "identity_verified": (
            type(raw.get("id")) is int and raw["id"] > 0 and raw.get("season") == 2026
            and all(type(team.get("id")) is int and team["id"] > 0
                    and re.fullmatch(r"[A-Z]{2,4}", str(team.get("abbreviation", ""))) for team in (home, away))
            and home.get("id") != away.get("id") and home.get("abbreviation") != away.get("abbreviation")
            and raw.get("home_team_id", home.get("id")) == home.get("id")
            and raw.get("visitor_team_id", away.get("id")) == away.get("id")
        ),
        "eligibility_conflict": raw.get("status_state") == "scheduled" and (
            (raw.get("period") or 0) != 0 or bool(raw.get("canceled") or raw.get("cancelled"))
            or any(word in text.lower() for word in ("final", "qtr", "halftime", "overtime"))
        ),
    }


class LiveNBAClient:
    TTL = 300  # Browsing only; financial verification always bypasses this cache.
    STALE_LIMIT = 3600
    ERROR_TTL = 60

    def __init__(self, key=NBA_API_KEY, endpoint=NBA_API_BASE_URL, transport=None):
        self.key = key
        self.endpoint = endpoint
        self.transport = transport or requests.Session()
        self.cache = {}
        self.failures = {}
        self.lock = Lock()
        self.retry_until = 0.0

    def _cooldown_error(self):
        remaining = ceil(self.retry_until - monotonic())
        if remaining > 0:
            return FeedUnavailable(
                f"The NBA provider needs a short break. Please retry in {remaining} seconds. Fresh picks remain paused; your funds are unchanged.",
                retry_after=remaining, code="cooldown")
        return None

    def _retry_delay(self, response, default):
        value = response.headers.get("Retry-After")
        if isinstance(value, str):
            try:
                if value.strip().isdigit():
                    return max(1, int(value.strip()))
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(1, ceil((target - datetime.now(timezone.utc)).total_seconds()))
            except (ValueError, TypeError, OverflowError):
                pass
        return default

    def _request(self, url, **kwargs):
        """Called under self.lock. One provider-wide cooldown, no automatic retries."""
        blocked = self._cooldown_error()
        if blocked:
            raise blocked
        try:
            response = self.transport.get(url, **kwargs)
        except requests.RequestException:
            self.retry_until = monotonic() + 10
            raise FeedUnavailable("Unable to reach the NBA provider. Please retry in 10 seconds; no questionable pick was accepted.", retry_after=10) from None
        if response.status_code == 429 or response.status_code >= 500 or response.status_code in (401, 403):
            default = 60 if response.status_code in (401, 403, 429) else 10
            delay = self._retry_delay(response, default)
            self.retry_until = monotonic() + delay
            reason = "rate limit reached" if response.status_code == 429 else "request failed"
            raise FeedUnavailable(
                f"NBA provider {reason} (HTTP {response.status_code}). Please retry in {delay} seconds. Fresh picks remain paused; your funds are unchanged.",
                retry_after=delay, code="rate_limit" if response.status_code == 429 else "provider_error")
        return response

    def _retrieve(self, season, start, end):
        url = urlsplit(self.endpoint)
        if (url.scheme, url.netloc, url.path) not in {
            ("https", "api.balldontlie.io", "/v1/games"),
            ("https", "api.balldontlie.io", "/nba/v1/games"),
        } or url.query or url.fragment:
            raise FeedUnavailable("The NBA games endpoint is not an approved BALLDONTLIE URL.")
        if not self.key:
            raise FeedUnavailable("NBA API credentials are not configured. Live games are unavailable.")
        params = {"seasons[]": int(season[:4]), "start_date": start.isoformat(),
                  "end_date": end.isoformat(), "per_page": 100}
        result, cursors = [], set()
        deadline = monotonic() + 22
        try:
            for _ in range(4):
                remaining = deadline - monotonic()
                if remaining < 1:
                    raise FeedUnavailable("NBA schedule retrieval timed out. Please try again shortly.")
                response = self._request(self.endpoint, headers={"Authorization": self.key},
                                              params=params.copy(), timeout=(min(3, remaining / 2), min(10, remaining / 2)), allow_redirects=False)
                if response.status_code in (401, 403):
                    raise FeedUnavailable("NBA provider authorization or subscription access was denied.")
                if response.status_code == 429:
                    raise FeedUnavailable("NBA provider rate limit reached. Please try again in a minute.")
                if response.status_code != 200:
                    raise FeedUnavailable("The NBA provider is temporarily unavailable.")
                if not 0 <= int(response.headers.get("Age", "0")) <= 15:
                    raise FeedUnavailable("The NBA provider schedule is stale. Refresh games before making a pick.")
                payload = response.json()
                rows = payload.get("data")
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise FeedUnavailable("The NBA provider returned an unexpected response.")
                # A season mismatch must never appear as genuine current-season data.
                if any(row.get("season") != int(season[:4]) for row in rows):
                    raise FeedUnavailable("NBA provider season verification failed. No games were displayed.")
                result.extend(rows)
                cursor = (payload.get("meta") or {}).get("next_cursor")
                if cursor is None:
                    return list({row["id"]: row for row in result}.values())
                if str(cursor) in cursors:
                    break
                cursors.add(str(cursor))
                params["cursor"] = cursor
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            raise FeedUnavailable("Unable to reach or read the NBA provider. Your bankroll and archive are unaffected.") from None
        raise FeedUnavailable("The NBA provider returned an incomplete paginated schedule. Please narrow the date range.")

    def verify_game(self, game_id: int) -> dict:
        """Uncached, fail-closed verification for financial actions only."""
        if type(game_id) is not int or game_id <= 0:
            raise FeedUnavailable("Invalid NBA game ID.")
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme, parsed.netloc, parsed.path) not in {
            ("https", "api.balldontlie.io", "/v1/games"),
            ("https", "api.balldontlie.io", "/nba/v1/games"),
        } or parsed.query or parsed.fragment or not self.key:
            raise FeedUnavailable("Fresh NBA verification is not configured.")
        try:
            with self.lock:
                response = self._request(self.endpoint + "/" + str(game_id),
                    headers={"Authorization": self.key, "Cache-Control": "no-cache"},
                    timeout=(3, 10), allow_redirects=False)
            if response.status_code != 200:
                raise FeedUnavailable("Fresh NBA verification failed. No wager or result was accepted.")
            if not 0 <= int(response.headers.get("Age", "0")) <= 15:
                raise FeedUnavailable("The provider response is stale. Please try again later.")
            raw = response.json().get("data")
            return self._verified_payload(raw, game_id)
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise FeedUnavailable("Fresh NBA verification is unavailable. Please retry; no questionable bet will be accepted.") from None

    def verify_games(self, game_dates):
        """Fresh paginated date queries for settlement only; never browsing cache.

        Yield each requested ID once so its bets can commit before fetching a
        later page. Neighboring dates cover provider/Central date boundaries.
        """
        if not game_dates:
            return
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme, parsed.netloc, parsed.path) not in {
            ("https", "api.balldontlie.io", "/v1/games"),
            ("https", "api.balldontlie.io", "/nba/v1/games"),
        } or parsed.query or parsed.fragment or not self.key:
            raise FeedUnavailable("Fresh NBA verification is not configured.")
        remaining = set(game_dates)
        for game_id in game_dates:
            if game_id not in remaining:
                continue
            try:
                day = date.fromisoformat(game_dates[game_id])
                params = {"seasons[]": [2026], "per_page": 100,
                          "dates[]": [(day + timedelta(days=n)).isoformat() for n in (-1, 0, 1)]}
                cursors = set()
                while True:
                    with self.lock:
                        response = self._request(self.endpoint, params=params.copy(),
                            headers={"Authorization": self.key, "Cache-Control": "no-cache"},
                            timeout=(3, 10), allow_redirects=False)
                    if response.status_code != 200:
                        raise FeedUnavailable("Fresh NBA result retrieval failed; remaining picks stay pending.")
                    if not 0 <= int(response.headers.get("Age", "0")) <= 15:
                        raise FeedUnavailable("The provider result response is stale; remaining picks stay pending.")
                    payload = response.json()
                    rows = payload.get("data")
                    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                        raise FeedUnavailable("Unexpected NBA results response; no results were guessed.")
                    ids = [row.get("id") for row in rows]
                    if any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
                        raise FeedUnavailable("NBA result identities are invalid or duplicated.")
                    # Validate all relevant results before yielding this page.
                    # Keep their retrieval timestamps: iteration never renews freshness.
                    verified = [(row["id"], self._verified_payload(row, row["id"]))
                                for row in rows if row["id"] in remaining]
                    for identity, game in verified:
                        remaining.remove(identity)
                        yield identity, game
                    if not remaining:
                        return
                    cursor = (payload.get("meta") or {}).get("next_cursor")
                    if cursor is None:
                        break
                    if type(cursor) is not int or cursor <= 0 or cursor in cursors:
                        raise FeedUnavailable("NBA results pagination is incomplete; remaining picks stay pending.")
                    cursors.add(cursor)
                    params["cursor"] = cursor
                if game_id in remaining:
                    # A rescheduled game can move outside its stored date;
                    # list filters may also omit a season type. Verify that
                    # exact ID freshly rather than guessing or dropping it.
                    game = self.verify_game(game_id)
                    remaining.remove(game_id)
                    yield game_id, game
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                raise FeedUnavailable("Fresh NBA results could not be verified; remaining picks stay pending.") from None

    def _verified_payload(self, raw, game_id):
        if not isinstance(raw, dict) or raw.get("id") != game_id or raw.get("season") != 2026:
            raise FeedUnavailable("NBA game identity or season could not be verified.")
        home, away = raw.get("home_team"), raw.get("visitor_team")
        if not isinstance(home, dict) or not isinstance(away, dict):
            raise FeedUnavailable("NBA teams could not be verified.")
        import re
        for team in (home, away):
            if type(team.get("id")) is not int or team["id"] <= 0 or not re.fullmatch(r"[A-Z]{2,4}", str(team.get("abbreviation", ""))):
                raise FeedUnavailable("NBA team identity could not be verified.")
        if home["id"] == away["id"] or home["abbreviation"] == away["abbreviation"]:
            raise FeedUnavailable("NBA participating teams are inconsistent.")
        if raw.get("home_team_id", home["id"]) != home["id"] or raw.get("visitor_team_id", away["id"]) != away["id"]:
            raise FeedUnavailable("NBA team IDs disagree within the provider response.")
        if raw.get("status_state") == "scheduled" and (
            (raw.get("period") or 0) != 0 or raw.get("canceled") or raw.get("cancelled")
            or any(word in str(raw.get("status") or "").lower() for word in ("final", "qtr", "halftime", "overtime"))
        ):
            raise FeedUnavailable("NBA game status is inconsistent. Picks remain blocked.")
        game = normalize_game(raw)
        # Financial eligibility uses the explicit lifecycle field, not a
        # guessed state from free-form text.
        game["status_state"] = str(raw.get("status_state") or "unknown").lower()
        if raw.get("postponed"):
            game["status_state"] = "postponed"
        game.update(home_team_id=home["id"], away_team_id=away["id"],
                    verified_at=datetime.now(timezone.utc).isoformat())
        return game

    def read(self, season: str, anchor: date, days_back=3, days_ahead=7) -> dict:
        if season != "2026-27":
            raise FeedUnavailable("Live games are available only for the current season.")
        # Buffer one provider calendar day on each side, then group by Central
        # date. This handles UTC midnight and late West Coast games correctly.
        start = anchor - timedelta(days=days_back + 1)
        end = anchor + timedelta(days=days_ahead + 1)
        if days_back == 0 and days_ahead == 0:
            # One buffered calendar-week request serves neighboring daily views.
            # Display filtering below remains strictly the selected Central day.
            monday = anchor - timedelta(days=anchor.weekday())
            start, end = monday - timedelta(days=1), monday + timedelta(days=7)
        key = (season, start.isoformat(), end.isoformat())
        with self.lock:
            now = monotonic()
            hit = self.cache.get(key)
            failure = self.failures.get(key)
            stale, error = False, None
            blocked = self._cooldown_error()
            if blocked:
                if not hit or now - hit[0] > self.STALE_LIMIT:
                    raise blocked
                stale, error = True, str(blocked)
            elif not hit or now - hit[0] >= self.TTL:
                try:
                    failure_ttl = (getattr(failure[1], "retry_after", None) or self.ERROR_TTL) if failure else 0
                    if failure and now - failure[0] < failure_ttl:
                        raise FeedUnavailable(failure[1])
                    raw = self._retrieve(season, start, end)
                    hit = (now, raw, datetime.now(timezone.utc).isoformat())
                    self.cache[key] = hit
                    self.failures.pop(key, None)
                except FeedUnavailable as exc:
                    error = str(exc)
                    if not failure or now - failure[0] >= self.ERROR_TTL:
                        self.failures[key] = (now, exc)
                    if not hit or now - hit[0] > self.STALE_LIMIT:
                        raise FeedUnavailable(error) from None
                    stale = True
            for bucket in (self.cache, self.failures):
                while len(bucket) > 64:
                    del bucket[next(iter(bucket))]
            games = [display_eligibility(normalize_game(row), hit[2], stale) for row in hit[1]]
            lower, upper = anchor - timedelta(days=days_back), anchor + timedelta(days=days_ahead)
            games = [g for g in games if lower.isoformat() <= g["date"] <= upper.isoformat()]
            games.sort(key=lambda g: (g["date"], g["datetime_utc"] or "~", str(g["id"])))
            return deepcopy({
                "season_id": season, "provider_season": 2026, "source": "BALLDONTLIE",
                "verified": True, "stale": stale, "fetched_at_utc": hit[2], "warning": error,
                "timezone": "America/Chicago", "date": anchor.isoformat(), "games": games,
                "today": [g for g in games if g["date"] == anchor.isoformat()],
                "upcoming": [g for g in games if g["date"] > anchor.isoformat() and g["status_state"] != "final"],
                "recent_finals": [g for g in reversed(games) if g["date"] <= anchor.isoformat() and g["status_state"] == "final"],
            })


_client = LiveNBAClient()


def get_live_client():
    return _client
