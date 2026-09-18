"""News sources, news ML, and the optional LLM reading.

No network: feeds and the Anthropic client are replaced with fakes. What is
pinned: a slow feed cannot hold the panel hostage, the catalog stays internally
consistent, relevance ranks the right headline first, story grouping needs more
than one headline, and the LLM path sends the parameters the Claude API guide
requires (structured output, refusal fallback) and never trusts an unchecked
answer.
"""
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import config
from src import news
from src.ml import llm, newsml
from src.news import sources


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


class CatalogTests(unittest.TestCase):
    def test_catalog_is_consistent(self):
        codes = [f.code for f in sources.FEEDS_CATALOG]
        self.assertEqual(len(codes), len(set(codes)))
        for feed in sources.FEEDS_CATALOG:
            self.assertIn(feed.category, sources.CATEGORIES, feed.code)
            self.assertIn(feed.kind, sources.KINDS, feed.code)
            self.assertTrue(feed.url.startswith("https://"), feed.code)
            self.assertTrue(feed.good_for, feed.code)
            if feed.search_url:
                self.assertIn("{q}", feed.search_url)
        for sector, categories in sources.SECTOR_CATEGORIES.items():
            self.assertTrue(set(categories) <= set(sources.CATEGORIES), sector)
            self.assertTrue(sources.for_sector(sector), sector)
        self.assertGreaterEqual(len(sources.by_categories(None)), 10)
        self.assertEqual(sources.catalog()["total"], len(codes) + 1)     # + Hacker News

    def test_every_sector_monitor_has_feeds(self):
        for sector in ("politics", "technology", "culture", "soccer", "esports"):
            self.assertTrue(news.select_feeds(sector=sector))


class ParserTests(unittest.TestCase):
    def test_rss2_rdf_and_atom(self):
        rss = b"""<rss><channel><item><title>Fed holds rates</title><link>https://a/1</link>
            <pubDate>Wed, 16 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>"""
        rdf = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
            xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>DW item</title><link>https://b/1</link>
            <dc:date>2026-09-16T10:00:00Z</dc:date></item></rdf:RDF>"""
        atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom item</title>
            <link rel="alternate" href="https://c/1"/><published>2026-09-16T09:00:00Z</published></entry></feed>"""
        self.assertEqual(news._parse_rss(rss, "S", "World")[0]["time"], "2026-09-16T18:00:00+00:00")
        self.assertEqual(news._parse_rss(rdf, "DW", "World")[0]["url"], "https://b/1")
        self.assertEqual(news._parse_rss(atom, "A", "AI")[0]["time"], "2026-09-16T09:00:00+00:00")
        self.assertEqual(news._parse_rss(b"<not xml", "X", "World"), [])

    def test_future_timestamps_are_dropped(self):
        rows = news._not_future([{"title": "t", "time": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}])
        self.assertIsNone(rows[0]["time"])


class HeadlinesTests(unittest.TestCase):
    @patch("src.news.textstore")
    @patch("src.news._hacker_news", return_value=[])
    @patch("src.news.fetch_feed")
    def test_slow_and_failing_feeds_do_not_block(self, fetch, _hn, _store):
        def fake(feed, query=None):
            if feed.code == "bbc":
                time.sleep(1.5)
            if feed.code == "npr":
                raise RuntimeError("down")
            return [{"title": f"{feed.code} headline about markets", "url": f"https://x/{feed.code}",
                     "source": feed.name, "category": feed.category, "time": ago(1), "feed": feed.code}], True
        fetch.side_effect = fake
        out = news.headlines(limit=100, budget_s=0.8)
        self.assertIn("BBC World", out["pending_sources"])
        self.assertIn("NPR", [f["source"] for f in out["failed_sources"]])
        self.assertTrue(any(r["feed"] == "google" for r in out["news"]))

    @patch("src.news.textstore")
    @patch("src.news._hacker_news", return_value=[])
    @patch("src.news.fetch_feed")
    def test_sector_and_query(self, fetch, _hn, _store):
        # Titles differ per feed: identical titles are one story and are
        # de-duplicated across sources on purpose.
        fetch.side_effect = lambda feed, query=None: ([
            {"title": f"Senate vote on budget {feed.code}", "url": f"https://x/{feed.code}/1", "source": feed.name,
             "category": feed.category, "time": ago(2), "feed": feed.code},
            {"title": f"Unrelated sports score {feed.code}", "url": f"https://x/{feed.code}/2", "source": feed.name,
             "category": feed.category, "time": ago(2), "feed": feed.code},
        ], False)
        out = news.headlines("senate", limit=200, sector="politics", budget_s=5)
        self.assertIn("Politics", out["categories"])
        others = [r for r in out["news"] if r["feed"] not in ("google", "google-id", "hn")]
        self.assertTrue(others)
        self.assertTrue(all("senate" in r["title"].lower() for r in others))


class NewsMlTests(unittest.TestCase):
    def test_tone_with_negation_and_indonesian(self):
        self.assertGreater(newsml.tone("Stocks surge to record high"), 0)
        self.assertLess(newsml.tone("Talks collapse as sanctions widen"), 0)
        self.assertLess(newsml.tone("Senate does not approve the deal"), 0)
        self.assertGreater(newsml.tone("IHSG menguat, rupiah naik"), 0)
        self.assertEqual(newsml.tone("The committee met on Tuesday"), 0.0)

    def test_keywords_keep_entities(self):
        words = newsml.keywords("Will the Fed cut rates by 50 bps at the September 2026 meeting?")
        self.assertIn("Fed", words)
        self.assertIn("50", words)
        self.assertNotIn("2026", words)

    def test_relevance_ranks_the_right_headline_first(self):
        items = [{"title": "Apple unveils new iPhone", "url": "1"},
                 {"title": "Federal Reserve cuts interest rates by half a point", "url": "2"},
                 {"title": "Fed officials signal more rate cuts ahead", "url": "3"}]
        ranked = newsml.rank("Will the Fed cut interest rates in September?", items)
        self.assertIn(ranked[0]["url"], ("2", "3"))
        self.assertNotIn("1", [r["url"] for r in ranked[:2]])

    def test_stories_group_duplicates_and_drop_singletons(self):
        items = [
            {"title": "Fed cuts interest rates by quarter point", "source": "A", "time": ago(3), "url": "a"},
            {"title": "Federal Reserve cuts interest rates quarter point", "source": "B", "time": ago(2), "url": "b"},
            {"title": "Fed cuts rates by a quarter point, signals more", "source": "C", "time": ago(1), "url": "c"},
            {"title": "Local team wins football final", "source": "D", "time": ago(1), "url": "d"},
        ]
        found = newsml.stories(items, threshold=0.2)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["size"], 3)
        self.assertEqual(found[0]["source_count"], 3)
        self.assertIn("summary", newsml.digest(items, hours=24)["stories"][0])

    def test_rising_terms_need_growth_and_two_sources(self):
        items = [{"title": f"Tariff shock hits exporters {i}", "source": f"S{i % 3}", "time": ago(2)} for i in range(6)]
        items += [{"title": "Weather update for the weekend", "source": "S1", "time": ago(40 + i)} for i in range(6)]
        terms = [t["term"] for t in newsml.rising_terms(items, hours=24, baseline_hours=72)["terms"]]
        self.assertTrue(any("tariff" in t for t in terms))
        self.assertFalse(any("weather" in t for t in terms))

    def test_market_features_windows(self):
        # IDF is fitted on the candidate pool, so a realistic pool needs other news
        # in it; otherwise "bitcoin" is in every document and weighs nothing.
        noise = [{"title": f"{topic} story {i}", "source": "Z", "time": ago(5), "url": f"n{i}"}
                 for i, topic in enumerate(["Election poll shift", "Football transfer news", "Storm warning issued",
                                            "Tech earnings beat", "Oil supply talks"] * 3)]
        items = [{"title": "Bitcoin price hits new record", "source": "A", "time": ago(2), "url": "1"},
                 {"title": "Bitcoin record price rally extends", "source": "B", "time": ago(50), "url": "2"},
                 {"title": "Bitcoin price record set weeks ago", "source": "C", "time": ago(200), "url": "3"}] + noise
        feats = newsml.market_features("Will Bitcoin hit a new record price?", items)
        self.assertEqual(feats["n_24h"], 1)
        self.assertEqual(feats["n_72h"], 2)
        self.assertEqual(feats["sources_72h"], 2)


def _schema_is_strict(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            if node.get("additionalProperties") is not False:
                return False
            if set(node.get("required", [])) != set(node.get("properties", {})):
                return False
        return all(_schema_is_strict(v) for v in node.values())
    if isinstance(node, list):
        return all(_schema_is_strict(v) for v in node)
    return True


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class LlmTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from src.core import cache

        # Answers are cached for an hour; keep the tests off the real data/cache.
        self.tmp = tempfile.TemporaryDirectory()
        self.disk = patch.object(cache, "_DISK_DIR", Path(self.tmp.name))
        self.disk.start()
        with cache._LOCK:
            for key in [k for k in cache._MEM if k.startswith("llm.")]:
                del cache._MEM[key]

    def tearDown(self):
        from src.core import cache

        self.disk.stop()
        with cache._LOCK:
            for key in [k for k in cache._MEM if k.startswith("llm.")]:
                del cache._MEM[key]
        self.tmp.cleanup()

    def test_schema_is_valid_for_structured_outputs(self):
        self.assertTrue(_schema_is_strict(llm.SCHEMA))

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""})
    @patch.object(config, "ANTHROPIC_API_KEY", "")
    def test_without_a_key_it_says_so(self):
        self.assertFalse(llm.status()["available"])
        from src.core.errors import MissingCredential

        with self.assertRaises(MissingCredential):
            llm.analyse({"question": "Q?"}, [{"title": "t", "url": "u"}])

    @patch.object(config, "LLM_EFFORT", "medium")
    @patch.object(config, "LLM_MODEL", "claude-opus-5")
    def test_request_shape_and_cleaning(self):
        answer = {"stance": "YES", "probability_yes": 140, "confidence": "tinggi", "summary": "Ringkas.",
                  "key_points": [{"point": "Bukti", "headlines": [1, 9]}], "what_would_change_it": ["x"],
                  "deadline_note": "d", "resolution_risks": ["r"]}
        response = SimpleNamespace(stop_reason="end_turn", model="claude-opus-5", stop_details=None,
                                   content=[SimpleNamespace(type="text", text=json.dumps(answer))],
                                   usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                                         cache_creation_input_tokens=0, cache_read_input_tokens=0))
        messages = FakeMessages(response)
        client = SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)
        import anthropic

        with patch("src.ml.llm._client", return_value=(anthropic, client)):
            out = llm.analyse({"question": "Will X happen?", "price_text": "40%"},
                              [{"title": "Headline one", "url": "https://h/1"}])
        call = messages.calls[0]
        self.assertEqual(call["model"], "claude-opus-5")
        self.assertEqual(call["betas"], [llm.FALLBACK_BETA])
        self.assertEqual(call["extra_body"], {"fallbacks": "default"})
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["output_config"]["effort"], "medium")
        self.assertNotIn("thinking", call)
        self.assertTrue(out["ok"])
        self.assertEqual(out["probability_yes"], .99)             # 140 clamped, never trusted raw
        self.assertEqual(out["key_points"][0]["headlines"], [1])  # headline 9 does not exist
        self.assertEqual(out["meta"]["cost_usd"], round(1000 * 5 / 1e6 + 200 * 25 / 1e6, 4))

    @patch.object(config, "LLM_MODEL", "claude-opus-5")
    def test_refusal_is_not_read_as_an_answer(self):
        response = SimpleNamespace(stop_reason="refusal", model="claude-opus-5",
                                   stop_details=SimpleNamespace(category="cyber"), content=[],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=0,
                                                         cache_creation_input_tokens=0, cache_read_input_tokens=0))
        messages = FakeMessages(response)
        client = SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)
        import anthropic

        with patch("src.ml.llm._client", return_value=(anthropic, client)):
            out = llm.analyse({"question": "Refused?"}, [])
        self.assertFalse(out["ok"])
        self.assertTrue(out["refused"])
        self.assertEqual(out["category"], "cyber")


if __name__ == "__main__":
    unittest.main()
