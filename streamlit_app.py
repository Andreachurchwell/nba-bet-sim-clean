"""SheBETS Classic: virtual NBA picks."""
import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from dashboard_views import performance_frames
from ui_components import brand_header, story_card, matchup_grid, matchup_card, pending_pick_card, empty_card
from backend.api.services.game_eligibility import pick_reason
from wager_controls import whole_dollars, maximum_dollars, initial_wager

ROOT = Path(__file__).resolve().parent
API = os.getenv("SHEBETS_API_URL", "http://127.0.0.1:8000")
st.set_page_config(page_title="SheBETS Classic", page_icon="\U0001F3C0", layout="wide")
st.markdown("<style>" + (ROOT / "assets/classic_style.css").read_text(encoding="utf-8") + "</style>", unsafe_allow_html=True)
st.markdown(brand_header(), unsafe_allow_html=True)


def remember_widget(widget_key, saved_key):
    st.session_state[saved_key] = st.session_state[widget_key]


def remember_team(intent):
    draft = st.session_state.get("pick_draft")
    if draft and draft["intent"] == intent:
        draft["pick"] = st.session_state[f"winner_{intent}"]


def synchronize_wager(intent, source_key, other_key, limit):
    draft = st.session_state.get("pick_draft")
    if not draft or draft["intent"] != intent:
        return
    if draft.get("wager_ui_version") != 2:
        # An already-open decimal widget may fire its old callback during an
        # upgrade. Restore the durable draft first, before reading that payload.
        dollars = initial_wager(draft, limit)
        draft.pop("stake_cents", None)
        draft.pop("wager_error", None)
        draft.update(stake=dollars, stake_dollars=dollars, wager_ui_version=2)
        st.session_state[source_key] = st.session_state[other_key] = dollars
        return
    try:
        dollars = whole_dollars(st.session_state[source_key])
        if not 1 <= dollars <= limit:
            raise ValueError("Choose a wager within your available bankroll and the $5,000 limit.")
    except ValueError as exc:
        draft["wager_error"] = str(exc)
        return
    draft.update(stake=dollars, stake_dollars=dollars)
    draft.pop("wager_error", None)
    # Callbacks run before rendering; the two controls have different keys.
    st.session_state[source_key] = dollars
    st.session_state[other_key] = dollars

@st.cache_data(ttl=60)
def fetch_seasons() -> dict:
    response = requests.get(f"{API}/seasons", timeout=30)
    response.raise_for_status()
    return response.json()


def read_json(path: str, season: str, snapshot: str | None = None, anchor: str | None = None) -> dict:
    # Both season and snapshot are explicit cache arguments, never hidden state.
    params = {"season": season}
    if snapshot:
        params["snapshot"] = snapshot
    if anchor:
        params["date"] = anchor
    response = requests.get(f"{API}{path}", params=params, timeout=30)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", "Local season data is unavailable")
            message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = "Backend could not read local season data"
        raise ValueError(message)
    response.raise_for_status()
    payload = response.json()
    if payload.get("season_id") != season:
        raise ValueError("Backend returned a different season; refusing to display mixed data")
    return payload


@st.cache_data(ttl=15)
def fetch_json(path: str, season: str, snapshot: str | None = None, anchor: str | None = None) -> dict:
    return read_json(path, season, snapshot, anchor)


@st.cache_data(ttl=300)
def fetch_schedule(season: str, anchor: str) -> dict:
    # Financial refreshes must not evict the provider-backed browsing cache.
    return read_json("/games/live", season, anchor=anchor)


def can_pick(game, enabled):
    candidate = {**game, "status_state": game.get("eligibility_state", game.get("status_state")),
                 "verified_at": game.get("eligibility_verified_at")}
    return bool(enabled and game.get("pick_eligible") is True and not pick_reason(candidate, max_age_seconds=300))


def explained_game(game, enabled):
    if can_pick(game, enabled):
        return game
    candidate = {**game, "status_state": game.get("eligibility_state", game.get("status_state")),
                 "verified_at": game.get("eligibility_verified_at")}
    reason = game.get("pick_reason") or pick_reason(candidate, max_age_seconds=300)
    return {**game, "pick_reason": reason or ("Picks paused â€” betting unavailable" if not enabled else "Backend game verification required")}


def cards(games, title, message, enabled=False, season=None, group="games", saved_picks=()):
    st.subheader(title)
    if not games:
        st.markdown(empty_card("No games scheduled", message), unsafe_allow_html=True)
        return
    if not enabled:
        st.markdown(matchup_grid([explained_game(game, False) for game in games], saved_picks), unsafe_allow_html=True)
        return
    for start in range(0, len(games), 3):
        for col, game in zip(st.columns(3), games[start:start + 3]):
            with col:
                eligible = can_pick(game, enabled)
                st.markdown(matchup_card(explained_game(game, enabled), eligible, saved_picks), unsafe_allow_html=True)
                if eligible and st.button("Select Game", key=f"pick_{group}_{season}_{game['id']}", type="primary", width="stretch"):
                    st.session_state["pick_draft"] = {"season": season, "game": game, "intent": str(uuid4())}
                    st.session_state.pop("pick_review", None)


def post_action(path, season, payload=None, query=None):
    try:
        # A settlement pass can check many games. Each provider lookup remains
        # bounded by backend timeouts; do not abandon a healthy pass after 45s.
        timeout = (5, None) if path == "/bets/settle" else 45
        response = requests.post(f"{API}{path}", params={**(query or {}), "season": season}, json=payload, timeout=timeout)
    except requests.RequestException:
        if path == "/bets/settle":
            raise ValueError("Settlement could not be confirmed. Refresh My Picks before trying again; completed payouts will not be paid twice.") from None
        raise ValueError("The result could not be confirmed. Retry this same pick; do not start another until the result is known.") from None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", "The action could not be accepted.")
            if isinstance(detail, list):
                message = "Check your team and virtual amount (two decimal places, up to $5,000)."
            else:
                message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = "The backend could not confirm this action. Retry the same pick."
        raise ValueError(message)
    result = response.json()
    if result.get("season_id") != season:
        raise ValueError("The response season was unexpected. Refresh your history before retrying.")
    return result


def dismiss_pick_dialog():
    st.session_state.pop("pick_draft", None)
    st.session_state.pop("pick_review", None)


def submit_pick_dialog(season, intent):
    draft = st.session_state.get("pick_draft")
    if not draft or draft["season"] != season or draft["intent"] != intent:
        return
    game, pick, stake = draft["game"], draft["pick"], draft["stake"]
    try:
        result = post_action("/bets/", season, {"season_id": season, "game_id": game["id"],
            "pick": pick, "stake": f"{stake:.2f}", "idempotency_key": draft["idempotency_key"]})
        team = game["home_name"] if pick == game["home"] else game["away_name"]
        action = "already recorded" if result.get("repeated") else "placed"
        st.session_state["pick_success"] = f"{team}: ${stake:,.2f} virtual wager {action}. Bankroll: ${result['balance']:,.2f}."
        st.session_state.pop("pick_draft", None)
        fetch_json.clear()
    except ValueError as exc:
        draft["submission_error"] = str(exc)


@st.dialog("Your Pick", width="medium", on_dismiss=dismiss_pick_dialog)
def pick_panel(season, balance):
    draft = st.session_state.get("pick_draft")
    if not draft or draft["season"] != season:
        # A dialog callback cleared the draft. Close it and refresh the page.
        st.rerun()
    game, intent = draft["game"], draft["intent"]
    with st.container(border=True):
        st.markdown(matchup_card(game), unsafe_allow_html=True)
        st.caption(f"Bankroll: ${balance:,.2f}. Game eligibility and funds are checked when you place your pick.")
        pick = st.radio("Pick a winner", [game["away"], game["home"]],
                        index=[game["away"], game["home"]].index(draft.get("pick", game["away"])),
                        format_func=lambda team: game["away_name"] if team == game["away"] else game["home_name"],
                        key=f"winner_{intent}", on_change=remember_team, args=(intent,))
        limit = maximum_dollars(balance)
        # Seed widget values only before rendering. Durable draft fields survive
        # page changes and early failed reads, unlike Streamlit widget state.
        dollars = initial_wager(draft, limit)
        slider_key, number_key = f"wager_slider_{intent}", f"wager_{intent}"
        if draft.get("wager_ui_version") != 2 or draft.get("stake_dollars") != dollars:
            st.session_state[slider_key] = st.session_state[number_key] = dollars
            draft.pop("wager_error", None)
        draft.pop("stake_cents", None)
        draft.update(stake=dollars, stake_dollars=dollars, wager_ui_version=2, pick=pick)
        for key in (slider_key, number_key):
            if key not in st.session_state:
                st.session_state[key] = dollars
        st.slider("Choose virtual wager ($)", min_value=1 if limit > 1 else 0,
                  max_value=max(1, limit), step=1, key=slider_key, format="$%d",
                  disabled=limit <= 1, on_change=synchronize_wager,
                  args=(intent, slider_key, number_key, limit))
        with st.container(key="exact_wager"):
            st.number_input("Exact virtual wager ($)", min_value=1, max_value=max(1, limit),
                            step=1, format="%d", key=number_key, disabled=limit == 0,
                            on_change=synchronize_wager, args=(intent, number_key, slider_key, limit))
        if limit == 0:
            st.info("At least $1 in available virtual funds is needed for a whole-dollar pick.")
        if draft.get("wager_error"):
            st.error(draft["wager_error"])
        stake = draft["stake"]
        first, second = st.columns(2)
        first.metric("Potential profit", f"${stake:,.2f}")
        second.metric("Potential total return", f"${stake * 2:,.2f}")
        st.caption("Virtual money only. A win returns twice your wager, including the stake. A loss returns $0.")
        # One stable key per opened pick, including edits, reruns and retries.
        # Never generate a new key merely because a response was lost.
        legacy = st.session_state.pop("pick_review", None)
        legacy_key = legacy["key"] if legacy and legacy.get("fingerprint", [None])[0] == intent else intent
        draft.setdefault("idempotency_key", legacy_key)
        if draft.get("submission_error"):
            st.error(draft.pop("submission_error"))
        st.button("Place My Pick", key=f"place_{intent}", type="primary",
                  disabled=limit == 0 or bool(draft.get("wager_error")),
                  on_click=submit_pick_dialog, args=(season, intent))
        st.button("Cancel", key=f"close_{intent}", on_click=dismiss_pick_dialog)


def show_snapshots(path: str, season: str, title: str):
    st.subheader(title)
    try:
        payload = fetch_json(path, season)
        files = payload.get("snapshots", [])
        if not files:
            st.markdown(empty_card("No saved snapshot", payload["message"]), unsafe_allow_html=True)
            return
        selected = st.selectbox("Saved snapshot", files, key=f"snapshot_{path}_{season}")
        payload = fetch_json(path, season, selected)
        st.caption(f"Historical snapshot: {selected}. Original time labels and incomplete fields are preserved.")
        if payload["games"]:
            st.markdown(matchup_grid([{**game, "pick_reason": "Season archive â€” permanently read-only"} for game in payload["games"]]), unsafe_allow_html=True)
            st.download_button("Download selected snapshot", pd.DataFrame(payload["games"]).to_csv(index=False),
                               file_name=f"{season}_{selected}", mime="text/csv", key=f"download_{path}_{season}")
        else:
            st.info(payload["message"])
    except (requests.RequestException, ValueError, KeyError) as exc:
        st.warning(f"Saved snapshot unavailable: {exc}")


def show_live_games(season, enabled=False, balance=0, saved_picks=()):
    st.subheader("NBA Games")
    st.caption("All live game times: America/Chicago (CST/CDT).")
    day_key = f"game_day_{season}"
    saved_day_key = f"selected_game_day_{season}"
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    if day_key not in st.session_state:
        st.session_state[day_key] = st.session_state.get(saved_day_key, today)
    def navigate(offset=None):
        st.session_state[day_key] = (datetime.now(ZoneInfo("America/Chicago")).date() if offset is None
                                     else st.session_state[day_key] + timedelta(days=offset))
        st.session_state[saved_day_key] = st.session_state[day_key]
        st.session_state.pop("pick_draft", None)
        st.session_state.pop("pick_review", None)
    previous, current, following = st.columns(3)
    previous.button("Previous Day", on_click=navigate, args=(-1,), key=f"previous_{season}", width="stretch")
    current.button("Today", on_click=navigate, key=f"today_{season}", width="stretch", type="primary")
    following.button("Next Day", on_click=navigate, args=(1,), key=f"next_{season}", width="stretch")
    anchor = st.date_input("Game day", key=day_key)
    st.session_state[saved_day_key] = anchor
    st.markdown(f'<div class="selected-game-date">{anchor:%A, %B %d, %Y}<span>America/Chicago</span></div>', unsafe_allow_html=True)
    draft = st.session_state.get("pick_draft")
    if draft and draft["game"].get("date") != anchor.isoformat():
        st.session_state.pop("pick_draft", None)
        st.session_state.pop("pick_review", None)
    try:
        feed = fetch_schedule(season, anchor.isoformat())
        if not feed.get("verified"):
            st.warning(feed.get("message", "Live games could not be verified."))
            st.markdown(empty_card("Games unavailable", "Try again shortly. Your bankroll and pick history are unaffected."), unsafe_allow_html=True)
            return
        if feed.get("stale"):
            st.warning("Showing an older verified response. " + (feed.get("warning") or "The provider could not refresh."))
        fetched = feed.get("fetched_at_utc")
        if fetched:
            try:
                updated = datetime.fromisoformat(fetched).astimezone(ZoneInfo("America/Chicago")).strftime("%b %d, %I:%M %p %Z")
            except ValueError:
                updated = "time unavailable"
            st.caption(f"Source: BALLDONTLIE | Last successful fetch: {updated} | Schedules cached up to 5 minutes; placement always checks fresh status.")
        # Defense against a malformed/wrong-day response: never leak another
        # date's cards into the selected day, even if the provider window grows.
        daily = [game for game in feed.get("today", []) if game.get("date") == anchor.isoformat()]
        cards(daily, "Today's Games" if anchor == today else f"Games on {anchor:%b %d}",
              "No NBA games are scheduled for this selected date. Try Previous Day, Today, or Next Day.",
              enabled and not feed.get("stale"), season, "daily", saved_picks)
        # Render after the schedule without inserting a panel into its layout.
        # Dialog controls rerun only this fragment; cancel/submit close it with
        # one deliberate full rerun to refresh the surrounding page.
        if enabled and st.session_state.get("pick_draft", {}).get("season") == season:
            pick_panel(season, balance)
    except (requests.RequestException, ValueError, KeyError) as exc:
        st.warning(f"Live games unavailable: {exc}. Your season dashboard is unaffected.")


def show_performance(bets, stats, season):
    st.subheader("Performance")
    if not bets:
        st.markdown(empty_card("No performance data", "Charts will appear after your first pick."), unsafe_allow_html=True)
        return
    perf, daily = performance_frames(bets, stats["starting_bankroll"])
    st.caption("Reconstructed bankroll by bet-placement date. Eventual payouts are attributed to placement dates; exact historical settlement times were not recorded.")
    line = px.line(perf.dropna(subset=["placed_at"]), x="placed_at", y="bankroll")
    line.update_traces(line=dict(width=3, color="#9A57D6"), fill="tozeroy", fillcolor="rgba(217,184,240,0.16)",
                       hovertemplate="%{x|%b %d, %Y}<br>Bankroll: $%{y:,.2f}<extra></extra>")
    bar = px.bar(daily, x="day", y="cash_flow", color="result",
                 color_discrete_map={"Inflow": "#F3C96B", "Outflow": "#9A57D6"})
    bar.update_traces(hovertemplate="%{x|%b %d, %Y}<br>Cash flow: $%{y:,.2f}<extra></extra>")
    for figure, title, axis, key in (
        (line, f"{season} Reconstructed Bankroll by Bet Date", "Bankroll ($)", "bankroll"),
        (bar, f"{season} Daily Cash Flow by Bet Date", "Cash flow ($)", "cash_flow"),
    ):
        figure.update_layout(template="plotly_white", title=dict(text=title, font=dict(size=17, color="#45235F")),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                             font=dict(family="Segoe UI, Arial", color="#715D7D", size=11),
                             xaxis_title=None, yaxis_title=axis, height=340,
                             margin=dict(l=15, r=15, t=55, b=20), legend=dict(orientation="h", title=None))
        figure.update_xaxes(showgrid=False)
        figure.update_yaxes(gridcolor="#EEE0F2", zerolinecolor="#D9B8F0")
        st.plotly_chart(figure, width="stretch", key=f"{key}_{season}")


# Keep a durable copy even when upgrading an already-open app session whose
# widgets predate the callbacks, or when a read fails before they can render.
for widget_key, saved_key in (("active_section", "navigation_page"), ("selected_season", "chosen_season")):
    if widget_key in st.session_state:
        st.session_state[saved_key] = st.session_state[widget_key]

try:
    registry = fetch_seasons()
    choices = [item["season_id"] for item in registry["seasons"]]
    labels = {item["season_id"]: item["label"] for item in registry["seasons"]}
    context, control = st.columns([3, 2], vertical_alignment="center")
    with control:
        if "selected_season" not in st.session_state:
            st.session_state["selected_season"] = st.session_state.get("chosen_season", registry["default_season"])
        season = st.selectbox("NBA season", choices, index=choices.index(registry["default_season"]),
                              format_func=lambda value: labels[value], key="selected_season",
                              on_change=remember_widget, args=("selected_season", "chosen_season"))
    archived = next(item["archived"] for item in registry["seasons"] if item["season_id"] == season)
    betting_enabled = registry.get("betting_enabled", False) and not archived
    settlement_enabled = registry.get("settlement_enabled", False) and not archived
    if st.session_state.get("pick_draft", {}).get("season") not in (None, season):
        st.session_state.pop("pick_draft", None); st.session_state.pop("pick_review", None)
    with context:
        st.markdown(f'<div class="season-context"><strong>{season}</strong> &nbsp; / &nbsp; {"Season Archive" if archived else "Current NBA Season"}</div>', unsafe_allow_html=True)
        st.markdown('<div class="maintenance-note">&#9675; ' + ("Archive &nbsp; Read-only" if archived else ("Classic picks &nbsp; Virtual money only" if betting_enabled else "Read-only &nbsp; Betting &amp; settlement paused")) + '</div>', unsafe_allow_html=True)
    if st.session_state.get("pick_success"):
        notice = st.warning if st.session_state.pop("settlement_paused", False) else st.success
        notice(st.session_state.pop("pick_success"))
    if st.button("Refresh view", help="Refreshes displayed reads. Schedules may remain cached for five minutes; pick placement always verifies fresh status."):
        fetch_json.clear()
        fetch_seasons.clear()
        fetch_schedule.clear()
        st.rerun()

    stats = fetch_json("/seasons/stats", season)
    bets = fetch_json("/bets/", season)["bets"]
    wallet = fetch_json("/bets/wallet", season)
    if wallet["balance"] != stats["available_bankroll"]:
        st.error("Season wallet changed between reads. Refresh before viewing this dashboard.")
        st.stop()

    # st.tabs has no keyed server-side selection in the installed Streamlit.
    # Every betting widget reruns the script, so navigation must be a widget
    # backed by session state rather than a recreated, Home-first tab group.
    with st.container(key="section_navigation"):
        if "active_section" not in st.session_state:
            st.session_state["active_section"] = st.session_state.get("navigation_page", "Home / Dashboard")
        section = st.radio("Navigation",
            ["Home / Dashboard", "NBA Games", "My Picks", "Performance", "Season Archive"],
            key="active_section", horizontal=True, label_visibility="collapsed",
            on_change=remember_widget, args=("active_section", "navigation_page"))
    if section == "Home / Dashboard":
        st.markdown(story_card(season, archived), unsafe_allow_html=True)
        st.markdown('<div class="section-kicker">SEASON SUMMARY</div>', unsafe_allow_html=True)
        st.subheader("Dashboard")
        metrics = [
            ("Bankroll", f"${wallet['balance']:,.2f}"),
            ("Starting bankroll", f"${stats['starting_bankroll']:,.2f}"),
            ("Profit/loss (settled bets)", f"${stats['profit_loss']:,.2f}"),
            ("Total bets", stats["total_bets"]), ("Wins", stats["wins"]), ("Losses", stats["losses"]),
            ("Win percentage", "\u2014" if stats["win_percentage"] is None else f"{stats['win_percentage']:.1f}%"),
            ("ROI", "\u2014" if stats["roi"] is None else f"{stats['roi']:.1f}%"),
            ("Pending bets", stats["pending_bets"]), ("Pending stakes", f"${stats['pending_stakes']:,.2f}"),
        ]
        for start in (0, 4, 8):
            cols = st.columns(4 if start < 8 else 2)
            for col, (label, value) in zip(cols, metrics[start:start + (4 if start < 8 else 2)]):
                col.metric(label, value)
        if archived:
            st.warning(f"{stats['pending_bets']} archived pick(s) remain unresolved, with ${stats['pending_stakes']:,.2f} in pending stakes. Original records and the recorded wallet balance are preserved read-only.")
        else:
            st.caption("Select a game in NBA Games to place a virtual pick." if betting_enabled else "Betting is currently paused.")
    elif section == "NBA Games":
        if archived:
            st.info("Archive view: original saved schedules only. No live requests or new results are substituted.")
            show_snapshots("/games", season, "Archived schedules")
        else:
            show_live_games(season, betting_enabled, wallet["balance"], bets)
    elif section == "My Picks":
        st.subheader("My Picks")
        if not bets:
            st.markdown(empty_card("No picks yet", "No picks recorded for this season. Choose an eligible matchup in NBA Games to begin." if betting_enabled else "No picks recorded. Your virtual bankroll is untouched; betting remains paused."), unsafe_allow_html=True)
        else:
            st.caption(f"{len(bets):,} original records, including the preserved duplicate." if archived else f"{len(bets):,} picks this season.")
            if not archived:
                pending = [row for row in bets if row["status"] == "open"]
                settled = [row for row in bets if row["status"] != "open"]
                st.subheader("Pending Picks")
                if pending:
                    st.markdown('<div class="matchup-grid">' + ''.join(pending_pick_card(row) for row in pending) + '</div>', unsafe_allow_html=True)
                else:
                    st.caption("No pending picks.")
                st.subheader("Settled Picks")
                if settled:
                    st.markdown('<div class="matchup-grid">' + ''.join(pending_pick_card(row) for row in settled) + '</div>', unsafe_allow_html=True)
                else:
                    st.caption("Settled picks will appear here after verified results.")
                if settlement_enabled and pending and st.button("Refresh Results / Settle Eligible Bets", type="primary"):
                    try:
                        with st.spinner("Checking pending game results..."):
                            result = post_action("/bets/settle", season, query={"after_game_id": st.session_state.get(f"settle_cursor_{season}", 0)})
                        st.session_state[f"settle_cursor_{season}"] = result["next_after_game_id"]
                        summary = (f"Settled {result['settled']} picks: {result['won']} won, {result['lost']} lost, "
                                   f"{result['refunded']} refunded. {result['pending']} still open. "
                                   f"Bankroll: ${result['balance']:,.2f}. Checked {result['checked_games']} games.")
                        if result.get("interrupted"):
                            summary += " Checking paused; completed payouts are saved."
                        if result.get("retry_after"):
                            summary += f" Try again in {result['retry_after']} seconds."
                        st.session_state["pick_success"] = summary + " " + " ".join(result.get("warnings", []))
                        st.session_state["settlement_paused"] = result.get("interrupted", False)
                        fetch_json.clear(); st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            st.dataframe(pd.DataFrame(bets), width="stretch", height=360)
            st.download_button("Download season bet history", pd.DataFrame(bets).to_csv(index=False),
                               file_name=f"bets_{season}.csv", mime="text/csv", key=f"bets_download_{season}")
    elif section == "Performance":
        show_performance(bets, stats, season)
    elif section == "Season Archive":
        if archived:
            st.subheader("Season Archive")
            st.caption("The 2025-26 ledger, wallet and saved snapshots are preserved read-only.")
            show_snapshots("/results", season, "Saved results, just as recorded")
        else:
            st.markdown(empty_card("Season Archive", "Choose 2025-26 in the season selector above to revisit your original picks, recorded balance, performance and saved results."), unsafe_allow_html=True)
    st.markdown('<footer class="app-footer"><span>SheBETS Classic</span><span>Virtual money only. No real-money wagering.</span></footer>', unsafe_allow_html=True)
except (requests.RequestException, ValueError, KeyError) as exc:
    st.error(f"Unable to read selected-season data: {exc}. No legacy fallback was used.")
