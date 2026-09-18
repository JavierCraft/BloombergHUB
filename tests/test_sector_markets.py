"""Sector pages without Polymarket: Limitless + Manifold fallback, and the sector ML list.

Polymarket's hosts are blocked from this connection on most days; these tests
pin down what the sector pages show instead — labelled per market, analysed the
same way — and that a quote with nobody behind it is never used as an entry price.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.errors import NetworkBlocked  # noqa: E402
from src.markets import limitless, sector  # noqa: E402
from src.ml import deadlines, store  # noqa: E402


def raw_limitless(slug="nip-vs-m80", prices=(0.5575, 0.4425), buy=0.583, sell=0.532, hours=24,
                  categories=("Esports",), title="NIP vs M80"):
    end = datetime.now(timezone.utc) + timedelta(hours=hours)
    return {
        "marketType": "single", "slug": slug, "title": title, "prices": list(prices),
        "outcomeTokens": ["Yes", "No"], "tokens": {"yes": "111", "no": "222"},
        "tradePrices": {"buy": {"market": [buy]}, "sell": {"market": [sell]}},
        "categories": list(categories), "volumeFormatted": "1250.5", "createdAt": "2026-09-16T10:00:00Z",
        "expirationTimestamp": end.timestamp() * 1000, "description": "<p>Resolves <b>YES</b> if NIP wins.</p>",
        "expired": False, "status": "FUNDED", "winningOutcomeIndex": None,
    }


def manifold_raw(market_id="m1", p=0.4, hours=48, bettors=20, question="Will X happen by Friday?"):
    now = store.now_ms()
    return {"id": market_id, "question": question, "probability": p, "closeTime": now + hours * 3_600_000,
            "createdTime": now - 10 * 86_400_000, "uniqueBettorCount": bettors, "totalLiquidity": 100,
            "volume24Hours": 12, "volume": 500, "url": f"https://manifold.markets/x/{market_id}", "token": "MANA"}


class LimitlessSummaryTests(unittest.TestCase):
    def test_normal_book(self):
        m = limitless.summary(raw_limitless())
        self.assertEqual((m["source"], m["id"]), ("limitless", "nip-vs-m80"))
        self.assertEqual((m["best_bid"], m["best_ask"]), (0.532, 0.583))
        self.assertAlmostEqual(m["spread"], 0.051)
        self.assertEqual([o["price"] for o in m["outcomes"]], [0.5575, 0.4425])
        self.assertEqual(m["rules"], "Resolves YES if NIP wins.")
        self.assertTrue(m["url"].endswith("/nip-vs-m80"))

    def test_quote_with_nobody_behind_it_is_not_a_price(self):
        # Measured: "Map 2 Winner" markets show ask 0.0 before the map starts.
        for buy, sell in ((0.0, 0.0), (0.0, 0.4), (0.999, 0.001), (1.0, 0.5), (None, 0.5)):
            raw = raw_limitless(buy=buy, sell=sell)
            if buy is None:
                raw["tradePrices"]["buy"]["market"] = []
            m = limitless.summary(raw)
            self.assertIsNone(m["best_ask"], (buy, sell))
            self.assertIsNone(m["best_bid"], (buy, sell))
            self.assertIsNone(m["spread"], (buy, sell))

    def test_cents_are_normalised_together(self):
        m = limitless.summary(raw_limitless(prices=(55, 45), buy=57, sell=53))
        self.assertEqual([o["price"] for o in m["outcomes"]], [0.55, 0.45])
        self.assertEqual((m["best_bid"], m["best_ask"]), (0.53, 0.57))

    def test_group_markets_are_skipped(self):
        self.assertIsNone(limitless.summary(dict(raw_limitless(), marketType="group")))


class SectorFallbackTests(unittest.TestCase):
    def test_blocked_polymarket_falls_back_and_labels_every_market(self):
        real = [limitless.summary(raw_limitless(slug=f"m-{i}", title=f"Match {i}")) for i in range(8)]
        play = [sector.manifold_market(manifold_raw(market_id=f"x{i}")) for i in range(8)]

        def blocked(*a, **k):
            raise NetworkBlocked("gamma-api.polymarket.com", "sertifikat tidak cocok")

        with patch.object(sector.Polymarket, "search", blocked), \
                patch.object(limitless.Limitless, "sector", lambda self, s, n: real[:n]), \
                patch.object(sector, "manifold_sector", lambda s, q, n: play[:n]):
            result = sector.sector_markets("esports", "", 12)

        self.assertTrue(result["reachable"])
        self.assertTrue(result["fallback"])
        self.assertEqual(result["source"], "limitless+manifold")
        self.assertEqual(len(result["markets"]), 12)
        sources = [m["source"] for m in result["markets"]]
        self.assertEqual(sources[:6], ["limitless"] * 6, "real money first")
        self.assertIn("manifold", sources)
        self.assertEqual(result["failed"][0]["source"], "Polymarket")
        self.assertIn("Limitless", result["reason"])
        for m in result["markets"]:
            self.assertIn("ev", m["analysis"])

    def test_nothing_answers(self):
        def blocked(*a, **k):
            raise NetworkBlocked("host", "tidak bisa terhubung")

        with patch.object(sector.Polymarket, "search", blocked), \
                patch.object(limitless.Limitless, "sector", blocked), \
                patch.object(sector, "manifold_sector", blocked):
            result = sector.sector_markets("politics", "", 12)
        self.assertFalse(result["reachable"])
        self.assertEqual([f["source"] for f in result["failed"]], ["Polymarket", "Limitless", "Manifold"])


class SectorDeadlineTests(unittest.TestCase):
    def run_sector(self, name, limitless_rows, manifold_rows):
        def blocked(*a, **k):
            raise NetworkBlocked("gamma-api.polymarket.com", "sertifikat tidak cocok")

        def fake_get(url, params=None, label=""):
            return manifold_rows if "search-markets" in url else []

        with patch.object(deadlines.Polymarket, "search", blocked), \
                patch.object(limitless.Limitless, "categories_active", lambda self, ids, pages=2: limitless_rows), \
                patch("src.markets._get", fake_get), \
                patch.object(deadlines, "manifold_changes", lambda markets, now: [None] * len(markets)):
            return deadlines.sector_deadlines(name, 14, 40)

    def test_window_dedupe_exclusion_and_order(self):
        rows = [
            limitless.summary(raw_limitless(slug="later", hours=72, categories=("Football",), title="Arsenal vs Spurs")),
            limitless.summary(raw_limitless(slug="soon", hours=10, categories=("Football",), title="Chelsea vs Brentford")),
            limitless.summary(raw_limitless(slug="cs", hours=12, categories=("Sports", "Esports"), title="NIP vs M80")),
            limitless.summary(raw_limitless(slug="far", hours=24 * 30, categories=("Football",), title="Title race")),
            limitless.summary(raw_limitless(slug="nobook", hours=20, buy=0.0, sell=0.0, categories=("Football",))),
        ]
        manifold_rows = [manifold_raw("a", hours=30), manifold_raw("a", hours=30),
                         manifold_raw("few", hours=30, bettors=3)]
        result = self.run_sector("soccer", rows, manifold_rows)
        ids = [(m["source"], m["id"]) for m in result["markets"]]
        self.assertEqual(ids, [("limitless", "soon"), ("manifold", "a"), ("limitless", "later")])
        self.assertEqual(result["by_source"], {"limitless": 2, "manifold": 1})
        self.assertEqual(result["sector"], "soccer")
        self.assertEqual([f["source"] for f in result["failed"]], ["Polymarket"])
        for m in result["markets"]:
            self.assertIn("model", m)
            self.assertIn("ev", m)

    def test_esports_page_keeps_esports(self):
        rows = [limitless.summary(raw_limitless(slug="cs", hours=12, categories=("Sports", "Esports")))]
        result = self.run_sector("esports", rows, [])
        self.assertEqual([m["id"] for m in result["markets"]], ["cs"])

    def test_unknown_sector(self):
        with self.assertRaises(ValueError):
            deadlines.sector_deadlines("astrology")

    def test_match_markets_carry_a_caveat(self):
        now = store.now_ms()
        snap = deadlines.polymarket_snapshot(limitless.summary(raw_limitless(hours=30)), now, source="limitless")
        with patch.object(deadlines.model, "predict_market",
                          lambda s, n: {"available": True, "prob_yes": 0.5, "caveats": []}):
            enriched = deadlines.enrich(snap, now)
        self.assertTrue(any("pertandingan" in c for c in enriched["model"]["caveats"]))


class GatherTests(unittest.TestCase):
    def test_failures_become_notes_in_source_order(self):
        def ok():
            return [{"id": 1}]

        def boom():
            raise NetworkBlocked("host", "tidak bisa terhubung")

        markets, failed = deadlines._gather({"Polymarket": boom, "Limitless": ok, "Manifold": boom})
        self.assertEqual(markets, [{"id": 1}])
        self.assertEqual([f["source"] for f in failed], ["Polymarket", "Manifold"])


class SectorEndpointTests(unittest.TestCase):
    def test_sector_parameter(self):
        from app import app

        client = app.test_client()
        response = client.get("/api/deadlines?sector=astrology")
        self.assertEqual(response.status_code, 400)
        payload = {"count": 0, "markets": [], "failed": [], "by_source": {}, "sector": "politics"}
        with patch.object(deadlines, "sector_deadlines", lambda s, d, n: dict(payload, days=d, limit=n)), \
                patch("src.core.cache.get", lambda *a, **k: None), patch("src.core.cache.put", lambda *a, **k: None):
            body = client.get("/api/deadlines?sector=politics&days=14&limit=40").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual((body["data"]["sector"], body["data"]["days"], body["data"]["limit"]), ("politics", 14.0, 40))


if __name__ == "__main__":
    unittest.main()
