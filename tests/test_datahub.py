"""Public data APIs, the all-sources inventory, and their endpoints — no network.

Parsers get small payloads shaped like the real answers probed on 17 September
2026. Every parser must also survive an empty or missing payload: a source that
answers with nothing shows an empty table, never a 500.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.datahub as datahub  # noqa: E402
from src.core import cache, registry  # noqa: E402
from src.core.errors import NetworkBlocked, UpstreamError  # noqa: E402
from src.datahub import parsers  # noqa: E402
from src.datahub.catalog import BY_CODE, DATA_SOURCES, SECTOR_PAGES  # noqa: E402
from src.news.sources import FEEDS_CATALOG  # noqa: E402


class TempCache(unittest.TestCase):
    """Cache and status file in a throwaway folder: tests never touch data/."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(cache, "_DISK_DIR", root / "cache"),
            patch.object(datahub, "STATUS_DIR", root / "datahub"),
            patch.object(datahub, "STATUS_FILE", root / "datahub" / "status.json"),
        ]
        for p in self.patches:
            p.start()
        with cache._LOCK:
            cache._MEM.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        with cache._LOCK:
            cache._MEM.clear()
        self.tmp.cleanup()


class CatalogTests(unittest.TestCase):
    def test_every_source_has_a_parser_page_and_url(self):
        self.assertEqual(len(DATA_SOURCES), len(BY_CODE), "codes must be unique")
        for src in DATA_SOURCES:
            self.assertIn(src.parser, parsers.PARSERS, src.code)
            self.assertTrue(src.urls and all(u.startswith("https://") for u in src.urls), src.code)
            self.assertTrue(set(src.sectors) <= set(SECTOR_PAGES), src.code)
            self.assertTrue(src.good_for, src.code)

    def test_every_sector_page_has_data(self):
        for page in SECTOR_PAGES:
            self.assertTrue(datahub.for_page(page), page)

    def test_yesterday_placeholder_is_filled(self):
        src = BY_CODE["wikipedia-mostread"]
        url = datahub.urls_for(src, datetime(2026, 9, 17, 3, tzinfo=timezone.utc))[0]
        self.assertTrue(url.endswith("/featured/2026/09/16"), url)
        self.assertNotIn("{", url)

    def test_counts_add_up(self):
        counts = datahub.counts()
        self.assertEqual(counts["registry"], len(registry.SOURCES))
        self.assertEqual(counts["feed"], len(FEEDS_CATALOG) + 1)   # + Hacker News
        self.assertEqual(counts["api"], len(DATA_SOURCES))
        self.assertEqual(counts["all"], counts["registry"] + counts["feed"] + counts["api"])
        self.assertGreater(counts["all"], 200)


class ParserTests(unittest.TestCase):
    def test_every_parser_survives_empty_payloads(self):
        for name, fn in parsers.PARSERS.items():
            for payloads in ([None], [{}], [[]], [None, None, None, None, None]):
                with self.subTest(parser=name, payloads=payloads):
                    self.assertEqual(fn(payloads), [])

    def test_crypto_and_sentiment(self):
        rows = parsers.coingecko_prices([{"bitcoin": {"usd": 76404, "idr": 1358773398, "usd_24h_change": 1.2371,
                                                      "usd_market_cap": 1.5e12}}])
        self.assertEqual(rows, [{"asset": "BTC", "usd": 76404.0, "idr": 1358773398.0, "change_24h_pct": 1.24,
                                 "market_cap_usd": 1.5e12}])
        rows = parsers.fear_greed([{"data": [{"value": "50", "value_classification": "Neutral",
                                              "timestamp": "1789603200"}]}])
        self.assertEqual(rows, [{"date": "2026-09-17", "value": 50, "reading": "Neutral"}])

    def test_official_numbers(self):
        rows = parsers.treasury_debt([{"data": [{"record_date": "2026-09-15", "tot_pub_debt_out_amt": "40114400000000",
                                                 "debt_held_public_amt": "32406400000000",
                                                 "intragov_hold_amt": "7708100000000"}]}])
        self.assertEqual(rows[0]["total_debt_trillion_usd"], 40.1144)
        cpi = {"Results": {"series": [{"data": [
            {"year": "2026", "period": "M08", "periodName": "August", "value": "334.98", "footnotes": [{}]},
            {"year": "2025", "period": "M08", "periodName": "August", "value": "323.97", "footnotes": [{}]},
        ]}]}}
        rows = parsers.bls_cpi([cpi])
        self.assertEqual(rows[0]["period"], "August 2026")
        self.assertEqual(rows[0]["yoy_pct"], 3.4)
        self.assertIsNone(rows[1]["yoy_pct"], "no prior year in the payload means no YoY, not a guess")

    def test_multi_url_sources_keep_what_answered(self):
        rows = parsers.gold_api([{"symbol": "XAU", "price": 4318.7, "updatedAt": "t"}, None,
                                 {"symbol": "HG", "price": 4.61, "updatedAt": "t"}])
        self.assertEqual([r["symbol"] for r in rows], ["XAU", "HG"])
        rows = parsers.steam_players([{"response": {"player_count": 10}}, None,
                                      {"response": {"player_count": 99}}, None, None])
        self.assertEqual([r["game"] for r in rows], ["PUBG", "Counter-Strike 2"])

    def test_bmkg_dedupes_the_latest_quake_listed_twice(self):
        quake = {"DateTime": "2026-09-17T06:20:53+00:00", "Magnitude": "5.1", "Tanggal": "17 Sep 2026",
                 "Jam": "13:20:53 WIB", "Kedalaman": "24 km", "Wilayah": "Laut", "Potensi": "-", "Dirasakan": "II"}
        rows = parsers.bmkg_gempa([{"Infogempa": {"gempa": quake}}, {"Infogempa": {"gempa": [quake, dict(quake, DateTime="x")]}}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["magnitude"], 5.1)

    def test_itunes_link_prefers_the_html_page(self):
        entry = {"im:name": {"label": "Film"}, "link": [
            {"attributes": {"rel": "enclosure", "type": "video/x-m4v", "href": "https://video"}},
            {"attributes": {"rel": "alternate", "type": "text/html", "href": "https://page"}}]}
        rows = parsers.itunes_movies([{"feed": {"entry": [entry]}}])
        self.assertEqual(rows[0]["url"], "https://page")


class FetchTests(TempCache):
    def test_single_url_rows_and_cache(self):
        calls = []

        def getter(url, label):
            calls.append(url)
            return {"data": [{"value": "20", "value_classification": "Extreme Fear", "timestamp": "1789603200"}]}

        payload, cached, _ = datahub.fetch("fear-greed", getter)
        self.assertFalse(cached)
        self.assertEqual(payload["rows"][0]["reading"], "Extreme Fear")
        self.assertEqual(payload["partial"], [])
        _, cached, _ = datahub.fetch("fear-greed", getter)
        self.assertTrue(cached)
        self.assertEqual(len(calls), 1, "second call inside the TTL must come from the cache")

    def test_partial_failure_is_reported_not_hidden(self):
        def getter(url, label):
            if url.endswith("/XAG"):
                raise NetworkBlocked("api.gold-api.com", "tidak menjawab tepat waktu")
            return {"symbol": url.rsplit("/", 1)[1], "price": 1.5, "updatedAt": "t"}

        payload, _, _ = datahub.fetch("gold-api", getter)
        self.assertEqual([r["symbol"] for r in payload["rows"]], ["XAU", "HG"])
        self.assertEqual(len(payload["partial"]), 1)
        self.assertTrue(payload["partial"][0]["url"].endswith("/XAG"))

    def test_total_failure_raises_the_real_error(self):
        def getter(url, label):
            raise UpstreamError(label, "HTTP 503", 503)

        with self.assertRaises(UpstreamError):
            datahub.fetch("gold-api", getter)

    def test_unknown_code(self):
        from src.core.errors import BadRequest

        with self.assertRaises(BadRequest):
            datahub.fetch("nope")


class InventoryTests(TempCache):
    def test_totals_match_counts_and_probe_statuses_win(self):
        datahub._write_status({
            "started_at": "2026-09-17T08:00:00+00:00", "finished_at": "2026-09-17T08:01:00+00:00", "elapsed_s": 60,
            "feeds": {FEEDS_CATALOG[0].code: {"status": "blocked", "detail": "diblokir", "rows": None}},
            "apis": {"fear-greed": {"status": "live", "detail": "7 baris", "rows": 7}},
        })
        inv = datahub.inventory()
        self.assertEqual(inv["totals"]["all"]["total"], datahub.counts()["all"])
        by_code = {(r["type"], r["code"]): r for r in inv["sources"]}
        self.assertEqual(by_code[("feed", FEEDS_CATALOG[0].code)]["status"], "blocked")
        self.assertEqual(by_code[("api", "fear-greed")]["rows"], 7)
        self.assertEqual(by_code[("api", "gold-api")]["status"], "unchecked")
        self.assertGreaterEqual(inv["totals"]["all"]["problem"], 1)
        self.assertEqual(inv["last_probe"]["elapsed_s"], 60)

    def test_cache_marks_a_source_live_without_a_probe(self):
        cache.put("datahub", ["fear-greed", datahub.urls_for(BY_CODE["fear-greed"])],
                  {"rows": [{"value": 1}, {"value": 2}], "partial": []})
        row = next(r for r in datahub.inventory()["sources"] if r["code"] == "fear-greed")
        self.assertEqual((row["status"], row["rows"]), ("live", 2))

    def test_probe_all_records_every_feed_and_api(self):
        from src import news

        def fake_feed(feed, query=None):
            if feed.code == FEEDS_CATALOG[1].code:
                raise NetworkBlocked("example.com", "tidak bisa terhubung")
            return [{"title": "t"}], True

        def fake_fetch(code, getter=None):
            return {"rows": [] if code == "lobsters" else [{"x": 1}], "partial": []}, False, 0.0

        with patch.object(news, "fetch_feed", fake_feed), \
                patch.object(news, "_hacker_news", lambda q, n: [{"title": "hn"}]), \
                patch.object(datahub, "fetch", fake_fetch):
            result = datahub.probe_all(include_registry=False)
        self.assertEqual(len(result["feeds"]), len(FEEDS_CATALOG) + 1)
        self.assertEqual(len(result["apis"]), len(DATA_SOURCES))
        self.assertEqual(result["feeds"][FEEDS_CATALOG[1].code]["status"], "blocked")
        self.assertEqual(result["apis"]["lobsters"]["status"], "empty")
        self.assertTrue(json.loads(datahub.STATUS_FILE.read_text(encoding="utf-8"))["finished_at"])
        summary = datahub.probe_summary(result)
        self.assertEqual(summary["feeds"]["problem"], 1)
        self.assertEqual(summary["apis"]["live"], len(DATA_SOURCES))


class EndpointTests(TempCache):
    def setUp(self):
        super().setUp()
        from app import app

        self.client = app.test_client()

    def get(self, path):
        response = self.client.get(path)
        return response.status_code, response.get_json()

    def test_catalog_filters_by_page(self):
        status, body = self.get("/api/datahub/catalog?page=tech")
        self.assertEqual(status, 200)
        codes = {s["code"] for s in body["data"]["sources"]}
        self.assertIn("openrouter-models", codes)
        self.assertNotIn("bmkg-gempa", codes)
        self.assertEqual(self.get("/api/datahub/catalog?page=astrology")[0], 400)

    def test_source_rows_keep_column_order(self):
        rows = [{"rank": 1, "coin": "Derive", "symbol": "DRV"}]
        with patch.object(datahub, "fetch", lambda code: ({"rows": rows, "partial": [], "fetched_at": "t"}, True, 12.0)):
            status, body = self.get("/api/datahub/coingecko-trending")
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["columns"], ["rank", "coin", "symbol"])
        self.assertEqual(body["data"]["rows"], rows)
        self.assertTrue(body["meta"]["cached"])
        self.assertEqual(body["data"]["good_for"], BY_CODE["coingecko-trending"].good_for)

    def test_bad_code_and_upstream_error(self):
        self.assertEqual(self.get("/api/datahub/../../etc")[0], 404)
        status, body = self.get("/api/datahub/nope")
        self.assertEqual((status, body["error"]["code"]), (400, "BAD_REQUEST"))

        def boom(code):
            raise NetworkBlocked("api.coingecko.com", "tidak bisa terhubung")

        with patch.object(datahub, "fetch", boom):
            status, body = self.get("/api/datahub/coingecko-prices")
        self.assertEqual((status, body["error"]["code"]), (503, "NETWORK_BLOCKED"))

    def test_sources_all_and_probe_job(self):
        status, body = self.get("/api/sources/all")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["totals"]["all"]["total"], datahub.counts()["all"])
        from src.ml import jobs

        with patch.object(jobs, "start", lambda kind, fn, summarise=None, **kw: {"kind": kind, "state": "queued"}):
            response = self.client.post("/api/sources/probe")
        self.assertEqual(response.get_json()["data"], {"kind": "probe", "state": "queued"})

    def test_pages_show_the_total_and_versioned_assets(self):
        total = str(datahub.counts()["all"])
        for path in ("/", "/sources"):
            response = self.client.get(path)
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'<span class="value">{total}</span>', html, path)
            self.assertRegex(html, r"/static/js/datahub\.js\?v=\d+")
        html = self.client.get("/politics").get_data(as_text=True)
        self.assertIn('data-sector-ml="politics"', html)
        self.assertIn('data-datahub="politics"', html)
        self.assertIn('data-datahub="tech"', self.client.get("/tech").get_data(as_text=True))
        self.assertIn('data-datahub="sports"', self.client.get("/sports").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
