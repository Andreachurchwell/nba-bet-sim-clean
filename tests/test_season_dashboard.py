import json
import shutil
import socket
import httpx
import streamlit as st
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from backend.api.main import app
from backend.api.services.season_reads import get_store
from backend.api.services.season_store import SeasonStore
from backend.api.services.live_games import FeedUnavailable, get_live_client
from backend.api.core import config
from backend.api.services.betting import BettingService
from dashboard_views import performance_frames
from test_season_store import PROJECT, snapshot, isolated_seasons


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        isolated_seasons(self.base / "seasons")
        # Decoy legacy files prove missing season data never falls back.
        (self.base / "wallet.json").write_text('{"balance":999999}')
        self.store = SeasonStore(self.base / "seasons")
        BettingService(self.store).snapshot("2026-27")  # Create only the coordination lock before read-only snapshots.
        app.dependency_overrides[get_store] = lambda: self.store
        offline = Mock()
        offline.read.side_effect = FeedUnavailable("Live feed unavailable in isolated dashboard tests.")
        app.dependency_overrides[get_live_client] = lambda: offline
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def get(self, path, season="2026-27", **params):
        return self.client.get(path, params={"season": season, **params})

    def test_default_and_archive_baselines(self):
        before = snapshot(self.base)
        self.assertEqual(self.client.get("/seasons").json()["default_season"], "2026-27")
        fresh = self.client.get("/seasons/stats").json()
        for field in ("total_bets", "wins", "losses", "pending_bets", "pending_stakes", "profit_loss"):
            self.assertEqual(fresh[field], 0)
        self.assertEqual(fresh["starting_bankroll"], 5000)
        self.assertEqual(fresh["available_bankroll"], 5000)
        self.assertIsNone(fresh["win_percentage"])
        self.assertIsNone(fresh["roi"])
        archived = self.get("/seasons/stats", "2025-26").json()
        self.assertEqual(archived["available_bankroll"], 5075)
        self.assertEqual(archived["total_bets"], 6)
        self.assertEqual((archived["wins"], archived["losses"], archived["pending_bets"]), (2, 3, 1))
        self.assertEqual(archived["pending_stakes"], 25)
        self.assertEqual(archived["profit_loss"], 100)
        self.assertAlmostEqual(archived["win_percentage"], 2 / 5 * 100)
        self.assertAlmostEqual(archived["roi"], 100 / 220 * 100)
        self.assertEqual(snapshot(self.base), before)

    def test_all_read_routes_scoped_and_side_effect_free(self):
        before = snapshot(self.base)
        for season in ("2025-26", "2026-27", "2025-26", "2026-27"):
            for path in ("/bets/", "/bets/wallet", "/seasons/stats", "/games", "/results"):
                response = self.get(path, season)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["season_id"], season)
        self.assertEqual(self.get("/bets/").json()["bets"], [])
        self.assertEqual(snapshot(self.base), before)

    def test_all_bet_and_settlement_posts_rejected(self):
        before = snapshot(self.base)
        payload = {"date": "2026-10-31", "game_id": 1, "matchup": "MIA @ CHI", "pick": "CHI", "stake": 5}
        for season in ("2025-26", "2026-27"):
            for path in ("/bets/", "/bets", "/bets/settle", "/bets/settle/"):
                with patch.object(config, "MAINTENANCE_READ_ONLY", True):
                    response = self.client.post(path, params={"season": season}, json=payload)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["detail"]["code"], "maintenance_read_only")
        with patch.object(config, "MAINTENANCE_READ_ONLY", True):
            self.assertEqual(self.client.post("/bets/", json={}).status_code, 503)
        self.assertEqual(snapshot(self.base), before)

    def test_missing_files_and_invalid_seasons(self):
        (self.base / "seasons/2026-27/wallet.json").unlink()
        before = snapshot(self.base)
        for path in ("/bets/wallet", "/seasons/stats"):
            response = self.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertIn("Missing", response.json()["detail"]["message"])
        for path in ("/bets/", "/bets/wallet", "/games", "/results", "/seasons/stats"):
            self.assertEqual(self.get(path, "../2025-26").status_code, 400)
        self.assertEqual(snapshot(self.base), before)

    def test_snapshots_are_local_and_explicit(self):
        before = snapshot(self.base)
        for path in ("/games", "/results"):
            fresh = self.get(path).json()
            self.assertEqual(fresh["games"], [])
            self.assertEqual(fresh["snapshots"], [])
            self.assertIn("disabled", fresh["message"])
            archived = self.get(path, "2025-26").json()
            self.assertTrue(archived["snapshots"])
            selected = self.get(path, "2025-26", snapshot=archived["snapshots"][0]).json()
            self.assertTrue(selected["games"])
            self.assertTrue(all(r["source_file"] == archived["snapshots"][0] for r in selected["games"]))
        self.assertEqual(self.get("/games", "2025-26", date="1990-01-01").json()["games"], [])
        self.assertEqual(self.get("/results", "2025-26", snapshot="results_absent.csv").status_code, 404)
        self.assertEqual(self.get("/results", "2025-26", snapshot="../ledger.csv").status_code, 422)
        self.assertEqual(snapshot(self.base), before)

    def test_historical_chart_math_and_empty_new_season(self):
        rows = self.store.read_ledger("2025-26")
        perf, daily = performance_frames(rows, self.store.metadata("2025-26")["starting_bankroll"])
        self.assertEqual(len(perf), 6)
        self.assertEqual(perf["bankroll"].iloc[-1], 5075)
        self.assertEqual(daily["cash_flow"].sum(), 75)
        empty, days = performance_frames(self.store.read_ledger("2026-27"), 5000)
        self.assertTrue(empty.empty and days.empty)

    def test_streamlit_switching_and_cached_season_isolation(self):
        def local_get(url, params=None, **kwargs):
            # Every UI request is handled in memory, not on a live server.
            return self.client.get(url.split("8000", 1)[-1], params=params)
        before = snapshot(self.base)
        st.cache_data.clear()
        original_connect = socket.socket.connect
        def local_only(sock, address):
            if address[0] not in ("127.0.0.1", "::1"):
                raise AssertionError("No external network")
            return original_connect(sock, address)
        with patch("requests.get", side_effect=local_get), patch("socket.socket.connect", new=local_only):
            view = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=20).run()
            self.assertEqual(len(view.exception), 0)
            self.assertEqual(view.selectbox(key="selected_season").value, "2026-27")
            metrics = {item.label: item.value for item in view.metric}
            self.assertEqual(metrics["Bankroll"], "$5,000.00")
            self.assertEqual(metrics["Total bets"], "0")
            self.assertEqual(metrics["Win percentage"], "\u2014")
            self.assertEqual(len(view.get("plotly_chart")), 0)
            view.selectbox(key="selected_season").select("2025-26").run()
            self.assertEqual(len(view.exception), 0)
            metrics = {item.label: item.value for item in view.metric}
            self.assertEqual(metrics["Bankroll"], "$5,075.00")
            self.assertEqual(metrics["Total bets"], "6")
            self.assertEqual(metrics["Pending stakes"], "$25.00")
            self.assertEqual(metrics["Profit/loss (settled bets)"], "$100.00")
            self.assertTrue(any("$25.00" in warning.value for warning in view.warning))
            self.assertFalse(any("Place Bet" in button.label or "Settle" in button.label for button in view.button))
            view.radio(key="active_section").set_value("Performance").run()
            self.assertEqual(len(view.get("plotly_chart")), 2)
            view.radio(key="active_section").set_value("Home / Dashboard").run()
            view.selectbox(key="selected_season").select("2026-27").run()
            self.assertEqual(len(view.exception), 0)
            metrics = {item.label: item.value for item in view.metric}
            self.assertEqual(metrics["Bankroll"], "$5,000.00")
            self.assertEqual(metrics["Total bets"], "0")
            self.assertEqual(len(view.get("plotly_chart")), 0)
        self.assertEqual(snapshot(self.base), before)

    def test_ui_rejects_mismatched_season_response(self):
        def wrong_season(url, params=None, **kwargs):
            response = self.client.get(url.split("8000", 1)[-1], params=params)
            if url.endswith("/seasons/stats"):
                body = response.json()
                body["season_id"] = "2025-26"
                return httpx.Response(200, json=body, request=httpx.Request("GET", url))
            return response
        st.cache_data.clear()
        with patch("requests.get", side_effect=wrong_season):
            view = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=20).run()
            self.assertEqual(len(view.exception), 0)
            self.assertTrue(any("different season" in item.value for item in view.error))
            self.assertEqual(len(view.metric), 0)
            self.assertEqual(len(view.get("plotly_chart")), 0)

    def test_ui_explains_missing_wallet_without_fallback(self):
        (self.base / "seasons/2026-27/wallet.json").unlink()
        before = snapshot(self.base)
        st.cache_data.clear()
        with patch("requests.get", side_effect=lambda url, params=None, **kwargs:
                   self.client.get(url.split("8000", 1)[-1], params=params)):
            view = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=20).run()
            self.assertEqual(len(view.exception), 0)
            self.assertTrue(any("Missing wallet.json" in item.value for item in view.error))
            self.assertEqual(len(view.metric), 0)
        self.assertEqual(snapshot(self.base), before)

    def test_ui_successful_feed_renders_safe_matchup_cards(self):
        from backend.api.services.live_games import normalize_game
        from test_live_games import fixture
        feed = Mock()
        game = normalize_game(fixture())
        feed.read.return_value = {
            "season_id": "2026-27", "verified": True, "stale": False,
            "fetched_at_utc": "2026-10-31T20:00:00+00:00", "games": [game],
            "today": [game], "upcoming": [], "recent_finals": [],
        }
        app.dependency_overrides[get_live_client] = lambda: feed
        st.cache_data.clear()
        before = snapshot(self.base)
        with patch("requests.get", side_effect=lambda url, params=None, **kwargs:
                   self.client.get(url.split("8000", 1)[-1], params=params)):
            view = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=20).run()
            self.assertEqual(len(view.exception), 0)
            view.radio(key="active_section").set_value("NBA Games").run()
            from datetime import date
            view.date_input(key="game_day_2026-27").set_value(date(2026,10,31)).run()
            markup = "\n".join(item.value for item in view.markdown)
            self.assertIn("Original SheBETS logo", markup)
            self.assertIn("matchup-card", markup)
            self.assertIn("Chicago Bulls", markup)
            self.assertIn("Miami Heat", markup)
            self.assertIn("team-badge", markup)
            self.assertIn("07:00 PM CDT", markup)
            self.assertTrue("EVEN MONEY" in markup or "VIEW ONLY" in markup, "Card must show a betting availability badge")

    def test_daily_navigation_and_no_other_day_cards(self):
        from datetime import datetime,timedelta,time,timezone
        from backend.api.services.live_games import CENTRAL,normalize_game
        from backend.api.services.game_eligibility import display_eligibility
        from test_live_games import fixture
        provider=Mock()
        def read(season,anchor,days_back,days_ahead):
            self.assertEqual((days_back,days_ahead),(0,0))
            stamp=datetime.now(timezone.utc).isoformat()
            game=display_eligibility(normalize_game(fixture(
                date=anchor.isoformat(),datetime=datetime.combine(anchor,time(19),CENTRAL).isoformat(),
                home_team={'id':4,'abbreviation':'CHI','full_name':'SELECTED DAY TEAM'})),stamp)
            wrong={**game,'id':202,'date':(anchor+timedelta(days=1)).isoformat(),'home_name':'WRONG DAY TEAM'}
            return {'season_id':season,'verified':True,'stale':False,'fetched_at_utc':stamp,
                    'games':[game,wrong],'today':[game,wrong],'upcoming':[wrong],'recent_finals':[]}
        provider.read.side_effect=read
        app.dependency_overrides[get_live_client]=lambda:provider
        st.cache_data.clear();before=snapshot(self.base)
        today=datetime.now(CENTRAL).date()
        with patch('requests.get',side_effect=lambda url,params=None,**kwargs:self.client.get(url.split('8000',1)[-1],params=params)):
            view=AppTest.from_file(str(PROJECT/'streamlit_app.py'),default_timeout=20).run()
            self.assertEqual(len(view.exception),0)
            view.radio(key='active_section').set_value('NBA Games').run()
            self.assertEqual(view.date_input(key='game_day_2026-27').value,today)
            def assert_daily():
                markup='\n'.join(item.value for item in view.markdown if '<header' not in item.value)
                self.assertTrue('SELECTED DAY TEAM' in markup)
                self.assertFalse('WRONG DAY TEAM' in markup)
                self.assertFalse(any(s.value in ('Upcoming Matchups','Recent Finals') for s in view.subheader))
            assert_daily()
            view.button(key='next_2026-27').click().run()
            self.assertEqual(view.date_input(key='game_day_2026-27').value,today+timedelta(days=1));assert_daily()
            view.button(key='previous_2026-27').click().run()
            self.assertEqual(view.date_input(key='game_day_2026-27').value,today)
            view.button(key='previous_2026-27').click().run()
            self.assertEqual(view.date_input(key='game_day_2026-27').value,today-timedelta(days=1))
            view.button(key='today_2026-27').click().run()
            self.assertEqual(view.date_input(key='game_day_2026-27').value,today)
            view.date_input(key='game_day_2026-27').set_value(today+timedelta(days=10)).run();assert_daily()
            self.assertEqual(len(view.exception),0)
        self.assertEqual(snapshot(self.base),before)
        self.assertEqual(snapshot(self.base), before)

    def test_selected_navigation_survives_reruns_and_restores_game_date(self):
        from datetime import datetime,timedelta
        from backend.api.services.live_games import CENTRAL
        provider=app.dependency_overrides[get_live_client]()
        st.cache_data.clear()
        before=snapshot(self.base)
        with patch('requests.get',side_effect=lambda url,params=None,**kwargs:self.client.get(url.split('8000',1)[-1],params=params)):
            view=AppTest.from_file(str(PROJECT/'streamlit_app.py'),default_timeout=20).run()
            self.assertEqual(view.radio(key='active_section').value,'Home / Dashboard')
            provider.read.assert_not_called()
            self.assertEqual(len(view.date_input),0)
            view.radio(key='active_section').set_value('NBA Games').run()
            chosen=datetime.now(CENTRAL).date()+timedelta(days=3)
            view.date_input(key='game_day_2026-27').set_value(chosen).run()
            view.radio(key='active_section').set_value('My Picks').run()
            view.run()
            self.assertEqual(view.radio(key='active_section').value,'My Picks')
            self.assertEqual(len(view.date_input),0)
            view.radio(key='active_section').set_value('NBA Games').run()
            self.assertEqual(view.date_input(key='game_day_2026-27').value,chosen)
            view.run()
            self.assertEqual(view.radio(key='active_section').value,'NBA Games')
            self.assertEqual(len(view.exception),0)
        self.assertEqual(snapshot(self.base),before)


if __name__ == "__main__":
    unittest.main()
