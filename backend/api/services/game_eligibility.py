"""Shared scheduled-state, future-tipoff and verification-age rules."""
from datetime import datetime, timezone, timedelta


def pick_reason(game: dict, now: datetime | None = None, max_age_seconds=30) -> str:
    now = now or datetime.now(timezone.utc)
    state = game.get("status_state")
    reasons = {
        "final": "Final — picks closed", "in_progress": "Game in progress",
        "postponed": "Game postponed", "canceled": "Game canceled", "cancelled": "Game canceled",
        "delayed": "Game delayed — picks paused", "suspended": "Game suspended — picks paused",
        "abandoned": "Game abandoned — picks closed",
    }
    if state in reasons:
        return reasons[state]
    if state != "scheduled":
        return "Game status unavailable — picks paused"
    try:
        tip = datetime.fromisoformat(game["datetime_utc"])
        if not game.get("time_verified") or tip.tzinfo is None:
            return "Tipoff time unavailable"
    except (KeyError, ValueError, TypeError):
        return "Tipoff time unavailable"
    if tip <= now:
        return "Picks closed — game already started"
    try:
        verified = datetime.fromisoformat(game["verified_at"])
        if verified.tzinfo is None or not timedelta(0) <= now - verified <= timedelta(seconds=max_age_seconds):
            return "Fresh game verification required — refresh games"
    except (KeyError, ValueError, TypeError):
        return "Fresh game verification required — refresh games"
    return ""


def display_eligibility(game, fetched_at, stale=False, now=None):
    # Use only the explicit provider lifecycle for eligibility; the free-form
    # status fallback remains display-only. Placement verifies again uncached.
    candidate = {**game, "status_state": game.get("eligibility_state", "unknown"), "verified_at": fetched_at}
    # Cached schedules may open a form for five minutes. They never approve a
    # debit: placement still calls verify_game uncached and uses the 30s rule.
    reason = pick_reason(candidate, now, max_age_seconds=300)
    if not reason and not game.get("identity_verified"):
        reason = "Game identity could not be verified — picks paused"
    if not reason and game.get("eligibility_conflict"):
        reason = "Provider status is inconsistent — picks paused"
    if not reason and stale:
        reason = "Fresh game verification required — refresh games"
    return {**game, "pick_eligible": not bool(reason), "pick_reason": reason,
            "eligibility_verified_at": fetched_at}
