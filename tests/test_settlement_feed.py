"""Fresh settlement batches use synthetic provider responses, never live picks."""
import unittest
from datetime import date
from unittest.mock import Mock

from backend.api.services.live_games import LiveNBAClient, FeedUnavailable
from test_live_games import fixture, response


def final(game_id, **kwargs):
    return fixture(id=game_id, status_state="final", status="Final",
                   home_team_score=110, visitor_team_score=100, **kwargs)


class SettlementFeedTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.client = LiveNBAClient(key="synthetic", transport=self.transport)
        self.targets = {i: "2026-11-02" for i in range(101, 110)}

    def test_nine_games_one_fresh_request_with_timezone_buffer(self):
        self.transport.get.return_value = response([final(i) for i in self.targets])
        result = dict(self.client.verify_games(self.targets))
        self.assertEqual(set(result), set(self.targets))
        self.assertEqual(self.transport.get.call_count, 1)
        kwargs = self.transport.get.call_args.kwargs
        self.assertEqual(kwargs["params"]["dates[]"], ["2026-11-01", "2026-11-02", "2026-11-03"])
        self.assertEqual(kwargs["headers"]["Cache-Control"], "no-cache")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(all(g["status_state"] == "final" for g in result.values()))

    def test_browsing_cache_cannot_approve_results(self):
        self.client.cache[("2026-27", "2026-11-01", "2026-11-03")] = (0, [final(101)])
        self.transport.get.return_value = response([fixture(id=101, status_state="postponed")])
        game = dict(self.client.verify_games({101: "2026-11-02"}))[101]
        self.assertEqual(game["status_state"], "postponed")
        self.assertEqual(self.transport.get.call_count, 1)

    def test_stale_or_unsafe_response_fails_closed(self):
        stale = response([final(101)])
        stale.headers = {"Age": "16"}
        for reply in (stale, response([final(101, season=2025)]),
                      response([final(101), final(101)]), response([]),
                      response([final(101, home_team={"id": 16, "abbreviation": "MIA"})])):
            self.transport.get.return_value = reply
            with self.subTest(reply=reply), self.assertRaises(FeedUnavailable):
                list(self.client.verify_games({101: "2026-11-02"}))

    def test_pagination_and_nonoverlapping_dates_visit_all_games_once(self):
        self.transport.get.side_effect = [response([final(101)], cursor=9),
                                          response([final(102)]), response([final(103)])]
        result = list(self.client.verify_games({101: "2026-11-02", 102: "2026-11-02", 103: "2026-12-01"}))
        self.assertEqual([i for i, _ in result], [101, 102, 103])
        self.assertEqual(self.transport.get.call_count, 3)
        self.assertEqual(self.transport.get.call_args_list[1].kwargs["params"]["cursor"], 9)

    def test_provider_failure_after_page_stops_without_retry(self):
        error = response([], status=503)
        error.headers = {"Retry-After": "45"}
        self.transport.get.side_effect = [response([final(101)], cursor=9), error]
        stream = self.client.verify_games({101: "2026-11-02", 102: "2026-11-02"})
        self.assertEqual(next(stream)[0], 101)
        with self.assertRaises(FeedUnavailable) as caught:
            next(stream)
        self.assertEqual(caught.exception.retry_after, 45)
        self.assertEqual(caught.exception.code, "provider_error")
        with self.assertRaises(FeedUnavailable):
            self.client.read("2026-27", date(2026, 11, 2), 0, 0)
        with self.assertRaises(FeedUnavailable):
            self.client.verify_game(102)
        self.assertEqual(self.transport.get.call_count, 2)

    def test_missing_date_result_gets_fresh_id_lookup_for_rescheduled_game(self):
        single = Mock(status_code=200, headers={})
        single.json.return_value = {"data": fixture(id=101, status_state="canceled")}
        self.transport.get.side_effect = [response([]), single]
        game = dict(self.client.verify_games({101: "2026-11-02"}))[101]
        self.assertEqual(game["status_state"], "canceled")
        self.assertEqual(self.transport.get.call_count, 2)
        self.assertTrue(self.transport.get.call_args.args[0].endswith("/games/101"))

    def test_empty_work_needs_no_configuration_or_request(self):
        client = LiveNBAClient(key="", transport=self.transport)
        self.assertEqual(list(client.verify_games({})), [])
        self.transport.get.assert_not_called()
