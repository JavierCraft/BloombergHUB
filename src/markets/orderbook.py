"""Indicative Equilibrium Price and Volume (IEP / IEV) for a prediction-market
order book.

IEP and IEV are the numbers the Indonesia Stock Exchange shows during its
pre-opening and pre-closing call auctions: the price at which the most shares
would change hands if every resting order were matched right now, and that
number of shares. The matching rule is the standard call-auction uncrossing,
applied in this order:

  1. the price that maximises executable volume;
  2. among ties, the smallest surplus (volume left unmatched on one side);
  3. among ties, the price closest to a reference (last trade, else the mid);
  4. among ties, the lower price — deterministic, so a refresh cannot flip it.

Prediction markets trade continuously, not in a call auction. In a continuous
book the best bid sits below the best ask, so no price clears any volume and the
honest IEV is **zero**. That is reported as zero, with the reason, rather than
replaced by a number that looks more interesting. A crossed snapshot does occur
(a stale level, a fast market, Manifold limit orders resting against each other)
and then IEV is real.

What stays useful when IEV is zero is the same book read against a fair value.
If a model says 62% and asks rest at 55–60¢, those shares are positive-EV to
take, and taking them is exactly what would move the book to the model's price.
`fair_value_depth()` reports that volume, its cost and its expected profit. It
is labelled as volume *to the model's price*, never as IEV, because it depends
on a belief rather than on orders.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

PRICE_DP = 4


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def levels(raw: Iterable | None, size_key: str = "size") -> list[tuple[float, float]]:
    """Normalise a book side into sorted `(price, shares)` pairs.

    Accepts Polymarket CLOB levels (`{"price": "0.48", "size": "30"}` — strings),
    already-numeric dicts, or plain `(price, size)` pairs. Prices outside (0, 1)
    and non-positive sizes are dropped; equal prices are merged. A malformed
    level shrinks the book, it never distorts it.
    """
    merged: dict[float, float] = {}
    for item in raw or []:
        if isinstance(item, dict):
            price, size = _num(item.get("price")), _num(item.get(size_key))
        else:
            try:
                price, size = _num(item[0]), _num(item[1])
            except (TypeError, IndexError, KeyError):
                continue
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            continue
        key = round(price, PRICE_DP)
        merged[key] = merged.get(key, 0.0) + size
    return sorted(merged.items())


def _depth(side: list[tuple[float, float]]) -> dict:
    shares = sum(s for _, s in side)
    return {
        "levels": len(side),
        "shares": round(shares, 2),
        "notional": round(sum(p * s for p, s in side), 2),
    }


def uncross(bids: Iterable | None, asks: Iterable | None,
            reference: float | None = None, size_key: str = "size") -> dict:
    """Call-auction uncrossing over one YES book. Prices are YES probabilities."""
    bid_levels = levels(bids, size_key)
    ask_levels = levels(asks, size_key)
    best_bid = bid_levels[-1][0] if bid_levels else None
    best_ask = ask_levels[0][0] if ask_levels else None
    mid = (round((best_bid + best_ask) / 2, PRICE_DP)
           if best_bid is not None and best_ask is not None else None)
    ref = _num(reference)
    ref = ref if ref is not None and 0 < ref < 1 else mid

    base = {
        "best_bid": best_bid, "best_ask": best_ask, "mid": mid, "reference": ref,
        "bid_depth": _depth(bid_levels), "ask_depth": _depth(ask_levels),
        "method": ("Volume maksimum → surplus terkecil → terdekat ke harga acuan → "
                   "harga terendah (aturan lelang praperdagangan)."),
    }

    if not bid_levels or not ask_levels:
        return {**base, "crossed": False, "iep": None, "iev": 0.0, "iev_notional": 0.0,
                "surplus": None, "surplus_side": None,
                "reason": "Salah satu sisi buku kosong, jadi tidak ada volume yang bisa dipertemukan."}

    best = None
    for price in sorted({p for p, _ in bid_levels} | {p for p, _ in ask_levels}):
        demand = sum(s for p, s in bid_levels if p >= price)
        supply = sum(s for p, s in ask_levels if p <= price)
        volume = min(demand, supply)
        surplus = demand - supply
        key = (-volume, abs(surplus), abs(price - ref) if ref is not None else 0.0, price)
        if best is None or key < best[0]:
            best = (key, price, volume, demand, supply, surplus)

    _, price, volume, demand, supply, surplus = best
    if volume <= 0:
        return {**base, "crossed": False, "iep": None, "iev": 0.0, "iev_notional": 0.0,
                "surplus": None, "surplus_side": None,
                "reason": (f"Buku tidak bersilangan: bid tertinggi {best_bid:.3f} di bawah ask "
                           f"terendah {best_ask:.3f}, jadi tidak ada harga yang mempertemukan "
                           "volume. IEV = 0 — normal untuk pasar yang diperdagangkan terus-menerus.")}

    side = "beli" if surplus > 0 else "jual" if surplus < 0 else None
    return {
        **base,
        "crossed": True,
        "iep": price,
        "iev": round(volume, 2),
        "iev_notional": round(volume * price, 2),
        "demand_at_iep": round(demand, 2),
        "supply_at_iep": round(supply, 2),
        "surplus": round(abs(surplus), 2),
        "surplus_side": side,
        "reason": (f"Buku bersilangan: {volume:,.0f} lembar akan tertukar di {price:.3f}"
                   + (f", menyisakan surplus {side} {abs(surplus):,.0f} lembar." if side else ".")),
    }


def fair_value_depth(bids: Iterable | None, asks: Iterable | None, fair: float | None,
                     size_key: str = "size") -> dict:
    """Shares that are positive-EV to take against a fair YES probability.

    Buying YES: every ask priced below `fair`; EV per share is `fair − price`.
    Buying NO:  every bid priced above `fair` (selling YES at p is buying NO at
    1 − p); EV per share is `price − fair`.
    """
    fair = _num(fair)
    if fair is None or not 0 < fair < 1:
        return {"known": False, "reason": "Nilai wajar model tidak tersedia."}

    take = [(p, s) for p, s in levels(asks, size_key) if p < fair]
    hit = sorted(((p, s) for p, s in levels(bids, size_key) if p > fair), reverse=True)

    def summarise(rows: list[tuple[float, float]], yes_side: bool) -> dict:
        shares = sum(s for _, s in rows)
        if yes_side:
            cost = sum(p * s for p, s in rows)
            profit = sum((fair - p) * s for p, s in rows)
            worst = rows[-1][0] if rows else None
        else:
            cost = sum((1 - p) * s for p, s in rows)
            profit = sum((p - fair) * s for p, s in rows)
            worst = round(1 - rows[-1][0], PRICE_DP) if rows else None
        return {
            "levels": len(rows),
            "shares": round(shares, 2),
            "cost": round(cost, 2),
            "vwap": round(cost / shares, 4) if shares else None,
            "worst_price": worst,
            "expected_profit": round(profit, 2),
            "ev_pct": round(profit / cost * 100, 2) if cost else None,
        }

    yes = summarise(take, True)
    no = summarise(hit, False)
    side = ("YES" if yes["shares"] > no["shares"] else "NO") if (yes["shares"] or no["shares"]) else None
    return {
        "known": True,
        "fair": round(fair, 4),
        "buy_yes": yes,
        "buy_no": no,
        "side": side,
        "shares_to_fair": round(yes["shares"] + no["shares"], 2),
        "note": ("Volume yang harus tertukar agar buku bergeser ke harga model. Ini bergantung "
                 "pada keyakinan model, bukan pada pesanan — jadi bukan IEV, dan hanya berarti "
                 "sejauh model itu sendiri teruji."),
    }


def analyse_book(bids: Iterable | None, asks: Iterable | None, reference: float | None = None,
                 fair: float | None = None, size_key: str = "size") -> dict:
    """IEP/IEV plus, when a fair value is given, the depth up to it."""
    return {
        "auction": uncross(bids, asks, reference, size_key),
        "fair_value": fair_value_depth(bids, asks, fair, size_key) if fair is not None else None,
    }


def from_polymarket(book: dict | None, fair: float | None = None) -> dict:
    """CLOB `/book` payload → analysis. Sizes are shares; prices are strings."""
    book = book or {}
    return {
        **analyse_book(book.get("bids"), book.get("asks"),
                       reference=_num(book.get("last_trade_price")), fair=fair),
        "unit": "lembar",
        "source": "Polymarket CLOB",
        "tick_size": _num(book.get("tick_size")),
        "book_time": book.get("timestamp"),
    }


def from_manifold(book: dict | None, fair: float | None = None) -> dict:
    """Manifold order book (see `Manifold.order_book`) → analysis in shares."""
    book = book or {}
    return {
        **analyse_book(book.get("bids"), book.get("asks"), fair=fair, size_key="shares"),
        "unit": "lembar",
        "source": "Manifold (pesanan limit)",
        "note": ("Manifold juga punya penentu harga otomatis (AMM) di luar buku ini, jadi transaksi "
                 "tetap bisa terjadi walaupun IEV buku limit = 0."),
    }
