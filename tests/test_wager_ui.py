"""UI regression tests use only synthetic records and in-memory HTTP responses."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import streamlit as st
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from backend.api.main import app
from backend.api.services.betting import BettingService
from backend.api.services.game_eligibility import display_eligibility
from backend.api.services.live_games import CENTRAL, get_live_client
from backend.api.services.season_reads import get_store
from backend.api.services.season_store import SeasonStore
from test_betting import FakeProvider
from test_season_store import PROJECT, isolated_seasons


class WagerUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "seasons"
        isolated_seasons(self.root)
        self.store, self.provider = SeasonStore(self.root), FakeProvider()
        self.service = BettingService(self.store, self.provider)
        self.service.snapshot("2026-27")
        self.day = datetime.now(CENTRAL).date() + timedelta(days=2)
        def read(season, anchor, *args):
            game = self.provider.verify_game(101)
            game = display_eligibility({**game, "date": anchor.isoformat(), "identity_verified": True,
                "eligibility_state": "scheduled"}, game["verified_at"])
            return {"season_id": season, "verified": True, "stale": False,
                    "fetched_at_utc": game["verified_at"], "games": [game], "today": [game]}
        self.provider.read = read
        app.dependency_overrides[get_store] = lambda: self.store
        app.dependency_overrides[get_live_client] = lambda: self.provider
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.failure = None
        def get(url, params=None, **kwargs):
            if self.failure and url.endswith(self.failure):
                from requests import ConnectionError
                raise ConnectionError("Synthetic transient read failure")
            return self.client.get(url.split("8000", 1)[-1], params=params)
        self.get_patch = patch("requests.get", side_effect=get)
        self.get_patch.start()
        self.addCleanup(self.get_patch.stop)
        self.posts = self.enterContext(patch("requests.post"))

    def open_pick(self):
        view = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=30).run()
        view.radio(key="active_section").set_value("NBA Games").run()
        view.date_input(key="game_day_2026-27").set_value(self.day).run()
        view.button(key="pick_daily_2026-27_101").click().run()
        self.assertEqual(len(view.exception), 0)
        return view, view.session_state["pick_draft"]["intent"]

    def test_transient_read_does_not_reset_navigation_or_draft(self):
        view, intent = self.open_pick()
        view.radio(key="winner_" + intent).set_value("CHI").run()
        view.number_input(key="wager_" + intent).set_value(35).run()
        st.cache_data.clear()
        self.failure = "/seasons/stats"
        view.number_input(key="wager_" + intent).set_value(36).run()
        self.assertTrue(view.error)
        self.failure = None
        st.cache_data.clear()
        view.run()
        self.assertEqual(view.radio(key="active_section").value, "NBA Games")
        self.assertEqual(view.radio(key="winner_" + intent).value, "CHI")
        self.assertEqual(view.number_input(key="wager_" + intent).value, 36)
        self.assertEqual(view.date_input(key="game_day_2026-27").value, self.day)
        self.posts.assert_not_called()

    def test_pick_controls_belong_to_one_native_dialog_fragment(self):
        view, intent = self.open_pick()
        dialogs = view.get("dialog")
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(dialogs[0].proto.dialog.is_open)
        self.assertEqual(dialogs[0].proto.dialog.title, "Your Pick")
        # AppTest reruns whole scripts; inspect Streamlit's registered metadata
        # to verify that real-browser control events target only the dialog.
        metadata = view.session_state._state._new_widget_state.widget_metadata
        widgets = [view.radio(key="winner_" + intent), view.slider(key="wager_slider_" + intent),
                   view.number_input(key="wager_" + intent), view.button(key="place_" + intent)]
        fragment_ids = {metadata[w.id].fragment_id for w in widgets}
        self.assertEqual(len(fragment_ids), 1)
        self.assertNotIn(None, fragment_ids)
        self.assertIsNone(metadata[view.radio(key="active_section").id].fragment_id)
        view.radio(key="winner_" + intent).set_value("CHI").run()
        view.slider(key="wager_slider_" + intent).set_value(125).run()
        view.number_input(key="wager_" + intent).set_value(106).run()
        self.assertTrue(view.get("dialog")[0].proto.dialog.is_open)
        self.assertEqual(view.radio(key="active_section").value, "NBA Games")
        self.assertEqual(view.radio(key="winner_" + intent).value, "CHI")
        self.assertEqual(view.slider(key="wager_slider_" + intent).value, 106)
        self.assertEqual(view.session_state["pick_draft"]["game"]["id"], 101)
        self.assertEqual(view.date_input(key="game_day_2026-27").value, self.day)
        view.button(key="close_" + intent).click().run()
        self.assertFalse(any(d.proto.dialog.is_open for d in view.get("dialog")))
        self.assertNotIn("pick_draft", view.session_state)
        self.assertEqual(view.date_input(key="game_day_2026-27").value, self.day)
        self.posts.assert_not_called()

    def test_native_dialog_dismiss_does_not_reopen_or_change_day(self):
        view, _ = self.open_pick()
        dialog = view.get("dialog")[0].proto.dialog
        self.assertTrue(dialog.dismissible)
        # AppTest has no close-X helper. Send the native dialog trigger used by
        # X, Escape, and backdrop dismissal, preserving other widget values.
        states = view._tree.get_widget_states()
        states.widgets.add(id=dialog.id, trigger_value=True)
        view._run(states)
        self.assertFalse(any(d.proto.dialog.is_open for d in view.get("dialog")))
        self.assertNotIn("pick_draft", view.session_state)
        self.assertEqual(view.radio(key="active_section").value, "NBA Games")
        self.assertEqual(view.date_input(key="game_day_2026-27").value, self.day)
        view.run()
        self.assertFalse(any(d.proto.dialog.is_open for d in view.get("dialog")))
        self.posts.assert_not_called()

    def test_slider_and_exact_input_sync_preserve_selection_without_submitting(self):
        view, intent = self.open_pick()
        view.radio(key="winner_" + intent).set_value("CHI").run()
        for amount in (125, 150, 106):
            view.slider(key="wager_slider_" + intent).set_value(amount).run()
            self.assertEqual(view.number_input(key="wager_" + intent).value, amount)
        for amount in (1, 25, 26, 27, 28):
            view.number_input(key="wager_" + intent).set_value(amount).run()
            self.assertEqual(view.slider(key="wager_slider_" + intent).value, amount)
            self.assertEqual(view.session_state["pick_draft"]["stake_dollars"], amount)
            self.assertEqual(view.radio(key="active_section").value, "NBA Games")
        view.radio(key="active_section").set_value("Performance").run()
        view.radio(key="active_section").set_value("NBA Games").run()
        self.assertEqual(view.number_input(key="wager_" + intent).value, 28)
        self.assertEqual(view.slider(key="wager_slider_" + intent).value, 28)
        self.assertEqual(view.radio(key="winner_" + intent).value, "CHI")
        self.assertEqual(view.session_state["pick_draft"]["game"]["id"], 101)
        self.assertEqual(view.date_input(key="game_day_2026-27").value, self.day)
        self.assertEqual(len(view.exception), 0)
        self.assertEqual(self.service.snapshot("2026-27")[1]["balance"], 5000)
        self.posts.assert_not_called()

    def test_integer_controls_and_spinner_hiding_are_scoped(self):
        view, intent = self.open_pick()
        slider = view.slider(key="wager_slider_" + intent)
        number = view.number_input(key="wager_" + intent)
        self.assertEqual(slider.step, 1)
        self.assertEqual(number.step, 1)
        self.assertEqual(slider.proto.format, "$%d")
        self.assertEqual(number.proto.format, "%d")
        self.assertIs(type(slider.value), int)
        self.assertIs(type(number.value), int)
        self.assertFalse(number.disabled)
        self.assertIn("st-key-exact_wager", (PROJECT / "assets/classic_style.css").read_text())
        self.assertIn('data-testid="stNumberInputStepUp"', (PROJECT / "assets/classic_style.css").read_text())
        view.number_input(key="wager_" + intent).set_value(106).run()
        self.assertEqual(view.slider(key="wager_slider_" + intent).value, 106)
        self.assertFalse(view.error)
        self.posts.assert_not_called()

    def test_old_decimal_draft_is_normalized_without_losing_selection(self):
        view, intent = self.open_pick()
        view.radio(key="winner_" + intent).set_value("CHI").run()
        draft = dict(view.session_state["pick_draft"])
        draft.pop("wager_ui_version", None)
        draft.pop("stake_dollars", None)
        draft.update(stake=105.99, stake_cents=10599, wager_error="Old decimal-place error")
        view.session_state["pick_draft"] = draft
        view.session_state["wager_slider_" + intent] = 106.05
        view.session_state["wager_" + intent] = 105.99
        view.run()
        self.assertEqual(len(view.exception), 0)
        self.assertFalse(view.error)
        self.assertEqual(view.slider(key="wager_slider_" + intent).value, 105)
        self.assertEqual(view.number_input(key="wager_" + intent).value, 105)
        self.assertEqual(view.radio(key="active_section").value, "NBA Games")
        self.assertEqual(view.radio(key="winner_" + intent).value, "CHI")
        self.assertEqual(view.session_state["pick_draft"]["game"]["id"], 101)
        self.assertEqual(view.session_state["pick_draft"]["idempotency_key"], draft["idempotency_key"])
        self.posts.assert_not_called()

    def test_out_of_range_edits_are_rejected_without_widget_errors(self):
        view, intent = self.open_pick()
        for amount in (0, -1, 5001):
            view.number_input(key="wager_" + intent).set_value(amount).run()
            self.assertEqual(len(view.exception), 0)
            self.assertTrue(view.button(key="place_" + intent).disabled)
            self.assertEqual(view.slider(key="wager_slider_" + intent).value,
                             view.session_state["pick_draft"]["stake"])
            view.number_input(key="wager_" + intent).set_value(2).run()
            self.assertFalse(view.button(key="place_" + intent).disabled)
        self.posts.assert_not_called()

    def test_controls_limit_wager_to_available_bankroll(self):
        self.service.place("2026-27", 202, "CHI", "4987.66", str(uuid4()))
        view, intent = self.open_pick()
        self.assertEqual(view.number_input(key="wager_" + intent).max, 12)
        self.assertEqual(view.slider(key="wager_slider_" + intent).max, 12)
        self.assertEqual(view.number_input(key="wager_" + intent).value, 12)
        self.assertEqual(view.number_input(key="wager_" + intent).step, 1)
        self.posts.assert_not_called()

    def test_sub_dollar_and_zero_balance_are_handled(self):
        self.service.place("2026-27", 202, "CHI", "4999.99", str(uuid4()))
        view, intent = self.open_pick()
        self.assertEqual(view.number_input(key="wager_" + intent).value, 1)
        self.assertTrue(view.slider(key="wager_slider_" + intent).disabled)
        self.assertTrue(view.button(key="place_" + intent).disabled)
        self.service.place("2026-27", 202, "MIA", "0.01", str(uuid4()))
        st.cache_data.clear()
        view.run()
        self.assertTrue(view.number_input(key="wager_" + intent).disabled)
        self.assertTrue(view.button(key="place_" + intent).disabled)
        self.assertEqual(len(view.exception), 0)
        self.posts.assert_not_called()

    def test_saved_pick_indicators_survive_fresh_session_and_exclude_archive(self):
        # Two settled picks and a later open pick on the same synthetic game.
        self.service.place("2026-27", 101, "CHI", "10", str(uuid4()))
        self.service.place("2026-27", 101, "MIA", "5", str(uuid4()))
        self.provider.changes = {"status_state": "final", "home_score": 110, "away_score": 100}
        self.service.settle("2026-27")
        self.provider.changes = {}
        self.service.place("2026-27", 101, "CHI", "15", str(uuid4()))
        # A valid unrelated current-season pick must not appear on this card.
        self.service.place("2026-27", 202, "MIA", "3", str(uuid4()))
        for _ in range(2):
            st.cache_data.clear()
            view = AppTest.from_file(str(PROJECT / "streamlit_app.py"), default_timeout=30).run()
            view.radio(key="active_section").set_value("NBA Games").run()
            markup = "\n".join(m.value for m in view.markdown if "matchup-card" in m.value)
            self.assertIn("Pick placed", markup)
            self.assertIn("3 picks", markup)
            self.assertIn("CHI $25.00 (2 picks)", markup)
            self.assertIn("MIA $5.00", markup)
            self.assertNotIn("MIA $3.00", markup)
            self.assertNotIn('alt="LAL logo"', markup)
            self.assertNotIn('aria-label="LAL team abbreviation"', markup)
            self.assertTrue(any(b.key == "pick_daily_2026-27_101" for b in view.button))
            view.radio(key="active_section").set_value("My Picks").run()
            view.radio(key="active_section").set_value("NBA Games").run()
            self.assertTrue(any("CHI $25.00 (2 picks)" in m.value for m in view.markdown))
        self.posts.assert_not_called()

    def test_logos_render_on_games_selected_game_and_open_and_settled_picks(self):
        from test_team_logos import make_test_logos
        assets = Path(self.temp.name) / "synthetic_logos"
        make_test_logos(assets)
        self.service.place("2026-27", 202, "CHI", "10", str(uuid4()))
        self.provider.changes = {"status_state": "final", "home_score": 110, "away_score": 100}
        self.service.settle("2026-27")
        self.provider.changes = {}
        self.service.place("2026-27", 101, "MIA", "15", str(uuid4()))
        st.cache_data.clear()
        with patch("ui_components.ASSETS", assets):
            view, _ = self.open_pick()
            markup = "\n".join(m.value for m in view.markdown if 'class="matchup-card"' in m.value)
            # Both daily cards and the selected-game panel reuse this renderer.
            self.assertEqual(markup.count('alt="CHI logo"'), 2)
            self.assertEqual(markup.count('alt="MIA logo"'), 2)
            view.radio(key="active_section").set_value("My Picks").run()
            cards = [m.value for m in view.markdown if 'class="pick-card"' in m.value]
            self.assertEqual(len(cards), 2)
            for card in cards:
                self.assertIn('alt="CHI logo"', card)
                self.assertIn('alt="MIA logo"', card)
            self.assertTrue(any("Pending" in c for c in cards))
            self.assertTrue(any("Won" in c for c in cards))
            self.assertEqual(len(view.exception), 0)
            self.posts.assert_not_called()


class WagerPresentationTests(unittest.TestCase):
    def test_whole_dollar_validation_and_bankroll_caps(self):
        from wager_controls import whole_dollars, maximum_dollars, initial_wager
        self.assertEqual(maximum_dollars(20000), 5000)
        self.assertEqual(maximum_dollars(12.99), 12)
        self.assertEqual(maximum_dollars(0.99), 0)
        self.assertEqual(whole_dollars("106"), 106)
        self.assertEqual(initial_wager({"stake": 105.99}, 5000), 105)
        for value in ("nan", "inf", "invalid", "106.05", "105.99", "0.01"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                whole_dollars(value)

    def test_pick_summary_matches_game_and_escapes_team_text(self):
        from ui_components import placed_pick_summary
        rows = [{"game_id": "101", "pick": "<script>", "stake": "12.34"},
                {"game_id": "202", "pick": "MIA", "stake": "99"}]
        self.assertIn("&lt;script&gt; $12.34", placed_pick_summary(101, rows))
        self.assertNotIn("<script>", placed_pick_summary(101, rows))
        self.assertEqual(placed_pick_summary(999, rows), "")
