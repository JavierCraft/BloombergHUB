"""Resolved markets and their price paths — the training data.

Two sources, one output shape (`history` dicts, see `_history()`):

**Manifold** answers from this connection and keeps every trade of every
resolved market. Measured on 17 September 2026:

  * `/bets?points=true` is fast but *not* complete: for a market with 21.000
    trades it returned 1.009 points, unordered, and paginating it by time did not
    recover the rest. It is not used.
  * `/bets` with `before=<bet id>` returns trades newest-first and pages
    reliably. That is what this module reads — only back to the start of the
    snapshot window, so a busy market costs one or two pages, not twenty.
  * a market resolved before its scheduled close has its `closeTime` moved to
    the resolution moment. The original deadline is gone, so the moment trading
    stopped is used as the deadline (see `features.py`).

**Polymarket** has the better data — Gamma keeps the scheduled `endDate` and the
actual `closedTime` apart, and CLOB serves the price history — but its hosts are
blocked from this connection most days. The collector is complete and runs
whenever they answer; while they do not, the dataset says so rather than
pretending to include them.

Resolved markets never change, so each price path is cached on disk forever
under `data/ml/raw/`. A second training run re-reads the cache and only fetches
markets it has not seen.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import threading
from typing import Any, Callable

from src.markets import POLYMARKET_CLOB, POLYMARKET_GAMMA, _get
from src.ml import features, store

MANIFOLD = "https://api.manifold.markets/v0"
HISTORY_VERSION = 2
# Largest snapshot offset (14 days) + the 7-day momentum look-back + a margin.
WINDOW_DAYS = features.MAX_OFFSET_DAYS + 8
MAX_PAGES = 6
WORKERS = 4    # Manifold allows 500 requests/minute per IP; 4 workers stay well under

Progress = Callable[[str, int, int, str], None]


def _noop(stage: str, done: int, total: int, note: str = "") -> None:
    return None


def _history(**kw) -> dict:
    base = {
        "version": HISTORY_VERSION, "source": None, "id": None, "question": None, "url": None,
        "created_ms": None, "deadline_ms": None, "scheduled_deadline_ms": None, "resolved_ms": None,
        "closed_early": False, "outcome": None, "points": [], "initial_prob": None,
        "complete": False, "covered_from_ms": None, "fetched_at": store.now_iso(),
    }
    base.update(kw)
    return base


# ----------------------------------------------------------------- Manifold


def manifold_resolved(limit: int = 600, min_bettors: int = 15, max_age_days: int = 1095,
                      max_pages: int = 80) -> list[dict]:
    """Resolved YES/NO binary markets, newest-created first.

    `/search-markets` caps `offset` at 1.000 ("offset must be <= 1000"), and a
    thousand resolved markets span barely a month. `/markets` pages by cursor
    with no cap, so the listing walks backwards through creation time instead.
    """
    cutoff = store.now_ms() - max_age_days * features.DAY_MS
    out: list[dict] = []
    before = None
    for _ in range(max_pages):
        params: dict[str, Any] = {"limit": 1000}
        if before:
            params["before"] = before
        batch = _get(f"{MANIFOLD}/markets", params, "Manifold") or []
        if not batch:
            break
        for m in batch:
            if not m.get("isResolved") or m.get("resolution") not in ("YES", "NO"):
                continue
            if m.get("outcomeType") != "BINARY" or m.get("mechanism") not in (None, "cpmm-1"):
                continue
            # Sweepcash (token CASH) markets mirror a mana market; counting both doubles one question.
            if m.get("token") not in (None, "MANA"):
                continue
            if (m.get("uniqueBettorCount") or 0) < min_bettors:
                continue
            if not (m.get("createdTime") and m.get("closeTime") and m.get("resolutionTime")):
                continue
            if m["resolutionTime"] < cutoff:
                continue
            out.append(m)
            if len(out) >= limit:
                return out
        before = batch[-1].get("id")
        if not before or len(batch) < 1000 or (batch[-1].get("createdTime") or 0) < cutoff:
            break
    return out


def manifold_history(market: dict, use_cache: bool = True) -> dict | None:
    """Trades back to the start of the snapshot window, as a price path."""
    mid = market.get("id")
    path = store.RAW / "manifold" / f"{store.safe_name(mid)}.json"
    if use_cache:
        cached = store.read_json(path)
        if cached and cached.get("version") == HISTORY_VERSION:
            return cached

    created, close, resolved = market["createdTime"], market["closeTime"], market["resolutionTime"]
    deadline = min(close, resolved)
    window_start = deadline - WINDOW_DAYS * features.DAY_MS
    points: list[list[float]] = []
    oldest_prob_before = None
    complete = False
    before = None

    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"contractId": mid, "limit": 1000}
        if before:
            params["before"] = before
        batch = _get(f"{MANIFOLD}/bets", params, "Manifold") or []
        for bet in batch:
            t, after = bet.get("createdTime"), bet.get("probAfter")
            if t is None or after is None or bet.get("isRedemption"):
                continue
            if t > deadline:
                continue
            points.append([int(t), round(float(after), 5)])
            if bet.get("probBefore") is not None:
                oldest_prob_before = float(bet["probBefore"])
        if len(batch) < 1000:
            complete = True
            break
        if batch[-1].get("createdTime", 0) < window_start:
            break
        before = batch[-1].get("id")
        if not before:
            break

    if not points:
        return None
    points.sort()
    history = _history(
        source="manifold", id=mid, question=market.get("question"), url=market.get("url"),
        created_ms=int(created), deadline_ms=int(deadline), scheduled_deadline_ms=None,
        resolved_ms=int(resolved), closed_early=abs(close - resolved) < 60_000,
        outcome=1 if market.get("resolution") == "YES" else 0,
        points=points, initial_prob=oldest_prob_before if complete else None,
        complete=complete, covered_from_ms=int(created) if complete else points[0][0],
        bettors=market.get("uniqueBettorCount"),
    )
    store.write_json(path, history)
    return history


# --------------------------------------------------------------- Polymarket


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def polymarket_resolved(limit: int = 400, min_volume: float = 5000, page: int = 100) -> list[dict]:
    """Closed two-outcome markets whose final prices are exactly 1 and 0."""
    out: list[dict] = []
    offset = 0
    while len(out) < limit:
        batch = _get(f"{POLYMARKET_GAMMA}/markets", {
            "closed": "true", "limit": page, "offset": offset,
            "order": "closedTime", "ascending": "false", "volume_num_min": min_volume,
        }, "Polymarket") or []
        if not batch:
            break
        for m in batch:
            labels = _json_list(m.get("outcomes"))
            tokens = _json_list(m.get("clobTokenIds"))
            try:
                prices = [float(x) for x in _json_list(m.get("outcomePrices"))]
            except (TypeError, ValueError):
                continue
            if len(labels) != 2 or len(tokens) != 2 or len(prices) != 2:
                continue
            if sorted(prices) != [0.0, 1.0]:
                continue   # unresolved, disputed, or settled 50/50 — not a label
            end = store.iso_to_ms(m.get("endDate"))
            closed = store.iso_to_ms(m.get("closedTime"))
            created = store.iso_to_ms(m.get("startDate") or m.get("createdAt"))
            if not end or not created:
                continue
            events = m.get("events") or []
            slug = (events[0].get("slug") if events and isinstance(events[0], dict) else None) or m.get("slug")
            out.append({
                "id": m.get("conditionId"), "question": m.get("question"),
                "outcomes": labels, "token0": str(tokens[0]),
                "outcome": 1 if prices[0] == 1.0 else 0,
                "end_ms": end, "closed_ms": closed, "created_ms": created,
                "url": f"https://polymarket.com/event/{slug}" if slug else None,
            })
            if len(out) >= limit:
                break
        offset += len(batch)
        if len(batch) < page:
            break
    return out


def polymarket_history(market: dict, use_cache: bool = True) -> dict | None:
    path = store.RAW / "polymarket" / f"{store.safe_name(market.get('id'))}.json"
    if use_cache:
        cached = store.read_json(path)
        if cached and cached.get("version") == HISTORY_VERSION:
            return cached

    # Snapshots count back from the scheduled end; trading stopped at `closed`.
    # A market that closed long before its window opened was never live near
    # its deadline and contributes nothing.
    end, closed = market["end_ms"], market.get("closed_ms")
    stop = min(end, closed) if closed else end
    start = max(market["created_ms"], end - WINDOW_DAYS * features.DAY_MS)
    if stop <= start:
        return None
    data = _get(f"{POLYMARKET_CLOB}/prices-history", {
        "market": market["token0"], "startTs": int(start // 1000), "endTs": int(stop // 1000),
        "fidelity": 60,
    }, "Polymarket CLOB") or {}
    points = []
    for item in data.get("history") or []:
        try:
            t, p = int(item["t"]) * 1000, float(item["p"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(p) and 0 <= p <= 1 and t <= stop:
            points.append([t, round(p, 5)])
    if not points:
        return None
    points.sort()
    complete = start <= market["created_ms"]
    history = _history(
        source="polymarket", id=market["id"], question=market.get("question"), url=market.get("url"),
        created_ms=int(market["created_ms"]), deadline_ms=int(stop),
        scheduled_deadline_ms=int(end), resolved_ms=int(closed) if closed else None,
        closed_early=bool(closed and closed < end - features.HOUR_MS),
        outcome=market["outcome"], points=points,
        initial_prob=points[0][1] if complete else None,
        complete=complete, covered_from_ms=points[0][0],
    )
    store.write_json(path, history)
    return history


# ------------------------------------------------------------------ dataset

DATASET = store.ROOT / "dataset.jsonl"
DATASET_META = store.ROOT / "dataset.json"


class NotEnoughData(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def cached_histories(sources: tuple[str, ...]) -> list[dict]:
    """Every price path already on disk. Resolved markets never change."""
    out = []
    for source in sources:
        for path in sorted((store.RAW / source).glob("*.json")):
            history = store.read_json(path)
            if history and history.get("version") == HISTORY_VERSION:
                out.append(history)
    return out


def build(sources: tuple[str, ...] = ("manifold", "polymarket"), max_markets: int = 600,
          progress: Progress = _noop, offline: bool = False) -> dict:
    """Fetch histories not yet on disk, then build the dataset from *all* of them.

    Building from the disk cache rather than from this run's downloads means a
    source that is down today (Polymarket, usually) keeps the rows it
    contributed before, and each run only adds data — it never shrinks it.
    `offline=True` skips fetching: the way to retrain after changing features.
    """
    stats: dict[str, dict] = {}

    for source in (() if offline else sources):
        stat = {"listed": 0, "fetched": 0, "failed": None, "errors": 0}
        stats[source] = stat
        progress(f"{source}: daftar pasar", 0, 0, "mengambil daftar pasar yang sudah selesai")
        try:
            if source == "manifold":
                listed = manifold_resolved(limit=max_markets)
                fetch = manifold_history
            elif source == "polymarket":
                listed = polymarket_resolved(limit=max_markets)
                fetch = polymarket_history
            else:
                continue
        except Exception as exc:  # noqa: BLE001 — a blocked source is reported, not fatal
            stat["failed"] = getattr(exc, "message", None) or str(exc)[:200]
            continue
        stat["listed"] = len(listed)
        errors_lock = threading.Lock()

        def safe(market: dict, fetch=fetch, stat=stat, errors_lock=errors_lock) -> dict | None:
            try:
                return fetch(market)
            except Exception:  # noqa: BLE001
                with errors_lock:
                    stat["errors"] += 1
                return None

        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for history in pool.map(safe, listed):
                done += 1
                stat["fetched"] += int(bool(history))
                if done % 10 == 0 or done == len(listed):
                    progress(f"{source}: riwayat harga", done, len(listed),
                             f"{stat['fetched']} riwayat terbaca")

    progress("menyusun dataset", 0, 1, "dari semua riwayat di disk")
    rows: list[dict] = []
    for history in cached_histories(sources):
        new_rows = features.historical_rows(history)
        if not new_rows:
            continue
        stat = stats.setdefault(history["source"], {})
        stat["markets"] = stat.get("markets", 0) + 1
        stat["deadline_unknown"] = stat.get("deadline_unknown", 0) + int(not new_rows[0]["deadline_known"])
        stat["rows"] = stat.get("rows", 0) + len(new_rows)
        stat["closed_early"] = stat.get("closed_early", 0) + int(bool(history.get("closed_early")))
        stat["outcome_yes"] = stat.get("outcome_yes", 0) + int(history.get("outcome") == 1)
        rows.extend(new_rows)

    if not rows:
        reasons = "; ".join(f"{s}: {v.get('failed')}" for s, v in stats.items() if v.get("failed"))
        raise NotEnoughData("Belum ada riwayat harga yang bisa dipakai untuk melatih model."
                            + (f" Sumber gagal — {reasons}" if reasons else ""))

    rows.sort(key=lambda r: (r["deadline_ms"], str(r["market_id"]), -r["offset_days"]))
    store.write_jsonl(DATASET, rows)
    meta = {
        "built_at": store.now_iso(), "rows": len(rows),
        "markets": len({(r["source"], r["market_id"]) for r in rows}),
        "sources": stats, "offsets_days": list(features.OFFSETS_DAYS),
        "window_days": WINDOW_DAYS,
    }
    store.write_json(DATASET_META, meta)
    return {"rows": rows, "meta": meta}


def load_dataset() -> tuple[list[dict], dict]:
    return store.read_jsonl(DATASET), store.read_json(DATASET_META, {}) or {}
