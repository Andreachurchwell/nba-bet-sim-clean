"""Count actual transport calls, never hit BALLDONTLIE or saved wallets."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.api.services.live_games import LiveNBAClient, FeedUnavailable, normalize_game
from backend.api.services.game_eligibility import display_eligibility
from backend.api.services.betting import BettingService, BetError
from test_live_games import fixture, response


def verified_response(raw=None):
    result=Mock(status_code=200,headers={})
    result.json.return_value={'data': raw or fixture(home_team={'id':4,'abbreviation':'CHI'},visitor_team={'id':16,'abbreviation':'MIA'})}
    return result


class ProviderRequestCounts(TestCase):
    def setUp(self):
        self.transport=Mock()
        self.transport.get.return_value=response([fixture()])
        self.client=LiveNBAClient(key='test-not-real',transport=self.transport)
        self.day=date(2026,10,29)  # Thursday, so next/previous stay in one week.

    def test_form_reruns_and_same_week_dates_reuse_one_provider_response(self):
        clock=[0]
        with patch('backend.api.services.live_games.monotonic',side_effect=lambda:clock[0]):
            for second in (0,16,32,48,64):
                clock[0]=second
                self.client.read('2026-27',self.day,0,0)
            self.client.read('2026-27',self.day+timedelta(days=1),0,0)
            self.client.read('2026-27',self.day-timedelta(days=1),0,0)
            self.assertEqual(self.transport.get.call_count,1)
            clock[0]=301
            self.client.read('2026-27',self.day,0,0)
            self.assertEqual(self.transport.get.call_count,2)

    def test_new_week_and_concurrent_reads_are_bounded(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:self.client.read('2026-27',self.day,0,0),range(8)))
        self.assertEqual(self.transport.get.call_count,1)
        self.client.read('2026-27',self.day+timedelta(days=7),0,0)
        self.assertEqual(self.transport.get.call_count,2)

    def test_confirmation_does_not_reuse_schedule_or_successful_verification(self):
        self.client.read('2026-27',self.day,0,0)
        self.transport.get.return_value=verified_response()
        self.client.verify_game(101)
        self.client.verify_game(101)
        self.assertEqual(self.transport.get.call_count,3)
        self.assertTrue(self.transport.get.call_args.args[0].endswith('/101'))

    def test_retry_after_seconds_blocks_all_dates_and_verifications(self):
        limited=response([],status=429);limited.headers={'Retry-After':'120'}
        self.transport.get.return_value=limited
        clock=[0]
        with patch('backend.api.services.live_games.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(FeedUnavailable) as first:self.client.verify_game(101)
            self.assertEqual(first.exception.retry_after,120)
            clock[0]=60
            for game_id in (101,102,103):
                with self.assertRaises(FeedUnavailable) as again:self.client.verify_game(game_id)
                self.assertEqual(again.exception.retry_after,60)
            for day in (self.day,self.day+timedelta(days=10)):
                with self.assertRaises(FeedUnavailable):self.client.read('2026-27',day,0,0)
            self.assertEqual(self.transport.get.call_count,1)
            clock[0]=121;self.transport.get.return_value=verified_response()
            self.client.verify_game(101)
            self.assertEqual(self.transport.get.call_count,2)

    def test_retry_after_http_date_and_missing_or_invalid_header(self):
        for header,minimum in ((format_datetime(datetime.now(timezone.utc)+timedelta(seconds=120)),118),('not-a-date',60),(None,60)):
            client=LiveNBAClient(key='test',transport=self.transport)
            limited=response([],status=429);limited.headers={} if header is None else {'Retry-After':header}
            self.transport.get.return_value=limited
            with self.assertRaises(FeedUnavailable) as blocked:client.verify_game(101)
            self.assertGreaterEqual(blocked.exception.retry_after,minimum)

    def test_schedule_429_then_bet_retry_and_date_changes_make_no_more_calls(self):
        limited=response([],status=429);limited.headers={'Retry-After':'90'}
        self.transport.get.return_value=limited
        with self.assertRaises(FeedUnavailable):self.client.read('2026-27',self.day,0,0)
        for _ in range(4):
            with self.assertRaises(FeedUnavailable):self.client.verify_game(101)
            with self.assertRaises(FeedUnavailable):self.client.read('2026-27',self.day+timedelta(days=8),0,0)
        self.assertEqual(self.transport.get.call_count,1)

    def test_outage_backoff_and_safe_stale_display(self):
        self.client.read('2026-27',self.day,0,0)
        key=next(iter(self.client.cache))
        stamp,rows,fetched=self.client.cache[key]
        self.client.cache[key]=(stamp-301,rows,fetched)
        failed=response([],status=503);failed.headers={'Retry-After':'45'}
        self.transport.get.return_value=failed
        old=self.client.read('2026-27',self.day,0,0)
        self.assertTrue(old['stale'])
        self.assertTrue(old['warning'])
        with self.assertRaises(FeedUnavailable):self.client.verify_game(101)
        self.assertEqual(self.transport.get.call_count,2)

    def test_browsing_cache_never_authorizes_an_old_bet_verification(self):
        now=datetime.now(timezone.utc)
        game=normalize_game(fixture(datetime=(now+timedelta(hours=2)).isoformat()))
        old_stamp=(now-timedelta(seconds=60)).isoformat()
        preview=display_eligibility(game,old_stamp,now=now)
        self.assertTrue(preview['pick_eligible'])  # Opening the form is harmless.
        with self.assertRaises(BetError):
            BettingService(None,now=lambda:now)._eligible({**game,'verified_at':old_stamp})

    def test_form_flow_has_one_schedule_and_one_required_verification(self):
        clock=[0]
        with patch('backend.api.services.live_games.monotonic',side_effect=lambda:clock[0]):
            for second in (0,16,32,48):
                clock[0]=second;self.client.read('2026-27',self.day,0,0)
            self.transport.get.return_value=verified_response()
            self.client.verify_game(101)
            clock[0]=64;self.transport.get.return_value=response([fixture()])
            self.client.read('2026-27',self.day,0,0)
        self.assertEqual(self.transport.get.call_count,2)
