"""Betting mathematics — pure stdlib, zero network calls, cannot fail.

This mirrors the one skill in `machina-sports/sports-skills` marked
`mode: compute` with no `external_network`. The audit's point stands: with every
Polymarket endpoint blocked, this is the layer that still runs today, so it is
worth doing properly rather than approximately.

Two bugs the previous version shipped, both fatal on this machine:

  * `np.math.factorial` — `np.math` was removed in numpy 2.x, so every Poisson
    call raised AttributeError. Replaced with a recurrence that never touches
    factorials at all and stays stable for large xG.
  * `-> pd.DataFrame` in a signature while `import pandas` sat on the last line
    of the file. Under PEP 649 (3.14) that happens to survive; on 3.9-3.13 it is
    a NameError at import. pandas is gone from this module entirely now.

De-vigging note: the multiplicative method (divide by the overround) is the one
everyone writes and it is biased — it shaves too much off longshots. `power` and
`shin` are here because on a two-way market near 50/50 they agree, and on a
20-to-1 longshot they do not, which is exactly where prediction-market edges live.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Odds conversion
# ---------------------------------------------------------------------------


def decimal_to_implied(decimal_odds: float) -> float:
    """Decimal odds -> implied probability (still contains the vig)."""
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds harus > 1.0")
    return 1.0 / decimal_odds


def implied_to_decimal(implied: float) -> float:
    if not 0.0 < implied < 1.0:
        raise ValueError("Probabilitas harus di antara 0 dan 1")
    return 1.0 / implied


def american_to_decimal(american: float) -> float:
    if american == 0:
        raise ValueError("Odds American tidak boleh 0")
    if american > 0:
        return (american / 100.0) + 1.0
    return (100.0 / abs(american)) + 1.0


def decimal_to_american(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds harus > 1.0")
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1.0) * 100, 1)
    return round(-100.0 / (decimal_odds - 1.0), 1)


def probability_to_american(prob: float) -> float:
    return decimal_to_american(implied_to_decimal(prob))


def parse_odds(value: float | str, fmt: str = "auto") -> float:
    """Accept American (+150/-110), decimal (2.50), or probability (0.42) -> decimal.

    `auto` is deliberately conservative: anything in (0,1) is a probability,
    anything in (1,100) without a sign is decimal, anything >=100 or explicitly
    signed is American. A bare `2` is decimal odds, never +2.
    """
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        signed = text.startswith(("+", "-"))
        number = float(text)
        if fmt == "auto" and signed:
            return american_to_decimal(number)
    else:
        number = float(value)
        signed = False

    if fmt == "american":
        return american_to_decimal(number)
    if fmt == "decimal":
        return number
    if fmt == "probability":
        return implied_to_decimal(number)

    if signed or abs(number) >= 100:
        return american_to_decimal(number)
    if 0.0 < number < 1.0:
        return implied_to_decimal(number)
    return number


# ---------------------------------------------------------------------------
# De-vigging
# ---------------------------------------------------------------------------


def _multiplicative(implied: Sequence[float]) -> list[float]:
    total = sum(implied)
    return [p / total for p in implied]


def _power(implied: Sequence[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Solve k such that sum(p_i ** k) == 1. Bisection — no scipy needed."""
    lo, hi = 0.001, 10.0

    def total(k: float) -> float:
        return sum(p ** k for p in implied)

    if total(lo) < 1.0 or total(hi) > 1.0:
        return _multiplicative(implied)

    for _ in range(max_iter):
        mid = (lo + hi) / 2
        value = total(mid)
        if abs(value - 1.0) < tol:
            break
        if value > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    out = [p ** k for p in implied]
    scale = sum(out)
    return [p / scale for p in out]


def _shin(implied: Sequence[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Shin (1993): models the vig as insider trading, z = insider fraction.

    Handles favourite-longshot bias better than dividing by the overround.
    """
    booksum = sum(implied)
    if booksum <= 1.0 or len(implied) < 2:
        return _multiplicative(implied)

    lo, hi = 0.0, 0.5
    for _ in range(max_iter):
        z = (lo + hi) / 2
        probs = [
            (math.sqrt(z * z + 4 * (1 - z) * (p * p) / booksum) - z) / (2 * (1 - z))
            for p in implied
        ]
        total = sum(probs)
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = z
        else:
            hi = z
    scale = sum(probs)
    return [p / scale for p in probs]


DEVIG_METHODS = {
    "multiplicative": _multiplicative,
    "power": _power,
    "shin": _shin,
}


def devig(odds: Sequence[float], method: str = "power") -> dict:
    """Strip the bookmaker margin from any number of outcomes.

    Args:
        odds: decimal odds, one per outcome
        method: multiplicative | power | shin
    """
    if len(odds) < 2:
        raise ValueError("Butuh minimal 2 outcome")
    if method not in DEVIG_METHODS:
        raise ValueError(f"Metode tidak dikenal: {method}. Pilih {list(DEVIG_METHODS)}")

    implied = [decimal_to_implied(o) for o in odds]
    booksum = sum(implied)
    fair = DEVIG_METHODS[method](implied)

    return {
        "method": method,
        "probabilities": [round(p, 6) for p in fair],
        "fair_decimal_odds": [round(1.0 / p, 4) if p > 0 else None for p in fair],
        "fair_american_odds": [
            round(probability_to_american(p), 1) if 0 < p < 1 else None for p in fair
        ],
        "raw_implied": [round(p, 6) for p in implied],
        "overround": round(booksum - 1.0, 6),
        "overround_pct": f"{(booksum - 1.0) * 100:.2f}%",
        "vig_per_outcome_pct": f"{((booksum - 1.0) / len(odds)) * 100:.2f}%",
        "all_methods": {
            name: [round(p, 6) for p in fn(implied)] for name, fn in DEVIG_METHODS.items()
        },
    }


def devig_two_way(odds_a: float, odds_b: float, method: str = "power") -> dict:
    """Two-way de-vig. Keeps the old prob_a/prob_b keys for existing callers."""
    result = devig([odds_a, odds_b], method=method)
    result["prob_a"] = result["probabilities"][0]
    result["prob_b"] = result["probabilities"][1]
    return result


def devig_multi_way(odds_list: Sequence[float], method: str = "power") -> list[float]:
    return devig(odds_list, method=method)["probabilities"]


# ---------------------------------------------------------------------------
# Staking
# ---------------------------------------------------------------------------


def kelly_from_prob(prob: float, decimal_odds: float, fraction: float = 1.0) -> float:
    """Kelly stake as a fraction of bankroll, from an explicit probability.

    f* = (bp - q) / b, where b = decimal_odds - 1.

    Prefer this over `kelly_fraction`. The old signature took an "edge" and
    reconstructed p as `1/odds + edge`, which quietly assumes the offered price
    is the market's fair price — false whenever there is vig, which is always.
    """
    if not 0.0 <= prob <= 1.0:
        raise ValueError("Probabilitas harus 0..1")
    if decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    f = (b * prob - (1.0 - prob)) / b
    return max(0.0, f * fraction)


def kelly_fraction(edge: float, odds_decimal: float) -> float:
    """Legacy API: edge expressed as probability points above the offered price."""
    if edge <= 0 or odds_decimal <= 1:
        return 0.0
    prob = min(1.0, 1.0 / odds_decimal + edge)
    return kelly_from_prob(prob, odds_decimal)


def half_kelly(edge: float, odds_decimal: float) -> float:
    return kelly_fraction(edge, odds_decimal) / 2.0


def expected_value(prob: float, decimal_odds: float, stake: float = 1.0) -> float:
    """EV in units of stake. Positive means the bet is worth taking."""
    return stake * (prob * decimal_odds - 1.0)


def detect_arbitrage(*outcomes: float) -> dict:
    """True arbitrage exists when the implied probabilities sum below 1."""
    if len(outcomes) < 2:
        raise ValueError("Butuh minimal 2 outcome")
    implied = [decimal_to_implied(o) for o in outcomes]
    total = sum(implied)

    if total >= 1.0:
        return {
            "exists": False,
            "book_sum": round(total, 6),
            "overround_pct": f"{(total - 1.0) * 100:.2f}%",
            "message": "Tidak ada arbitrase — jumlah probabilitas di atas 100%.",
        }

    profit_pct = (1.0 / total - 1.0) * 100
    stakes = [imp / total for imp in implied]
    return {
        "exists": True,
        "book_sum": round(total, 6),
        "profit_pct": f"{profit_pct:.3f}%",
        "profit_per_100": round(profit_pct, 3),
        "overround_pct": f"{(total - 1.0) * 100:.2f}%",
        "stakes_normalized": {f"outcome_{i}": round(s, 5) for i, s in enumerate(stakes)},
        "stake_per_1000": {f"outcome_{i}": round(s * 1000, 2) for i, s in enumerate(stakes)},
        "message": "Periksa limit dan latensi sebelum percaya angka ini.",
    }


# ---------------------------------------------------------------------------
# Prediction-market edge (Polymarket prices ARE probabilities, 0.00-1.00)
# ---------------------------------------------------------------------------


def market_edge(
    model_prob: float,
    market_price: float,
    bankroll: float = 1000.0,
    kelly_multiplier: float = 0.5,
    fee_bps: float = 0.0,
) -> dict:
    """Compare a model probability against a prediction-market price.

    This is the calculation the whole platform exists to make: the model says
    62%, the market says 55c, so is there an edge and how much do you stake?

    `kelly_multiplier` defaults to 0.5 — half-Kelly — because full Kelly assumes
    your probability is exactly right, and it never is.
    """
    if not 0.0 < market_price < 1.0:
        raise ValueError("Harga market harus di antara 0 dan 1 (harga share Polymarket)")
    if not 0.0 <= model_prob <= 1.0:
        raise ValueError("Probabilitas model harus 0..1")

    fee = fee_bps / 10_000.0
    effective_price = market_price * (1.0 + fee)
    decimal_odds = 1.0 / effective_price

    edge_pp = (model_prob - market_price) * 100
    ev_per_dollar = expected_value(model_prob, decimal_odds)
    kelly_full = kelly_from_prob(model_prob, decimal_odds)
    kelly_used = kelly_full * kelly_multiplier

    # Break-even: how wrong can the model be before this bet loses money?
    breakeven_prob = effective_price

    return {
        "model_prob": round(model_prob, 6),
        "market_price": round(market_price, 6),
        "effective_price": round(effective_price, 6),
        "decimal_odds": round(decimal_odds, 4),
        "american_odds": round(decimal_to_american(decimal_odds), 1),
        "edge_pp": round(edge_pp, 3),
        "edge_pct_of_price": round((model_prob / market_price - 1.0) * 100, 3),
        "ev_per_dollar": round(ev_per_dollar, 5),
        "ev_per_100": round(ev_per_dollar * 100, 3),
        "kelly_full": round(kelly_full, 5),
        "kelly_used": round(kelly_used, 5),
        "kelly_multiplier": kelly_multiplier,
        "stake": round(bankroll * kelly_used, 2),
        "bankroll": bankroll,
        "breakeven_prob": round(breakeven_prob, 6),
        "verdict": (
            "BET" if ev_per_dollar > 0.02 else
            "MARGINAL" if ev_per_dollar > 0 else
            "NO BET"
        ),
        "note": (
            "Keuntungan setipis ini biasanya habis oleh selisih harga beli-jual, "
            "jadi pertimbangkan untuk melewatinya."
            if 0 < ev_per_dollar <= 0.02 else ""
        ),
    }


# ---------------------------------------------------------------------------
# Poisson match model
# ---------------------------------------------------------------------------


def _poisson_pmf(lam: float, max_k: int) -> list[float]:
    """PMF by recurrence: p(0)=e^-lam, p(k)=p(k-1)*lam/k. No factorials."""
    if lam < 0:
        raise ValueError("xG tidak boleh negatif")
    pmf = [math.exp(-lam)]
    for k in range(1, max_k + 1):
        pmf.append(pmf[-1] * lam / k)
    return pmf


def match_probabilities(
    home_xg: float,
    away_xg: float,
    max_goals: int = 10,
    total_line: float = 2.5,
) -> dict:
    """Full Poisson match model: 1X2, over/under, BTTS, top correct scores.

    Independent-Poisson is the standard baseline. It underestimates draws
    slightly (goals are mildly correlated) — good enough for screening, not for
    final pricing. Adjust with Dixon-Coles if you take this further.
    """
    home_pmf = _poisson_pmf(home_xg, max_goals)
    away_pmf = _poisson_pmf(away_xg, max_goals)

    home_win = draw = away_win = 0.0
    over = under = 0.0
    btts_yes = 0.0
    scores: list[tuple[str, float]] = []

    for h, ph in enumerate(home_pmf):
        for a, pa in enumerate(away_pmf):
            p = ph * pa
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
            if h + a > total_line:
                over += p
            else:
                under += p
            if h > 0 and a > 0:
                btts_yes += p
            scores.append((f"{h}-{a}", p))

    mass = home_win + draw + away_win  # < 1 by the tail beyond max_goals
    scores.sort(key=lambda s: s[1], reverse=True)

    def row(name: str, prob: float) -> dict:
        prob = prob / mass if mass else 0.0
        return {
            "outcome": name,
            "probability": round(prob, 5),
            "percent": f"{prob * 100:.2f}%",
            "fair_decimal": round(1.0 / prob, 3) if prob > 1e-9 else None,
            "fair_american": round(probability_to_american(prob), 1) if 1e-9 < prob < 1 else None,
        }

    return {
        "inputs": {"home_xg": home_xg, "away_xg": away_xg, "total_line": total_line,
                   "max_goals": max_goals},
        "one_x_two": [row("Home", home_win), row("Draw", draw), row("Away", away_win)],
        "totals": [row(f"Over {total_line}", over), row(f"Under {total_line}", under)],
        "btts": [row("BTTS Yes", btts_yes), row("BTTS No", mass - btts_yes)],
        "top_scores": [
            {"score": name, "probability": round(p / mass, 5), "percent": f"{p / mass * 100:.2f}%"}
            for name, p in scores[:10]
        ],
        "tail_mass_ignored": round(1.0 - mass, 6),
    }


def poisson_poisson(home_xg: float, away_xg: float, max_goals: int = 6) -> list[dict]:
    """Legacy API — now returns records instead of a DataFrame (no pandas here)."""
    return match_probabilities(home_xg, away_xg, max_goals=max_goals)["one_x_two"]


# ---------------------------------------------------------------------------
# Calibration — the audit's rule: judge by Brier, not PnL
# ---------------------------------------------------------------------------


def brier_score(predictions: Sequence[float], outcomes: Sequence[float]) -> float:
    """Mean squared error of probabilistic forecasts. Lower is better."""
    if len(predictions) != len(outcomes):
        raise ValueError("Panjang prediksi dan hasil harus sama")
    if not predictions:
        raise ValueError("Tidak ada prediksi")
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)


def log_loss(predictions: Sequence[float], outcomes: Sequence[float], eps: float = 1e-15) -> float:
    """Punishes confident-and-wrong far harder than Brier does."""
    if len(predictions) != len(outcomes):
        raise ValueError("Panjang prediksi dan hasil harus sama")
    total = 0.0
    for p, o in zip(predictions, outcomes):
        p = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(predictions)


def calibration_table(
    predictions: Sequence[float],
    outcomes: Sequence[float],
    bins: int = 10,
) -> dict:
    """Murphy decomposition: Brier = reliability - resolution + uncertainty.

    Reliability is the number that matters. Low reliability means that when you
    say 70% it happens about 70% of the time — which is the only property that
    survives contact with a market.
    """
    if len(predictions) != len(outcomes):
        raise ValueError("Panjang prediksi dan hasil harus sama")
    n = len(predictions)
    if n == 0:
        raise ValueError("Tidak ada prediksi")

    base_rate = sum(outcomes) / n
    buckets: list[dict] = []
    reliability = resolution = 0.0

    for i in range(bins):
        lo = i / bins
        hi = (i + 1) / bins
        idx = [
            j for j in range(n)
            if (predictions[j] >= lo and (predictions[j] < hi or (i == bins - 1 and predictions[j] <= hi)))
        ]
        if not idx:
            buckets.append({
                "bin": f"{lo:.0%}-{hi:.0%}", "count": 0,
                "mean_predicted": None, "observed": None, "gap_pp": None,
            })
            continue

        mean_pred = sum(predictions[j] for j in idx) / len(idx)
        observed = sum(outcomes[j] for j in idx) / len(idx)
        weight = len(idx) / n
        reliability += weight * (mean_pred - observed) ** 2
        resolution += weight * (observed - base_rate) ** 2

        buckets.append({
            "bin": f"{lo:.0%}-{hi:.0%}",
            "count": len(idx),
            "mean_predicted": round(mean_pred, 4),
            "observed": round(observed, 4),
            "gap_pp": round((mean_pred - observed) * 100, 2),
        })

    uncertainty = base_rate * (1 - base_rate)
    brier = brier_score(predictions, outcomes)

    return {
        "n": n,
        "base_rate": round(base_rate, 4),
        "brier": round(brier, 6),
        "log_loss": round(log_loss(predictions, outcomes), 6),
        "reliability": round(reliability, 6),
        "resolution": round(resolution, 6),
        "uncertainty": round(uncertainty, 6),
        "skill_vs_base_rate": round(1 - brier / uncertainty, 4) if uncertainty > 0 else None,
        "bins": buckets,
        "reading": (
            "Perkiraan Anda tepat" if reliability < 0.01 else
            "Ketepatannya sedang" if reliability < 0.03 else
            "Ketepatannya buruk — jangan dipakai untuk uang sungguhan"
        ),
    }


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0
