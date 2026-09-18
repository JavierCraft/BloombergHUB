import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from eth_abi import encode
from src.markets import Polymarket
from src.polymarket.events import decode_event
from src.polymarket.onchain import PolygonReader, USDC_E, TOPIC_POSITION_SPLIT
from src.sports.match_analysis import compare
from src.markets.flow import market_flow


class MarketTests(unittest.TestCase):
    @patch('src.markets._get')
    def test_sector_uses_tag_and_volume_order(self, get):
        get.return_value=[{'slug':'culture-event','markets':[
            {'question':'Low','volume24hr':10}, {'question':'High','volume24hr':100}]}]
        rows=Polymarket().search('',2,'culture')
        self.assertEqual(get.call_args.args[1]['tag_slug'],'pop-culture')
        self.assertEqual(rows[0]['question'],'High')

    @patch('src.markets._get')
    def test_search_uses_keyword_and_event_url(self, get):
        get.return_value = {'events': [{'slug': 'match-event', 'markets': [
            {'conditionId': 'one', 'question': 'Team A?', 'outcomes': '["A", "B"]',
             'outcomePrices': '["0.63", "0.37"]', 'active': True},
            {'question': 'Old', 'closed': True}]}]}
        rows = Polymarket().search('Team A Team B')
        self.assertEqual(get.call_args.args[1]['q'], 'Team A Team B')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['outcomes'][1]['price'], .37)
        self.assertTrue(rows[0]['url'].endswith('/match-event'))

    @patch('src.markets._get')
    def test_missing_or_malformed_prices_are_not_zero_odds(self, get):
        get.return_value = [{'outcomes': '["Yes", "No"]', 'outcomePrices': '["bad"]'}]
        self.assertEqual(Polymarket().search()[0]['outcomes'], [])

    @patch('src.markets._get')
    def test_multi_outcomes_and_zero_price(self, get):
        get.return_value = [{'outcomes': ['A', 'B', 'C'], 'outcomePrices': [0, .4, .6]}]
        self.assertEqual(len(Polymarket().search()[0]['outcomes']), 3)


class EventTests(unittest.TestCase):
    def log(self, data, topics):
        return {'data': data, 'topics': topics, 'blockNumber': 100, 'logIndex': 3,
                'transactionHash': bytes.fromhex('ab'*32), 'address': '0x'+'11'*20}

    def test_split_amount_exact_and_no_trade_price(self):
        log = self.log(encode(['address', 'uint256[]', 'uint256'], [USDC_E, [1,2], 1234567]),
                       [TOPIC_POSITION_SPLIT, '0x'+'00'*12+'12'*20, '0x'+'00'*32, '0x'+'ab'*32])
        row = decode_event(log, 'position_splits')
        self.assertEqual(row['amount'], '1.234567')
        self.assertEqual(row['partition'], ['1','2'])
        self.assertIsNone(row['price'])
        self.assertEqual(row['condition_id'], '0x'+'ab'*32)

    def test_redemption_indexed_collateral(self):
        log = self.log(encode(['bytes32','uint256[]','uint256'], [b'a'*32,[1],8000000]),
                       ['0x'+'00'*32, '0x'+'00'*12+'12'*20, '0x'+'00'*12+USDC_E[2:], '0x'+'00'*32])
        self.assertEqual(decode_event(log, 'redemptions')['amount'], '8')

    def test_resolution_vector(self):
        log = self.log(encode(['uint256','uint256[]'], [2,[0,1]]), ['0x'+'01'*32]*4)
        row = decode_event(log,'markets_resolved')
        self.assertEqual(row['payout_numerators'], ['0','1'])
        self.assertEqual(row['payout_denominator'], '1')

    def test_unknown_collateral_keeps_raw_units(self):
        log = self.log(encode(['address','uint256[]','uint256'], ['0x'+'ff'*20,[1,2],10]), ['0x'+'01'*32]*4)
        row = decode_event(log,'position_merges')
        self.assertEqual(row['amount_raw'],'10')
        self.assertNotIn('amount',row)

    def test_partial_rpc_failure_not_zero(self):
        def get_logs(q):
            self.assertEqual(q['fromBlock'],91)
            if q['topics'] == [TOPIC_POSITION_SPLIT]:
                raise RuntimeError('upstream down')
            return []
        reader = PolygonReader.__new__(PolygonReader)
        reader.w3 = SimpleNamespace(eth=SimpleNamespace(get_logs=get_logs, block_number=100))
        result = reader.settlement_activity(10)
        self.assertIsNone(result['summary']['position_splits'])
        self.assertIn('position_splits',result['incomplete'])
        self.assertEqual(result['summary']['position_merges'],0)


class FormTests(unittest.TestCase):
    def test_unsupported_game_is_explicit(self):
        self.assertIsNone(compare('valorant','A','B')['forecast_probability'])

    @patch('src.sports.esports.Dota2.teams')
    def test_ambiguous_team_not_guessed(self, teams):
        teams.return_value = [{'name':'A','team_id':1},{'name':'A','team_id':2}]
        self.assertIsNone(compare('dota2','A','B')['favorite'])

    @patch('src.sports.esports.Dota2.team_matches')
    @patch('src.sports.esports.Dota2.teams')
    def test_old_matches_excluded_and_win_rate_not_forecast(self, teams, matches):
        teams.return_value=[{'name':'A','team_id':1},{'name':'B','team_id':2}]
        def history(team):
            return [{'match_id':i,'start_time':time.time()-60-i*3600,'radiant':True,
                     'radiant_win':team==1} for i in range(6)] + [
                       {'match_id':99,'start_time':time.time()-100*86400,'radiant':True,'radiant_win':True}]
        matches.side_effect=history
        result=compare('dota2','A','B')
        self.assertEqual(result['teams'][0]['sample'],6)
        self.assertEqual(result['favorite'],'A')
        self.assertIsNone(result['forecast_probability'])


class FlowTests(unittest.TestCase):
    @patch('src.markets.flow._get')
    def test_buy_percentage_excludes_sales_and_other_markets(self, get):
        condition='0x'+'ab'*32
        def trade(outcome,side,size,condition_id=condition):
            return {'conditionId':condition_id,'outcome':outcome,'side':side,
                    'price':.5,'size':size,'timestamp':1700000000,'proxyWallet':'0x123'}
        get.return_value=[trade('Yes','BUY',60),trade('No','BUY',20),trade('No','SELL',1000),trade('Other','BUY',500,'other')]
        result=market_flow(condition)
        self.assertEqual(result['sample_size'],3)
        self.assertEqual(result['outcomes'][0]['buy_share_pct'],75)
        self.assertEqual(result['dominant_buy_flow'],'Yes')
        self.assertEqual(result['outcomes'][1]['net_taker_usd'],'-490.0')

    @patch('src.markets.flow._get')
    def test_no_buy_is_not_zero_probability(self,get):
        get.return_value=[{'conditionId':'test','outcome':'No','side':'SELL','price':.5,'size':10,'timestamp':1700000000}]
        result=market_flow('test')
        self.assertIsNone(result['outcomes'][0]['buy_share_pct'])
        self.assertIsNone(result['dominant_buy_flow'])


if __name__ == '__main__':
    unittest.main()
