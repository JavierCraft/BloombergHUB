"""Local deterministic browser smoke test; upstream APIs are mocked."""
from pathlib import Path
import sys
import threading
import logging
from datetime import datetime, timezone
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
from flask import request, jsonify
from werkzeug.serving import make_server
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select

hits = Counter()


class SimpleText:
    """A detached snapshot of an element's text, so later repaints cannot
    invalidate an assertion that has already been read."""

    def __init__(self, value):
        self.text = value


def text_of(driver, selector):
    """Visible text of the first match, tolerating a re-render mid-read.

    These panels repaint on a 500ms timer under test, so an element found on one
    line can be detached by the time its text is read. That is the polling doing
    its job, not a failure — so it is retried rather than raised.
    """
    try:
        found = driver.find_elements(By.CSS_SELECTOR, selector)
        return found[0].text if found else ''
    except StaleElementReferenceException:
        return ''


def splash_gone(driver):
    """Screenshots taken while the page-transition splash fades show only the logo."""
    return driver.execute_script(
        "const s = document.getElementById('splash');"
        "return !s || (s.classList.contains('pergi') && getComputedStyle(s).opacity === '0');")


news_failure = False
# Bumped on every /api/news hit so the live stream sees genuinely new headlines
# and the "prepend, do not redraw" path is exercised rather than assumed.
news_batch = 0


def _analysis(price=.65):
    """The derived reading the real endpoint now ships with every market."""
    return {
        'lean': {'known': True, 'outcome': 'Yes', 'price': price, 'pct': f'{price*100:.1f}%',
                 'margin_pp': 30.0, 'runner_up': 'No', 'band': 'condong', 'outcomes_count': 2,
                 'implied_odds': round(1 / price, 2)},
        'character': {'change_1h_pp': 0.4, 'change_24h_pp': -1.2, 'change_1w_pp': 3.0,
                      'change_1mo_pp': None, 'spread_pp': 1.0, 'liquidity': 500.0,
                      'volume24h': 1000.0, 'stability': 'tenang',
                      'stability_why': 'Fixture: pergerakan kecil.', 'depth': 'tipis',
                      'depth_why': 'Fixture: buku tipis.', 'thin': True,
                      'wide_spread': False, 'fresh_move': False},
        'economics': {'known': True, 'entry_price': price, 'entry_pct': f'{price*100:.1f}%',
                      'breakeven_pct': f'{price*100:.1f}%', 'profit_per_unit': 0.54,
                      'profit_pct_if_right': 53.8, 'loss_pct_if_wrong': 100.0,
                      'round_trip_cost_pp': 1.0, 'days_to_resolution': 12.0,
                      'annualised_pct_if_right': 1636.0, 'note': 'Fixture economics note.'},
        'strategy': {'stance': 'hati-hati', 'headline': 'Fixture stance headline.',
                     'points': ['Fixture: bukunya tipis.'],
                     'breakeven': 'Fixture breakeven line.',
                     'next_step': 'Fixture next step.'},
        'ev': _ev(price),
        'reading': 'Fixture reading: Yes dihargai 65.0%.',
        'why_note': 'Fixture caveat about causation.',
    }


def _model(price=.65, prob=.71):
    return {'available': True, 'prob_yes': prob, 'market_prob_yes': price, 'edge_pp': round((prob - price) * 100, 2),
            'direction': 'naik', 'days_left': 2.0, 'bucket': '1–3 hari', 'model_id': 'deadline-fixture',
            'algorithm': 'anchored_logit', 'trained_at': '2026-09-17T00:00:00+00:00', 'beats_market': False,
            'brier_model': 0.162, 'brier_market': 0.163,
            'drivers': [{'feature': 'chg_7d', 'label': 'gerak harga 7 hari', 'contribution': 0.08}], 'caveats': []}


def _ev(price=.65, prob=.71):
    ask = price + .01
    rows = [
        {'outcome': 'Yes', 'price': price, 'ask': ask, 'bid': price - .01, 'entry_source': 'best ask',
         'breakeven_pct': ask * 100, 'ev_consensus_pct': round((price / ask - 1) * 100, 2),
         'model_prob': prob, 'ev_model_pct': round((prob / ask - 1) * 100, 2), 'ev_per_share': prob - ask},
        {'outcome': 'No', 'price': 1 - price, 'ask': 1 - price + .01, 'bid': 1 - price - .01, 'entry_source': 'best ask',
         'breakeven_pct': (1 - price + .01) * 100, 'ev_consensus_pct': -2.8, 'model_prob': 1 - prob,
         'ev_model_pct': -19.4, 'ev_per_share': -.08},
    ]
    return {'known': True, 'rows': rows, 'best': rows[0], 'model': _model(price, prob),
            'verdict': 'Fixture EV verdict: model belum terbukti.', 'note': 'Fixture EV note.'}


@app.before_request
def mock_api():
    if not request.path.startswith('/api/'):
        return None
    path = request.path
    hits[path] += 1
    data = []
    now = datetime.now(timezone.utc).isoformat()
    if path == '/api/news':
        if news_failure:
            return jsonify(ok=False, error={'message': 'Fixture offline', 'code': 'NETWORK'})
        q = request.args.get('q', 'top')
        data = {'news': [{'title': f'{q} AI update {news_batch}-{i}', 'url': f'https://example.com/news/{news_batch}/{i}',
                          'source': 'Fixture', 'time': now, 'summary': 'Fixture evidence'} for i in range(3)],
                'count': 3, 'stored_new': 0, 'file': 'fixture', 'query': q, 'fetched_at': now}
    elif path == '/api/markets/polymarket':
        data = {'reachable': True, 'reason': 'Fixture Polymarket', 'markets': [
            {'id': '0x' + 'ab' * 32, 'question': request.args.get('q', 'AI') + ' market?',
             'outcomes': [{'outcome': 'Yes', 'price': .65}, {'outcome': 'No', 'price': .35}],
             'volume': 10000, 'volume24h': 1000, 'liquidity': 500, 'updated_at': now,
             'spread': .01, 'change24h': -.012, 'change1h': .004, 'change1w': .03,
             'rules': 'Resolve against official result', 'clob_token_ids': ['12345678901234567890', '2'],
             'url': 'https://polymarket.com/event/fixture', 'analysis': _analysis()}],
            'brief': {'known': True, 'sector': 'fixture', 'markets': 1,
                      'total_volume24h': 1000.0, 'concentration_pct': 100.0,
                      'busiest': {'question': 'Busiest fixture market', 'outcome': 'Yes',
                                  'pct': '65.0%', 'volume24h': 1000.0},
                      'biggest_mover': {'question': 'Mover fixture market', 'outcome': 'Yes',
                                        'pct': '65.0%', 'change_24h_pp': -1.2},
                      'most_contested': {'question': 'Contested fixture market', 'outcome': 'Yes', 'pct': '51.0%'},
                      'most_settled': {'question': 'Settled fixture market', 'outcome': 'Yes', 'pct': '95.0%'},
                      'resolving_soonest': {'question': 'Soonest fixture market', 'outcome': 'Yes',
                                            'pct': '65.0%', 'days': 1.5},
                      'note': 'Fixture brief note.'}}
    elif path == '/api/esports/board':
        data = {'game': 'dota2', 'game_name': 'Dota 2', 'listed_markets': 2, 'failed': [],
                'note': 'Fixture board note.',
                'summary': {'fixtures': 2, 'matched': 1, 'unmatched': 1,
                            'min_confidence': .5, 'note': 'Fixture match note.'},
                'rows': [
                    {'starts_utc': now, 'team_a': 'Team Alpha', 'team_b': 'Team Beta',
                     'format': 'Bo3', 'tournament': 'Fixture Cup',
                     'poly': {'matched': True, 'confidence': 1.0, 'tied': False,
                              'favorite': 'Team Alpha', 'favorite_pct': '65.0%',
                              'favorite_price': .65, 'margin_pp': 30.0,
                              'volume24h': 1234.0, 'liquidity': 500.0,
                              'event_title': 'Dota 2: Team Alpha vs Team Beta',
                              'teams': ['Team Alpha', 'Team Beta'],
                              'url': 'https://polymarket.com/event/fixture',
                              'outcomes': [{'outcome': 'Team Alpha', 'price': .65, 'pct': '65.0%'},
                                           {'outcome': 'Team Beta', 'price': .35, 'pct': '35.0%'}]},
                     'form': {'known': True, 'favorite': 'Team Alpha', 'favorite_pct': 58.2,
                              'rating_gap': 57.0, 'teams': [], 'method': 'Elo',
                              'caveat': 'Fixture caveat.'}},
                    {'starts_utc': now, 'team_a': 'Team Gamma', 'team_b': 'Team Delta',
                     'format': 'Bo1', 'tournament': 'Fixture Cup',
                     'poly': {'matched': False, 'confidence': 0.0, 'best_candidate': None,
                              'reason': 'Fixture: tidak ada pasar yang cocok.'},
                     'form': {'known': False, 'reason': 'Fixture: tidak ada rating.'}}]}
    elif path == '/api/sports/soccer/fixtures':
        data = {'note': 'Fixture soccer note.', 'rows': [
            {'event_title': 'Alpha FC vs. Beta United', 'url': 'https://polymarket.com/event/fixture',
             'condition_id': '0x' + 'cd' * 32, 'teams': ['Alpha FC', 'Beta United'],
             'favorite': 'Alpha FC', 'favorite_pct': '48.0%', 'favorite_price': .48,
             'margin_pp': 20.0, 'tied': False, 'volume24h': 900.0, 'liquidity': 400.0,
             'start_date': now, 'end_date': now, 'overround_pct': .5, 'has_draw': True,
             'outcomes': [{'outcome': 'Alpha FC', 'price': .48, 'pct': '48.0%'},
                          {'outcome': 'Draw', 'price': .28, 'pct': '28.0%'},
                          {'outcome': 'Beta United', 'price': .24, 'pct': '24.0%'}]}]}
    elif path.startswith('/api/markets/polymarket/flow/'):
        data = {'note': 'Fixture taker sample', 'sample_size': 10, 'fetched_at': now, 'from_utc': now, 'to_utc': now,
                'dominant_buy_flow': 'Yes', 'outcomes': [{'outcome': 'Yes', 'buy_share_pct': 75, 'buy_usd': '30', 'sell_usd': '2', 'net_taker_usd': '28', 'wallets': 2}], 'trades': []}
    elif path == '/api/esports/analysis':
        data = {'favorite': None, 'teams': [], 'note': 'Fixture: insufficient history'}
    elif path == '/api/polygon/block':
        data = {'block': 100}
    elif path == '/api/health':
        data = {'summary': {'live': 3, 'degraded': 0, 'blocked': 0}}
    elif path == '/api/news/files':
        data = {'file_count': 0, 'total_bytes': 0, 'folder': 'fixture', 'files': []}
    elif path == '/api/markets/related':
        data = {'manifold': []}
    elif path == '/api/polygon/settlement':
        market = {'question': 'Fixture settled market?', 'leader': 'Yes', 'leader_pct': '72.0%',
                  'leader_price': .72, 'closed': False, 'url': 'https://polymarket.com/event/fixture',
                  'outcomes': [{'outcome': 'Yes', 'price': .72}, {'outcome': 'No', 'price': .28}],
                  'volume24h': 100, 'liquidity': 50, 'end_date': now}
        data = {'blocks_scanned': 60, 'from_block': 90, 'to_block': 150,
                'window': {'from_block': 90, 'to_block': 150, 'from_time_utc': now,
                           'to_time_utc': now, 'seconds': 120},
                'summary': {'position_splits': 2, 'position_merges': 0, 'redemptions': 1,
                            'neg_risk_redemptions': 0, 'markets_resolved': 0, 'markets_created': 1},
                'stats': {'position_splits': {
                    'count': 2, 'decoded': 2, 'unique_wallets': 2, 'unique_conditions': 1,
                    'with_usdc_amount': 2, 'usdc_total': '15.5', 'usdc_largest': '10',
                    'usdc_smallest': '5.5', 'usdc_median': '10', 'usdc_mean': '7.75',
                    'first_block': 90, 'last_block': 150, 'per_minute': 1.0,
                    'top_conditions': [{'condition_id': '0x' + 'ef' * 32, 'events': 2,
                                        'usdc': '15.5', 'market': market}]}},
                'details': {'position_splits': [
                    {'event': 'position_splits', 'block': 150, 'log_index': 1,
                     'transaction': '0x' + '11' * 32, 'time_utc': now, 'amount': '10',
                     'unit': 'USDC.e', 'wallet': '0x' + '22' * 20,
                     'condition_id': '0x' + 'ef' * 32, 'market': market,
                     'url': 'https://polygonscan.com/tx/0x' + '11' * 32}]},
                'details_limit_per_event': 20,
                'market_lookup': {'asked': 1, 'resolved': 1, 'failed': None,
                                  'price_as_of': now, 'note': 'Fixture lookup note.'},
                'fetched_at': now, 'incomplete': [], 'elapsed_ms': 5,
                'caveat': 'Fixture caveat.'}
    elif path == '/api/polygon/wallet':
        return jsonify(ok=False, error={'code': 'NETWORK', 'message': 'Fixture wallet unavailable'})
    elif path == '/api/polygon/exchange':
        data = {'order_fills': 0, 'events': {}, 'total_logs': 0, 'blocks_scanned': 10, 'label': 'Fixture'}
    elif path == '/api/sports/soccer/leagues':
        data = {'leagues': ['ENG-Premier League'], 'default': 'ENG-Premier League'}
    elif path == '/api/markets/iev':
        data = {'auction': {'crossed': False, 'iep': None, 'iev': 0.0, 'iev_notional': 0.0, 'surplus': None,
                            'surplus_side': None, 'best_bid': .64, 'best_ask': .66, 'mid': .65, 'reference': .65,
                            'bid_depth': {'levels': 3, 'shares': 900, 'notional': 580},
                            'ask_depth': {'levels': 3, 'shares': 700, 'notional': 470},
                            'method': 'Fixture method', 'reason': 'Fixture: buku tidak bersilangan, IEV = 0.'},
                'fair_value': {'known': True, 'fair': .71,
                               'buy_yes': {'levels': 2, 'shares': 400, 'cost': 266, 'vwap': .665,
                                           'worst_price': .67, 'expected_profit': 18, 'ev_pct': 6.8},
                               'buy_no': {'levels': 0, 'shares': 0, 'cost': 0, 'vwap': None, 'worst_price': None,
                                          'expected_profit': 0, 'ev_pct': None},
                               'side': 'YES', 'shares_to_fair': 400, 'note': 'Fixture fair note.'},
                'unit': 'lembar', 'source': 'Polymarket CLOB'}
    elif path == '/api/deadlines':
        deadline = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 2 * 86400, timezone.utc).isoformat()
        market = {'source': 'manifold', 'money': 'mana', 'id': 'dl1', 'token_id': None,
                  'question': 'Will the Fixture Fed cut rates by Friday?', 'url': 'https://manifold.markets/fixture',
                  'rules': 'Fixture rules.', 'event_title': None, 'created_ms': 1789000000000,
                  'created': '2026-09-10T00:00:00+00:00', 'deadline_ms': 4070908800000, 'deadline': deadline,
                  'days_left': 2.0, 'hours_left': 48.0, 'time_left': '2.0 hari', 'bucket': '1–3 hari',
                  'outcomes': [{'outcome': 'Yes', 'price': .65}, {'outcome': 'No', 'price': .35}], 'p_yes': .65,
                  'best_bid': None, 'best_ask': None, 'spread': None, 'liquidity': 800, 'volume24h': 120,
                  'volume': 5000, 'chg_1d': .02, 'chg_7d': .05, 'bettors': 40,
                  'model': _model(), 'ev': _ev()}
        data = {'fetched_at': now, 'days': 7, 'count': 1, 'by_source': {'manifold': 1},
                'by_bucket': [{'bucket': '1–3 hari', 'count': 1}], 'with_model': 1, 'positive_ev': 1,
                'model': {'trained': True, 'beats_market': False, 'brier_model': .162, 'brier_market': .163,
                          'tested_markets': 863},
                'failed': [{'source': 'Polymarket', 'reason': 'Fixture: diblokir'}],
                'markets': [market], 'note': 'Fixture deadline note.'}
    elif path == '/api/ml/report':
        data = {'model_id': 'deadline-fixture', 'algorithm': 'anchored_logit', 'trained_at': now,
                'beats_market': False, 'verdict': 'Fixture verdict: belum bisa dibedakan dari kebetulan.',
                'metrics': {'contested': {'brier_model': .162, 'brier_market': .163, 'brier_skill_vs_market': .003,
                                          'log_loss_model': .49, 'log_loss_market': .494, 'direction_accuracy': .54,
                                          'direction_n': 4000, 'accuracy_model': .78, 'accuracy_market': .78}},
                'bootstrap': {'mean': .0005, 'low': -.0003, 'high': .0011, 'markets': 863, 'reps': 500},
                'data': {'rows': 6339, 'markets': 1036, 'by_source': {'manifold': 6339}, 'tested_markets': 863,
                         'contested_rows': 4657, 'folds': 5, 'first_deadline': now, 'last_deadline': now},
                'candidates': {'anchored_logit': {'contested': {'brier_model': .162, 'brier_market': .163,
                                                                'log_loss_model': .49, 'log_loss_market': .494,
                                                                'direction_accuracy': .54, 'auc_model': .85}}},
                'backtest': {'note': 'Fixture backtest note.', 'overall': {'n': 96, 'win_rate': .54, 'avg_entry': .46,
                                                                           'edge_pp': 8, 'roi_pct': 20, 'avg_ev_pct': 7.9},
                             'by_bucket': [], 'by_side': []},
                'deadline_effect': [{'bucket': '≤1 hari', 'n': 1618, 'markets': 607, 'avg_favorite_price': .737,
                                     'favorite_win_rate': .758, 'gap_pp': 2.1, 'se_pp': 1.06, 'brier_market': .155,
                                     'brier_model': .154, 'reading': 'Fixture effect reading.'}],
                'coefficients': {'intercept': 0, 'rows': [{'feature': 'chg_7d', 'label': 'gerak harga 7 hari',
                                                           'coef': .03, 'effect': 'menaikkan peluang YES'}],
                                 'note': 'Fixture coefficient note.'},
                'calibration_model': {'bins': []}, 'calibration_market': {'bins': []},
                'caveats': ['Fixture caveat one.']}
    elif path == '/api/ml/status':
        data = {'model': {'trained': True}, 'train_job': None, 'scan_job': None, 'autoscan': {}, 'llm': {}}
    elif path == '/api/paper/summary':
        data = {'at': now, 'positions': {'open': 1, 'settled': 0, 'void': 0},
                'overall': {'n': 0, 'wins': 0, 'win_rate': None, 'avg_entry': None, 'edge_pp': None,
                            'staked': 0, 'pnl': 0, 'roi_pct': None},
                'max_drawdown': 0, 'equity_curve': [], 'by_bucket': [], 'by_source': [], 'by_side': [],
                'predictions': {'total': 66, 'open': 30,
                                'metrics': {'n': 36, 'brier_model': .15, 'brier_market': .16, 'direction_accuracy': .6,
                                            'accuracy_model': .8},
                                'by_bucket': [{'bucket': '1–3 hari', 'n': 36, 'brier_model': .15, 'brier_market': .16,
                                               'direction_accuracy': .6}], 'calibration': None},
                'shadow': {'overall': {'n': 36, 'wins': 22, 'win_rate': .611, 'avg_entry': .55, 'edge_pp': 6.1,
                                       'roi_pct': 9.5, 'avg_ev_pct': 3},
                           'by_bucket': [{'group': '1–3 hari', 'n': 36, 'wins': 22, 'win_rate': .611, 'avg_entry': .55,
                                          'edge_pp': 6.1, 'roi_pct': 9.5, 'avg_ev_pct': 3}],
                           'flat': 30, 'note': 'Fixture shadow note.'},
                'news_signal': {'ready': False, 'n': 12, 'needed': 60, 'note': 'Fixture news signal note.'},
                'verdict': 'Fixture paper verdict.', 'settings': {'autoscan_min': 0}}
    elif path == '/api/paper/ledger':
        data = {'positions': [{'id': 'p1', 'opened_at': now, 'question': 'Fixture open position?',
                               'side_label': 'Yes', 'source': 'manifold', 'entry_price': .66, 'model_prob_side': .71,
                               'ev_pct': 7.6, 'stake': 10, 'deadline': now, 'status': 'open', 'pnl': None,
                               'origin': 'auto-scan', 'bucket': '1–3 hari', 'check_note': None}],
                'predictions': []}
    elif path == '/api/news/catalog':
        data = {'probed_at': '2026-09-17', 'total': 2,
                'categories': [{'code': 'Official', 'label': 'Sumber resmi', 'feeds': 1},
                               {'code': 'AI', 'label': 'AI & LLM', 'feeds': 1}],
                'kinds': {'resmi': 'Fixture kind.'},
                'feeds': [{'name': 'Fixture Fed feed', 'category_label': 'Sumber resmi', 'kind': 'resmi', 'lang': 'en',
                           'core': True, 'searchable': False, 'good_for': 'Fixture: sumber resolusi FOMC.',
                           'caution': '', 'url': 'https://example.com/fed.xml'}],
                'rejected': [{'source': 'Fixture blocked feed', 'reason': '404'}], 'note': 'Fixture catalog note.'}
    elif path == '/api/news/ml/digest':
        data = {'hours': 24, 'headlines_considered': 3, 'note': 'Fixture digest note.',
                'stories': [{'title': 'Fixture story about rates', 'url': 'https://example.com/story', 'size': 3,
                             'sources': ['A', 'B'], 'source_count': 2, 'first_seen': now, 'last_seen': now,
                             'terms': ['rates', 'fed'], 'tone': .2, 'tone_label': 'netral/campuran',
                             'summary': 'Fixture story summary.', 'headlines': []}]}
    elif path == '/api/news/ml/trends':
        data = {'hours': 24, 'recent_headlines': 10, 'baseline_headlines': 20, 'note': 'Fixture trend note.',
                'terms': [{'term': 'tariff', 'headlines': 5, 'sources': 3, 'previous': 0, 'share_pct': 50,
                           'growth': 11.0, 'examples': [{'title': 'Fixture tariff headline'}]}]}
    elif path == '/api/news/ml/market':
        data = {'query': 'Fixture', 'n_24h': 1, 'n_72h': 2, 'sources_72h': 2, 'tone_72h': .1,
                'tone_label': 'netral/campuran', 'acceleration': 1.0, 'note': 'Fixture evidence note.',
                'evidence': [{'title': 'Fixture relevant headline', 'url': 'https://example.com/r', 'source': 'A',
                              'time': now, 'relevance': .41, 'tone': .2}]}
    elif path == '/api/tech/llm/radar':
        data = {'hours': 336, 'count': 1, 'feeds': ['OpenAI'], 'failed': [], 'note': 'Fixture radar note.',
                'families': [{'family': 'claude', 'mentions': 2}],
                'rows': [{'time': now, 'lab_or_desk': 'Fixture desk', 'kind': 'analisis',
                          'title': 'Fixture: Claude Opus 5 hands-on', 'models': 'Claude Opus 5',
                          'release_signal': True, 'url': 'https://example.com/llm'}]}
    elif path == '/api/tech/llm/claude':
        data = {'live': False, 'as_of': '2026-06-24', 'note': 'Fixture model list note.',
                'models': [{'id': 'claude-opus-5', 'name': 'Claude Opus 5', 'context': '1M', 'price': '$5 / $25',
                            'note': 'Fixture'}]}
    columns = []
    if path == '/api/datahub/catalog':
        page = request.args.get('page', '')
        data = {'probed_at': '2026-09-17', 'total': 2, 'categories': [], 'sources': [
            {'code': 'fear-greed', 'name': 'Fixture Fear & Greed', 'category': 'Crypto', 'category_label': 'Kripto',
             'kind': 'data', 'urls': ['https://example.com/fng'], 'parser': 'fear_greed', 'sectors': [page],
             'good_for': 'Fixture: sentimen kripto sepekan.', 'ttl': 3600, 'caution': '', 'docs': ''},
            {'code': 'treasury-debt', 'name': 'Fixture Treasury debt', 'category': 'Macro', 'category_label': 'Makroekonomi',
             'kind': 'resmi', 'urls': ['https://example.com/debt'], 'parser': 'treasury_debt', 'sectors': [page],
             'good_for': 'Fixture: sumber resolusi utang AS.', 'ttl': 21600, 'caution': 'Fixture caution.', 'docs': ''}]}
    elif path.startswith('/api/datahub/'):
        code = path.rsplit('/', 1)[1]
        rows_by_code = {
            'coingecko-prices': [{'asset': 'BTC', 'usd': 76404.5, 'idr': 1358773398, 'change_24h_pct': 1.24, 'market_cap_usd': 1.5e12},
                                 {'asset': 'ETH', 'usd': 2443.1, 'idr': 43000000, 'change_24h_pct': -0.5, 'market_cap_usd': 2.9e11},
                                 {'asset': 'SOL', 'usd': 100.2, 'idr': 1770000, 'change_24h_pct': 3.3, 'market_cap_usd': 5e10}],
            'gold-api': [{'metal': 'Emas (troy oz)', 'symbol': 'XAU', 'price_usd': 4318.7, 'updated': now}],
            'frankfurter': [{'pair': 'USD/IDR', 'rate': 17670.0, 'date': '2026-09-16', 'source': 'ECB'}],
            'fear-greed': [{'date': '2026-09-17', 'value': 50, 'reading': 'Neutral'}],
            'bmkg-gempa': [{'time_wib': '17 Sep 2026 13:20:53 WIB', 'magnitude': 5.1, 'depth': '24 km',
                            'region': 'Fixture Laut Jailolo', 'tsunami_potential': '-', 'felt': 'II'}],
            'treasury-debt': [{'date': '2026-09-15', 'total_debt_trillion_usd': 40.1144, 'held_by_public_trillion': 32.4,
                               'intragovernmental_trillion': 7.7}],
        }
        rows = rows_by_code.get(code, [{'rank': 1, 'title': 'Fixture row', 'url': 'https://example.com/row'}])
        columns = list(rows[0].keys())
        data = {'code': code, 'name': f'Fixture {code}', 'good_for': f'Fixture good-for {code}.', 'caution': '',
                'ttl': 900, 'docs': 'https://example.com/docs', 'urls': ['https://example.com'], 'rows': rows,
                'fetched_at': now, 'partial': []}
    elif path == '/api/sources/all':
        def count(total, usable, problem):
            return {'total': total, 'usable': usable, 'problem': problem, 'unchecked': total - usable - problem}

        data = {'totals': {'all': count(219, 150, 6), 'registry': count(25, 18, 6), 'feed': count(158, 99, 0),
                           'api': count(36, 33, 0)},
                'rejected': {'feeds': 15, 'apis': 28, 'note': 'Fixture rejected note.'},
                'last_probe': None, 'probe_job': None, 'sources': [
                    {'type': 'feed', 'type_label': 'Feed berita', 'code': 'bbc', 'name': 'Fixture BBC World',
                     'category': 'World', 'kind': 'media', 'status': 'live', 'status_label': 'Live',
                     'detail': 'Terakhir diambil 3 menit lalu', 'rows': 20, 'url': 'https://example.com/bbc',
                     'good_for': 'Fixture: kabar dunia.', 'caution': ''},
                    {'type': 'api', 'type_label': 'API data', 'code': 'gold-api', 'name': 'Fixture Gold API',
                     'category': 'Kurs & komoditas', 'kind': 'agregator', 'status': 'blocked', 'status_label': 'Blocked',
                     'detail': 'Fixture: diblokir', 'rows': None, 'url': 'https://example.com/gold',
                     'good_for': 'Fixture: harga emas.', 'caution': ''},
                    {'type': 'registry', 'type_label': 'Integrasi', 'code': 'ml', 'name': 'Fixture ML model',
                     'category': 'Compute', 'kind': 'komputasi', 'status': 'ready', 'status_label': 'Ready',
                     'detail': 'Berjalan lokal', 'rows': None, 'url': 'https://example.com/ml',
                     'good_for': 'Fixture: model tenggat.', 'caution': ''}]}
    meta = {'columns': columns, 'source': 'fixture', 'elapsed_ms': 1}
    # One endpoint always answers with a stale-cache note, so the warning bar
    # itself stays under test. It renders an inline SVG that has a viewBox and
    # no intrinsic size; as a flex child that once grew to fill the viewport.
    if path == '/api/polygon/settlement':
        meta['notes'] = ['Sumber sedang bermasalah - menampilkan data lama (138s).']
    return jsonify(ok=True, data=data, meta=meta)


@app.after_request
def speed_up_polling(response):
    if response.mimetype == 'text/html':
        # The live headline stream polls at 15s, below the old 30s threshold.
        script = '''<script>const realInterval = window.setInterval; window.setInterval = (f,ms,...a) => realInterval(f, ms>=10000 ? 500 : ms,...a);</script>'''
        response.set_data(response.get_data(as_text=True).replace('<head>', '<head>' + script))
    return response


def main():
    global news_failure, news_batch
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 5097, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    opts = Options()
    opts.add_argument('-headless')
    opts.binary_location = 'C:/Users/javie/.cache/selenium/firefox/win64/148.0.2/firefox.exe'
    service = Service('C:/Users/javie/.cache/selenium/geckodriver/win64/0.36.0/geckodriver.exe', log_output='artifacts/geckodriver.log', service_args=['--profile-root', str(Path('artifacts').resolve())])
    driver = webdriver.Firefox(options=opts, service=service)
    wait = WebDriverWait(driver, 20)
    try:
        driver.set_window_size(1440, 1100)

        # Overview: total sources, ML radar with direction, market snapshot, stories, public data tabs.
        driver.get('http://127.0.0.1:5097/')
        wait.until(lambda d: '▲ naik 71.0%' in text_of(d, '#ml'))
        from src import datahub
        assert text_of(driver, '.stat.hero .value') == str(datahub.counts()['all'])
        wait.until(lambda d: '$76,405' in text_of(d, '#snapshot') and 'M5.1' in text_of(d, '#snapshot'))
        wait.until(lambda d: 'Fixture story about rates' in text_of(d, '#stories'))
        wait.until(lambda d: 'Fixture Treasury debt' in text_of(d, '[data-datahub] .dh-tabs'))
        wait.until(lambda d: 'Neutral' in text_of(d, '[data-datahub] .dh-body'))
        assert 'Fixture good-for fear-greed.' in text_of(driver, '[data-datahub] .dh-body')
        tab = driver.find_elements(By.CSS_SELECTOR, '[data-datahub] .dh-tabs button')[1]
        driver.execute_script('arguments[0].click()', tab)
        wait.until(lambda d: '40.1144' in text_of(d, '[data-datahub] .dh-body'))
        assert text_of(driver, '[data-datahub] thead th') == 'Date'      # parser column order, not key-sorted
        row = driver.find_element(By.CSS_SELECTOR, '#ml tr[data-market] td')
        driver.execute_script('arguments[0].click()', row)
        wait.until(lambda d: 'Fixture EV verdict' in text_of(d, '#ml-detail-body'))
        assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth')
        wait.until(splash_gone)
        driver.save_screenshot('artifacts/overview.png')

        # Sources: every source in one table, filterable by type and status.
        driver.get('http://127.0.0.1:5097/sources')
        wait.until(lambda d: 'Fixture BBC World' in text_of(d, '#all'))
        assert text_of(driver, '#t-usable') == '150'
        wait.until(splash_gone)
        Select(driver.find_element(By.ID, 'f-type')).select_by_value('api')
        wait.until(lambda d: 'Fixture BBC World' not in text_of(d, '#all') and 'Fixture Gold API' in text_of(d, '#all'))
        Select(driver.find_element(By.ID, 'f-type')).select_by_value('')
        Select(driver.find_element(By.ID, 'f-status')).select_by_value('problem')
        wait.until(lambda d: 'Fixture ML model' not in text_of(d, '#all') and 'Fixture Gold API' in text_of(d, '#all'))

        # A sector page leads with its machine-learning card and opens a market detail in place.
        driver.get('http://127.0.0.1:5097/politics')
        wait.until(lambda d: '▲ naik 71.0%' in text_of(d, '[data-ml-radar]'))
        assert 'dinilai model' in text_of(driver, '[data-ml-summary]')
        row = driver.find_element(By.CSS_SELECTOR, '[data-ml-radar] tr[data-market] td')
        driver.execute_script('arguments[0].click()', row)
        wait.until(lambda d: 'Fixture EV verdict' in text_of(d, '[data-ml-detail-body]'))
        assert 'Will the Fixture Fed cut rates by Friday?' in text_of(driver, '[data-ml-detail-title]')
        wait.until(lambda d: 'Fixture Fear & Greed' in text_of(d, '[data-datahub] .dh-tabs'))

        for page in ['news', 'politics', 'tech', 'culture', 'sports', 'esports']:
            driver.get('http://127.0.0.1:5097/' + page)
            wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '.market-analysis'))
            assert driver.find_elements(By.CSS_SELECTOR, '.monitor-select select')
            assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth')

        # The derived reading and the conditional plan reach the page.
        driver.get('http://127.0.0.1:5097/tech')
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '.sector-brief'))
        # The leaderboard includes AI and exposes the actual source headlines.
        trend_selector = 'select[aria-label="Leaderboard topik dalam judul berita"]'
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, trend_selector + ' option[value="ai"]'))
        Select(driver.find_element(By.CSS_SELECTOR, trend_selector)).select_by_value('ai')
        wait.until(lambda d: 'Contoh berita sumber' in text_of(d, '.trend-preview'))
        assert driver.find_elements(By.CSS_SELECTOR, '.trend-preview a[href^="https://example.com/"]')
        wait.until(lambda d: 'ai AI update' in text_of(d, '[data-headlines]'))
        details = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, '.market-analysis'))
        driver.execute_script('arguments[0].open=true', details)
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '.analysis .breakeven'))
        assert 'Fixture reading' in details.text
        assert 'Fixture stance headline' in details.text
        assert driver.find_elements(By.CSS_SELECTOR, '.analysis ul.plan li')

        # Headlines stream: new titles are prepended, the old list is not redrawn.
        assert driver.find_elements(By.CSS_SELECTOR, '.live-dot')
        items = '[data-headlines] a.item'
        wait.until(lambda d: 'Fixture evidence' in text_of(d, '[data-headlines]'))
        before = len(driver.find_elements(By.CSS_SELECTOR, items))
        news_batch += 1
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, items)) > before)
        assert driver.find_elements(By.CSS_SELECTOR, items + '.baru')

        # A failing source must keep what is already on screen.
        news_failure = True
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '.live-dot.bad'))
        assert 'Fixture evidence' in text_of(driver, '[data-headlines]')
        news_failure = False

        # Esports schedule carries market odds, a model lean, and the logo.
        driver.get('http://127.0.0.1:5097/esports')
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '#schedule .odds-bar'))
        schedule = SimpleText(text_of(driver, '#schedule'))
        assert 'Team Alpha 65.0%' in schedule.text
        assert 'tidak ada pasar yang cocok' in schedule.text     # the honest miss
        assert 'Team Alpha 58.2%' in schedule.text               # the form lean
        for img in driver.find_elements(By.CSS_SELECTOR, '.game-logo'):
            assert driver.execute_script('return arguments[0].complete && arguments[0].naturalWidth>0', img)

        button = wait.until(lambda d: d.find_element(By.XPATH, "//button[text()='Analisis']"))
        driver.execute_script('arguments[0].click()', button)
        wait.until(lambda d: 'insufficient history' in text_of(d, '#match-analysis'))
        wait.until(lambda d: 'Team Alpha Team Beta' in text_of(d, '.market-analysis summary'))

        details = driver.find_element(By.CSS_SELECTOR, '.market-analysis')
        driver.execute_script('arguments[0].open=true', details)
        flow_button = driver.find_element(By.XPATH, "//button[text()='Lihat arus taruhan & dominasi BUY']")
        driver.execute_script('arguments[0].click()', flow_button)
        wait.until(lambda d: 'Fixture taker sample' in text_of(d, '.market-analysis'))
        count = hits['/api/markets/polymarket']
        wait.until(lambda d: hits['/api/markets/polymarket'] > count + 1)
        # Read the attribute in the page, not through a held element: the panel
        # repaints every 500ms under test, and a held reference can go stale
        # between finding it and reading it.
        assert driver.execute_script(
            "const d = document.querySelector('.market-analysis'); return !!(d && d.open);")
        driver.save_screenshot('artifacts/esports-monitor.png')

        # Soccer: the 1X2 board reassembled from three Yes/No books.
        driver.get('http://127.0.0.1:5097/sports')
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '#fixtures .odds-bar'))
        fixtures = SimpleText(text_of(driver, '#fixtures'))
        assert 'Alpha FC vs. Beta United' in fixtures.text
        assert 'Draw 28.0%' in fixtures.text
        assert 'overround' in fixtures.text

        # On-chain: timestamps, money, and the market behind the condition id.
        # The per-event breakdown lives inside a collapsed <details>, and
        # Selenium reads visible text only, so it is opened before asserting.
        driver.get('http://127.0.0.1:5097/on-chain')
        wait.until(lambda d: 'Positions opened' in text_of(d, '#s-out'))
        onchain = SimpleText(text_of(driver, '#s-out'))
        assert '$15,50' in onchain.text or '$15.50' in onchain.text   # money on the tile
        stale_icon = driver.find_element(By.CSS_SELECTOR, '.stale svg')
        assert stale_icon.size['height'] <= 20, f"stale icon blew up: {stale_icon.size}"
        assert 'rentang 120 detik' in onchain.text                     # real block timestamps
        opened = driver.find_element(By.CSS_SELECTOR, '#s-out details.explain')
        driver.execute_script('arguments[0].open=true', opened)
        wait.until(lambda d: 'Wallet berbeda' in text_of(d, '#s-out details.explain'))
        detail = text_of(driver, '#s-out details.explain')
        assert 'Fixture settled market?' in detail                     # condition id resolved
        assert 'Yes 72.0%' in detail
        driver.save_screenshot('artifacts/onchain-detail.png')

        # EV rides along with every sector market; IEV is one click away and
        # survives the panel's own refresh.
        driver.get('http://127.0.0.1:5097/tech')
        details = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, '.market-analysis'))
        driver.execute_script('arguments[0].open=true', details)
        wait.until(lambda d: 'Fixture EV verdict' in text_of(d, '.market-analysis'))
        assert driver.find_elements(By.CSS_SELECTOR, '.market-analysis .ev-table')
        iev_button = wait.until(lambda d: d.find_element(By.XPATH, "//button[text()='Hitung IEP / IEV dari buku pesanan']"))
        driver.execute_script('arguments[0].click()', iev_button)
        wait.until(lambda d: 'Fixture: buku tidak bersilangan' in text_of(d, '.market-analysis'))
        count = hits['/api/markets/polymarket']
        wait.until(lambda d: hits['/api/markets/polymarket'] > count + 1)
        wait.until(lambda d: 'Fixture: buku tidak bersilangan' in text_of(d, '.market-analysis'))
        wait.until(lambda d: 'Claude Opus 5 hands-on' in text_of(d, '#llm-radar'))
        wait.until(lambda d: 'claude-opus-5' in text_of(d, '#llm-claude'))

        # Deadlines: countdown, direction, source failure said out loud, detail with EV and news.
        driver.get('http://127.0.0.1:5097/deadlines')
        wait.until(lambda d: 'Will the Fixture Fed cut rates by Friday?' in text_of(d, '#d-table'))
        radar = SimpleText(text_of(driver, '#d-table'))
        assert '▲ naik 71.0%' in radar.text, radar.text
        assert 'Fixture: diblokir' in radar.text
        assert 'hari' in text_of(driver, '#d-table .dl-deadline .left')
        wait.until(lambda d: 'Fixture effect reading' in text_of(d, '#d-effect'))
        row = driver.find_element(By.CSS_SELECTOR, 'tr[data-market]')
        driver.execute_script('arguments[0].click()', row)
        wait.until(lambda d: 'Fixture EV verdict' in text_of(d, '#d-detail'))
        evidence_button = driver.find_element(By.XPATH, "//button[text()='Analisis berita (ML): relevansi & nada']")
        driver.execute_script('arguments[0].click()', evidence_button)
        wait.until(lambda d: 'Fixture relevant headline' in text_of(d, '#d-detail'))
        assert driver.find_elements(By.XPATH, "//button[text()='Paper trade: Yes']")
        assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth')
        wait.until(splash_gone)
        driver.save_screenshot('artifacts/deadlines.png')

        # Paper test: shadow win rate next to break-even, ledger, model report.
        driver.get('http://127.0.0.1:5097/paper')
        wait.until(lambda d: 'Fixture paper verdict' in text_of(d, '#t-verdict'))
        assert '61.1%' in text_of(driver, '#t-win')
        assert 'break-even 55.0%' in text_of(driver, '.stat.hero')
        wait.until(lambda d: 'Fixture open position?' in text_of(d, '#t-ledger'))
        wait.until(lambda d: 'Fixture verdict' in text_of(d, '#m-report'))
        assert 'Fixture effect reading' in text_of(driver, '#m-report')
        wait.until(splash_gone)
        driver.save_screenshot('artifacts/paper.png')

        # News: the learning catalog and the extractive story digest.
        driver.get('http://127.0.0.1:5097/news')
        wait.until(lambda d: 'Fixture Fed feed' in text_of(d, '#catalog'))
        wait.until(lambda d: 'Fixture story about rates' in text_of(d, '#ml-news'))
        assert driver.find_elements(By.CSS_SELECTOR, '#cat option[value="Official"]')

        driver.set_window_size(390, 844)
        for page in ('deadlines', 'paper'):
            driver.get('http://127.0.0.1:5097/' + page)
            wait.until(lambda d: 'Fixture' in text_of(d, 'main'))
            assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth'), page

        driver.get('http://127.0.0.1:5097/tech')
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '.market-analysis'))
        assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth')
        driver.save_screenshot('artifacts/tech-monitor-mobile.png')

        driver.set_window_size(390, 844)
        driver.get('http://127.0.0.1:5097/esports')
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '#schedule .odds-bar'))
        assert driver.execute_script('return document.documentElement.scrollWidth <= window.innerWidth')

        print('Browser passed: overview ML radar + snapshot + stories + public data, sources inventory filters, '
              'sector ML card + detail, six pages, streaming headlines, analysis + strategy, '
              'esports odds/form/logos, soccer 1X2, on-chain market names, EV + IEV surviving refresh, '
              'LLM radar, deadlines radar + detail, paper test + model report, news catalog + stories, '
              'mobile width.')
    finally:
        driver.quit()
        server.shutdown()


if __name__ == '__main__':
    Path('artifacts').mkdir(exist_ok=True)
    main()
