"""Markets whose deadline is close — with model probability, direction and EV.

This is the list the paper test trades from, and the "Deadlines" page shows.
Each market is normalised into one snapshot shape regardless of source:

    source, id, token_id, question, url, rules, event_title,
    created_ms, deadline_ms, days_left, hours_left,
    outcomes, p_yes, best_bid, best_ask, spread, liquidity, volume24h,
    chg_1d, chg_7d, bettors

then gets `model` (see `model.predict_market`) and `ev` (see `markets.ev`).

Polymarket is asked first. From this connection it is blocked on most days, and
when it is the list says so and carries on with Manifold — a play-money market,
labelled as such, whose prices behave differently from real-money ones.
"""
from __future__ import annotations

import concurrent.futures
import re
from typing import Any

from src.markets import Manifold, Polymarket
from src.markets.ev import expected_value
from src.ml import features, model, store

SPORTS = re.compile(
    r"\bvs\.?(?=\s)|\bversus\b|\bgame \d\b|\b(bo[1357]|map \d|o/u|over/under|spread:|handicap"
    r"|moneyline|1st half|total (goals|points|kills))\b", re.I)

MANIFOLD_COST_PP = 1.0   # Manifold has no posted spread; assume one point of slippage each way


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _time_left(ms: float) -> str:
    hours = ms / features.HOUR_MS
    if hours < 1:
        return f"{max(0, hours * 60):.0f} menit"
    if hours < 48:
        return f"{hours:.1f} jam"
    return f"{hours / 24:.1f} hari"


def polymarket_snapshot(m: dict, now_ms: int, source: str = "polymarket") -> dict | None:
    """Snapshot from a Polymarket-shaped summary (Limitless summaries share the shape)."""
    outcomes = m.get("outcomes") or []
    deadline = store.iso_to_ms(m.get("end_date"))
    if len(outcomes) != 2 or deadline is None:
        return None
    tokens = m.get("clob_token_ids") or []
    return {
        "source": source, "money": "USDC",
        "id": m.get("id"), "token_id": tokens[0] if tokens else None,
        "question": m.get("question"), "url": m.get("url"), "rules": m.get("rules"),
        "event_title": m.get("event_title"),
        "created_ms": store.iso_to_ms(m.get("start_date") or m.get("created_at")),
        "deadline_ms": deadline,
        "outcomes": outcomes, "p_yes": outcomes[0]["price"],
        "best_bid": _f(m.get("best_bid")), "best_ask": _f(m.get("best_ask")),
        "spread": _f(m.get("spread")), "liquidity": _f(m.get("liquidity")),
        "volume24h": _f(m.get("volume24h")), "volume": _f(m.get("volume")),
        "chg_1d": _f(m.get("change24h")), "chg_7d": _f(m.get("change1w")),
        "bettors": None,
    }


def manifold_snapshot(m: dict, changes: dict | None, now_ms: int) -> dict | None:
    p = _f(m.get("probability"))
    if p is None or not m.get("closeTime"):
        return None
    changes = changes or {}
    p1, p7 = changes.get("p_1d_ago"), changes.get("p_7d_ago")
    return {
        "source": "manifold", "money": "mana (uang main)",
        "id": m.get("id"), "token_id": None,
        "question": m.get("question"), "url": m.get("url"),
        "rules": m.get("textDescription") or None, "event_title": None,
        "created_ms": int(m["createdTime"]) if m.get("createdTime") else None,
        "deadline_ms": int(m["closeTime"]),
        "outcomes": [{"outcome": "Yes", "price": round(p, 4)}, {"outcome": "No", "price": round(1 - p, 4)}],
        "p_yes": round(p, 4),
        "best_bid": None, "best_ask": None, "spread": None,
        "liquidity": _f(m.get("totalLiquidity")), "volume24h": _f(m.get("volume24Hours")),
        "volume": _f(m.get("volume")),
        "chg_1d": (p - p1) if p1 is not None else None,
        "chg_7d": (p - p7) if p7 is not None else None,
        "bettors": m.get("uniqueBettorCount"),
    }


def enrich(snapshot: dict, now_ms: int) -> dict:
    left = snapshot["deadline_ms"] - now_ms
    snapshot["days_left"] = round(left / features.DAY_MS, 3)
    snapshot["hours_left"] = round(left / features.HOUR_MS, 2)
    snapshot["time_left"] = _time_left(left)
    snapshot["bucket"] = features.deadline_bucket(snapshot["days_left"])
    snapshot["deadline"] = store.ms_to_iso(snapshot["deadline_ms"])
    snapshot["created"] = store.ms_to_iso(snapshot["created_ms"]) if snapshot.get("created_ms") else None
    snapshot["model"] = model.predict_market(snapshot, now_ms)
    if snapshot["model"].get("available") and SPORTS.search(snapshot.get("question") or ""):
        # The training set is Manifold questions ("will X happen by Y"), with almost no
        # head-to-head matches; a match price is set by people who watch the teams.
        snapshot["model"].setdefault("caveats", []).append(
            "Ini pasar pertandingan. Model dilatih dari pertanyaan 'akan terjadi sebelum tanggal X', "
            "hampir tanpa pertandingan — koreksinya terhadap harga di sini paling lemah.")
    market_like = {"outcomes": snapshot["outcomes"], "best_bid": snapshot.get("best_bid"),
                   "best_ask": snapshot.get("best_ask")}
    cost = MANIFOLD_COST_PP if snapshot["source"] == "manifold" else 0.0
    snapshot["ev"] = expected_value(market_like, snapshot["model"], cost_pp=cost)
    return snapshot


CHANGES_TTL = 600        # Manifold momentum from trade history; a 1-day/7-day change barely moves in 10 minutes
CHANGES_WORKERS = 6


def _failure(source: str, exc: BaseException) -> dict:
    return {"source": source, "reason": getattr(exc, "message", None) or str(exc)[:160]}


def manifold_changes(markets: list[dict], now: int) -> list[dict | None]:
    """1-day and 7-day price change per Manifold market, cached per market.

    Rebuilding them pages through the market's trades — 1.5 seconds a market,
    measured — so a sector list of 25 markets took 10 seconds on every refresh.
    """
    from src.core import cache

    client = Manifold()

    def one(m: dict) -> dict | None:
        try:
            value, _cached, _age = cache.cached_call("manifold-changes", m["id"], CHANGES_TTL,
                                                     lambda: client.price_changes(m["id"], now))
            return value
        except Exception:  # noqa: BLE001 — momentum unknown is reported per market
            return None

    if not markets:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=CHANGES_WORKERS) as pool:
        return list(pool.map(one, markets))


def _gather(jobs: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Run one fetch per source side by side; a failing source becomes a note, not an error."""
    markets: list[dict] = []
    failed: list[dict] = []
    if not jobs:
        return markets, failed
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for future in concurrent.futures.as_completed(futures):
            try:
                markets.extend(future.result())
            except Exception as exc:  # noqa: BLE001 — one blocked source does not empty the page
                failed.append(_failure(futures[future], exc))
    order = list(jobs)
    failed.sort(key=lambda f: order.index(f["source"]))
    return markets, failed


def collect(days: float = 7, sources: tuple[str, ...] = ("polymarket", "limitless", "manifold"),
            limit: int = 60, query: str | None = None, include_sports: bool = True, min_bettors: int = 10,
            min_liquidity: float = 0.0) -> dict:
    now = store.now_ms()
    words = [w for w in (query or "").lower().split() if len(w) > 1]

    def wanted(question: str | None) -> bool:
        text = (question or "").lower()
        if words and not all(w in text for w in words):
            return False
        return include_sports or not SPORTS.search(question or "")

    def polymarket() -> list[dict]:
        out = []
        for m in Polymarket().closing_soon(days, limit=max(limit * 3, 100)):
            if not wanted(m.get("question")):
                continue
            snap = polymarket_snapshot(m, now)
            if snap and (snap["liquidity"] or 0) >= min_liquidity:
                out.append(snap)
        return out

    def limitless() -> list[dict]:
        from src.markets.limitless import Limitless

        out = []
        for m in Limitless().closing_soon(days, limit=max(limit * 3, 150)):
            # A Limitless market with no real book (spread wider than 25 points)
            # has no entry price worth an EV, so it is left out, not guessed.
            if not wanted(m.get("question")) or m.get("best_ask") is None:
                continue
            snap = polymarket_snapshot(m, now, source="limitless")
            if snap:
                out.append(snap)
        return out

    def manifold() -> list[dict]:
        listed = [m for m in Manifold().closing_soon(days, limit=max(limit * 3, 100))
                  if wanted(m.get("question")) and (m.get("uniqueBettorCount") or 0) >= min_bettors][:limit]
        out = []
        for m, change in zip(listed, manifold_changes(listed, now)):
            snap = manifold_snapshot(m, change, now)
            if snap:
                out.append(snap)
        return out

    jobs = {"Polymarket": polymarket, "Limitless": limitless, "Manifold": manifold}
    markets, failed = _gather({name: fn for name, fn in jobs.items() if name.lower() in sources})
    return _result(markets, failed, now, days, max(limit, 1) * 2, NOTE)


NOTE = ("Diurutkan dari tenggat terdekat. 'Arah' adalah perbandingan peluang model dengan harga "
        "sekarang: naik berarti model menilai YES lebih mungkin dari harganya. Limitless memakai uang "
        "sungguhan (USDC); pasarnya yang tanpa buku pesanan tidak ditampilkan. Manifold memakai "
        "uang main — perilaku harganya tidak sama dengan pasar uang sungguhan.")


def _result(markets: list[dict], failed: list[dict], now: int, days: float, keep: int, note: str) -> dict:
    seen: set[tuple[str, str]] = set()
    unique = []
    for snap in markets:
        key = (snap["source"], str(snap["id"]))
        if snap["deadline_ms"] > now and key not in seen:
            seen.add(key)
            unique.append(snap)
    markets = [enrich(s, now) for s in unique]
    markets.sort(key=lambda s: s["deadline_ms"])

    per_source: dict[str, int] = {}
    buckets: dict[str, int] = {}
    for s in markets:
        per_source[s["source"]] = per_source.get(s["source"], 0) + 1
        buckets[s["bucket"]] = buckets.get(s["bucket"], 0) + 1
    with_model = [s for s in markets if s["model"].get("available")]
    positive = [s for s in with_model if (s["ev"].get("best") or {}).get("ev_model_pct", 0) > 0]

    return {
        "fetched_at": store.now_iso(),
        "days": days,
        "count": len(markets),
        "by_source": per_source,
        "by_bucket": [{"bucket": b, "count": buckets[b]} for b in features.BUCKET_ORDER if b in buckets],
        "with_model": len(with_model),
        "positive_ev": len(positive),
        "model": model.status(),
        "failed": failed,
        "markets": markets[:keep],
        "note": note,
    }


# Manifold topics whose near-deadline markets belong on each sector page (slugs
# checked against /v0/group on 17 September 2026; "esports" alone had none
# closing within three weeks, "gaming" does).
SECTOR_TOPICS: dict[str, tuple[str, ...]] = {
    "politics": ("politics-default", "us-politics"),
    "technology": ("technology-default", "ai"),
    "culture": ("culture-default", "entertainment", "movies"),
    "soccer": ("soccer", "sports-default"),
    "sports": ("sports-default", "soccer"),
    "esports": ("esports", "gaming"),
    "crypto": ("crypto-speculation",),
    "markets": ("economics-default",),
}
SECTORS = tuple(SECTOR_TOPICS)
# Limitless files esports matches under "Sports" as well; a football page should not list them.
SECTOR_EXCLUDE: dict[str, re.Pattern] = {
    "soccer": re.compile(r"esports|counter-strike|cs2|dota|valorant|league of legends", re.I),
    "sports": re.compile(r"esports|counter-strike|cs2|dota|valorant|league of legends", re.I),
}


def sector_deadlines(sector: str, days: float = 14, limit: int = 40, min_bettors: int = 10) -> dict:
    """Near-deadline markets of one sector page — the markets the model can rate.

    Same shape as `collect`. Polymarket by sector tag when it answers, Limitless
    by the sector's categories, Manifold by the sector's topics — all three at once.
    """
    from src.markets import MANIFOLD, _get
    from src.markets.limitless import SECTOR_CATEGORIES, Limitless

    if sector not in SECTOR_TOPICS:
        raise ValueError(f"Sektor tidak dikenal: {sector}")
    now = store.now_ms()
    horizon = now + days * features.DAY_MS

    def in_window(ms: Any) -> bool:
        return isinstance(ms, (int, float)) and now < ms <= horizon

    def polymarket() -> list[dict]:
        out = []
        for m in Polymarket().search("", 50, sector):
            snap = polymarket_snapshot(m, now)
            if snap and in_window(snap["deadline_ms"]):
                out.append(snap)
        return out

    def limitless() -> list[dict]:
        exclude = SECTOR_EXCLUDE.get(sector)
        out = []
        for m in Limitless().categories_active(SECTOR_CATEGORIES.get(sector, ()), pages=4):
            if m.get("best_ask") is None:
                continue
            if exclude and exclude.search(f"{m.get('event_title') or ''} {m.get('question') or ''}"):
                continue
            snap = polymarket_snapshot(m, now, source="limitless")
            if snap and in_window(snap["deadline_ms"]):
                out.append(snap)
        return out

    def manifold() -> list[dict]:
        def topic(slug: str) -> list[dict]:
            return _get(f"{MANIFOLD}/search-markets", {
                "term": "", "filter": "open", "contractType": "BINARY", "sort": "close-date",
                "topicSlug": slug, "limit": 200}, "Manifold") or []

        listed: dict[str, dict] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for data in pool.map(topic, SECTOR_TOPICS[sector]):
                for m in data:
                    if (in_window(m.get("closeTime")) and (m.get("uniqueBettorCount") or 0) >= min_bettors
                            and m.get("token") in (None, "MANA")):
                        listed.setdefault(m["id"], m)
        chosen = sorted(listed.values(), key=lambda m: m["closeTime"])[:limit]
        out = []
        for m, change in zip(chosen, manifold_changes(chosen, now)):
            snap = manifold_snapshot(m, change, now)
            if snap:
                out.append(snap)
        return out

    markets, failed = _gather({"Polymarket": polymarket, "Limitless": limitless, "Manifold": manifold})
    result = _result(markets, failed, now, days, limit, NOTE)
    result["sector"] = sector
    return result


def one(source: str, market_id: str) -> dict:
    """A single market in snapshot shape, for manual paper trades."""
    now = store.now_ms()
    if source == "polymarket":
        raw = Polymarket().by_condition([market_id]).get(market_id.lower())
        if not raw:
            raise LookupError("Pasar Polymarket tidak ditemukan.")
        snap = polymarket_snapshot(Polymarket._summary(raw), now)
    elif source == "manifold":
        client = Manifold()
        raw = client.raw_market(market_id)
        snap = manifold_snapshot(raw, client.price_changes(market_id, now), now)
    elif source == "limitless":
        from src.markets.limitless import Limitless, summary

        item = summary(Limitless().market(market_id))
        if not item:
            raise LookupError("Pasar Limitless ini tidak punya harga dua sisi.")
        snap = polymarket_snapshot(item, now, source="limitless")
    else:
        raise LookupError("Sumber harus polymarket, limitless, atau manifold.")
    if not snap:
        raise LookupError("Pasar ini tidak punya harga dua sisi dan tenggat yang bisa dibaca.")
    return enrich(snap, now)
