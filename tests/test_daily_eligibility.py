import unittest
from datetime import datetime,date,timezone,timedelta
from unittest.mock import Mock

from backend.api.services.game_eligibility import pick_reason,display_eligibility
from backend.api.services.live_games import LiveNBAClient,normalize_game
from backend.api.services.live_games import FeedUnavailable
from backend.api.services.betting import BettingService,BetError
from ui_components import matchup_card
from test_live_games import fixture,response


class DailyEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,31,22,3,54,tzinfo=timezone.utc)
        self.raw=fixture(datetime='2026-10-31T23:00:00Z')

    def annotated(self,raw=None,stale=False):
        return display_eligibility(normalize_game(raw or self.raw),self.now.isoformat(),stale,self.now)

    def test_philadelphia_scheduled_but_tipoff_passed(self):
        game=self.annotated(fixture(datetime='2026-10-31T22:00:00Z'))
        self.assertFalse(game['pick_eligible'])
        self.assertIn('already started',game['pick_reason'])
        self.assertIn('05:00 PM CDT',game['tipoff_local'])
        self.assertTrue('already started' in matchup_card(game))
        with self.assertRaises(BetError):
            BettingService(None,now=lambda:self.now)._eligible({**game,'verified_at':self.now.isoformat()})

    def test_shared_frontend_backend_rules(self):
        changes=[({},True),({'status_state':'in_progress'},False),({'status_state':'final'},False),
                 ({'status_state':'postponed'},False),({'status_state':'canceled'},False),
                 ({'datetime':None},False),({'datetime':'2026-10-31T22:00:00Z'},False)]
        for change,expected in changes:
            raw={**self.raw,**change};game=self.annotated(raw)
            self.assertEqual(game['pick_eligible'],expected)
            candidate={**game,'status_state':game['eligibility_state'],'verified_at':self.now.isoformat()}
            self.assertEqual(not bool(pick_reason(candidate,self.now)),expected)
            if expected:BettingService(None,now=lambda:self.now)._eligible(candidate)
            else:
                with self.assertRaises(BetError):BettingService(None,now=lambda:self.now)._eligible(candidate)

    def test_missing_identity_stale_data_and_inconsistent_provider_block(self):
        missing=fixture(home_team={'abbreviation':'CHI'},datetime='2026-10-31T23:00:00Z')
        self.assertFalse(self.annotated(missing)['pick_eligible'])
        self.assertFalse(self.annotated(stale=True)['pick_eligible'])
        bad={**self.raw,'period':1}
        self.assertFalse(self.annotated(bad)['pick_eligible'])
        stale=display_eligibility(normalize_game(self.raw),(self.now-timedelta(seconds=301)).isoformat(),now=self.now)
        self.assertFalse(stale['pick_eligible'])

    def test_selected_central_date_filters_utc_boundaries(self):
        rows=[fixture(id=1,datetime='2026-10-31T04:59:59Z'),
              fixture(id=2,datetime='2026-10-31T05:00:00Z'),
              fixture(id=3,datetime='2026-11-01T04:59:59Z'),
              fixture(id=4,datetime='2026-11-01T05:00:00Z')]
        transport=Mock();transport.get.return_value=response(rows)
        feed=LiveNBAClient(key='test',transport=transport).read('2026-27',date(2026,10,31),0,0)
        self.assertEqual([g['id'] for g in feed['games']],[2,3])
        self.assertEqual(feed['upcoming'],[])
        self.assertEqual({g['date'] for g in feed['today']},{'2026-10-31'})

    def test_missing_and_unknown_times_have_human_explanations(self):
        for change,reason in (({'datetime':None},'Tipoff time unavailable'),
                              ({'status_state':'in_progress'},'Game in progress'),
                              ({'status_state':'final'},'Final'),
                              ({'status_state':'postponed'},'Game postponed')):
            game=self.annotated({**self.raw,**change})
            self.assertIn(reason,game['pick_reason'])
            self.assertTrue(reason in matchup_card(game))

    def test_stale_provider_schedule_never_grants_eligibility(self):
        transport=Mock();reply=response([self.raw]);reply.headers={'Age':'60'}
        transport.get.return_value=reply
        with self.assertRaises(FeedUnavailable):
            LiveNBAClient(key='test',transport=transport).read('2026-27',date(2026,10,31),0,0)
