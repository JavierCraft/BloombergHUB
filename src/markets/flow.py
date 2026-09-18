"""Taker flow from public trades; this measures a sample, not all holders."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from src.markets import _get


def market_flow(condition_id):
    raw = _get('https://data-api.polymarket.com/trades',
               {'market': condition_id, 'limit': 100, 'takerOnly': 'true'}, 'Polymarket Data')
    groups, trades = {}, []
    for t in raw:
        if str(t.get('conditionId', '')).lower() != condition_id.lower():
            continue
        try:
            price, size = Decimal(str(t['price'])), Decimal(str(t['size']))
            if not price.is_finite() or not size.is_finite() or not 0 <= price <= 1 or size <= 0:
                continue
            stamp = datetime.fromtimestamp(int(t['timestamp']), timezone.utc).isoformat()
        except (KeyError, ValueError, TypeError, OverflowError, OSError, InvalidOperation):
            continue
        side, outcome = t.get('side'), t.get('outcome')
        if side not in ('BUY', 'SELL') or not outcome:
            continue
        row = groups.setdefault(outcome, {'buy': Decimal(0), 'sell': Decimal(0), 'wallets': set()})
        row['buy' if side == 'BUY' else 'sell'] += price*size
        if t.get('proxyWallet'):
            row['wallets'].add(t['proxyWallet'].lower())
        trades.append({'time_utc': stamp, 'outcome':outcome, 'side':side,
                       'price':str(price), 'shares':str(size), 'notional_usd':str(price*size),
                       'transaction':t.get('transactionHash')})
    total_buy = sum(g['buy'] for g in groups.values())
    outcomes = [{'outcome':outcome, 'buy_usd':str(g['buy']), 'sell_usd':str(g['sell']),
                 'net_taker_usd':str(g['buy']-g['sell']), 'wallets':len(g['wallets']),
                 'buy_share_pct':round(float(g['buy']/total_buy*100),2) if total_buy else None}
                for outcome, g in groups.items()]
    outcomes.sort(key=lambda r: Decimal(r['buy_usd']), reverse=True)
    trades.sort(key=lambda r:r['time_utc'],reverse=True)
    dominant = outcomes[0]['outcome'] if total_buy and outcomes and (
        len(outcomes)==1 or Decimal(outcomes[0]['buy_usd']) != Decimal(outcomes[1]['buy_usd'])) else None
    return {'outcomes':outcomes, 'trades':trades, 'sample_size':len(trades),
            'dominant_buy_flow':dominant,
            'from_utc':trades[-1]['time_utc'] if trades else None,
            'to_utc':trades[0]['time_utc'] if trades else None,
            'fetched_at':datetime.now(timezone.utc).isoformat(),
            'note':'Maksimal 100 transaksi taker dari Data API. Porsi dihitung dari nilai BUY (harga × shares), bukan jumlah orang, seluruh volume, atau posisi terbuka. Wallet bisa muncul di lebih dari satu outcome. SELL dipisahkan; ini bukan arus seluruh pasar.'}
