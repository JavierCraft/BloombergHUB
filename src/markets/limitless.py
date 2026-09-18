"""Limitless Exchange — a real-money (USDC on Base) prediction market that
answers from this connection while Polymarket, Kalshi and SX Bet do not.

Probed 17 September 2026: 603 active markets — football matches (162), props
(114), esports (75), politics (64), crypto (71 incl. 5-minute up/down), daily
stock up/down, pre-TGE token launches. Everything here is read-only.

Shapes worth knowing, all measured:

  * `prices` is `[yes, no]` — usually fractions, sometimes cents (`[50, 50]`).
  * `tradePrices.buy.market[0]` is the best YES ask and `sell.market[0]` the best
    YES bid; they match `/orderbook`. A market with no real book shows 0.001 /
    0.999 — a 99.8-point spread, i.e. no price at all, and treated as such.
  * `/orderbook` sizes are in millionths of a share (6-decimal USDC collateral).
  * "group" markets carry no price of their own and are skipped here.
  * `/markets/{slug}` carries `status` and `winningOutcomeIndex` for settlement.
"""
from __future__ import annotations

import concurrent.futures
import html as html_mod
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from src.markets import _get

LIMITLESS = "https://api.limitless.exchange"
SITE = "https://limitless.exchange/markets"

# Category ids from /categories, grouped by the sector pages that show them.
SECTOR_CATEGORIES: dict[str, tuple[int, ...]] = {
    "politics": (51, 23, 65),          # Politics, Economy, Military
    "technology": (19, 70, 71, 43),     # Company News, AI Stocks, Earnings, Pre-TGE
    "culture": (50,),                   # Specials
    "soccer": (49, 57, 1, 59, 60),      # Football Matches, Football, Sports, NHL, F1
    "sports": (49, 57, 1, 59, 60),
    "esports": (53,),
    "crypto": (2, 28, 61, 62, 67),
    "markets": (30, 8, 64, 23),         # Daily up/down, Financials, Oil & Gas, Economy
}
# Recurring coin flips shorter than this are data, not markets a model trained on
# 6-hour-plus horizons can say anything about.
MIN_HOURS = 6
MAX_SPREAD = 0.25


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value) if isinstance(value, str) else None
        return out if isinstance(out, list) else []
    except ValueError:
        return []


def _clean_html(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def _iso(ms: Any) -> str | None:
    value = _f(ms)
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="seconds")


def summary(m: dict) -> dict | None:
    """One Limitless market in the same shape as `Polymarket._summary`."""
    if m.get("marketType") not in (None, "single"):
        return None
    prices = [_f(p) for p in _list(m.get("prices"))]
    if len(prices) != 2 or None in prices:
        return None
    cents = sum(prices) > 1.5       # cents, not fractions
    if cents:
        prices = [p / 100 for p in prices]
    if not all(0 <= p <= 1 for p in prices):
        return None
    labels = _list(m.get("outcomeTokens")) or ["Yes", "No"]
    trade = m.get("tradePrices") if isinstance(m.get("tradePrices"), dict) else {}
    buy = (trade.get("buy") or {}).get("market") or []
    sell = (trade.get("sell") or {}).get("market") or []
    ask = _f(buy[0]) if buy else None
    bid = _f(sell[0]) if sell else None
    if cents and ask is not None and bid is not None and max(ask, bid) > 1:
        ask, bid = ask / 100, bid / 100
    spread = round(ask - bid, 4) if ask is not None and bid is not None else None
    # A side of 0 or 1 means nobody is quoting it (measured: "Map 2 Winner" markets
    # show ask 0.0 before the map starts). Half a book is no entry price.
    if (ask is None or bid is None or not 0 < bid <= ask < 1
            or (spread is not None and spread > MAX_SPREAD)):
        bid = ask = spread = None   # no real book: a 0.001/0.999 quote is not a price
    tokens = m.get("tokens") if isinstance(m.get("tokens"), dict) else {}
    slug = m.get("slug") or ""
    categories = [c for c in _list(m.get("categories")) if isinstance(c, str)]
    return {
        "source": "limitless",
        "id": slug,
        "question": m.get("title"),
        "volume": _f(m.get("volumeFormatted")),
        "liquidity": None,
        "volume24h": None,
        "outcomes": [{"outcome": str(labels[0]).title() if labels else "Yes", "price": prices[0]},
                     {"outcome": str(labels[1]).title() if len(labels) > 1 else "No", "price": prices[1]}],
        "clob_token_ids": [str(tokens.get("yes") or ""), str(tokens.get("no") or "")],
        "created_at": m.get("createdAt"),
        "start_date": m.get("startAt") or m.get("createdAt"),
        "end_date": _iso(m.get("expirationTimestamp")),
        "rules": _clean_html(m.get("description"))[:3000],
        "resolution_source": None,
        "change1h": None, "change24h": None, "change1w": None, "change1mo": None,
        "best_bid": bid, "best_ask": ask, "spread": spread,
        "last_trade_price": None,
        "slug": slug,
        "event_title": ", ".join(categories[:2]) or None,
        "closed": bool(m.get("expired")),
        "status": m.get("status"),
        "winning_index": m.get("winningOutcomeIndex"),
        "url": f"{SITE}/{slug}" if slug else "https://limitless.exchange",
    }


class Limitless:
    """Read-only client."""

    def categories(self) -> list[dict]:
        return _get(f"{LIMITLESS}/categories", None, "Limitless") or []

    def active(self, category_id: int | None = None, pages: int = 4, limit: int = 25) -> list[dict]:
        path = f"{LIMITLESS}/markets/active" + (f"/{category_id}" if category_id else "")

        def page(n: int) -> list[dict]:
            data = _get(path, {"page": n, "limit": limit}, "Limitless") or {}
            return data.get("data") or []

        rows: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for batch in pool.map(page, range(1, pages + 1)):
                rows.extend(batch)
        seen, out = set(), []
        for raw in rows:
            item = summary(raw)
            if item and item["id"] not in seen:
                seen.add(item["id"])
                out.append(item)
        return out

    def categories_active(self, category_ids: tuple[int, ...], pages: int = 2) -> list[dict]:
        """Active markets of several categories, fetched side by side."""
        if not category_ids:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(2, len(category_ids))) as pool:
            batches = list(pool.map(lambda cid: self.active(cid, pages=pages), category_ids))
        return [item for batch in batches for item in batch]

    def sector(self, sector: str, limit: int = 12) -> list[dict]:
        """Markets of a sector, liquid-looking first, too-short coin flips last."""
        rows: list[dict] = []
        seen: set[str] = set()
        for item in self.categories_active(SECTOR_CATEGORIES.get(sector, ()), pages=2):
            if item["id"] not in seen:
                seen.add(item["id"])
                rows.append(item)
        now = datetime.now(timezone.utc).timestamp()

        def rank(item: dict):
            end = item.get("end_date")
            hours = ((datetime.fromisoformat(end).timestamp() - now) / 3600) if end else 1e9
            return (hours < MIN_HOURS, item.get("best_ask") is None, -(item.get("volume") or 0))

        rows.sort(key=rank)
        return rows[:limit]

    def search(self, term: str, limit: int = 12) -> list[dict]:
        data = _get(f"{LIMITLESS}/markets/search", {"query": term, "limit": min(50, limit * 2)},
                    "Limitless") or {}
        out = []
        for raw in data.get("markets") or []:
            item = summary(raw)
            if item:
                out.append(item)
        return out[:limit]

    def closing_soon(self, days: float = 7, limit: int = 100) -> list[dict]:
        now = datetime.now(timezone.utc).timestamp()
        horizon = now + days * 86400
        rows = [m for m in self.active(pages=26)
                if m.get("end_date") and now < datetime.fromisoformat(m["end_date"]).timestamp() <= horizon]
        rows.sort(key=lambda m: m["end_date"])
        return rows[:limit]

    def market(self, slug: str) -> dict:
        return _get(f"{LIMITLESS}/markets/{slug}", None, "Limitless") or {}

    def order_book(self, slug: str) -> dict:
        """Book in YES prices and shares (sizes arrive in millionths)."""
        raw = _get(f"{LIMITLESS}/markets/{slug}/orderbook", None, "Limitless") or {}

        def side(rows: list) -> list[dict]:
            out = []
            for r in rows or []:
                price, size = _f(r.get("price")), _f(r.get("size"))
                if price is not None and size is not None:
                    out.append({"price": price, "size": size / 1_000_000})
            return out

        return {"bids": side(raw.get("bids")), "asks": side(raw.get("asks")),
                "last_trade_price": _f(raw.get("lastTradePrice")), "midpoint": _f(raw.get("midpoint"))}
