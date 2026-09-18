"""Decode ConditionalTokens events using the published Gnosis ABI.

https://github.com/gnosis/conditional-tokens-contracts/blob/master/contracts/ConditionalTokens.sol
Amounts are raw integer strings unless the collateral's decimals are known.
"""
from eth_abi import decode


def decode_event(log, kind):
    from src.polymarket.onchain import _hex, USDC_E, USDC_NATIVE
    topics = [_hex(t) for t in log['topics']]
    raw = bytes.fromhex(_hex(log['data'])[2:])
    row = {'event': kind, 'block': int(log['blockNumber']),
           'log_index': int(log['logIndex']), 'transaction': _hex(log['transactionHash']),
           'contract': log['address'], 'raw_data': _hex(log['data']), 'topics': topics,
           'price': None, 'price_note': 'Event settlement tidak memuat harga eksekusi pasar.'}
    row['url'] = 'https://polygonscan.com/tx/' + row['transaction']
    try:
        if kind in ('position_splits', 'position_merges'):
            collateral, partition, amount = decode(['address', 'uint256[]', 'uint256'], raw)
            row.update(wallet='0x'+topics[1][-40:], parent_collection=topics[2],
                       condition_id=topics[3], collateral=collateral,
                       partition=[str(x) for x in partition], amount_raw=str(amount))
        elif kind == 'redemptions':
            condition, partition, amount = decode(['bytes32', 'uint256[]', 'uint256'], raw)
            row.update(wallet='0x'+topics[1][-40:], collateral='0x'+topics[2][-40:],
                       parent_collection=topics[3], condition_id=_hex(condition),
                       partition=[str(x) for x in partition], amount_raw=str(amount))
        elif kind in ('markets_resolved', 'markets_created'):
            types = ['uint256', 'uint256[]'] if kind == 'markets_resolved' else ['uint256']
            values = decode(types, raw)
            row.update(condition_id=topics[1], oracle='0x'+topics[2][-40:],
                       question_id=topics[3], outcome_slots=values[0])
            if len(values) > 1:
                row['payout_numerators'] = [str(n) for n in values[1]]
                row['payout_denominator'] = str(sum(values[1]))
        else:
            row['decode_note'] = 'ABI adapter belum dipetakan; lihat log mentah.'
        if row.get('collateral', '').lower() in (USDC_E.lower(), USDC_NATIVE.lower()):
            from decimal import Decimal
            row['amount'] = str(Decimal(row['amount_raw']) / Decimal(10**6))
            row['unit'] = 'USDC.e' if row['collateral'].lower() == USDC_E.lower() else 'USDC'
    except Exception as exc:
        row['decode_note'] = 'Log tidak berhasil didekode: ' + type(exc).__name__
    return row
