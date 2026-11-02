"""Escaped presentation markup. No provider calls or data writes."""
import base64
from datetime import date
from functools import lru_cache
from html import escape
from pathlib import Path
from decimal import Decimal, InvalidOperation
from PIL import Image, UnidentifiedImageError
import re

ASSETS = Path(__file__).resolve().parent / "assets"
# User-selected assets for PRIVATE LOCAL DISPLAY only. This compatibility name
# does not assert licensing/redistribution approval. The files are Git-ignored.
APPROVED_TEAM_LOGOS = {
    "ATL": "hawks.png", "BOS": "celtics.png", "BKN": "nets.png",
    "CHA": "hornets.png", "CHI": "bulls.png", "CLE": "cavs.png",
    "DAL": "mavs.png", "DEN": "nuggets.png", "DET": "pistons.png",
    "GSW": "warriors.png", "HOU": "rockets.png", "IND": "pacers.png",
    "LAC": "clippers.png", "LAL": "lakers.png", "MEM": "grizzlies.png",
    "MIA": "heat.png", "MIL": "bucks.jpg", "MIN": "wolves.png",
    "NOP": "pelicans.png", "NYK": "knicks.jpg", "OKC": "okc.png",
    "ORL": "magic.jpg", "PHI": "76ers.png", "PHX": "suns.png", "POR": "blazers.png",
    "SAC": "kings.png", "SAS": "spurs.jpg", "TOR": "raptors.png",
    "UTA": "jazz.png", "WAS": "wizards.png",
}
TEAM_ALIASES = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS",
                "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}


def image_uri(path: str) -> str | None:
    try:
        target = Path(path)
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not target.resolve().is_relative_to(ASSETS.resolve()):
            return None
        stat = target.stat()
        return _cached_image_uri(str(target.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


@lru_cache(maxsize=64)
def _cached_image_uri(path: str, mtime_ns: int, size: int) -> str | None:
    """Validate/cache original bytes; never convert, crop, or rewrite artwork."""
    try:
        with Image.open(path) as source:
            expected = "PNG" if Path(path).suffix.lower() == ".png" else "JPEG"
            if source.format != expected:
                return None
            source.verify()
        mime = "image/png" if expected == "PNG" else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError):
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
    abbreviation = str(abbreviation or "TBD").strip().upper()
    canonical = TEAM_ALIASES.get(abbreviation, abbreviation)
    asset = APPROVED_TEAM_LOGOS.get(canonical)
    uri = image_uri(str(ASSETS / asset)) if asset else None
    if uri:
        return f'<span class="team-badge has-logo"><img src="{uri}" alt="{escape(canonical, quote=True)} logo"></span>'
    return f'<span class="team-badge" aria-label="{escape(abbreviation, quote=True)} team abbreviation">{escape(abbreviation)}</span>'


def placed_pick_summary(game_id, saved_picks) -> str:
    """Summarize the selected season's saved rows, including settled picks."""
    if game_id is None:
        return ""
    teams = {}
    count = 0
    for pick in saved_picks:
        if str(pick.get("game_id")) != str(game_id):
            continue
        try:
            amount = Decimal(str(pick.get("stake", "")))
            if not amount.is_finite() or amount <= 0:
                continue
        except InvalidOperation:
            continue
        team = str(pick.get("pick") or "Team unavailable")
        total, quantity = teams.get(team, (Decimal(0), 0))
        teams[team] = total + amount, quantity + 1
        count += 1
    if not count:
        return ""
    details = []
    for team, (total, quantity) in teams.items():
        suffix = f" ({quantity} picks)" if quantity > 1 else ""
        details.append(f"{escape(team)} ${total:,.2f}{suffix}")
    label = "Pick placed" if count == 1 else f"Pick placed · {count} picks"
    return f'<div class="placed-pick"><strong>{label}</strong><span>{"; ".join(details)}</span></div>'


def matchup_card(game: dict, can_pick=False, saved_picks=()) -> str:
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
    {placed_pick_summary(game.get("id"), saved_picks)}<div class="matchup-bottom"><span>◷ {escape(tip)}</span><span class="view-only">{"EVEN MONEY · VIRTUAL ONLY" if can_pick else "VIEW ONLY"}</span></div>{reason_html}</article>'''


def pending_pick_card(row: dict) -> str:
    status = {"open": "Pending", "won": "Won", "lost": "Lost", "void": "Refunded"}.get(row.get("status"), "Recorded")
    css = {"open": "pending", "won": "win", "lost": "loss", "void": "refund"}.get(row.get("status"), "pending")
    matchup = str(row.get("matchup", ""))
    teams = re.fullmatch(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})\s*", matchup)
    matchup_html = (f'<div class="pick-matchup">{team_badge(teams[1])}<span>{escape(matchup)}</span>{team_badge(teams[2])}</div>'
                    if teams else f'<p>{escape(matchup)}</p>')
    return f'<article class="pick-card"><div class="pick-card-top"><span>PICK DETAILS</span><span class="pick-status {css}">{status}</span></div><div class="pick-choice">{team_badge(row.get("pick"))}<h3>{escape(str(row.get("pick", "")))} to win</h3></div>{matchup_html}<strong>${escape(str(row.get("stake", "")))} virtual stake</strong><small>{escape(str(row.get("date", "")))}</small></article>'


def matchup_grid(games: list[dict], saved_picks=()) -> str:
    return '<div class="matchup-grid">' + "".join(matchup_card(game, saved_picks=saved_picks) for game in games) + "</div>"


def empty_card(title: str, message: str) -> str:
    return f'<div class="empty-card"><h3>{escape(title)}</h3><p>{escape(message)}</p></div>'
