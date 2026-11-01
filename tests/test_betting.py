"""Financial tests use isolated temporary data and synthetic provider responses."""
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.api.main import app
from backend.api.core import config
from backend.api.services.betting import BettingService, BetError, atomic_write, season_lock
from backend.api.services.live_games import FeedUnavailable, LiveNBAClient, get_live_client
from backend.api.services.season_reads import get_store
from backend.api.services.season_store import SeasonStore, ArchivedSeasonError
from test_season_store import PROJECT, snapshot, isolated_seasons


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.changes = {}
        self.unavailable = False

    def verify_game(self, game_id):
        self.calls += 1
        if self.unavailable:
            raise FeedUnavailable("Provider unavailable in isolated tests")
        now = datetime.now(timezone.utc)
        return {"id": game_id, "provider_season": 2026, "date": now.date().isoformat(),
                "datetime_utc": (now + timedelta(hours=2)).isoformat(), "time_verified": True,
                "verified_at": now.isoformat(), "status_state": "scheduled", "status": "Scheduled",
                "home": "CHI", "away": "MIA", "home_name": "Chicago Bulls", "away_name": "Miami Heat",
                "home_team_id": 4, "away_team_id": 16, "home_score": None, "away_score": None,
                "tipoff_local": "Test-only verified tipoff", **self.changes}


class BettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "seasons"
        isolated_seasons(self.root)
        self.store = SeasonStore(self.root)
        self.provider = FakeProvider()
        self.service = BettingService(self.store, self.provider)
        self.archive_before = snapshot(self.root / "2025-26")

    def tearDown(self):
        self.assertEqual(snapshot(self.root / "2025-26"), self.archive_before)

    def place(self, key=None, pick="CHI", stake="50.00", game_id=101):
        return self.service.place("2026-27", game_id, pick, stake, key or str(uuid4()))

    def balance(self):
        return self.service.snapshot("2026-27")[1]["balance"]

    def test_place_persist_and_retry_without_provider(self):
        key = str(uuid4())
        first = self.place(key)
        self.assertEqual(first["balance"], 4950)
        self.provider.unavailable = True
        again = self.place(key)
        self.assertTrue(again["repeated"])
        self.assertEqual(again["bet_id"], first["bet_id"])
        restarted = BettingService(SeasonStore(self.root), FakeProvider())
        rows, wallet = restarted.snapshot("2026-27")
        self.assertEqual(wallet["balance"], 4950)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open")

    def test_archive_impossible(self):
        for operation in (lambda: self.service.place("2025-26", 101, "CHI", "5", str(uuid4())),
                          lambda: self.service.settle("2025-26")):
            with self.assertRaises(ArchivedSeasonError): operation()

    def test_bad_stakes_and_insufficient_funds(self):
        for stake in ("-1", "0", "5000.01", "1.001", "NaN", "Infinity", True, "bad"):
            with self.subTest(stake=stake), self.assertRaises(BetError): self.place(stake=stake)
        self.place(stake="4999")
        with self.assertRaises(BetError): self.place(stake="2")
        self.assertEqual(self.balance(), 1)

    def test_ineligible_games_and_missing_or_stale_tipoff(self):
        for state in ("final", "in_progress", "postponed", "canceled", "delayed", "unknown", "suspended"):
            self.provider.changes = {"status_state": state}
            with self.assertRaises(BetError): self.place()
        for changes in ({"time_verified": False}, {"datetime_utc": None},
                        {"datetime_utc": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
                        {"verified_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()},
                        {"provider_season": 2025}, {"id": 999}):
            self.provider.changes = changes
            with self.assertRaises(BetError): self.place()
        self.assertEqual(self.balance(), 5000)

    def test_wrong_pick_idempotency_conflict_and_api_failure(self):
        with self.assertRaises(BetError): self.place(pick="LAL")
        key = str(uuid4()); self.place(key)
        with self.assertRaises(BetError) as error: self.place(key, stake="20")
        self.assertEqual(error.exception.status, 409)
        self.provider.unavailable = True
        with self.assertRaises(FeedUnavailable): self.place()
        self.assertEqual(self.balance(), 4950)

    def test_wins_losses_and_repeat_settlement(self):
        self.place(pick="CHI"); self.place(pick="MIA")
        self.provider.changes = {"status_state": "final", "home_score": 110, "away_score": 100}
        first = self.service.settle("2026-27")
        self.assertEqual(first["settled"], 2)
        self.assertEqual(first["balance"], 5000)
        self.assertEqual(self.service.settle("2026-27")["settled"], 0)
        rows, _ = self.service.snapshot("2026-27")
        self.assertEqual([r["status"] for r in rows], ["won", "lost"])
        self.assertEqual([r["payout"] for r in rows], ["100.00", "0"])
        self.assertTrue(all(r["settled_at"] and r["settled_verified_at"] for r in rows))

    def test_postponed_pending_and_canceled_refund_once(self):
        self.place()
        self.provider.changes = {"status_state": "postponed"}
        self.assertEqual(self.service.settle("2026-27")["settled"], 0)
        self.assertEqual(self.balance(), 4950)
        self.provider.changes = {"status_state": "canceled"}
        self.assertEqual(self.service.settle("2026-27")["balance"], 5000)
        self.assertEqual(self.service.settle("2026-27")["settled"], 0)
        self.assertEqual(self.service.snapshot("2026-27")[0][0]["status"], "void")

    def test_incomplete_tied_changed_team_or_failed_results_stay_pending(self):
        self.place()
        for changes in ({"status_state": "final", "home_score": None, "away_score": 5},
                        {"status_state": "final", "home_score": 100, "away_score": 100},
                        {"status_state": "final", "home_score": 110, "away_score": 100, "home_team_id": 99}):
            self.provider.changes = changes
            self.assertEqual(self.service.settle("2026-27")["settled"], 0)
        self.provider.unavailable = True
        result = self.service.settle("2026-27")
        self.assertTrue(result["warnings"])
        self.assertEqual(self.balance(), 4950)

    def test_concurrent_duplicate_and_overspend(self):
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.place(key), range(8)))
        self.assertEqual(len({r["bet_id"] for r in results}), 1)
        self.assertEqual(self.balance(), 4950)
        def attempt(_):
            try: self.place(stake="4000"); return True
            except BetError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, range(2))), 1)
        self.assertEqual(self.balance(), 950)

    def test_interrupted_commits_roll_forward_exactly_once(self):
        # Interrupt each materialization boundary after the journal commit point.
        for boundary in (2, 3, 4):
            with self.subTest(boundary=boundary):
                key = str(uuid4()); calls = 0
                def interrupted(path, content):
                    nonlocal calls
                    calls += 1
                    if calls == boundary: raise OSError("simulated process interruption")
                    return atomic_write(path, content)
                prior = self.balance()
                with patch("backend.api.services.betting.atomic_write", side_effect=interrupted):
                    with self.assertRaises(OSError): self.place(key)
                restarted = BettingService(SeasonStore(self.root), self.provider)
                self.assertEqual(restarted.snapshot("2026-27")[1]["balance"], prior - 50)
                self.assertTrue(restarted.place("2026-27", 101, "CHI", "50.00", key)["repeated"])

    def test_failed_journal_commit_changes_no_balance(self):
        with patch("backend.api.services.betting.atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.place()
        self.assertEqual(self.balance(), 5000)
        self.assertEqual(self.service.snapshot("2026-27")[0], [])

    def test_interrupted_settlement_recovery_never_double_pays(self):
        self.place()
        self.provider.changes = {"status_state": "final", "home_score": 110, "away_score": 100}
        calls = 0
        def interrupted(path, content):
            nonlocal calls
            calls += 1
            if calls == 3: raise OSError("interrupted before wallet")
            return atomic_write(path, content)
        with patch("backend.api.services.betting.atomic_write", side_effect=interrupted):
            with self.assertRaises(OSError): self.service.settle("2026-27")
        restarted = BettingService(SeasonStore(self.root), self.provider)
        self.assertEqual(restarted.snapshot("2026-27")[1]["balance"], 5050)
        self.assertEqual(restarted.settle("2026-27")["settled"], 0)

    def test_concurrent_settlement_pays_only_once(self):
        self.place()
        self.provider.changes = {"status_state": "final", "home_score": 110, "away_score": 100}
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda _:self.service.settle('2026-27'),range(5)))
        self.assertEqual(sum(result['settled'] for result in results),1)
        self.assertEqual(self.balance(),5050)

    def test_settlement_cursor_reaches_games_after_postponed_batch(self):
        for game_id in (101,102,103,104):self.place(game_id=game_id)
        self.provider.changes={'status_state':'postponed'}
        first=self.service.settle('2026-27')
        self.assertEqual(first['checked_games'],3)
        self.assertEqual(first['next_after_game_id'],103)
        self.provider.changes={'status_state':'final','home_score':110,'away_score':100}
        second=self.service.settle('2026-27',first['next_after_game_id'])
        self.assertEqual(second['settled'],1)
        self.assertEqual(second['next_after_game_id'],104)

    def test_corrupt_or_external_changes_block_without_reset(self):
        self.place()
        wallet = self.root / "2026-27/wallet.json"
        wallet.write_text('{"balance": 1234}')
        with self.assertRaises(BetError): self.service.snapshot("2026-27")
        self.assertEqual(json.loads(wallet.read_text())["balance"], 1234)

    def test_cross_process_single_writer(self):
        # Separate interpreters have no shared Python lock; the OS file lock
        # must prevent both $4,000 picks from spending the same $5,000 balance.
        code = '''
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_betting import FakeProvider
from backend.api.services.betting import BettingService,BetError
from backend.api.services.season_store import SeasonStore
try:
 result=BettingService(SeasonStore(Path(sys.argv[1])),FakeProvider()).place('2026-27',101,'CHI','4000',sys.argv[2])
 print('RESULT:ok')
except BetError: print('RESULT:rejected')
'''
        children = [subprocess.Popen([sys.executable, "-B", "-c", code, str(self.root), str(uuid4())],
                                     cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = []
        for process in children:
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, err)
            outputs.append(out)
        self.assertEqual(sum("RESULT:ok" in out for out in outputs), 1)
        self.assertEqual(self.balance(), 1000)

    def test_api_guard_and_explicit_season_validation(self):
        app.dependency_overrides[get_store] = lambda: self.store
        app.dependency_overrides[get_live_client] = lambda: self.provider
        try:
            with patch.object(config, "MAINTENANCE_READ_ONLY", False), patch.object(config, "BETTING_ENABLED", True), patch.object(config, "SETTLEMENT_ENABLED", True), TestClient(app) as client:
                payload = {"season_id": "2026-27", "game_id": 101, "pick": "CHI", "stake": "50.00", "idempotency_key": str(uuid4())}
                self.assertEqual(client.post('/bets/?season=2026-27', json=payload).status_code, 200)
                self.assertEqual(client.post('/bets/?season=2026-27', json=payload).json()["balance"], 4950)
                self.assertEqual(client.post('/bets/?season=2025-26', json=payload).status_code, 403)
                self.assertEqual(client.post('/bets/settle?season=2025-26').status_code, 403)
                self.assertEqual(client.post('/bets/', json=payload).status_code, 400)
                for stake in (0, -1, "NaN", "5000.01", "1.001"):
                    self.assertEqual(client.post('/bets/?season=2026-27', json={**payload, "stake": stake}).status_code, 422)
                self.assertEqual(client.get('/bets/wallet').json()['balance'], 4950)
        finally:
            app.dependency_overrides.clear()

    def test_process_lock_rejects_a_second_writer_while_held(self):
        code = '''
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_betting import FakeProvider
from backend.api.services.betting import BettingService,BetError
from backend.api.services.season_store import SeasonStore
try:
 BettingService(SeasonStore(Path(sys.argv[1])),FakeProvider()).place('2026-27',101,'CHI','5',sys.argv[2])
 print('UNSAFE_ACCEPT')
except BetError as exc: print('LOCKED:'+str(exc.status))
'''
        with season_lock(self.store, '2026-27'):
            result = subprocess.run([sys.executable,'-B','-c',code,str(self.root),str(uuid4())],
                                    cwd=PROJECT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('LOCKED:503',result.stdout)
        self.assertNotIn('UNSAFE_ACCEPT',result.stdout)
        self.assertEqual(self.balance(),5000)

    def test_streamlit_pick_navigation_cancel_and_exactly_once_submission(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        from backend.api.services.live_games import CENTRAL
        from backend.api.services.game_eligibility import display_eligibility
        provider = self.provider
        # All data below is synthetic and all money is in self.root (temp).
        future_day = datetime.now(CENTRAL).date() + timedelta(days=2)
        provider.changes={'date':future_day.isoformat(),
                          'datetime_utc':(datetime.now(timezone.utc)+timedelta(days=2,hours=2)).isoformat()}
        def live_read(season, anchor, *args):
            games=[]
            for game_id in (101, 202):
                game=provider.verify_game(game_id)
                game=display_eligibility({**game,'identity_verified':True,'eligibility_state':'scheduled','date':anchor.isoformat()},game['verified_at'])
                games.append(game)
            return {'season_id':season,'verified':True,'stale':False,'fetched_at_utc':datetime.now(timezone.utc).isoformat(),
                    'today':games,'upcoming':[],'recent_finals':[],'games':games}
        provider.read=live_read
        app.dependency_overrides[get_store]=lambda:self.store
        app.dependency_overrides[get_live_client]=lambda:provider
        client=TestClient(app)
        def local_get(url,params=None,**kwargs): return client.get(url.split('8000',1)[-1],params=params)
        def local_post(url,params=None,json=None,**kwargs):
            first=client.post(url.split('8000',1)[-1],params=params,json=json)
            # Simulate delivery of the same submit twice (e.g. double-click).
            second=client.post(url.split('8000',1)[-1],params=params,json=json)
            self.assertEqual(first.json()['bet_id'],second.json()['bet_id'])
            self.assertEqual(first.json()['balance'],second.json()['balance'])
            return first
        st.cache_data.clear()
        try:
            with patch.object(config,'MAINTENANCE_READ_ONLY',False),patch.object(config,'BETTING_ENABLED',True),patch.object(config,'SETTLEMENT_ENABLED',True),patch('requests.get',side_effect=local_get),patch('requests.post',side_effect=local_post) as posts:
                view=AppTest.from_file(str(PROJECT/'streamlit_app.py'),default_timeout=30).run()
                self.assertEqual(len(view.exception),0)
                self.assertEqual(view.radio(key='active_section').value,'Home / Dashboard')
                view.radio(key='active_section').set_value('NBA Games').run()
                view.date_input(key='game_day_2026-27').set_value(future_day).run()
                # Select the second game, not whichever card was rendered first.
                view.button(key='pick_daily_2026-27_202').click().run()
                self.assertEqual(view.radio(key='active_section').value,'NBA Games')
                draft=view.session_state['pick_draft']
                self.assertEqual(draft['game']['id'],202)
                self.assertEqual(draft['game']['home'],'CHI')
                self.assertEqual(draft['game']['away'],'MIA')
                self.assertEqual(draft['game']['date'],future_day.isoformat())
                self.assertEqual(view.date_input(key='game_day_2026-27').value,future_day)
                posts.assert_not_called()
                intent=draft['intent']
                view.radio(key='winner_'+intent).set_value('CHI').run()
                view.number_input(key='wager_'+intent).set_value(25.0).run()
                self.assertEqual(view.radio(key='active_section').value,'NBA Games')
                self.assertTrue(any(b.label=='Place My Pick' for b in view.button))
                self.assertEqual(len(view.checkbox),0)
                self.assertFalse(any(b.label in ('Review My Pick','Confirm My Pick') for b in view.button))
                self.assertEqual(self.balance(),5000)
                # Widget cleanup in another section must not discard the draft.
                view.radio(key='active_section').set_value('Home / Dashboard').run()
                view.radio(key='active_section').set_value('NBA Games').run()
                self.assertEqual(view.date_input(key='game_day_2026-27').value,future_day)
                self.assertEqual(view.radio(key='winner_'+intent).value,'CHI')
                self.assertEqual(view.number_input(key='wager_'+intent).value,25.0)
                next(b for b in view.button if b.label=='Cancel').click().run()
                self.assertEqual(view.radio(key='active_section').value,'NBA Games')
                self.assertEqual(view.date_input(key='game_day_2026-27').value,future_day)
                self.assertFalse(any(b.label=='Place My Pick' for b in view.button))
                self.assertNotIn('pick_draft',view.session_state)
                posts.assert_not_called()
                # Daily navigation still works after cancel; then pick again.
                view.button(key='next_2026-27').click().run()
                self.assertEqual(view.date_input(key='game_day_2026-27').value,future_day+timedelta(days=1))
                view.button(key='previous_2026-27').click().run()
                view.button(key='pick_daily_2026-27_202').click().run()
                intent=view.session_state['pick_draft']['intent']
                view.radio(key='winner_'+intent).set_value('CHI').run()
                view.number_input(key='wager_'+intent).set_value(25.0).run()
                next(b for b in view.button if b.label=='Place My Pick').click().run()
                self.assertEqual(len(view.exception),0)
                self.assertEqual(view.radio(key='active_section').value,'NBA Games')
                self.assertEqual(view.date_input(key='game_day_2026-27').value,future_day)
                self.assertFalse(any(b.label=='Place My Pick' for b in view.button))
                self.assertTrue(any('Chicago Bulls' in notice.value and '$25.00' in notice.value and '$4,975.00' in notice.value for notice in view.success))
                self.assertEqual(self.balance(),4975)
                self.assertEqual(posts.call_count,1)
                view.run()
                self.assertEqual(self.balance(),4975)
                self.assertEqual(posts.call_count,1)
                view.radio(key='active_section').set_value('My Picks').run()
                rows,_=self.service.snapshot('2026-27')
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['game_id'],'202')
                self.assertEqual(rows[0]['date'],future_day.isoformat())
                self.assertEqual(rows[0]['pick'],'CHI')
                self.assertEqual(rows[0]['stake'],'25.00')
                self.assertEqual(len(view.dataframe[0].value),1)
                view.radio(key='active_section').set_value('Home / Dashboard').run()
                metrics={m.label:m.value for m in view.metric}
                self.assertEqual(metrics['Bankroll'],'$4,975.00')
                self.assertEqual(metrics['Pending bets'],'1')
                view.radio(key='active_section').set_value('NBA Games').run()
                self.assertEqual(view.date_input(key='game_day_2026-27').value,future_day)
                view.selectbox(key='selected_season').select('2025-26').run()
                self.assertFalse(any(b.label in ('Select Game','Place My Pick','Refresh Results / Settle Eligible Bets') for b in view.button))
        finally:
            client.close();app.dependency_overrides.clear();st.cache_data.clear()

    def test_shared_rate_limit_blocks_bet_retries_without_debit(self):
        from test_live_games import response,fixture
        from unittest.mock import Mock
        transport=Mock();limited=response([],status=429);limited.headers={'Retry-After':'120'}
        transport.get.return_value=limited
        provider=LiveNBAClient(key='test',transport=transport)
        service=BettingService(self.store,provider);key=str(uuid4());clock=[0]
        with patch('backend.api.services.live_games.monotonic',side_effect=lambda:clock[0]):
            for _ in range(4):
                with self.assertRaises(FeedUnavailable):service.place('2026-27',101,'CHI','50',key)
            self.assertEqual(transport.get.call_count,1)
            self.assertEqual(self.balance(),5000)
            self.assertEqual(self.service.snapshot('2026-27')[0],[])
            app.dependency_overrides[get_store]=lambda:self.store
            app.dependency_overrides[get_live_client]=lambda:provider
            try:
                with TestClient(app) as client:
                    result=client.post('/bets/?season=2026-27',json={'season_id':'2026-27','game_id':101,'pick':'CHI','stake':'50','idempotency_key':key})
                    self.assertEqual(result.status_code,503)
                    self.assertEqual(result.headers['Retry-After'],'120')
                    self.assertEqual(transport.get.call_count,1)
            finally:app.dependency_overrides.clear()
            # Once retry is allowed, the new response still has to pass fresh
            # identity, lifecycle and future-tipoff checks before any debit.
            clock[0]=121
            fresh=Mock(status_code=200,headers={});fresh.json.return_value={'data':fixture(datetime=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat())}
            transport.get.return_value=fresh
            self.assertEqual(service.place('2026-27',101,'CHI','50',key)['balance'],4950)
            self.assertEqual(transport.get.call_count,2)
            service.place('2026-27',101,'CHI','50',key)
            self.assertEqual(transport.get.call_count,2)

    def _exercise_single_button_retry(self, mode):
        import streamlit as st
        import requests
        import httpx
        from streamlit.testing.v1 import AppTest
        from backend.api.services.live_games import CENTRAL
        from backend.api.services.game_eligibility import display_eligibility
        day=datetime.now(CENTRAL).date()+timedelta(days=1)
        self.provider.changes={'date':day.isoformat(),'datetime_utc':datetime(day.year,day.month,day.day,19,tzinfo=CENTRAL).isoformat()}
        def live_read(season,anchor,*args):
            game=self.provider.verify_game(101)
            game=display_eligibility({**game,'identity_verified':True,'eligibility_state':'scheduled'},game['verified_at'])
            return {'season_id':season,'verified':True,'stale':False,'fetched_at_utc':game['verified_at'],
                    'today':[game],'games':[game],'upcoming':[],'recent_finals':[]}
        self.provider.read=live_read
        app.dependency_overrides[get_store]=lambda:self.store
        app.dependency_overrides[get_live_client]=lambda:self.provider
        client=TestClient(app);attempts=[];st.cache_data.clear()
        def submit(url,params=None,json=None,**kwargs):
            attempts.append(dict(json))
            if len(attempts)==1 and mode=='rate-limit':
                return httpx.Response(503,json={'detail':{'message':'NBA provider rate limit reached. Retry in 120 seconds.'}},
                                      request=httpx.Request('POST',url),headers={'Retry-After':'120'})
            result=client.post(url.split('8000',1)[-1],params=params,json=json)
            if len(attempts)==1 and mode=='lost-response':
                raise requests.ConnectionError('Simulated lost response after commit')
            return result
        try:
            with patch('requests.get',side_effect=lambda url,params=None,**kwargs:client.get(url.split('8000',1)[-1],params=params)),patch('requests.post',side_effect=submit):
                view=AppTest.from_file(str(PROJECT/'streamlit_app.py'),default_timeout=30).run()
                view.radio(key='active_section').set_value('NBA Games').run()
                view.date_input(key='game_day_2026-27').set_value(day).run()
                view.button(key='pick_daily_2026-27_101').click().run()
                intent=view.session_state['pick_draft']['intent']
                view.radio(key='winner_'+intent).set_value('CHI').run()
                view.number_input(key='wager_'+intent).set_value(25.0).run()
                self.assertEqual(attempts,[])
                self.assertEqual(len(view.checkbox),0)
                view.button(key='place_'+intent).click().run()
                self.assertEqual(len(view.exception),0)
                self.assertEqual(len(attempts),1)
                if mode=='rate-limit':
                    self.assertEqual(self.balance(),5000)
                    self.assertEqual(self.service.snapshot('2026-27')[0],[])
                    self.assertTrue(any('rate limit' in item.value and '120' in item.value for item in view.error))
                else:
                    self.assertEqual(self.balance(),4975)
                    self.assertTrue(any('result could not be confirmed' in item.value for item in view.error))
                self.assertEqual(view.session_state['pick_draft']['intent'],intent)
                view.button(key='place_'+intent).click().run()
                self.assertEqual(len(view.exception),0)
                self.assertEqual(attempts[0]['idempotency_key'],attempts[1]['idempotency_key'])
                self.assertEqual(self.balance(),4975)
                self.assertEqual(len(self.service.snapshot('2026-27')[0]),1)
                self.assertTrue(any('Chicago Bulls' in item.value and '$25.00' in item.value and '$4,975.00' in item.value for item in view.success))
                view.run()
                self.assertEqual(len(attempts),2)
                self.assertEqual(self.balance(),4975)
        finally:
            client.close();app.dependency_overrides.clear();st.cache_data.clear()

    def test_single_button_rate_limit_is_explained_without_placement(self):
        self._exercise_single_button_retry('rate-limit')

    def test_single_button_lost_response_retries_without_double_debit(self):
        self._exercise_single_button_retry('lost-response')

    def test_settlement_429_makes_one_provider_call_not_three(self):
        from test_live_games import response
        from unittest.mock import Mock
        for game_id in (101,102,103):self.place(game_id=game_id)
        transport=Mock();limited=response([],status=429);limited.headers={'Retry-After':'120'}
        transport.get.return_value=limited
        service=BettingService(self.store,LiveNBAClient(key='test',transport=transport))
        first=service.settle('2026-27')
        self.assertEqual(first['settled'],0)
        self.assertEqual(first['pending'],3)
        self.assertEqual(transport.get.call_count,1)
        service.settle('2026-27')
        self.assertEqual(transport.get.call_count,1)
        self.assertEqual(self.balance(),4850)

    def test_form_reruns_and_financial_refresh_do_not_refetch_schedule(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        from backend.api.services.live_games import CENTRAL
        from test_live_games import fixture,response
        from unittest.mock import Mock
        day=datetime.now(CENTRAL).date()+timedelta(days=1)
        raw=fixture(date=day.isoformat(),datetime=datetime(day.year,day.month,day.day,19,tzinfo=CENTRAL).isoformat())
        transport=Mock()
        def dispatch(url,**kwargs):
            if url.endswith('/101'):
                answer=Mock(status_code=200,headers={});answer.json.return_value={'data':raw};return answer
            return response([raw])
        transport.get.side_effect=dispatch
        provider=LiveNBAClient(key='test',transport=transport)
        app.dependency_overrides[get_store]=lambda:self.store
        app.dependency_overrides[get_live_client]=lambda:provider
        client=TestClient(app);st.cache_data.clear();clock=[0]
        try:
            with patch('requests.get',side_effect=lambda url,params=None,**kwargs:client.get(url.split('8000',1)[-1],params=params)),patch('requests.post',side_effect=lambda url,params=None,json=None,**kwargs:client.post(url.split('8000',1)[-1],params=params,json=json)),patch('backend.api.services.live_games.monotonic',side_effect=lambda:clock[0]):
                view=AppTest.from_file(str(PROJECT/'streamlit_app.py'),default_timeout=30).run()
                self.assertEqual(transport.get.call_count,0)
                view.radio(key='active_section').set_value('NBA Games').run()
                view.date_input(key='game_day_2026-27').set_value(day).run()
                browsing_calls=transport.get.call_count
                clock[0]=16;view.button(key='pick_daily_2026-27_101').click().run()
                intent=view.session_state['pick_draft']['intent']
                clock[0]=32;view.radio(key='winner_'+intent).set_value('CHI').run()
                clock[0]=48;view.number_input(key='wager_'+intent).set_value(25.0).run()
                self.assertEqual(transport.get.call_count,browsing_calls)
                clock[0]=96;next(b for b in view.button if b.label=='Place My Pick').click().run()
                self.assertEqual(len(view.exception),0)
                self.assertEqual(transport.get.call_count,browsing_calls+1)
                self.assertEqual(self.balance(),4975)
                view.radio(key='active_section').set_value('My Picks').run()
                view.radio(key='active_section').set_value('Home / Dashboard').run()
                view.radio(key='active_section').set_value('NBA Games').run()
                self.assertEqual(transport.get.call_count,browsing_calls+1)
        finally:
            client.close();app.dependency_overrides.clear();st.cache_data.clear()


if __name__ == '__main__': unittest.main()
