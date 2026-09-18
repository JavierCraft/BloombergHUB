"""Turn an on-chain `conditionId` back into the market it belongs to.

The On-Chain page reads ConditionalTokens events straight from Polygon. Those
events carry a `conditionId` and an amount, and nothing else — no title, no
price. Until now the page said so and stopped there, because Gamma was blocked.

Gamma answers from this connection again (probed 11 September 2026), and it
accepts `condition_ids` as a repeatable query parameter. So the id can be looked
up and the event labelled with the question it actually settles, the outcome
prices at the time of the lookup, and a link to the market.

Three honesty rules this module keeps:

  * a `conditionId` that Gamma does not know stays unresolved. ConditionalTokens
    is a shared Gnosis contract — other services use it too, and a miss is a real
    answer, not a gap to fill with a guess.
  * the price returned is the price **now**, at lookup time. It is not the price
    the event executed at; split/merge/redeem events carry no execution price at
    all. Callers get `price_as_of` so the difference stays visible.
  * a Gamma failure degrades to "unresolved", never to a wrong label.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable

from src.markets import POLYMARKET_GAMMA, _get

# Gamma took 30 repeated `condition_ids` without complaint; 20 keeps the URL
# short enough for any proxy in between and still means few round trips.
CHUNK = 20


def _outcomes(market: dict) -> list[dict]:
    """Parse Gamma's outcome arrays, which arrive as JSON *strings*."""
    try:
        labels = market.get("outcomes") or []
        prices = market.get("outcomePrices") or []
        labels = json.loads(labels) if isinstance(labels, str) else labels
        prices = json.loads(prices) if isinstance(prices, str) else prices
        if len(labels) != len(prices):
            return []
        rows = []
        for label, price in zip(labels, prices):
            value = float(price)
            if not math.isfinite(value) or not 0 <= value <= 1:
                return []
            rows.append({"outcome": label, "price": value})
        return rows
    except (TypeError, ValueError):
        return []


def _event_slug(market: dict) -> str:
    events = market.get("events") or []
    if events and isinstance(events, list) and isinstance(events[0], dict):
        return events[0].get("slug") or ""
    return market.get("slug") or ""


def _summarise(market: dict) -> dict:
    outcomes = _outcomes(market)
    leader = max(outcomes, key=lambda o: o["price"]) if outcomes else None
    slug = _event_slug(market)
    return {
        "question": market.get("question"),
        "outcomes": outcomes,
        "leader": leader["outcome"] if leader else None,
        "leader_price": round(leader["price"], 4) if leader else None,
        "leader_pct": f"{leader['price'] * 100:.1f}%" if leader else None,
        "volume24h": market.get("volume24hr"),
        "liquidity": market.get("liquidityNum", market.get("liquidity")),
        "end_date": market.get("endDate"),
        "closed": bool(market.get("closed")),
        "url": f"https://polymarket.com/event/{slug}" if slug else None,
    }


def markets_by_condition(condition_ids: Iterable[str]) -> dict[str, Any]:
    """Look up many condition ids at once.

    Returns `{"markets": {condition_id: summary}, "asked": n, "resolved": n,
    "price_as_of": iso, "failed": reason|None}`. Never raises: the caller is a
    display path that must keep working when Gamma does not.
    """
    wanted = []
    seen: set[str] = set()
    for cid in condition_ids:
        key = str(cid or "").lower()
        if key.startswith("0x") and len(key) == 66 and key not in seen:
            seen.add(key)
            wanted.append(key)

    found: dict[str, dict] = {}
    failed: str | None = None

    for start in range(0, len(wanted), CHUNK):
        batch = wanted[start:start + CHUNK]
        try:
            rows = _get(f"{POLYMARKET_GAMMA}/markets",
                        [("condition_ids", cid) for cid in batch], "Polymarket Gamma")
        except Exception as exc:
            failed = getattr(exc, "message", None) or str(exc)[:140]
            break
        for market in rows or []:
            cid = str(market.get("conditionId") or "").lower()
            if cid in seen:
                found[cid] = _summarise(market)

    return {
        "markets": found,
        "asked": len(wanted),
        "resolved": len(found),
        "price_as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "failed": failed,
        "note": (
            "Judul dan harga diambil dari Gamma saat pencarian ini, bukan harga "
            "saat event terjadi. Event split/merge/redeem memang tidak memuat "
            "harga eksekusi. Condition ID yang tidak dikenali Gamma dibiarkan "
            "kosong — ConditionalTokens juga dipakai layanan selain Polymarket."
        ),
    }
