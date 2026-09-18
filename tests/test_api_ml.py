"""New endpoints through the real Flask app, upstreams mocked.

Every endpoint must answer in the one envelope, and bad input must come back as
an explained BAD_REQUEST — never as a 500 — because the pages render the error
envelope directly.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from app import app  # noqa: E402
from src.core import cache  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.client = app.test_client()
        # Point the cache at a throwaway folder. Clearing a namespace deletes
        # disk files, and a test must never delete the real data/cache.
        self.tmp = tempfile.TemporaryDirectory()
        self.disk = patch.object(cache, "_DISK_DIR", Path(self.tmp.name))
        self.disk.start()
        with cache._LOCK:
            cache._MEM.clear()

    def tearDown(self):
        self.disk.stop()
        with cache._LOCK:
            cache._MEM.clear()
        self.tmp.cleanup()

    def get(self, path):
        response = self.client.get(path)
        return response.status_code, response.get_json()

    def post(self, path, body):
        response = self.client.post(path, json=body)
        return response.status_code, response.get_json()

    def test_catalog_and_llm_status(self):
        status, body = self.get("/api/news/catalog")
        self.assertEqual(status, 200)
        self.assertGreater(body["data"]["total"], 100)
        status, body = self.get("/api/news/llm/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["model"], config.LLM_MODEL)

    def test_bad_input_is_explained(self):
        cases = [
            ("get", "/api/news?sector=astrology", None),
            ("get", "/api/news?category=Gossip", None),
            ("get", "/api/news?feeds=nope", None),
            ("get", "/api/markets/iev?source=polymarket&token=abc", None),
            ("get", "/api/markets/iev?source=manifold&id=../../x", None),
            ("get", "/api/markets/iev?source=kalshi", None),
            ("get", "/api/markets/iev?source=manifold&id=abc&fair=150", None),
            ("get", "/api/deadlines?source=kalshi", None),
            ("get", "/api/news/ml/market?q=a", None),
            ("post", "/api/ml/train", {"sources": ["kalshi"]}),
            ("post", "/api/ml/train", {"max_markets": "many"}),
            ("post", "/api/paper/scan", {"days": 99}),
            ("post", "/api/paper/scan", {"stake": "ten"}),
            ("post", "/api/paper/trade", {"source": "manifold", "market_id": "abc", "side": "MAYBE"}),
            ("post", "/api/paper/reset", {}),
            ("post", "/api/ml/predict", {"source": "kalshi", "market_id": "x"}),
        ]
        for method, path, payload in cases:
            status, body = self.get(path) if method == "get" else self.post(path, payload)
            self.assertEqual(status, 400, f"{method} {path} {payload} -> {status} {body}")
            self.assertEqual(body["error"]["code"], "BAD_REQUEST", path)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
    @patch.object(config, "ANTHROPIC_API_KEY", "")
    def test_llm_without_key_is_a_setup_step_not_a_crash(self):
        status, body = self.post("/api/news/llm", {"question": "Will the Fed cut rates?"})
        self.assertEqual(status, 424)
        self.assertEqual(body["error"]["code"], "MISSING_CREDENTIAL")

    @patch("src.markets.Manifold.order_book")
    def test_manifold_iev(self, book):
        book.return_value = {"bids": [{"price": .4, "shares": 100}], "asks": [{"price": .6, "shares": 50}]}
        status, body = self.get("/api/markets/iev?source=manifold&id=abc123&fair=55")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["auction"]["iev"], 0.0)
        self.assertTrue(body["data"]["fair_value"]["known"])

    @patch("src.ml.model.predict_market", return_value={"available": False, "reason": "fixture"})
    @patch("src.markets.Polymarket.search")
    def test_sector_markets_carry_ev(self, search, _predict):
        search.return_value = [{
            "id": "0x" + "ab" * 32, "question": "Will X?", "outcomes": [{"outcome": "Yes", "price": .6},
                                                                        {"outcome": "No", "price": .4}],
            "best_bid": .59, "best_ask": .61, "spread": .02, "liquidity": 9000, "volume24h": 100,
            "end_date": "2099-01-01T00:00:00Z", "clob_token_ids": ["1", "2"], "url": "https://polymarket.com/event/x",
        }]
        status, body = self.get("/api/markets/polymarket?sector=technology")
        self.assertEqual(status, 200)
        market = body["data"]["markets"][0]
        self.assertTrue(market["analysis"]["ev"]["known"])
        self.assertLess(market["analysis"]["ev"]["rows"][0]["ev_consensus_pct"], 0)
        self.assertIn("best_ev", body["data"]["brief"])

    def test_pages_render(self):
        for page in ("/deadlines", "/paper", "/news", "/tech"):
            self.assertEqual(self.client.get(page).status_code, 200, page)


if __name__ == "__main__":
    unittest.main()
