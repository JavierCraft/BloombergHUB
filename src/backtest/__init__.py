"""Backtesting and calibration — the layer the official SDK does not have.

Source pattern: `evan-kolberg/prediction-market-backtesting` (★1.188) — 254
files, 26 Polymarket-specific, 52 test files, and **zero signing files**. That
is a design decision, not a gap: it replays markets and measures calibration
without ever holding a key.

Two things worth carrying over verbatim:

  * its default branch is `v4.1-alpha`, not `main`. Scripts that assume `main`
    fail, which is why automated summaries kept reporting the repo as missing.
  * the metric that matters is the Brier score, not PnL. Over a short window a
    lucky model and a calibrated model produce identical equity curves. Only the
    calibrated one survives.

Everything here runs locally with no clone and no network, so the calibration
workflow is available today even with every market API blocked.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config import REPOS_DIR  # noqa: E402

BACKTEST_DIR = REPOS_DIR / "backtesting"
CLONE_COMMAND = (
    "git clone -b v4.1-alpha "
    "https://github.com/evan-kolberg/prediction-market-backtesting.git "
    f"{BACKTEST_DIR}"
)


class BacktestEngine:
    """Optional wrapper over the upstream repo, when it has been cloned."""

    def __init__(self, repo_dir: Path = BACKTEST_DIR):
        self.dir = Path(repo_dir)
        if not self.dir.exists():
            from src.core.errors import DataNotCloned

            raise DataNotCloned(
                "prediction-market-backtesting", str(self.dir), CLONE_COMMAND
            )

    def run(self, strategy: str = "default", **kwargs) -> dict:
        cmd = [sys.executable, "-m", "backtest", "--strategy", strategy]
        for key, value in kwargs.items():
            cmd += [f"--{key.replace('_', '-')}", str(value)]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(self.dir), timeout=600
        )
        return {
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-2000:],
        }


class Simulator:
    """Local backtester: no clone, no network, no keys.

    Takes a list of settled bets and reports what actually happened to the
    bankroll and — more importantly — whether the probabilities were honest.
    """

    @staticmethod
    def run(
        bets: Sequence[dict],
        bankroll: float = 1000.0,
        kelly_multiplier: float = 0.5,
        max_stake_pct: float = 0.05,
        flat_stake: float | None = None,
    ) -> dict:
        """Replay bets in order.

        Each bet is `{"prob": model probability, "price": market price 0-1,
        "outcome": 1 or 0, "label": optional}`. Prices are prediction-market
        share prices, so a win pays `1/price` per unit staked.

        `max_stake_pct` caps any single stake. Kelly on a mispriced longshot will
        happily suggest 40% of your bankroll; the cap is what keeps one wrong
        probability from ending the experiment.
        """
        from src.sports.betting import kelly_from_prob

        equity = [round(bankroll, 2)]
        rows: list[dict] = []
        peak = bankroll
        max_dd = 0.0
        wins = staked_total = returned_total = 0.0
        returns: list[float] = []
        predictions: list[float] = []
        outcomes: list[float] = []

        for i, bet in enumerate(bets):
            prob = float(bet["prob"])
            price = float(bet["price"])
            outcome = int(bet["outcome"])
            if not 0 < price < 1:
                raise ValueError(f"Bet #{i}: harga harus di antara 0 dan 1")
            if not 0 <= prob <= 1:
                raise ValueError(f"Bet #{i}: probabilitas harus 0..1")

            decimal_odds = 1.0 / price
            if flat_stake is not None:
                fraction = flat_stake / bankroll if bankroll > 0 else 0.0
            else:
                fraction = kelly_from_prob(prob, decimal_odds) * kelly_multiplier
            fraction = min(fraction, max_stake_pct)
            stake = round(max(0.0, bankroll * fraction), 2)

            payout = round(stake * decimal_odds, 2) if outcome else 0.0
            pnl = round(payout - stake, 2)
            before = bankroll
            bankroll = round(bankroll + pnl, 2)

            staked_total += stake
            returned_total += payout
            wins += outcome if stake > 0 else 0
            if stake > 0:
                returns.append(pnl / stake)
            predictions.append(prob)
            outcomes.append(outcome)

            peak = max(peak, bankroll)
            if peak > 0:
                max_dd = max(max_dd, (peak - bankroll) / peak)

            equity.append(bankroll)
            rows.append({
                "n": i + 1,
                "label": bet.get("label", f"bet {i + 1}"),
                "prob": round(prob, 4),
                "price": round(price, 4),
                "edge_pp": round((prob - price) * 100, 2),
                "stake": stake,
                "stake_pct": round(fraction * 100, 2),
                "outcome": outcome,
                "pnl": pnl,
                "bankroll": bankroll,
            })

            if bankroll <= 0:
                rows[-1]["label"] += " — bangkrut"
                break

        placed = [r for r in rows if r["stake"] > 0]
        n = len(placed)
        roi = ((returned_total - staked_total) / staked_total * 100) if staked_total else 0.0

        return {
            "bets": rows,
            "equity_curve": equity,
            "summary": {
                "n_bets_offered": len(rows),
                "n_bets_placed": n,
                "start_bankroll": equity[0],
                "end_bankroll": equity[-1],
                "profit": round(equity[-1] - equity[0], 2),
                "return_pct": round((equity[-1] / equity[0] - 1) * 100, 2) if equity[0] else 0,
                "total_staked": round(staked_total, 2),
                "roi_pct": round(roi, 2),
                "win_rate_pct": round(wins / n * 100, 2) if n else 0.0,
                "max_drawdown_pct": round(max_dd * 100, 2),
                "sharpe_per_bet": Simulator.sharpe(returns),
                "kelly_multiplier": kelly_multiplier,
                "max_stake_pct": max_stake_pct,
            },
            "calibration": Simulator.calibration(predictions, outcomes),
            "verdict": Simulator._verdict(predictions, outcomes, roi),
        }

    @staticmethod
    def calibration(predictions: Sequence[float], outcomes: Sequence[float]) -> dict:
        from src.sports.betting import calibration_table

        bins = 10 if len(predictions) >= 50 else 5
        return calibration_table(predictions, outcomes, bins=bins)

    @staticmethod
    def _verdict(predictions: Sequence[float], outcomes: Sequence[float], roi: float) -> str:
        """Judge the model on calibration first, profit second — the audit's rule."""
        from src.sports.betting import brier_score

        if not predictions:
            return "Tidak ada data."
        brier = brier_score(predictions, outcomes)
        base = sum(outcomes) / len(outcomes)
        naive = base * (1 - base)

        if len(predictions) < 30:
            return (
                f"Baru {len(predictions)} taruhan — terlalu sedikit untuk disimpulkan. "
                "Ketepatan baru bisa dinilai setelah ratusan taruhan."
            )
        if naive > 0 and brier >= naive:
            return (
                f"Ketepatannya ({brier:.4f}) tidak lebih baik daripada asal tebak ({naive:.4f}). "
                "Perkiraan Anda belum menambah informasi apa pun, jadi keuntungan di atas "
                "kemungkinan besar hanya kebetulan."
            )
        if roi > 0:
            return (
                f"Perkiraan Anda lebih tepat daripada asal tebak ({brier:.4f}), dan hasilnya "
                f"positif {roi:.1f}%. Kombinasi ini layak dilanjutkan."
            )
        return (
            f"Perkiraan Anda sudah cukup tepat ({brier:.4f}), tapi hasilnya masih {roi:.1f}%. "
            "Masalahnya ada pada cara memasang taruhan, bukan pada perkiraannya."
        )

    @staticmethod
    def sharpe(returns: Sequence[float]) -> float:
        n = len(returns)
        if n < 2:
            return 0.0
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
        sd = math.sqrt(variance)
        return round(mean / sd, 4) if sd > 0 else 0.0

    @staticmethod
    def max_drawdown(equity_curve: Sequence[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        worst = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return round(worst, 4)


# Backwards-compatible alias for the previous class name.
ManualBacktest = Simulator
