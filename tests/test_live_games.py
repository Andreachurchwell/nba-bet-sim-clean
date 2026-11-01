"""Synthetic provider payloads are test fixtures only; never served as real games."""
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.services.live_games import LiveNBAClient, FeedUnavailable, normalize_game, get_live_client
from backend.api.services.season_reads import get_store
from backend.api.services.season_store import SeasonStore
from ui_components import matchup_card, team_badge, image_uri, ASSETS


def fixture(**changes):
    value = {"id": 101, "season": 2026, "date": "2026-10-31", "datetime": "2026-11-01T00:00:00Z",
             "status_state": "scheduled", "status": "7:00 pm ET", "home_team_score": 0, "visitor_team_score": 0,
             "home_team": {"id": 4, "abbreviation": "CHI", "full_name": "Chicago Bulls"},
             "visitor_team": {"id": 16, "abbreviation": "MIA", "full_name": "Miami Heat"}}
    return {**value, **changes}


def response(rows, status=200, cursor=None):
    result = Mock(status_code=status, headers={})
    result.json.return_value = {"data": rows, "meta": {"next_cursor": cursor}}
    return result


class LiveFeedTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.transport.get.return_value = response([fixture()])
        self.client = LiveNBAClient(key="test-only-not-a-real-key", transport=self.transport)
        self.anchor = date(2026, 10, 31)

    def test_season_dates_timezone_and_no_scheduled_zero_scores(self):
        feed = self.client.read("2026-27", self.anchor)
        game = feed["today"][0]
        self.assertEqual(game["date"], "2026-10-31")
        self.assertIn("07:00 PM CDT", game["tipoff_local"])
        self.assertIsNone(game["home_score"])
        params = self.transport.get.call_args.kwargs["params"]
        self.assertEqual(params["seasons[]"], 2026)
        self.assertEqual(params["start_date"], "2026-10-27")
        self.assertEqual(params["end_date"], "2026-11-08")

    def test_dst_and_missing_or_naive_time(self):
        self.assertIn("01:30 AM CST", normalize_game(fixture(datetime="2026-11-01T07:30:00Z"))["tipoff_local"])
        for timestamp in (None, "2026-10-31", "2026-10-31T12:00:00", "bad"):
            game = normalize_game(fixture(datetime=timestamp))
            self.assertFalse(game["time_verified"])
            self.assertIn("TBD", game["tipoff_local"])

    def test_lifecycle_and_missing_scores(self):
        for state, expected in (("postponed", "Postponed"), ("canceled", "Canceled"), ("delayed", "Delayed"),
                                ("unknown", "Status unavailable")):
            self.assertEqual(normalize_game(fixture(status_state=state))["status"], expected)
        final = normalize_game(fixture(status_state="final", home_team_score=102, visitor_team_score=99))
        self.assertEqual((final["home_score"], final["away_score"]), (102, 99))
        self.assertIsNone(normalize_game(fixture(status_state="final", home_team_score=None))["home_score"])
        self.assertIsNone(normalize_game(fixture(status_state="final", home_team_score=float("nan")))["home_score"])

    def test_cache_keys_and_defensive_copies(self):
        feed = self.client.read("2026-27", self.anchor)
        feed["games"][0]["home"] = "BAD"
        self.assertEqual(self.client.read("2026-27", self.anchor)["games"][0]["home"], "CHI")
        self.assertEqual(self.transport.get.call_count, 1)
        self.client.read("2026-27", date(2026, 11, 1))
        self.assertEqual(self.transport.get.call_count, 2)
        with self.assertRaises(FeedUnavailable):
            self.client.read("2025-26", self.anchor)
        self.assertEqual(self.transport.get.call_count, 2)

    def test_pagination_and_deduplication(self):
        self.transport.get.side_effect = [response([fixture()], cursor=5), response([fixture(), fixture(id=102)])]
        result = self.client.read("2026-27", self.anchor)
        self.assertEqual(len(result["games"]), 2)
        self.assertEqual(self.transport.get.call_args.kwargs["params"]["cursor"], 5)

    def test_failed_pagination_is_not_presented_as_complete(self):
        self.transport.get.side_effect = [response([fixture()], cursor=5), response([], status=503)]
        with self.assertRaises(FeedUnavailable):
            self.client.read("2026-27", self.anchor)
        self.assertEqual(self.client.cache, {})

    def test_wrong_season_rejected(self):
        self.transport.get.return_value = response([fixture(season=2025)])
        with self.assertRaisesRegex(FeedUnavailable, "season verification"):
            self.client.read("2026-27", self.anchor)

    def test_provider_failures_sanitized_and_throttled(self):
        for status in (401, 403, 429, 500):
            client = LiveNBAClient(key="secret", transport=self.transport)
            self.transport.get.return_value = response([], status=status)
            with self.assertRaises(FeedUnavailable) as raised:
                client.read("2026-27", self.anchor)
            self.assertNotIn("secret", str(raised.exception))
            calls = self.transport.get.call_count
            with self.assertRaises(FeedUnavailable):
                client.read("2026-27", self.anchor)
            self.assertEqual(self.transport.get.call_count, calls)

    def test_timeout_missing_credentials_and_unapproved_endpoint(self):
        self.transport.get.side_effect = requests.Timeout("request contains secret")
        with self.assertRaisesRegex(FeedUnavailable, "Unable to reach"):
            self.client.read("2026-27", self.anchor)
        for key, endpoint in (("", "https://api.balldontlie.io/v1/games"), ("secret", "https://evil.invalid/v1/games")):
            client = LiveNBAClient(key=key, endpoint=endpoint, transport=self.transport)
            calls = self.transport.get.call_count
            with self.assertRaises(FeedUnavailable):
                client.read("2026-27", self.anchor)
            self.assertEqual(self.transport.get.call_count, calls)

    def test_stale_cache_labeled_and_bounded(self):
        self.client.read("2026-27", self.anchor)
        key = next(iter(self.client.cache))
        stamp, rows, fetched = self.client.cache[key]
        self.client.cache[key] = (stamp - self.client.TTL - 1, rows, fetched)
        self.transport.get.return_value = response([], status=503)
        stale = self.client.read("2026-27", self.anchor)
        self.assertTrue(stale["stale"])
        self.assertTrue(stale["warning"])
        self.client.cache[key] = (stamp - 4000, rows, fetched)
        with self.assertRaises(FeedUnavailable):
            self.client.read("2026-27", self.anchor)

    def test_groups_and_empty_schedule(self):
        self.transport.get.return_value = response([
            fixture(), fixture(id=102, date="2026-11-02", datetime="2026-11-02T23:00:00Z"),
            fixture(id=103, date="2026-10-30", datetime="2026-10-30T23:00:00Z", status_state="final")])
        feed = self.client.read("2026-27", self.anchor)
        self.assertEqual([len(feed[k]) for k in ("today", "upcoming", "recent_finals")], [1, 1, 1])
        self.client.cache.clear()
        self.transport.get.return_value = response([])
        self.assertEqual(self.client.read("2026-27", self.anchor)["games"], [])

    def test_endpoint_preserves_all_storage_and_archive_skips_provider(self):
        from test_season_store import snapshot, isolated_seasons
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "seasons"
            isolated_seasons(root)
            before = snapshot(root)
            app.dependency_overrides[get_store] = lambda: SeasonStore(root)
            app.dependency_overrides[get_live_client] = lambda: self.client
            try:
                with TestClient(app) as client:
                    archived = client.get("/games/live", params={"season": "2025-26"})
                    self.assertEqual(archived.status_code, 200)
                    self.transport.get.assert_not_called()
                    current = client.get("/games/live", params={"season": "2026-27", "date": "2026-10-31"})
                    self.assertTrue(current.json()["verified"])
                    self.assertEqual(client.get("/games/live?season=bad").status_code, 400)
                    self.assertEqual(client.get("/games/live?date=bad").status_code, 422)
                    from backend.api.core import config
                    with patch.object(config, "MAINTENANCE_READ_ONLY", True):
                        self.assertEqual(client.post("/bets/settle").status_code, 503)
                self.assertEqual(snapshot(root), before)
            finally:
                app.dependency_overrides.clear()

    def test_fresh_game_verification_uses_uncached_single_game_endpoint(self):
        self.client.read('2026-27',self.anchor)
        raw=fixture(home_team={'id':4,'abbreviation':'CHI','full_name':'Chicago Bulls'},
                    visitor_team={'id':16,'abbreviation':'MIA','full_name':'Miami Heat'})
        reply=Mock(status_code=200,headers={})
        reply.json.return_value={'data':raw}
        self.transport.get.return_value=reply
        for _ in range(2):
            game=self.client.verify_game(101)
            self.assertEqual(game['home_team_id'],4)
            self.assertEqual(game['away_team_id'],16)
            self.assertTrue(game['verified_at'])
        self.assertEqual(self.transport.get.call_count,3)
        self.assertTrue(self.transport.get.call_args.args[0].endswith('/games/101'))
        self.assertEqual(self.transport.get.call_args.kwargs['headers']['Cache-Control'],'no-cache')

    def test_fresh_game_rejects_stale_inconsistent_or_missing_fields(self):
        raw=fixture(home_team={'id':4,'abbreviation':'CHI'},visitor_team={'id':16,'abbreviation':'MIA'})
        cases=[({'data':raw},{'Age':'16'}),({'data':{**raw,'season':2025}},{}),
               ({'data':{**raw,'id':999}},{}),({'data':{**raw,'period':1}},{}),
               ({'data':{**raw,'status':'Final'}},{}),({'data':{**raw,'home_team_id':99}},{}),
               ({'data':{**raw,'home_team':{'abbreviation':'CHI'}}},{})]
        for body,headers in cases:
            reply=Mock(status_code=200,headers=headers);reply.json.return_value=body
            self.transport.get.return_value=reply
            with self.assertRaises(FeedUnavailable):self.client.verify_game(101)


class MatchupMarkupTests(unittest.TestCase):
    def test_ui_copy_is_concise_and_preserves_virtual_money_disclosure(self):
        from ui_components import brand_header,story_card,pending_pick_card,empty_card
        markup=' '.join([brand_header(),story_card('2026-27',False),story_card('2025-26',True),
                         pending_pick_card({'status':'open','pick':'CHI','stake':'25','matchup':'MIA @ CHI'}),
                         empty_card('No picks yet','Select a game to place a virtual pick.')])
        for old in ('A LITTLE STRATEGY','HER COURT','HER CALL','main-character','courtside confidence',
                    'Every pick has a story','THE SHEBETS WAY','YOUR COURTSIDE CALL'):
            self.assertFalse(old in markup, 'Removed slogan should not reappear: '+old)
        self.assertTrue('Original SheBETS logo' in markup)
        self.assertTrue('Virtual money only' in markup)
        self.assertTrue('PICK DETAILS' in markup)

    def test_abbreviation_fallback_and_missing_asset(self):
        badge = team_badge("CHI")
        self.assertIn('class="team-badge"', badge)
        self.assertNotIn("<img", badge)
        self.assertIsNone(image_uri(str(ASSETS / "missing.png")))
        self.assertIsNone(image_uri("https://unapproved.invalid/logo.png"))

    def test_markup_escapes_provider_text_and_omits_unverified_score(self):
        game = normalize_game(fixture())
        game["home_name"] = '<script>alert("x")</script>'
        markup = matchup_card(game)
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("07:00 PM CDT", markup)
        self.assertIn("VIEW ONLY", markup)


if __name__ == "__main__":
    unittest.main()
