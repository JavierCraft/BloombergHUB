"""Markets for a sector page — Polymarket first, and never an empty panel.

Polymarket's hosts are blocked from this connection on most days. Until
17 September 2026 that left every sector page with one error message where the
odds, EV, IEV and machine-learning reading should have been. Now:

  1. Polymarket Gamma, when it answers;
  2. otherwise Limitless (real money, USDC) by sector category, plus Manifold
     (play money) by topic — both labelled per market, never passed off as
     Polymarket.

Every market, whatever its source, goes through the same analysis: lean,
character, entry cost, EV at the ask, and the deadline model when its deadline
is inside the model's range.
"""
from __future__ import annotations

from typing import Any

from src.markets import Manifold, Polymarket, _get

MANIFOLD = "https://api.manifold.markets/v0"
MANIFOLD_TOPICS = {
    "politics": "politics-default",
    "technology": "technology-default",
    "culture": "culture-default",
    "soccer": "soccer",
    "sports": "sports-default",
    "esports": "esports",
    "crypto": "crypto-speculation",
    "markets": "economics-default",
}


def _iso(ms: Any) -> str | None:
    from src.ml.store import ms_to_iso

    try:
        return ms_to_iso(float(ms)) if ms is not None else None
    except (TypeError, ValueError):
        return None


def manifold_market(m: dict) -> dict | None:
    """A Manifold binary market in the shape the analysis expects."""
    p = m.get("probability")
    if not isinstance(p, (int, float)) or not 0 <= p <= 1:
        return None
    return {
        "source": "manifold",
        "id": m.get("id"),
        "question": m.get("question"),
        "volume": m.get("volume"),
        "liquidity": m.get("totalLiquidity"),
        "volume24h": m.get("volume24Hours"),
        "outcomes": [{"outcome": "Yes", "price": round(p, 4)}, {"outcome": "No", "price": round(1 - p, 4)}],
        "clob_token_ids": [],
        "created_at": _iso(m.get("createdTime")),
        "start_date": _iso(m.get("createdTime")),
        "end_date": _iso(m.get("closeTime")),
        "rules": m.get("textDescription"),
        "resolution_source": None,
        "change1h": None, "change24h": None, "change1w": None, "change1mo": None,
        "best_bid": None, "best_ask": None, "spread": None, "last_trade_price": None,
        "slug": m.get("slug"), "event_title": None, "closed": bool(m.get("isResolved")),
        "bettors": m.get("uniqueBettorCount"),
        "url": m.get("url"),
    }


def manifold_sector(sector: str, query: str = "", limit: int = 12) -> list[dict]:
    params: dict[str, Any] = {"term": query or "", "filter": "open", "contractType": "BINARY",
                              "sort": "liquidity" if not query else "score", "limit": min(50, limit * 2)}
    topic = MANIFOLD_TOPICS.get(sector)
    if topic and not query:
        params["topicSlug"] = topic
    data = _get(f"{MANIFOLD}/search-markets", params, "Manifold") or []
    out = [x for x in (manifold_market(m) for m in data) if x]
    return out[:limit]


def _snapshot(market: dict, now_ms: int) -> dict | None:
    """Model input from any normalised market (Polymarket, Limitless, Manifold)."""
    from src.ml import store

    outcomes = market.get("outcomes") or []
    deadline = store.iso_to_ms(market.get("end_date"))
    if len(outcomes) != 2 or deadline is None or deadline <= now_ms:
        return None

    def f(key):
        try:
            return float(market[key]) if market.get(key) not in (None, "") else None
        except (TypeError, ValueError):
            return None

    return {"question": market.get("question"), "p_yes": outcomes[0]["price"], "deadline_ms": deadline,
            "created_ms": store.iso_to_ms(market.get("start_date") or market.get("created_at")),
            "chg_1d": f("change24h"), "chg_7d": f("change1w")}


def analysed(markets: list[dict]) -> list[dict]:
    from src.markets.analysis import analyse
    from src.ml import model, store

    now = store.now_ms()
    out = []
    for m in markets:
        snap = _snapshot(m, now)
        prediction = (model.predict_market(snap, now) if snap else
                      {"available": False, "reason": "Pasar ini tidak punya dua outcome dan tenggat yang bisa dibaca model."})
        cost = 1.0 if m.get("source") == "manifold" else 0.0
        out.append(dict(m, analysis=analyse(m, prediction, cost_pp=cost)))
    return out


def sector_markets(sector: str = "", query: str = "", limit: int = 12) -> dict:
    from src.markets.analysis import sector_brief
    from src.markets.limitless import Limitless

    failed: list[dict] = []

    def note(source: str, exc: Exception) -> None:
        failed.append({"source": source, "reason": getattr(exc, "message", None) or str(exc)[:200]})

    markets: list[dict] = []
    try:
        markets = [dict(m, source="polymarket") for m in Polymarket().search(query, limit, sector)]
    except Exception as exc:  # noqa: BLE001 — a blocked source falls through to the next one
        note("Polymarket", exc)

    used = "polymarket"
    if not markets:
        used = "limitless+manifold"
        real: list[dict] = []
        play: list[dict] = []
        try:
            real = (Limitless().search(query, limit) if query
                    else Limitless().sector(sector or "politics", limit))
        except Exception as exc:  # noqa: BLE001
            note("Limitless", exc)
        try:
            play = manifold_sector(sector, query, limit)
        except Exception as exc:  # noqa: BLE001
            note("Manifold", exc)
        # Real money first, then play money, until the panel is full.
        half = max(1, limit // 2)
        markets = real[:half] + play[: limit - min(len(real), half)]
        if len(markets) < limit:
            markets += real[half: half + (limit - len(markets))]

    rows = analysed(markets)
    if used == "polymarket":
        reason = "Data dari Polymarket Gamma. Harga outcome adalah probabilitas tersirat, bukan persentase orang."
    elif rows:
        blocked = next((f["reason"] for f in failed if f["source"] == "Polymarket"), "tidak menjawab")
        reason = ("Polymarket tidak bisa dibuka dari koneksi ini (" + blocked + "). Yang tampil: Limitless — pasar "
                  "uang sungguhan (USDC) — dan Manifold — uang main. Sumber tiap pasar ditulis di judulnya; "
                  "analisis, EV, IEV, dan model machine learning berlaku sama.")
    else:
        reason = "Tidak ada sumber pasar prediksi yang menjawab saat ini."
    return {
        "reachable": bool(rows),
        "source": used,
        "fallback": used != "polymarket",
        "failed": failed,
        "reason": reason,
        "markets": rows,
        "brief": sector_brief(rows, sector or query),
        "hosts": ["gamma-api.polymarket.com", "api.limitless.exchange", "api.manifold.markets"],
    }
