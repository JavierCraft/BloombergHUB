"""Paper test: virtual positions from the model, settled against real resolutions.

What happens on each scan:

  1. **Settle first.** Every open position and prediction whose market may have
     resolved is checked against its source — Gamma for Polymarket, the market
     endpoint for Manifold. Resolved YES/NO pays 1 or 0 per share; Manifold's
     partial (MKT) resolution pays its resolution probability; CANCEL returns the
     stake. A source that cannot be reached leaves the item open with a note —
     never settled on a guess.
  2. **Predict.** Every market inside the deadline window with a model
     probability is logged as a prediction (at most once per 12 hours per
     market), with the market price at that moment and the news features then
     visible. Predictions are what accuracy and calibration are measured on —
     including the markets the model did *not* trade.
  3. **Trade.** Where the model's EV at the ask reaches the threshold, a paper
     position is opened with a flat virtual stake. Flat, so win rate and ROI
     measure the model rather than the sizing rule.

What is reported, and why in this order:

  * **Brier: model vs market** on settled predictions. If the model is not more
    accurate than the price, its trades are luck.
  * **Win rate next to the average entry price.** Buying 85¢ favourites wins
    85% of the time and makes nothing. The edge is the gap between the two.
  * **ROI, P/L and drawdown** of the flat-stake positions.
  * All of the above **per deadline bucket** — the question this test exists
    for: does the model get better as the deadline gets close?

Nothing here can place a real order. The ledger lives in `data/ml/paper/`.
"""
from __future__ import annotations

import concurrent.futures
import functools
import math
import re
import threading
import uuid
from typing import Any, Callable

import config
from src.ml import deadlines, features, model, newsml, store

# Settlement rewrites the ledger; a scan appends to it. Without one lock around
# both, a background scan landing mid-settlement would be written over and lost.
_LEDGER_LOCK = threading.RLock()


def _exclusive(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LEDGER_LOCK:
            return fn(*args, **kwargs)
    return wrapper

POSITIONS = store.PAPER / "positions.jsonl"
PREDICTIONS = store.PAPER / "predictions.jsonl"
SUMMARY_TXT = store.PAPER / "ringkasan.txt"

PREDICTION_EVERY_HOURS = 12
RECHECK_HOURS = 3
PRICE_BAND = (0.03, 0.97)
MIN_POLY_LIQUIDITY = 1000.0
NEWS_SIGNAL_MIN = 60
MAX_NEWS_SEARCHES = 30
FLAT_PP = 0.5            # below this gap between model and price, a prediction has no direction
SHADOW_COST_PP = 1.0     # added to the price when a prediction carries no ask (older rows)
MIN_MANIFOLD_BETTORS = 15  # the training data used the same floor
MIN_LIMITLESS_VOLUME = 100.0  # USDC traded; below this the quoted book is a placeholder
# "Will I weigh 150lbs?" — resolved by the creator about themselves. Fine to
# predict and log; useless as evidence about markets that trade real events.
PERSONAL = re.compile(r"^\s*(will|do|does|did|am|can|should)\s+(i|my|we)\b|\b(my|myself)\b", re.I)

Progress = Callable[[str, int, int, str], None]


def _noop(stage: str, done: int, total: int, note: str = "") -> None:
    return None


def load_positions() -> list[dict]:
    return store.read_jsonl(POSITIONS)


def load_predictions() -> list[dict]:
    return store.read_jsonl(PREDICTIONS)


def _compact_news(feats: dict | None) -> dict | None:
    if not feats:
        return None
    keep = ("n_24h", "n_72h", "sources_72h", "max_relevance", "tone_72h", "tone_label",
            "newest_age_h", "acceleration", "query", "searched")
    out = {k: feats.get(k) for k in keep}
    out["evidence"] = (feats.get("evidence") or [])[:5]
    return out


# ------------------------------------------------------------------ settlement


def _polymarket_results(ids: list[str]) -> tuple[dict[str, dict], str | None]:
    from src.markets import Polymarket, _json_list

    try:
        raw = Polymarket().by_condition(ids)
    except Exception as exc:  # noqa: BLE001
        return {}, getattr(exc, "message", None) or str(exc)[:160]
    results = {}
    for cid, m in raw.items():
        try:
            prices = [float(x) for x in _json_list(m.get("outcomePrices"))]
        except (TypeError, ValueError):
            prices = []
        uma = (m.get("umaResolutionStatus") or "").lower()
        if m.get("closed") and len(prices) == 2 and sorted(prices) == [0.0, 1.0] and uma in ("", "resolved"):
            results[cid] = {"resolved": True, "resolution": "YES" if prices[0] == 1.0 else "NO",
                            "note": "Diselesaikan di Polymarket."}
        elif m.get("closed"):
            results[cid] = {"resolved": False,
                            "note": f"Pasar sudah ditutup, menunggu resolusi UMA ({uma or 'belum ada status'})."}
        else:
            results[cid] = {"resolved": False, "note": "Pasar masih dibuka."}
    return results, None


def _limitless_results(ids: list[str]) -> tuple[dict[str, dict], str | None]:
    from src.markets.limitless import Limitless

    client = Limitless()
    results: dict[str, dict] = {}
    failure = None

    def one(slug: str):
        try:
            return slug, client.market(slug), None
        except Exception as exc:  # noqa: BLE001
            return slug, None, getattr(exc, "message", None) or str(exc)[:160]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for slug, m, error in pool.map(one, ids):
            if error:
                failure = error
                continue
            index = m.get("winningOutcomeIndex")
            if index in (0, 1) and (m.get("expired") or str(m.get("status", "")).upper() == "RESOLVED"):
                results[slug] = {"resolved": True, "resolution": "YES" if index == 0 else "NO",
                                 "note": "Diselesaikan di Limitless."}
            elif m.get("expired"):
                results[slug] = {"resolved": False, "note": "Pasar sudah berakhir, menunggu hasil resmi."}
            else:
                results[slug] = {"resolved": False, "note": "Pasar masih dibuka."}
    return results, failure


def _manifold_results(ids: list[str]) -> tuple[dict[str, dict], str | None]:
    from src.markets import Manifold

    client = Manifold()
    results: dict[str, dict] = {}
    failure = None

    def one(mid: str):
        try:
            return mid, client.raw_market(mid), None
        except Exception as exc:  # noqa: BLE001
            return mid, None, getattr(exc, "message", None) or str(exc)[:160]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for mid, m, error in pool.map(one, ids):
            if error:
                failure = error
                continue
            if m.get("isResolved"):
                results[mid] = {"resolved": True, "resolution": m.get("resolution"),
                                "resolution_prob": m.get("resolutionProbability"),
                                "note": "Diselesaikan di Manifold."}
            else:
                results[mid] = {"resolved": False, "note": "Pasar belum diselesaikan pembuatnya."}
    return results, failure


def payout(position: dict, resolution: str, resolution_prob: float | None = None) -> tuple[float, str]:
    """(payout in dollars, status) for one position under a resolution."""
    stake = float(position["stake"])
    shares = float(position["shares"])
    side = position["side"]
    if resolution == "CANCEL":
        return stake, "void"
    if resolution == "MKT":
        r = float(resolution_prob) if resolution_prob is not None else 0.5
        paid = shares * (r if side == "YES" else 1 - r)
    elif resolution in ("YES", "NO"):
        paid = shares if resolution == side else 0.0
    else:
        return stake, "void"
    pnl = paid - stake
    status = "won" if pnl > 1e-6 else "lost" if pnl < -1e-6 else "flat"
    return round(paid, 4), status


@_exclusive
def settle(now_ms: int | None = None, force: bool = False) -> dict:
    now = now_ms or store.now_ms()
    positions = load_positions()
    predictions = load_predictions()

    def due(item: dict) -> bool:
        if item.get("status") != "open":
            return False
        if force or item["deadline_ms"] <= now:
            return True
        checked = store.iso_to_ms(item.get("last_checked_at")) or 0
        return now - checked >= RECHECK_HOURS * features.HOUR_MS

    wanted: dict[str, set] = {"polymarket": set(), "limitless": set(), "manifold": set()}
    for item in positions + predictions:
        if due(item):
            wanted.setdefault(item["source"], set()).add(item["market_id"])

    results: dict[tuple, dict] = {}
    failures = []
    if wanted.get("polymarket"):
        found, error = _polymarket_results(sorted(wanted["polymarket"]))
        results.update({("polymarket", k): v for k, v in found.items()})
        if error:
            failures.append({"source": "Polymarket", "reason": error})
    if wanted.get("limitless"):
        found, error = _limitless_results(sorted(wanted["limitless"]))
        results.update({("limitless", k): v for k, v in found.items()})
        if error:
            failures.append({"source": "Limitless", "reason": error})
    if wanted.get("manifold"):
        found, error = _manifold_results(sorted(wanted["manifold"]))
        results.update({("manifold", k): v for k, v in found.items()})
        if error:
            failures.append({"source": "Manifold", "reason": error})

    settled_positions = settled_predictions = 0
    stamp = store.now_iso()
    for item in positions:
        if item.get("status") != "open":
            continue
        key = (item["source"], item["market_id"].lower() if item["source"] == "polymarket" else item["market_id"])
        result = results.get(key)
        if not result:
            if item["market_id"] in wanted.get(item["source"], set()):
                item["check_note"] = "Sumber tidak bisa dihubungi saat pengecekan; posisi tetap terbuka."
                item["last_checked_at"] = stamp
            continue
        item["last_checked_at"] = stamp
        item["check_note"] = result["note"]
        if not result["resolved"]:
            continue
        paid, status = payout(item, result["resolution"], result.get("resolution_prob"))
        item.update(status=status, resolution=result["resolution"], payout=paid,
                    pnl=round(paid - float(item["stake"]), 4), settled_at=stamp)
        settled_positions += 1

    for item in predictions:
        if item.get("status") != "open":
            continue
        key = (item["source"], item["market_id"].lower() if item["source"] == "polymarket" else item["market_id"])
        result = results.get(key)
        if not result:
            continue
        item["last_checked_at"] = stamp
        if not result["resolved"]:
            continue
        if result["resolution"] in ("YES", "NO"):
            item.update(status="settled", outcome=1 if result["resolution"] == "YES" else 0,
                        resolution=result["resolution"], settled_at=stamp)
        else:
            item.update(status="void", resolution=result["resolution"], settled_at=stamp)
        settled_predictions += 1

    store.write_jsonl(POSITIONS, positions)
    store.write_jsonl(PREDICTIONS, predictions)
    if settled_positions or settled_predictions:
        write_summary()
    return {"checked_markets": sum(len(v) for v in wanted.values()),
            "settled_positions": settled_positions, "settled_predictions": settled_predictions,
            "failed": failures, "at": stamp}


# ------------------------------------------------------------------- scanning


def _prediction(snap: dict, news: dict | None, llm: dict | None = None) -> dict:
    pred = snap["model"]
    best = snap["ev"].get("best") or {}
    rows = snap["ev"].get("rows") or [{}, {}]
    return {
        # What buying each side would have cost at this moment — lets every
        # prediction be scored as a shadow trade, not only the ones traded.
        "ask_yes": rows[0].get("ask"), "ask_no": rows[1].get("ask") if len(rows) > 1 else None,
        "id": uuid.uuid4().hex[:12],
        "made_at": store.now_iso(), "made_ms": store.now_ms(),
        "source": snap["source"], "market_id": snap["id"], "question": snap["question"],
        "url": snap.get("url"), "deadline": snap["deadline"], "deadline_ms": snap["deadline_ms"],
        "days_left": snap["days_left"], "bucket": snap["bucket"],
        "outcomes": [o["outcome"] for o in snap["outcomes"]],
        "market_prob_yes": pred["market_prob_yes"], "model_prob_yes": pred["prob_yes"],
        "direction": pred["direction"], "model_id": pred["model_id"],
        "model_beats_market": pred["beats_market"],
        "best_side": ("YES" if best.get("outcome") == snap["outcomes"][0]["outcome"] else "NO") if best else None,
        "best_ev_pct": best.get("ev_model_pct"),
        "news": news, "llm": llm,
        "status": "open", "outcome": None, "settled_at": None,
    }


def _position(snap: dict, side_index: int, stake: float, origin: str, news: dict | None,
              note: str = "", llm: dict | None = None) -> dict:
    pred = snap.get("model") or {}
    row = snap["ev"]["rows"][side_index]
    entry = float(row["ask"]) if row.get("ask") is not None else 0.0
    if not 0 < entry < 1:
        # A side priced at 0 or 1 has no purchase to simulate — and 0 would divide.
        raise ValueError(f"Harga masuk {row['outcome']} ({entry}) tidak bisa dibeli; pasar ini sudah pasti.")
    return {
        "id": uuid.uuid4().hex[:12],
        "opened_at": store.now_iso(), "origin": origin,
        "source": snap["source"], "money": snap.get("money"),
        "market_id": snap["id"], "token_id": snap.get("token_id"),
        "question": snap["question"], "url": snap.get("url"),
        "deadline": snap["deadline"], "deadline_ms": snap["deadline_ms"],
        "days_left": snap["days_left"], "bucket": snap["bucket"],
        "side": "YES" if side_index == 0 else "NO",
        "side_label": row["outcome"],
        "entry_price": round(entry, 4), "entry_source": row["entry_source"],
        "consensus_price": row["price"],
        "market_prob_yes": snap["p_yes"],
        "model_prob_yes": pred.get("prob_yes"), "model_prob_side": row.get("model_prob"),
        "model_id": pred.get("model_id"), "model_beats_market": pred.get("beats_market"),
        "ev_pct": row.get("ev_model_pct"),
        "stake": round(stake, 2), "shares": round(stake / entry, 4),
        "news": news, "llm": llm, "note": note,
        "status": "open", "resolution": None, "payout": None, "pnl": None,
        "settled_at": None, "last_checked_at": None, "check_note": None,
    }


@_exclusive
def scan(days: float | None = None, min_ev: float | None = None, stake: float | None = None,
         sources: tuple[str, ...] = ("polymarket", "limitless", "manifold"), with_news: bool = True,
         max_new: int = 20, progress: Progress = _noop) -> dict:
    days = days if days is not None else config.PAPER_MAX_DAYS
    min_ev = min_ev if min_ev is not None else config.PAPER_MIN_EV
    stake = stake if stake is not None else config.PAPER_STAKE

    progress("settle", 0, 1, "memeriksa posisi yang mungkin sudah selesai")
    settled = settle()
    status = model.status()
    if not status["trained"]:
        return {"ok": False, "settled": settled,
                "reason": "Model belum dilatih — tidak ada peluang yang bisa diuji. Latih model dulu."}

    progress("pasar bertenggat", 0, 1, f"≤ {days:g} hari")
    collected = deadlines.collect(days=days, sources=sources, limit=80)
    positions = load_positions()
    predictions = load_predictions()
    open_keys = {(p["source"], p["market_id"]) for p in positions if p.get("status") == "open"}
    now = store.now_ms()
    recent = {(p["source"], p["market_id"]) for p in predictions
              if now - (p.get("made_ms") or 0) < PREDICTION_EVERY_HOURS * features.HOUR_MS}

    candidates = []
    for snap in collected["markets"]:
        pred = snap["model"]
        if not pred.get("available") or not snap["ev"].get("known"):
            continue
        if not PRICE_BAND[0] <= snap["p_yes"] <= PRICE_BAND[1]:
            continue
        key = (snap["source"], snap["id"])
        best = snap["ev"].get("best")
        if snap["source"] == "polymarket":
            tradable = (snap.get("liquidity") or 0) >= MIN_POLY_LIQUIDITY
        elif snap["source"] == "limitless":
            tradable = snap.get("best_ask") is not None and (snap.get("volume") or 0) >= MIN_LIMITLESS_VOLUME
        else:
            tradable = ((snap.get("bettors") or 0) >= MIN_MANIFOLD_BETTORS
                        and not PERSONAL.search(snap.get("question") or ""))
        signal = bool(best and best["ev_model_pct"] >= min_ev * 100 and key not in open_keys and tradable)
        if key in recent and not signal:
            continue
        candidates.append((snap, signal))

    corpus = None
    if with_news:
        from src import news

        corpus = news.corpus()

    # A live Google News search per market is what gives news features their
    # value, but dozens of searches every scan would get this address throttled.
    # Signals always search; the rest search when the deadline is within a week,
    # up to a cap, and fall back to the local corpus beyond it.
    budget = {"left": MAX_NEWS_SEARCHES}
    plans = []
    for snap, signal in candidates:
        search = bool(with_news and (signal or snap["days_left"] <= 7) and budget["left"] > 0)
        budget["left"] -= int(search)
        plans.append((snap, search))

    def news_for(plan):
        snap, search = plan
        if not with_news:
            return None
        try:
            return _compact_news(newsml.market_news(snap["question"], corpus, search=search))
        except Exception:  # noqa: BLE001 — news is context, never a reason to skip a prediction
            return None

    progress("berita per pasar", 0, len(candidates), "relevansi dan nada")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        news_rows = list(pool.map(news_for, plans))

    new_predictions, opened = [], []
    for (snap, signal), news_feats in zip(candidates, news_rows):
        key = (snap["source"], snap["id"])
        if key not in recent:
            new_predictions.append(_prediction(snap, news_feats))
            recent.add(key)
        if signal and len(opened) < max_new:
            best = snap["ev"]["best"]
            index = 0 if best["outcome"] == snap["outcomes"][0]["outcome"] else 1
            opened.append(_position(snap, index, stake, "auto-scan", news_feats))
            open_keys.add(key)

    store.append_jsonl(PREDICTIONS, new_predictions)
    store.append_jsonl(POSITIONS, opened)
    write_summary()

    return {
        "concentration_note": concentration_note(opened),
        "ok": True,
        "scanned": collected["count"],
        "with_model": collected["with_model"],
        "predictions_logged": len(new_predictions),
        "opened": [{k: p[k] for k in ("question", "side_label", "entry_price", "ev_pct", "deadline",
                                      "source", "bucket")} for p in opened],
        "opened_count": len(opened),
        "settled": settled,
        "failed": collected["failed"],
        "min_ev_pct": min_ev * 100, "stake": stake, "days": days,
        "at": store.now_iso(),
    }


def concentration_note(positions: list[dict]) -> str | None:
    """Warn when most positions sit on one side: their results are not independent."""
    if len(positions) < 5:
        return None
    top = max(("YES", "NO"), key=lambda s: sum(1 for p in positions if p.get("side") == s))
    share = sum(1 for p in positions if p.get("side") == top) / len(positions)
    if share < 0.8:
        return None
    return (f"{share * 100:.0f}% dari {len(positions)} posisi ada di sisi {top}. Hasilnya akan sangat "
            "berkorelasi: ini menguji satu pola yang dipelajari model, bukan sekian taruhan yang saling "
            "lepas. Jangan membaca win rate-nya seolah berasal dari sekian percobaan independen.")


@_exclusive
def manual(source: str, market_id: str, side: str, stake: float | None = None,
           note: str = "") -> dict:
    """Open one paper position by hand. Works with or without a trained model."""
    snap = deadlines.one(source, market_id)
    if not snap["ev"].get("known"):
        raise LookupError("Harga masuk pasar ini tidak bisa dihitung.")
    side = side.upper()
    if side not in ("YES", "NO"):
        raise ValueError("Sisi harus YES atau NO.")
    position = _position(snap, 0 if side == "YES" else 1, stake or config.PAPER_STAKE,
                         "manual", None, note=note[:300])
    store.append_jsonl(POSITIONS, [position])
    write_summary()
    return position


@_exclusive
def reset() -> dict:
    """Archive the ledger rather than delete it."""
    folder = store.PAPER / f"arsip-{store.now_iso().replace(':', '').replace('+0000', 'Z')}"
    moved = 0
    for path in (POSITIONS, PREDICTIONS, SUMMARY_TXT):
        if path.exists():
            folder.mkdir(parents=True, exist_ok=True)
            path.replace(folder / path.name)
            moved += 1
    return {"archived_files": moved, "folder": str(folder) if moved else None}


# ---------------------------------------------------------------------- stats


def _trade_summary(items: list[dict]) -> dict:
    n = len(items)
    if not n:
        return {"n": 0, "wins": 0, "win_rate": None, "avg_entry": None, "edge_pp": None,
                "staked": 0.0, "pnl": 0.0, "roi_pct": None}
    wins = sum(1 for p in items if p["status"] == "won")
    staked = sum(float(p["stake"]) for p in items)
    pnl = sum(float(p["pnl"] or 0) for p in items)
    win_rate = wins / n
    avg_entry = sum(float(p["entry_price"]) for p in items) / n
    se = math.sqrt(max(avg_entry * (1 - avg_entry), 1e-9) / n)
    return {
        "n": n, "wins": wins,
        "win_rate": round(win_rate, 4),
        "avg_entry": round(avg_entry, 4),
        "edge_pp": round((win_rate - avg_entry) * 100, 2),
        "z": round((win_rate - avg_entry) / se, 2) if se else None,
        "staked": round(staked, 2), "pnl": round(pnl, 2),
        "roi_pct": round(pnl / staked * 100, 2) if staked else None,
    }


def shadow_trades(predictions: list[dict]) -> tuple[list[dict], int]:
    """Every settled prediction scored as if the side the model leaned to was bought.

    A model that sits close to the market rarely clears the EV threshold, so real
    paper positions accumulate slowly. The question "was the ML right, and what is
    its win rate" should not wait for that: each prediction with a direction is
    one shadow trade at the ask of that moment. Predictions without a direction
    (model within half a point of the price) are counted, not scored.
    """
    trades, flat = [], 0
    for p in predictions:
        if p.get("status") != "settled" or p.get("outcome") not in (0, 1):
            continue
        q, m = p.get("model_prob_yes"), p.get("market_prob_yes")
        if q is None or m is None:
            continue
        gap = (q - m) * 100
        if abs(gap) < FLAT_PP:
            flat += 1
            continue
        side = "YES" if gap > 0 else "NO"
        ask = p.get("ask_yes") if side == "YES" else p.get("ask_no")
        if ask is None:
            ask = min(0.999, (m if side == "YES" else 1 - m) + SHADOW_COST_PP / 100)
        ask = float(ask)
        prob_side = q if side == "YES" else 1 - q
        win = (side == "YES") == (p["outcome"] == 1)
        trades.append({"bucket": p.get("bucket"), "source": p.get("source"), "side": side,
                       "entry": ask, "win": win, "ev": prob_side / ask - 1,
                       "pnl": (1 / ask - 1) if win else -1.0})
    return trades, flat


def news_signal(predictions: list[dict]) -> dict:
    """Does news add information on top of the model? Answered only with enough data."""
    rows = [p for p in predictions if p.get("status") == "settled" and p.get("news")
            and p.get("model_prob_yes") is not None and p["news"].get("n_72h") is not None]
    if len(rows) < NEWS_SIGNAL_MIN:
        return {"ready": False, "n": len(rows), "needed": NEWS_SIGNAL_MIN,
                "note": (f"Butuh {NEWS_SIGNAL_MIN} prediksi selesai yang punya fitur berita untuk menguji "
                         "apakah berita menambah informasi di atas model. Sekarang baru "
                         f"{len(rows)}.")}
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import KFold

    rows.sort(key=lambda p: p.get("made_ms") or 0)
    q = np.clip(np.array([p["model_prob_yes"] for p in rows]), 0.01, 0.99)
    logit = np.log(q / (1 - q))
    y = np.array([p["outcome"] for p in rows])
    news = np.array([[math.log1p(p["news"]["n_72h"] or 0), p["news"].get("tone_72h") or 0.0,
                      p["news"].get("acceleration") or 0.0, p["news"].get("max_relevance") or 0.0]
                     for p in rows])
    base_X, full_X = logit[:, None], np.hstack([logit[:, None], news])
    if len(set(y.tolist())) < 2:
        return {"ready": False, "n": len(rows), "needed": NEWS_SIGNAL_MIN,
                "note": "Semua prediksi yang selesai berakhir sama; belum bisa diuji."}

    def cv_loss(X):
        losses = []
        for train, test in KFold(n_splits=5).split(X):
            if len(set(y[train].tolist())) < 2:
                continue
            clf = LogisticRegression(C=1.0, max_iter=2000).fit(X[train], y[train])
            prob = np.clip(clf.predict_proba(X[test])[:, 1], 1e-6, 1 - 1e-6)
            losses.append(float(-np.mean(y[test] * np.log(prob) + (1 - y[test]) * np.log(1 - prob))))
        return float(np.mean(losses)) if losses else None

    base, full = cv_loss(base_X), cv_loss(full_X)
    gain = (base - full) if base is not None and full is not None else None
    return {
        "ready": True, "n": len(rows),
        "log_loss_model_only": round(base, 5) if base is not None else None,
        "log_loss_with_news": round(full, 5) if full is not None else None,
        "improvement": round(gain, 5) if gain is not None else None,
        "verdict": ("Fitur berita menurunkan log loss di atas model — berita menambah informasi."
                    if gain is not None and gain > 0.005 else
                    "Belum terlihat tambahan informasi dari fitur berita di atas model."),
    }


def stats() -> dict:
    positions = load_positions()
    predictions = load_predictions()
    settled = [p for p in positions if p.get("status") in ("won", "lost", "flat")]
    settled.sort(key=lambda p: p.get("settled_at") or "")
    open_positions = [p for p in positions if p.get("status") == "open"]

    equity, cumulative, peak, drawdown = [], 0.0, 0.0, 0.0
    for p in settled:
        cumulative += float(p["pnl"] or 0)
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
        equity.append({"x": (p.get("settled_at") or "")[:16].replace("T", " "), "y": round(cumulative, 2)})

    overall = _trade_summary(settled)
    groups = {
        "by_bucket": [(b, [p for p in settled if p.get("bucket") == b]) for b in features.BUCKET_ORDER],
        "by_source": [(s, [p for p in settled if p.get("source") == s]) for s in ("polymarket", "limitless", "manifold")],
        "by_side": [(s, [p for p in settled if p.get("side") == s]) for s in ("YES", "NO")],
    }
    grouped = {name: [{"group": g, **_trade_summary(items)} for g, items in rows if items]
               for name, rows in groups.items()}

    done = [p for p in predictions if p.get("status") == "settled"
            and p.get("model_prob_yes") is not None and p.get("outcome") in (0, 1)]
    prediction_metrics: dict[str, Any] = {"n": len(done)}
    calibration = None
    by_bucket_pred = []
    if done:
        import numpy as np

        y = np.array([p["outcome"] for p in done])
        q = np.array([p["model_prob_yes"] for p in done])
        m = np.array([p["market_prob_yes"] for p in done])
        prediction_metrics = model._metrics(y, q, m)
        from src.sports.betting import calibration_table

        calibration = calibration_table(q.tolist(), y.tolist(), bins=10 if len(done) >= 50 else 5)
        for bucket in features.BUCKET_ORDER:
            idx = [i for i, p in enumerate(done) if p.get("bucket") == bucket]
            if idx:
                sub = model._metrics(y[idx], q[idx], m[idx])
                by_bucket_pred.append({"bucket": bucket, **sub})

    shadow, flat = shadow_trades(predictions)
    shadow_summary = {
        "overall": model.summarise_trades(shadow),
        "by_bucket": [{"group": b, **model.summarise_trades([t for t in shadow if t["bucket"] == b])}
                      for b in features.BUCKET_ORDER if any(t["bucket"] == b for t in shadow)],
        "flat": flat,
        "note": ("Setiap prediksi yang sudah selesai dinilai seolah sisi yang dicondongi model dibeli di "
                 "harga ask saat prediksi dibuat. Prediksi yang selisihnya dengan harga di bawah "
                 f"{FLAT_PP} poin tidak punya arah dan hanya dihitung."),
    }

    n = overall["n"]
    shadow_n = shadow_summary["overall"]["n"]
    if n == 0 and shadow_n == 0:
        verdict = ("Belum ada posisi atau prediksi yang selesai. Hasil pertama muncul setelah pasar dengan "
                   "tenggat terdekat diselesaikan; scan berikutnya mencocokkannya otomatis.")
    elif n < 30 and shadow_n >= 30:
        so = shadow_summary["overall"]
        verdict = (f"Posisi berbayar virtual baru {n}, tetapi {shadow_n} prediksi berarah sudah selesai: "
                   f"sisi yang dicondongi model menang {so['win_rate'] * 100:.1f}% lawan break-even "
                   f"{so['avg_entry'] * 100:.1f}% (selisih {so['edge_pp']:+.1f} poin), ROI bayangan "
                   f"{so['roi_pct']:+.1f}%.")
    elif n < 30:
        verdict = (f"Baru {n} posisi dan {shadow_n} prediksi berarah yang selesai — terlalu sedikit untuk "
                   "menyimpulkan apa pun."
                   + (f" Win rate posisi {overall['win_rate'] * 100:.0f}% dengan rata-rata harga masuk "
                      f"{overall['avg_entry'] * 100:.0f}¢ masih bisa sepenuhnya kebetulan." if n else ""))
    else:
        brier_line = ""
        if prediction_metrics.get("brier_model") is not None:
            better = prediction_metrics["brier_model"] < prediction_metrics["brier_market"]
            brier_line = (f" Pada {prediction_metrics['n']} prediksi selesai, Brier model "
                          f"{prediction_metrics['brier_model']:.4f} lawan pasar "
                          f"{prediction_metrics['brier_market']:.4f} — model "
                          f"{'lebih' if better else 'TIDAK lebih'} tepat dari harga.")
        z = overall.get("z") or 0
        verdict = (f"{n} posisi selesai: win rate {overall['win_rate'] * 100:.1f}% lawan break-even "
                   f"{overall['avg_entry'] * 100:.1f}% (selisih {overall['edge_pp']:+.1f} poin, z = {z:+.1f}), "
                   f"ROI {overall['roi_pct']:+.1f}%."
                   + (" Selisihnya lebih dari dua simpangan baku — kecil kemungkinan kebetulan."
                      if abs(z) >= 2 else " Selisihnya masih dalam batas kebetulan.")
                   + brier_line)

    return {
        "at": store.now_iso(),
        "positions": {"open": len(open_positions), "settled": len(settled),
                      "void": sum(1 for p in positions if p.get("status") == "void")},
        "overall": overall,
        "max_drawdown": round(drawdown, 2),
        "equity_curve": equity,
        **grouped,
        "predictions": {"total": len(predictions),
                        "open": sum(1 for p in predictions if p.get("status") == "open"),
                        "metrics": prediction_metrics, "by_bucket": by_bucket_pred,
                        "calibration": calibration},
        "shadow": shadow_summary,
        "news_signal": news_signal(predictions),
        "concentration_note": concentration_note(positions),
        "verdict": verdict,
        "settings": {"stake": config.PAPER_STAKE, "min_ev_pct": config.PAPER_MIN_EV * 100,
                     "max_days": config.PAPER_MAX_DAYS, "autoscan_min": config.PAPER_AUTOSCAN_MIN},
    }


def ledger(limit: int = 300) -> dict:
    positions = load_positions()
    predictions = load_predictions()
    positions.sort(key=lambda p: p.get("opened_at") or "", reverse=True)
    predictions.sort(key=lambda p: p.get("made_at") or "", reverse=True)
    return {"positions": positions[:limit], "predictions": predictions[:limit]}


def write_summary() -> None:
    """A Notepad-readable copy of the ledger's state."""
    try:
        s = stats()
    except Exception:  # noqa: BLE001 — a summary file must never break a scan
        return
    o = s["overall"]
    lines = [
        "# Paper test — Bloomberg Hub",
        f"# diperbarui : {s['at']}",
        f"# posisi     : {s['positions']['open']} terbuka, {s['positions']['settled']} selesai, "
        f"{s['positions']['void']} batal",
        "#" + "-" * 68, "",
        s["verdict"], "",
    ]
    if o["n"]:
        lines += [f"Win rate      : {o['win_rate'] * 100:.1f}%  (break-even {o['avg_entry'] * 100:.1f}%)",
                  f"P/L           : ${o['pnl']:+.2f} dari ${o['staked']:.2f}  (ROI {o['roi_pct']:+.1f}%)",
                  f"Max drawdown  : ${s['max_drawdown']:.2f}", ""]
        lines.append("Per sisa waktu saat masuk:")
        for row in s["by_bucket"]:
            lines.append(f"  {row['group']:<12} n={row['n']:<4} win {row['win_rate'] * 100:5.1f}%  "
                         f"break-even {row['avg_entry'] * 100:5.1f}%  ROI {row['roi_pct']:+6.1f}%")
        lines.append("")
    for p in sorted(load_positions(), key=lambda p: p.get("opened_at") or "", reverse=True)[:60]:
        result = p["status"].upper() + (f" {p['pnl']:+.2f}" if p.get("pnl") is not None else "")
        lines.append(f"[{result}] {p['question']}")
        lines.append(f"      {p['side_label']} @ {p['entry_price']:.3f} · stake ${p['stake']} · "
                     f"tenggat {p['deadline']} · {p['source']} · {p['origin']}")
    store.PAPER.mkdir(parents=True, exist_ok=True)
    with store.LOCK:
        tmp = SUMMARY_TXT.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(SUMMARY_TXT)
