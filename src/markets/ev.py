"""Expected value per outcome, at the price you can actually get.

Buying an outcome at entry price `e` when its true probability is `q`:

    EV per $1 staked : q / e − 1
    EV per share     : q − e          (a share pays $1 when the outcome wins)

Two probabilities are shown side by side, never merged:

  * **consensus** — the market's own displayed price. EV at consensus is never
    positive when you pay the ask: buying at the ask when the mid is fair loses
    half the spread. It is the cost of entry written as EV, and it is always
    available because it needs nothing but the book.
  * **model** — the machine-learning probability, when a model has been trained.
    Only this one can be positive, and it is only worth what the model's
    out-of-sample record says, which travels with it.

Entry prices come from the top of the book. Gamma publishes best bid/ask for
the first outcome only; for a two-outcome market the second outcome's ask is
`1 − best_bid` (buying NO at q is selling YES at 1 − q). When no book is given,
the displayed price plus an explicit cost assumption stands in, and says so.
"""
from __future__ import annotations

import math
from typing import Any


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def quotes(market: dict, cost_pp: float = 0.0) -> list[dict]:
    """Entry (ask) and exit (bid) per outcome."""
    outcomes = [o for o in (market.get("outcomes") or []) if _f(o.get("price")) is not None]
    bid0, ask0 = _f(market.get("best_bid")), _f(market.get("best_ask"))
    two_way = len(outcomes) == 2
    book = two_way and bid0 is not None and ask0 is not None and 0 < bid0 <= ask0 < 1

    rows = []
    for i, o in enumerate(outcomes):
        price = _f(o["price"])
        if book:
            ask = ask0 if i == 0 else 1 - bid0
            bid = bid0 if i == 0 else 1 - ask0
            source = "best ask"
        else:
            ask = min(0.999, price + cost_pp / 100) if price is not None else None
            bid = max(0.001, price - cost_pp / 100) if price is not None else None
            source = (f"harga outcome + asumsi biaya {cost_pp:g} poin" if cost_pp
                      else "harga outcome (buku tidak tersedia)")
        rows.append({
            "outcome": o.get("outcome"),
            "price": round(price, 4),
            "ask": round(ask, 4) if ask is not None else None,
            "bid": round(bid, 4) if bid is not None else None,
            "entry_source": source,
        })
    return rows


def ev_pct(prob: float | None, entry: float | None) -> float | None:
    if prob is None or entry is None or not 0 < entry < 1:
        return None
    return round((prob / entry - 1) * 100, 2)


def expected_value(market: dict, model: dict | None = None, cost_pp: float = 0.0) -> dict:
    """EV rows for every outcome.

    `model` is the output of `src.ml.model.predict_market` — a probability for
    the *first* outcome plus the model's track record — or None.
    """
    rows = quotes(market, cost_pp)
    if not rows:
        return {"known": False, "reason": "Harga outcome tidak tersedia, jadi EV tidak bisa dihitung."}

    prob_first = None
    if model and model.get("available") and model.get("prob_yes") is not None and len(rows) == 2:
        prob_first = float(model["prob_yes"])

    for i, row in enumerate(rows):
        row["breakeven_pct"] = round(row["ask"] * 100, 2) if row["ask"] is not None else None
        row["ev_consensus_pct"] = ev_pct(row["price"], row["ask"])
        if prob_first is not None:
            prob = prob_first if i == 0 else 1 - prob_first
            row["model_prob"] = round(prob, 4)
            row["ev_model_pct"] = ev_pct(prob, row["ask"])
            row["ev_per_share"] = round(prob - row["ask"], 4) if row["ask"] is not None else None
        else:
            row["model_prob"] = row["ev_model_pct"] = row["ev_per_share"] = None

    positive = [r for r in rows if (r["ev_model_pct"] or 0) > 0]
    best = max(positive, key=lambda r: r["ev_model_pct"]) if positive else None

    if prob_first is None:
        verdict = ("EV di harga konsensus selalu nol atau negatif — itu biaya masuk. EV positif "
                   "baru mungkin kalau ada perkiraan yang berbeda dari pasar: latih model di "
                   "halaman Paper Test, atau isi perkiraan Anda sendiri.")
    elif best is None:
        verdict = "Model tidak melihat EV positif di sisi mana pun pada harga masuk saat ini."
    elif model.get("beats_market"):
        verdict = (f"Model melihat EV {best['ev_model_pct']:+.1f}% untuk {best['outcome']} di "
                   f"{best['ask'] * 100:.1f}¢, dan model ini lebih tepat dari harga pasar pada data uji.")
    else:
        verdict = (f"Model melihat EV {best['ev_model_pct']:+.1f}% untuk {best['outcome']}, tetapi "
                   "model ini BELUM terbukti lebih tepat dari harga pasar — anggap bahan periksa, "
                   "bukan sinyal.")

    return {
        "known": True,
        "rows": rows,
        "best": best,
        "model": model or {"available": False},
        "verdict": verdict,
        "note": ("EV = peluang ÷ harga masuk − 1, per $1 taruhan, dipegang sampai penyelesaian. "
                 "Harga masuk memakai best ask; belum termasuk slippage untuk ukuran besar."),
    }
