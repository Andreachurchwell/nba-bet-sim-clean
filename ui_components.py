"""Escaped presentation markup. No provider calls or data writes."""
import base64
from datetime import date
from functools import lru_cache
from html import escape
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
# No existing team logo has documented reuse permission. Only locally reviewed
# assets may be added; never use untrusted provider/image URLs here.
APPROVED_TEAM_LOGOS = {}


@lru_cache(maxsize=32)
def image_uri(path: str) -> str | None:
    try:
        target = Path(path)
        if target.suffix.lower() != ".png" or not target.resolve().is_relative_to(ASSETS.resolve()):
            return None
        return "data:image/png;base64," + base64.b64encode(target.read_bytes()).decode("ascii")
    except OSError:
        return None


def brand_header() -> str:
    logo = image_uri(str(ASSETS / "she_bets_logo.png"))
    mark = f'<img class="brand-logo" src="{logo}" alt="Original SheBETS logo">' if logo else '<div class="brand-monogram">SB</div>'
    return f'''<header class="masthead"><div class="brand-lockup">{mark}<div>
    <div class="eyebrow">NBA WINNER PICKS</div>
    <h1>SheBETS <span>Classic</span></h1>
    <p>NBA picks. Virtual money only.</p></div></div>
    <div class="header-seal" aria-hidden="true"><span>NBA<br>CLASSIC</span></div></header>'''


def story_card(season: str, archived: bool) -> str:
    title = "Season Archive" if archived else "Season Overview"
    copy = ("Original picks and recorded results. Read-only."
            if archived else "Track your bankroll, picks, and results for this NBA season.")
    return f'''<section class="season-story"><div class="story-content"><span class="season-pill">{escape(season)} · {"THE ARCHIVE" if archived else "NBA SEASON"}</span>
    <h2>{title}</h2><p>{copy}</p><div class="story-foot">VIRTUAL MONEY ONLY</div></div>
    <div class="court-art" aria-hidden="true"><div class="court-ring"></div><div class="court-ball"></div><span>NBA BASKETBALL</span></div></section>'''


def team_badge(abbreviation: str) -> str:
    abbreviation = str(abbreviation or "TBD")
    asset = APPROVED_TEAM_LOGOS.get(abbreviation)
    uri = image_uri(str(ASSETS / asset)) if asset else None
    if uri:
        return f'<span class="team-badge"><img src="{uri}" alt="{escape(abbreviation, quote=True)} logo"></span>'
    return f'<span class="team-badge" aria-label="{escape(abbreviation, quote=True)} team abbreviation">{escape(abbreviation)}</span>'


def matchup_card(game: dict, can_pick=False) -> str:
    try:
        when = date.fromisoformat(str(game.get("date") or "")[:10]).strftime("%a, %b %d")
    except ValueError:
        when = "Date unavailable"
    state = str(game.get("status_state") or "unknown")
    status = str(game.get("status") or "Status unavailable")
    score_available = state in ("final", "in_progress", "suspended") or status.lower() == "final"
    rows = []
    for side, label in (("away", "AWAY"), ("home", "HOME")):
        abbr = str(game.get(side) or "TBD")
        name = str(game.get(side + "_name") or abbr)
        value = game.get(side + "_score")
        score = str(value) if score_available and value is not None and value != "" else "—"
        rows.append(f'<div class="team-row">{team_badge(abbr)}<div class="team-name"><small>{label} · {escape(abbr)}</small><strong>{escape(name)}</strong></div><b class="team-score">{escape(score)}</b></div>')
    tip = str(game.get("tipoff_local") or "Time not recorded")
    badge_class = "live" if state == "in_progress" else "neutral"
    explanation = str(game.get("pick_reason") or "")
    reason_html = f'<div class="pick-explanation">{escape(explanation)}</div>' if not can_pick and explanation else ""
    return f'''<article class="matchup-card"><div class="matchup-top"><span>{escape(when)}</span><span class="game-status {badge_class}">{escape(status)}</span></div>
    {rows[0]}<div class="matchup-divider"><span>VS</span></div>{rows[1]}
    <div class="matchup-bottom"><span>◷ {escape(tip)}</span><span class="view-only">{"EVEN MONEY · VIRTUAL ONLY" if can_pick else "VIEW ONLY"}</span></div>{reason_html}</article>'''


def pending_pick_card(row: dict) -> str:
    status = {"open": "Pending", "won": "Won", "lost": "Lost", "void": "Refunded"}.get(row.get("status"), "Recorded")
    css = {"open": "pending", "won": "win", "lost": "loss", "void": "refund"}.get(row.get("status"), "pending")
    return f'<article class="pick-card"><div class="pick-card-top"><span>PICK DETAILS</span><span class="pick-status {css}">{status}</span></div><h3>{escape(str(row.get("pick", "")))} to win</h3><p>{escape(str(row.get("matchup", "")))}</p><strong>${escape(str(row.get("stake", "")))} virtual stake</strong><small>{escape(str(row.get("date", "")))}</small></article>'


def matchup_grid(games: list[dict]) -> str:
    return '<div class="matchup-grid">' + "".join(matchup_card(game) for game in games) + "</div>"


def empty_card(title: str, message: str) -> str:
    return f'<div class="empty-card"><h3>{escape(title)}</h3><p>{escape(message)}</p></div>'
