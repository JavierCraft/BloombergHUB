"""Read-only prediction markets: Manifold order books and Polymarket Gamma.
Connectivity is checked at request time; errors are not inferred from old audits.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

MANIFOLD = "https://api.manifold.markets/v0"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"
USER_AGENT = "BloombergHub/1.0 (riset pasar prediksi)"
TIMEOUT = 20


def _get(url: str, params: dict | list[tuple[str, Any]] | None = None, label: str = "") -> Any:
    import requests

    from src.core.errors import NetworkBlocked, RateLimited, UpstreamError

    host = url.split("/")[2] if "//" in url else url
    name = label or host
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT,
                            headers={"User-Agent": USER_AGENT})
    except requests.exceptions.SSLError:
        raise NetworkBlocked(
            host, "sertifikat tidak cocok — ciri halaman pemblokir menyamar"
        ) from None
    except requests.exceptions.Timeout:
        raise NetworkBlocked(host, "tidak menjawab tepat waktu") from None
    except requests.exceptions.ConnectionError:
        raise NetworkBlocked(host, "tidak bisa terhubung") from None

    if resp.status_code == 429:
        raise RateLimited(name, 30)
    if resp.status_code >= 400:
        # Say what the upstream said. "Manifold sedang bermasalah" hid a plain
        # "offset must be <= 1000" — our request, not their outage.
        detail = ""
        try:
            payload = resp.json()
            detail = str(payload.get("message") or payload.get("error") or "")[:160] \
                if isinstance(payload, dict) else ""
        except ValueError:
            pass
        raise UpstreamError(name, f"HTTP {resp.status_code}" + (f": {detail}" if detail else ""),
                            resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise UpstreamError(name, "jawaban bukan JSON", resp.status_code) from None


class Manifold:
    """Pasar prediksi yang bisa dibuka dari sini. Tanpa kunci, tanpa pendaftaran."""

    @staticmethod
    def _summarise(m: dict) -> dict:
        prob = m.get("probability")
        return {
            "id": m.get("id"),
            "question": m.get("question"),
            "probability": round(prob, 4) if isinstance(prob, (int, float)) else None,
            "probability_pct": f"{prob * 100:.1f}%" if isinstance(prob, (int, float)) else None,
            "volume": round(m.get("volume") or 0),
            "liquidity": round(m.get("totalLiquidity") or 0),
            "outcome_type": m.get("outcomeType"),
            "closes_at": m.get("closeTime"),
            "creator": m.get("creatorName"),
            "url": m.get("url"),
        }

    def search(self, term: str, limit: int = 12, sort: str = "score") -> list[dict]:
        """Cari pasar berdasarkan kata kunci apa pun."""
        data = _get(f"{MANIFOLD}/search-markets", {
            "term": term, "limit": min(50, limit), "sort": sort,
            "filter": "open", "contractType": "BINARY",
        }, "Manifold")
        return [self._summarise(m) for m in (data or [])]

    def most_active(self, limit: int = 12) -> list[dict]:
        data = _get(f"{MANIFOLD}/search-markets", {
            "term": "", "limit": min(50, limit), "sort": "liquidity",
            "filter": "open", "contractType": "BINARY",
        }, "Manifold")
        return [self._summarise(m) for m in (data or [])]

    def market(self, market_id: str) -> dict:
        return self._summarise(_get(f"{MANIFOLD}/market/{market_id}", None, "Manifold"))

    def _raw_bets(self, market_id: str, limit: int = 200) -> list[dict]:
        return _get(f"{MANIFOLD}/bets",
                    {"contractId": market_id, "limit": min(1000, limit)}, "Manifold") or []

    def order_book(self, market_id: str) -> dict:
        """Susun buku pesanan dari pesanan limit yang masih menunggu.

        Pesanan YES jadi bid, pesanan NO jadi ask — keduanya dinyatakan dalam
        harga YES, jadi bisa diadu langsung.
        """
        raw = self._raw_bets(market_id, 1000)
        resting = [
            b for b in raw
            if b.get("limitProb") is not None
            and not b.get("isFilled") and not b.get("isCancelled")
        ]

        def collect(outcome: str) -> list[dict]:
            by_price: dict[float, dict] = {}
            for b in resting:
                if b.get("outcome") != outcome:
                    continue
                price = round(float(b["limitProb"]), 4)
                left = float(b.get("orderAmount") or 0) - float(b.get("amount") or 0)
                if left <= 0 or not 0 < price < 1:
                    continue
                slot = by_price.setdefault(price, {"price": price, "size": 0.0, "shares": 0.0,
                                                   "orders": 0})
                slot["size"] += left
                # `size` is mana still to spend. Shares are what an auction matches:
                # a YES order at p buys left/p shares, a NO order at p buys left/(1-p).
                slot["shares"] += left / price if outcome == "YES" else left / (1 - price)
                slot["orders"] += 1
            rows = list(by_price.values())
            for r in rows:
                r["size"] = round(r["size"])
                r["shares"] = round(r["shares"], 1)
                r["price_pct"] = f"{r['price'] * 100:.1f}%"
            return rows

        bids = sorted(collect("YES"), key=lambda r: -r["price"])
        asks = sorted(collect("NO"), key=lambda r: r["price"])

        best_bid = bids[0]["price"] if bids else None
        best_ask = asks[0]["price"] if asks else None
        spread = (round(best_ask - best_bid, 4)
                  if best_bid is not None and best_ask is not None else None)

        from src.markets.orderbook import from_manifold

        return {
            "iev": from_manifold({"bids": bids, "asks": asks}),
            "market_id": market_id,
            "bids": bids[:15],
            "asks": asks[:15],
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": f"{spread * 100:.1f}%" if spread is not None else None,
            "resting_orders": len(resting),
            "resting_value": round(sum(
                float(b.get("orderAmount") or 0) - float(b.get("amount") or 0)
                for b in resting
            )),
            "note": (
                "Buku ini berisi pesanan limit yang belum terisi. Pasar Manifold "
                "memakai penetapan harga otomatis, jadi transaksi tetap bisa terjadi "
                "walaupun bukunya kosong."
            ),
        }

    def recent_trades(self, market_id: str, limit: int = 40) -> list[dict]:
        """Aliran taruhan sungguhan — berapa dipasang, dan harganya bergeser ke
        mana. Inilah bagian 'keramaian' yang tidak terlihat dari harga saja.

        Manifold tidak mengirim nama pemasang di jalur ini, hanya nomor
        penggunanya. Yang ditampilkan karena itu adalah kode pendek dari nomor
        tersebut — bukan nama karangan. Kegunaannya tetap ada: kalau kode yang
        sama muncul berkali-kali, satu orang sedang menumpuk posisi.
        """
        rows = []
        for b in self._raw_bets(market_id, max(limit * 3, 120)):
            if b.get("isCancelled") or not b.get("amount"):
                continue
            before = b.get("probBefore")
            after = b.get("probAfter")
            uid = b.get("userId") or ""
            rows.append({
                "time": b.get("createdTime"),
                "trader": b.get("userName") or (f"#{uid[:6]}" if uid else "unknown"),
                "outcome": b.get("outcome"),
                "size": round(float(b.get("amount") or 0), 2),
                "price_before": round(before, 4) if isinstance(before, (int, float)) else None,
                "price_after": round(after, 4) if isinstance(after, (int, float)) else None,
                "move_pp": (round((after - before) * 100, 2)
                            if isinstance(before, (int, float))
                            and isinstance(after, (int, float)) else None),
                "via_api": bool(b.get("isApi")),
            })
        rows.sort(key=lambda r: r["time"] or 0, reverse=True)
        return rows[:limit]

    def closing_soon(self, days: float = 7, limit: int = 100) -> list[dict]:
        """Open binary markets whose close time falls within `days`, soonest first."""
        now = datetime.now(timezone.utc).timestamp() * 1000
        horizon = now + days * 86_400_000
        data = _get(f"{MANIFOLD}/search-markets", {
            "term": "", "filter": "open", "contractType": "BINARY", "sort": "close-date",
            "limit": min(1000, max(limit * 3, 100)),
        }, "Manifold") or []
        rows = [m for m in data
                if m.get("closeTime") and now < m["closeTime"] <= horizon
                and m.get("token") in (None, "MANA")]
        rows.sort(key=lambda m: m["closeTime"])
        return rows[:limit]

    def raw_market(self, market_id: str) -> dict:
        return _get(f"{MANIFOLD}/market/{market_id}", None, "Manifold")

    def price_changes(self, market_id: str, now_ms: float | None = None) -> dict:
        """Price now, a day ago, and a week ago, from the trade history.

        Manifold's market object carries no price-change fields, so they are
        rebuilt from trades — the same way the training data is built, which is
        the point: live features must be computed like historical ones.
        """
        now = now_ms or datetime.now(timezone.utc).timestamp() * 1000
        week = now - 7.5 * 86_400_000
        trades: list[tuple[int, float, float | None]] = []
        before = None
        complete = False
        for _ in range(3):
            params: dict[str, Any] = {"contractId": market_id, "limit": 1000}
            if before:
                params["before"] = before
            batch = _get(f"{MANIFOLD}/bets", params, "Manifold") or []
            for b in batch:
                if b.get("createdTime") is not None and b.get("probAfter") is not None:
                    trades.append((int(b["createdTime"]), float(b["probAfter"]),
                                   b.get("probBefore")))
            if len(batch) < 1000 or (batch[-1].get("createdTime") or 0) < week:
                complete = True
                break
            before = batch[-1].get("id")
        trades.sort()

        def price_at(t: float) -> float | None:
            last = None
            for created, after, prob_before in trades:
                if created > t:
                    if last is None and complete and prob_before is not None:
                        return float(prob_before)
                    break
                last = after
            return last

        return {"p_1d_ago": price_at(now - 86_400_000), "p_7d_ago": price_at(now - 7 * 86_400_000),
                "trades_7d": sum(1 for t in trades if t[0] >= now - 7 * 86_400_000),
                "complete": complete}

    def overview(self, market_id: str) -> dict:
        """Harga, buku pesanan, dan transaksi terakhir dalam satu panggilan."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f_market = pool.submit(self.market, market_id)
            f_book = pool.submit(self.order_book, market_id)
            f_trades = pool.submit(self.recent_trades, market_id, 40)
            return {
                "market": f_market.result(),
                "book": f_book.result(),
                "trades": f_trades.result(),
            }


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


class Polymarket:
    """Read-only Gamma search and public CLOB books."""

    @staticmethod
    def _outcomes(m: dict) -> list[dict]:
        try:
            labels = _json_list(m.get("outcomes"))
            prices = _json_list(m.get("outcomePrices"))
            if len(labels) != len(prices):
                return []
            return [{"outcome": label, "price": float(price)} for label, price in zip(labels, prices)
                    if math.isfinite(float(price)) and 0 <= float(price) <= 1]
        except (ValueError, TypeError):
            return []

    @staticmethod
    def _summary(m: dict) -> dict:
        events = m.get("events") or []
        event = events[0] if events and isinstance(events[0], dict) else {}
        slug = m.get("event_slug") or event.get("slug") or m.get("slug", "")
        return {
            "id": m.get("conditionId"),
            "question": m.get("question"),
            "volume": m.get("volumeNum", m.get("volume")),
            "liquidity": m.get("liquidityNum", m.get("liquidity")),
            "volume24h": m.get("volume24hr"),
            "outcomes": Polymarket._outcomes(m),
            "clob_token_ids": [str(t) for t in _json_list(m.get("clobTokenIds"))],
            "updated_at": m.get("updatedAt"),
            "created_at": m.get("createdAt"),
            "end_date": m.get("endDate"),
            "rules": m.get("description"),
            "resolution_source": m.get("resolutionSource"),
            "change1h": m.get("oneHourPriceChange"),
            "change24h": m.get("oneDayPriceChange"),
            "change1w": m.get("oneWeekPriceChange"),
            "change1mo": m.get("oneMonthPriceChange"),
            "best_bid": m.get("bestBid"),
            "best_ask": m.get("bestAsk"),
            "last_trade_price": m.get("lastTradePrice"),
            "start_date": m.get("startDate"),
            "slug": m.get("slug"),
            "event_title": event.get("title"),
            "spread": m.get("spread"),
            "closed": bool(m.get("closed")),
            "uma_status": m.get("umaResolutionStatus"),
            "url": f"https://polymarket.com/event/{slug}",
        }

    def closing_soon(self, days: float = 7, limit: int = 100) -> list[dict]:
        """Active markets whose `endDate` falls within `days`, soonest first.

        Filtering happens on both sides: Gamma is asked for the date window, and
        the answer is filtered again here, so a parameter Gamma ignores cannot
        let a market from next year into a "closing this week" list.
        """
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=days)
        params = {"active": "true", "closed": "false", "limit": min(500, max(limit * 2, 50)),
                  "end_date_min": now.isoformat(timespec="seconds"),
                  "end_date_max": horizon.isoformat(timespec="seconds"),
                  "order": "endDate", "ascending": "true"}
        from src.core.errors import UpstreamError

        try:
            data = _get(f"{POLYMARKET_GAMMA}/markets", params, "Polymarket") or []
        except UpstreamError:
            params.pop("order")
            params.pop("ascending")
            data = _get(f"{POLYMARKET_GAMMA}/markets", params, "Polymarket") or []

        rows = []
        for m in data:
            if m.get("closed") or not m.get("active", True):
                continue
            try:
                end = datetime.fromisoformat(str(m.get("endDate")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if now < end <= horizon:
                rows.append(self._summary(m))
        rows.sort(key=lambda r: r["end_date"] or "")
        return rows[:limit]

    def by_condition(self, condition_ids: list[str]) -> dict[str, dict]:
        """Raw Gamma markets for settlement, keyed by lower-case condition id."""
        found: dict[str, dict] = {}
        wanted = [c for c in dict.fromkeys(str(c).lower() for c in condition_ids) if c]
        for start in range(0, len(wanted), 20):
            batch = wanted[start:start + 20]
            for extra in ([], [("closed", "true")]):
                missing = [c for c in batch if c not in found]
                if not missing:
                    break
                rows = _get(f"{POLYMARKET_GAMMA}/markets",
                            [("condition_ids", c) for c in missing] + extra, "Polymarket") or []
                for m in rows:
                    key = str(m.get("conditionId") or "").lower()
                    if key:
                        found[key] = m
        return found

    def search(self, term: str = "", limit: int = 12, sector: str = "") -> list[dict]:
        if sector and not term:
            tags = {'technology':'tech','culture':'pop-culture','politics':'politics',
                    'soccer':'soccer','esports':'esports'}
            events = _get(f"{POLYMARKET_GAMMA}/events",
                          {'tag_slug':tags[sector], 'active':'true','closed':'false',
                           'limit':limit,'order':'volume24hr','ascending':'false'}, 'Polymarket')
            data = [dict(m, event_slug=e.get('slug')) for e in events for m in e.get('markets',[])]
            def volume(m):
                try:
                    value = float(m.get('volume24hr') or 0)
                    return value if math.isfinite(value) else 0
                except (TypeError,ValueError):
                    return 0
            data.sort(key=volume, reverse=True)
        elif term:
            result = _get(f"{POLYMARKET_GAMMA}/public-search",
                          {"q": term, "limit_per_type": limit, "events_status": "active"}, "Polymarket")
            data = [dict(m, event_slug=e.get("slug")) for e in result.get("events", [])
                    for m in e.get("markets", [])]
        else:
            data = _get(f"{POLYMARKET_GAMMA}/markets",
                        {"limit": limit, "active": "true", "closed": "false",
                         "order": "volume24hr", "ascending": "false"}, "Polymarket")
        return [self._summary(m) for m in (data or [])
                if not m.get("closed") and m.get("active", True)][:limit]

    def order_book(self, token_id: str) -> dict:
        return _get(f"{POLYMARKET_CLOB}/book", {"token_id": token_id}, "Polymarket")

    @staticmethod
    def status() -> dict:
        return {
            "reachable": None,
            "hosts": [POLYMARKET_GAMMA, POLYMARKET_CLOB],
            "reason": "Status koneksi mengikuti pengambilan terbaru di panel Polymarket.",
            "alternative": "Lapisan penyelesaiannya tetap terbaca di blockchain Polygon. "
                           "Lihat halaman On-Chain.",
        }


def related_markets(query: str, limit: int = 8) -> dict:
    """Hubungkan sebuah topik berita dengan pasar prediksi yang membahasnya.

    Ini jembatan antara halaman berita dan halaman pasar: satu peristiwa, lalu
    berapa peluang yang orang lain berikan padanya dengan uang sungguhan.
    """
    out: dict[str, Any] = {"query": query, "manifold": [], "polymarket": None, "failed": []}

    try:
        out["manifold"] = Manifold().search(query, limit=limit)
    except Exception as exc:
        out["failed"].append({
            "source": "Manifold",
            "reason": getattr(exc, "message", None) or str(exc)[:140],
        })

    out["polymarket"] = Polymarket.status()
    return out
