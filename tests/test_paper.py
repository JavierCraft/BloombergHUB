"""Paper test ledger: payouts, settlement, scanning, statistics.

The ledger is the evidence for "was the machine learning right", so the tests
pin the ways it could flatter itself: settling a market on a guess when the
source did not answer, opening the same position twice, counting a win rate
without its break-even, or treating a direction-less prediction as a trade.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ml import paper, store

DAY = 86_400_000


def position(**over):
    base = {"id": "p1", "opened_at": "2026-09-01T00:00:00+00:00", "origin": "auto-scan",
            "source": "manifold", "market_id": "m1", "question": "Q?", "deadline": "2026-09-02T00:00:00+00:00",
            "deadline_ms": 1_000, "days_left": 1.0, "bucket": "≤1 hari", "side": "YES", "side_label": "Yes",
            "entry_price": .5, "stake": 10.0, "shares": 20.0, "status": "open", "pnl": None}
    base.update(over)
    return base


def prediction(**over):
    base = {"id": "q1", "made_at": "2026-09-01T00:00:00+00:00", "made_ms": 0, "source": "manifold",
            "market_id": "m1", "question": "Q?", "deadline_ms": 1_000, "bucket": "≤1 hari",
            "market_prob_yes": .5, "model_prob_yes": .6, "ask_yes": .51, "ask_no": .51,
            "status": "open", "outcome": None}
    base.update(over)
    return base


def snapshot(market_id="m1", p=.5, model_p=.62, ask=.51, question=None, bettors=40):
    rows = [{"outcome": "Yes", "price": p, "ask": ask, "entry_source": "best ask", "model_prob": model_p,
             "ev_model_pct": round((model_p / ask - 1) * 100, 2)},
            {"outcome": "No", "price": 1 - p, "ask": ask, "entry_source": "best ask", "model_prob": 1 - model_p,
             "ev_model_pct": round(((1 - model_p) / ask - 1) * 100, 2)}]
    best = max(rows, key=lambda r: r["ev_model_pct"])
    return {
        "source": "manifold", "money": "mana", "id": market_id, "token_id": None, "bettors": bettors,
        "question": question or f"Will event {market_id} happen?",
        "url": "https://manifold.markets/x", "deadline": "2099-01-01T00:00:00+00:00", "deadline_ms": 4_070_908_800_000,
        "days_left": 2.0, "bucket": "1–3 hari", "time_left": "2 hari", "p_yes": p,
        "outcomes": [{"outcome": "Yes", "price": p}, {"outcome": "No", "price": 1 - p}], "liquidity": 500,
        "model": {"available": True, "prob_yes": model_p, "market_prob_yes": p, "direction": "naik",
                  "model_id": "test", "beats_market": False},
        "ev": {"known": True, "rows": rows, "best": best if best["ev_model_pct"] > 0 else None},
    }


class PaperTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(store, "PAPER", root),
            patch.object(paper, "POSITIONS", root / "positions.jsonl"),
            patch.object(paper, "PREDICTIONS", root / "predictions.jsonl"),
            patch.object(paper, "SUMMARY_TXT", root / "ringkasan.txt"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class PayoutTests(unittest.TestCase):
    def test_every_resolution(self):
        yes = position()
        self.assertEqual(paper.payout(yes, "YES"), (20.0, "won"))
        self.assertEqual(paper.payout(yes, "NO"), (0.0, "lost"))
        self.assertEqual(paper.payout(position(side="NO"), "NO"), (20.0, "won"))
        self.assertEqual(paper.payout(yes, "MKT", .25), (5.0, "lost"))
        self.assertEqual(paper.payout(yes, "CANCEL"), (10.0, "void"))


class SettleTests(PaperTestCase):
    @patch("src.ml.paper._manifold_results")
    def test_resolved_unresolved_and_unreachable(self, results):
        store.write_jsonl(paper.POSITIONS, [position(id="a", market_id="m1"),
                                            position(id="b", market_id="m2"),
                                            position(id="c", market_id="m3")])
        store.write_jsonl(paper.PREDICTIONS, [prediction(market_id="m1")])
        results.return_value = ({
            "m1": {"resolved": True, "resolution": "NO", "note": "done"},
            "m2": {"resolved": False, "note": "belum"},
        }, "m3 tidak menjawab")
        out = paper.settle(now_ms=10_000)
        rows = {p["id"]: p for p in paper.load_positions()}
        self.assertEqual(rows["a"]["status"], "lost")
        self.assertEqual(rows["a"]["pnl"], -10.0)
        self.assertEqual(rows["b"]["status"], "open")
        self.assertEqual(rows["c"]["status"], "open")               # never settled on a guess
        self.assertIn("tidak bisa dihubungi", rows["c"]["check_note"])
        self.assertEqual(paper.load_predictions()[0]["outcome"], 0)
        self.assertEqual(out["settled_positions"], 1)
        self.assertEqual(out["failed"][0]["source"], "Manifold")


class ScanTests(PaperTestCase):
    @patch("src.ml.paper.settle", return_value={"settled_positions": 0})
    @patch("src.ml.paper.model.status", return_value={"trained": True})
    @patch("src.ml.paper.deadlines.collect")
    def test_opens_once_and_logs_predictions_once(self, collect, _status, _settle):
        collect.return_value = {"count": 2, "with_model": 2, "failed": [],
                                "markets": [snapshot("m1", model_p=.62), snapshot("m2", model_p=.505)]}
        first = paper.scan(min_ev=.05, stake=10, with_news=False)
        self.assertEqual(first["opened_count"], 1)                  # only m1 clears 5% EV
        self.assertEqual(first["predictions_logged"], 2)
        second = paper.scan(min_ev=.05, stake=10, with_news=False)
        self.assertEqual(second["opened_count"], 0)                 # already open
        self.assertEqual(second["predictions_logged"], 0)           # inside the 12-hour window
        positions = paper.load_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["side"], "YES")
        self.assertAlmostEqual(positions[0]["shares"], 10 / .51, places=3)
        self.assertEqual(paper.load_predictions()[0]["ask_yes"], .51)

    @patch("src.ml.paper.settle", return_value={"settled_positions": 0})
    @patch("src.ml.paper.model.status", return_value={"trained": True})
    @patch("src.ml.paper.deadlines.collect")
    def test_personal_and_thin_markets_are_predicted_but_not_traded(self, collect, _status, _settle):
        collect.return_value = {"count": 3, "with_model": 3, "failed": [], "markets": [
            snapshot("me", model_p=.7, question="Will I weigh under 150lbs by October?"),
            snapshot("thin", model_p=.7, bettors=4),
            snapshot("ok", model_p=.7)]}
        result = paper.scan(min_ev=.05, with_news=False)
        self.assertEqual(result["predictions_logged"], 3)
        self.assertEqual([p["market_id"] for p in paper.load_positions()], ["ok"])

    def test_one_sided_positions_are_flagged(self):
        self.assertIsNone(paper.concentration_note([position(side="NO")] * 4))
        self.assertIn("berkorelasi", paper.concentration_note([position(side="NO")] * 9 + [position(side="YES")]))
        self.assertIsNone(paper.concentration_note([position(side="NO")] * 5 + [position(side="YES")] * 5))

    @patch("src.ml.paper.settle", return_value={})
    @patch("src.ml.paper.model.status", return_value={"trained": False})
    def test_untrained_model_does_not_pretend(self, _status, _settle):
        result = paper.scan(with_news=False)
        self.assertFalse(result["ok"])
        self.assertIn("belum dilatih", result["reason"])


class StatsTests(PaperTestCase):
    def test_win_rate_sits_next_to_break_even(self):
        settled = [position(id=str(i), status="won" if i < 6 else "lost", entry_price=.7,
                            pnl=4.2857 if i < 6 else -10.0, settled_at=f"2026-09-0{1 + i % 9}")
                   for i in range(10)]
        store.write_jsonl(paper.POSITIONS, settled)
        stats = paper.stats()
        overall = stats["overall"]
        self.assertEqual(overall["win_rate"], .6)
        self.assertEqual(overall["avg_entry"], .7)
        self.assertEqual(overall["edge_pp"], -10.0)                 # 60% wins at 70¢ loses money
        self.assertLess(overall["roi_pct"], 0)
        self.assertEqual(len(stats["equity_curve"]), 10)
        self.assertEqual(stats["by_bucket"][0]["group"], "≤1 hari")

    def test_shadow_trades_skip_flat_predictions(self):
        rows = [prediction(id="up-win", model_prob_yes=.6, market_prob_yes=.5, status="settled", outcome=1),
                prediction(id="down-win", model_prob_yes=.4, market_prob_yes=.5, status="settled", outcome=0),
                prediction(id="up-lose", model_prob_yes=.6, market_prob_yes=.5, status="settled", outcome=0),
                prediction(id="flat", model_prob_yes=.502, market_prob_yes=.5, status="settled", outcome=1),
                prediction(id="open", status="open")]
        trades, flat = paper.shadow_trades(rows)
        self.assertEqual(len(trades), 3)
        self.assertEqual(flat, 1)
        self.assertEqual(sum(t["win"] for t in trades), 2)
        store.write_jsonl(paper.PREDICTIONS, rows)
        stats = paper.stats()
        self.assertEqual(stats["shadow"]["overall"]["n"], 3)
        self.assertEqual(stats["predictions"]["metrics"]["n"], 4)

    def test_news_signal_waits_for_enough_data(self):
        self.assertFalse(paper.news_signal([prediction(status="settled", outcome=1, news={"n_72h": 1})])["ready"])


class ResetTests(PaperTestCase):
    def test_reset_archives_instead_of_deleting(self):
        store.write_jsonl(paper.POSITIONS, [position()])
        result = paper.reset()
        self.assertEqual(result["archived_files"], 1)
        self.assertFalse(paper.POSITIONS.exists())
        self.assertTrue((Path(result["folder"]) / "positions.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
