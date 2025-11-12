import os
from pathlib import Path
from datetime import date

import pandas as pd
import requests
import streamlit as st

API = "http://127.0.0.1:8000"

# ---------- PAGE + THEME ----------
st.set_page_config(page_title="CourtCash — NBA Bet Sim (MVP)", page_icon="🏀", layout="wide")

# CSS (dark theme polish)
css_path = Path("assets/streamlit_style.css")
if css_path.exists():
    st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)

# ---------- HEADER ----------
logo_path = Path("assets/courtcash_logo.svg")
with st.container():
    left, right = st.columns([1, 5], vertical_alignment="center")
    with left:
        if logo_path.exists():
            st.image(str(logo_path), use_container_width=True)
        else:
            st.markdown("<h2 style='margin:0'>🏀 CourtCash</h2>", unsafe_allow_html=True)
    with right:
        st.markdown(
            """
            <div class="headline">
              <h1>CourtCash — NBA Betting Simulator (class-only MVP)</h1>
              <p class="tagline">Practice picking winners. Fake bankroll. Real learning.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ensure local data folder exists for CSV saves
os.makedirs("data", exist_ok=True)

# ---------- HELPERS ----------
@st.cache_data(ttl=60)
def fetch_json(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()

def schedule_to_df(payload: dict) -> pd.DataFrame:
    rows = []
    for g in payload.get("games", []):
        rows.append({
            "date": payload.get("date"),
            "tipoff_local": g.get("tipoff_local"),
            "matchup": g.get("matchup"),
            "teams": g.get("teams"),
            "home": g.get("home"),
            "away": g.get("away"),
            "home_score": g.get("home_score"),
            "away_score": g.get("away_score"),
            "status": g.get("status"),
        })
    return pd.DataFrame(rows)

def results_to_df(payload: dict) -> pd.DataFrame:
    rows = []
    for g in payload.get("games", []):
        rows.append({
            "date": g.get("date"),
            "matchup": f"{g.get('away')} @ {g.get('home')}",
            "away": g.get("away"),
            "home": g.get("home"),
            "away_score": g.get("away_score"),
            "home_score": g.get("home_score"),
            "winner": g.get("winner"),
            "status": g.get("status"),
        })
    return pd.DataFrame(rows)

# ---------- UI ----------
tab1, tab2 = st.tabs(["Today's Schedule", "Recent Results"])

# ========== TAB 1: TODAY / PLACE BETS ==========
with tab1:
    st.subheader("Today's Schedule")
    picked = st.date_input("Date", value=date.today())
    team = st.text_input("Filter by team (abbr, e.g., LAL)", "")
    status = st.selectbox("Status", ["All", "Scheduled", "In Progress", "Final"])

    params = {"date": str(picked)}
    if team.strip():
        params["team"] = team.strip().upper()
    if status != "All":
        params["status"] = status

    try:
        schedule = fetch_json(f"{API}/games", params=params)

        # Wallet metric
        wallet_balance = None
        try:
            wallet = fetch_json(f"{API}/bets/wallet")
            wallet_balance = float(wallet.get("balance", 0))
        except Exception:
            pass

        mcol1, mcol2 = st.columns([1, 5])
        with mcol1:
            st.metric("Wallet", f"${(wallet_balance or 0):,.2f}")
        with mcol2:
            st.caption(f"{schedule.get('count', 0)} games")

        # Games + controls
        for g in schedule.get("games", []):
            st.markdown(
                f"""
                <div class="game-card">
                  <div class="game-top"><b>{g.get('matchup','?')}</b><span class="status">{g.get('status','')}</span></div>
                  <div class="game-sub">{g.get('teams','')}</div>
                  <div class="time">🕒 {g.get('tipoff_local','')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            cols = st.columns([1, 1, 1, 2])
            with cols[0]:
                stake = st.number_input(
                    f"Bet Amount ($) — #{g.get('id')}",
                    min_value=1.0, value=5.0, step=1.0,
                    key=f"stake_{g.get('id')}",
                )
            with cols[1]:
                pick = st.radio(
                    "Pick winner",
                    options=[g.get("home"), g.get("away")],
                    index=0,
                    key=f"pick_{g.get('id')}",
                )
            with cols[2]:
                if st.button("Place Bet", key=f"place_{g.get('id')}"):
                    payload = {
                        "date": schedule.get("date"),
                        "game_id": g.get("id"),
                        "matchup": g.get("matchup"),
                        "pick": pick,
                        "stake": float(stake),
                    }
                    try:
                        resp = requests.post(f"{API}/bets/", json=payload, timeout=15)
                        resp.raise_for_status()
                        data = resp.json()
                        st.success(f"Bet placed. New balance: ${data.get('balance',0):,.2f}")
                        st.cache_data.clear()
                        st.rerun()
                    except requests.HTTPError as e:
                        st.error(f"Failed to place bet: {e.response.text if e.response is not None else e}")
                    except Exception as ex:
                        st.error(f"Failed to place bet: {ex}")

            st.divider()

        # Save schedule CSV
        df = schedule_to_df(schedule)
        if not df.empty and st.button("💾 Save schedule to CSV"):
            fname = f"data/schedule_{str(picked)}.csv"
            df.to_csv(fname, index=False)
            st.success(f"Saved {len(df)} rows to **{fname}**")

        # Bets + settle
        st.subheader("My Bets")
        try:
            bets = fetch_json(f"{API}/bets/")
            st.dataframe(pd.DataFrame(bets.get("bets", [])), use_container_width=True, height=280)
        except Exception:
            st.info("No bets yet.")

        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("Settle open bets (last 3 days)"):
                try:
                    resp = requests.post(f"{API}/bets/settle?days=3", timeout=20)
                    resp.raise_for_status()
                    msg = resp.json()
                    st.success(
                        f"Settled {msg.get('settled',0)} bet(s). "
                        f"New balance: ${msg.get('new_balance',0):,.2f}"
                    )
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Settle failed: {e}")

    except requests.RequestException as e:
        st.error(f"Backend error: {e}")

# ========== TAB 2: RECENT RESULTS ==========
with tab2:
    st.subheader("Final Scores (last N days)")
    colA, colB = st.columns([3, 1])
    with colA:
        days = st.slider("Days back", min_value=1, max_value=30, value=7)
    with colB:
        if st.button("↻ Force refresh"):
            st.cache_data.clear()
            st.rerun()

    try:
        results = fetch_json(f"{API}/results", params={"days": days})
        count = results.get("count", 0)
        games = results.get("games", [])

        st.caption(f"{count} final games")
        if count == 0 or not games:
            st.info(
                "No final games found in this date range — try a smaller range (2–3 days). "
                "The public API may lag posting results."
            )
        else:
            rows = []
            for g in games:
                rows.append({
                    "date": (g.get("date") or "")[:10],
                    "matchup": f"{g.get('away','?')} @ {g.get('home','?')}",
                    "score": f"{g.get('away_score','?')}–{g.get('home_score','?')}",
                    "winner": g.get("winner"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=360)

            if st.button("💾 Save results to CSV"):
                df_r = results_to_df(results)
                fname = f"data/results_last_{days}d.csv"
                df_r.to_csv(fname, index=False)
                st.success(f"Saved {len(df_r)} rows to **{fname}**")

    except requests.HTTPError as e:
        st.error(f"Backend error: {e}\nURL: {API}/results?days={days}")
    except Exception as e:
        st.exception(e)


