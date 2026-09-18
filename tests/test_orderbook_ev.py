"""IEP/IEV uncrossing, depth to fair value, and EV at the executable price.

The cases pinned here are the ones where a plausible-looking number would be
wrong: an uncrossed continuous book must report IEV = 0 rather than something
more interesting, a tie on volume must be broken by surplus and then by distance
to the reference, and EV at the market's own price must never come out positive.
"""
import unittest
from unittest.mock import patch

from src.markets import Manifold
from src.markets.analysis import analyse, sector_brief
from src.markets.ev import expected_value, quotes
from src.markets.orderbook import (fair_value_depth, from_manifold, from_polymarket, levels,
                                   uncross)


class LevelsTests(unittest.TestCase):
    def test_strings_are_parsed_merged_and_filtered(self):
        rows = levels([{"price": "0.50", "size": "10"}, {"price": "0.5", "size": "5"},
                       {"price": "1.2", "size": "9"}, {"price": "0.4", "size": "-1"},
                       {"price": "bad", "size": "3"}, (0.3, 2)])
        self.assertEqual(rows, [(0.3, 2.0), (0.5, 15.0)])


class UncrossTests(unittest.TestCase):
    def test_crossed_book_ties_broken_by_surplus_then_reference(self):
        bids = [(0.60, 100), (0.55, 50)]
        asks = [(0.50, 30), (0.58, 80), (0.65, 40)]
        result = uncross(bids, asks)
        # 0.58 and 0.60 both clear 100 shares with a surplus of 10; mid is 0.55,
        # so the nearer price wins.
        self.assertTrue(result["crossed"])
        self.assertEqual(result["iep"], 0.58)
        self.assertEqual(result["iev"], 100)
        self.assertEqual(result["surplus"], 10)
        self.assertEqual(result["surplus_side"], "jual")

    def test_continuous_book_reports_zero_not_a_guess(self):
        result = uncross([(0.48, 100)], [(0.52, 100)])
        self.assertFalse(result["crossed"])
        self.assertEqual(result["iev"], 0.0)
        self.assertIsNone(result["iep"])
        self.assertIn("tidak bersilangan", result["reason"])
        self.assertEqual(result["mid"], 0.5)

    def test_empty_side(self):
        result = uncross([], [(0.5, 10)])
        self.assertEqual(result["iev"], 0.0)
        self.assertFalse(result["crossed"])


class FairValueTests(unittest.TestCase):
    def test_depth_below_fair_value(self):
        depth = fair_value_depth([(0.45, 7)], [(0.50, 10), (0.55, 20), (0.70, 5)], 0.62)
        self.assertEqual(depth["buy_yes"]["shares"], 30)
        self.assertAlmostEqual(depth["buy_yes"]["cost"], 16.0)
        self.assertAlmostEqual(depth["buy_yes"]["expected_profit"], 2.6)
        self.assertEqual(depth["buy_no"]["shares"], 0)
        self.assertEqual(depth["side"], "YES")

    def test_bids_above_fair_are_no_purchases(self):
        depth = fair_value_depth([(0.70, 10)], [(0.80, 5)], 0.60)
        self.assertEqual(depth["buy_no"]["shares"], 10)
        self.assertAlmostEqual(depth["buy_no"]["cost"], 3.0)          # (1 - 0.70) × 10
        self.assertAlmostEqual(depth["buy_no"]["expected_profit"], 1.0)
        self.assertEqual(depth["buy_no"]["worst_price"], 0.3)

    def test_missing_fair_value(self):
        self.assertFalse(fair_value_depth([], [], None)["known"])


class SourceShapesTests(unittest.TestCase):
    def test_polymarket_clob_payload(self):
        book = {"bids": [{"price": "0.48", "size": "30"}], "asks": [{"price": "0.52", "size": "25"}],
                "last_trade_price": "0.5", "tick_size": "0.01", "timestamp": "123"}
        result = from_polymarket(book, 0.6)
        self.assertEqual(result["auction"]["best_ask"], 0.52)
        self.assertEqual(result["auction"]["reference"], 0.5)
        self.assertEqual(result["fair_value"]["buy_yes"]["shares"], 25)

    @patch.object(Manifold, "_raw_bets")
    def test_manifold_limit_orders_become_shares(self, raw):
        raw.return_value = [
            {"limitProb": 0.4, "outcome": "YES", "orderAmount": 50, "amount": 10},   # 40 mana at 0.4
            {"limitProb": 0.6, "outcome": "NO", "orderAmount": 20, "amount": 0},     # 20 mana at 1-0.6
            {"limitProb": 0.3, "outcome": "YES", "orderAmount": 5, "amount": 5},     # filled: gone
        ]
        book = Manifold().order_book("abc")
        self.assertEqual(book["bids"][0]["shares"], 100.0)
        self.assertEqual(book["asks"][0]["shares"], 50.0)
        self.assertIn("auction", book["iev"])
        self.assertFalse(book["iev"]["auction"]["crossed"])
        self.assertEqual(from_manifold(book, 0.5)["fair_value"]["buy_no"]["shares"], 0)


def market(**over):
    base = {"question": "Will X?", "id": "0x" + "ab" * 32, "url": "https://polymarket.com/event/x",
            "outcomes": [{"outcome": "Yes", "price": .55}, {"outcome": "No", "price": .45}],
            "best_bid": .54, "best_ask": .56, "spread": .02, "liquidity": 20000, "volume24h": 500,
            "change1h": 0, "change24h": .01, "change1w": .02, "end_date": "2099-01-01T00:00:00Z"}
    base.update(over)
    return base


class ExpectedValueTests(unittest.TestCase):
    def test_second_outcome_ask_mirrors_first_outcome_bid(self):
        rows = quotes(market())
        self.assertEqual(rows[0]["ask"], .56)
        self.assertEqual(rows[1]["ask"], .46)
        self.assertEqual(rows[1]["bid"], .44)

    def test_consensus_ev_is_never_positive_at_the_ask(self):
        ev = expected_value(market())
        self.assertTrue(all(r["ev_consensus_pct"] <= 0 for r in ev["rows"]))
        self.assertIsNone(ev["best"])
        self.assertIn("biaya masuk", ev["verdict"])

    def test_model_ev_and_unproven_label(self):
        ev = expected_value(market(), {"available": True, "prob_yes": .62, "beats_market": False})
        self.assertAlmostEqual(ev["rows"][0]["ev_model_pct"], round((.62 / .56 - 1) * 100, 2))
        self.assertEqual(ev["best"]["outcome"], "Yes")
        self.assertIn("BELUM", ev["verdict"])

    def test_without_book_the_cost_assumption_is_labelled(self):
        ev = expected_value(market(best_bid=None, best_ask=None), cost_pp=1.0)
        self.assertEqual(ev["rows"][0]["ask"], .56)
        self.assertIn("asumsi biaya", ev["rows"][0]["entry_source"])

    def test_analysis_carries_ev_and_brief_finds_best(self):
        strong = dict(market(question="A"), analysis=analyse(market(), {"available": True, "prob_yes": .70,
                                                                         "beats_market": True}))
        weak = dict(market(question="B"), analysis=analyse(market(), {"available": True, "prob_yes": .58,
                                                                       "beats_market": True}))
        self.assertIn("ev", strong["analysis"])
        brief = sector_brief([weak, strong], "tech")
        self.assertEqual(brief["best_ev"]["question"], "A")
        self.assertEqual(brief["positive_ev_markets"], 2)
        self.assertEqual(brief["modelled_markets"], 2)


if __name__ == "__main__":
    unittest.main()
