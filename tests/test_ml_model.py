"""Features, histories and the deadline model.

Three properties matter more than any accuracy number:

  * **parity** — a training row and a live prediction built from the same
    market state produce identical features;
  * **no leakage** — trades after a snapshot cannot change that snapshot's row;
  * **honest verdicts** — synthetic data with a real signal is recognised as
    beating the market, and data without one is not.
"""
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ml import features, history, model, store

DAY = features.DAY_MS


def fake_history(outcome=1, points=None, complete=True, created=0, deadline=30 * DAY, resolved=None):
    return {
        "version": history.HISTORY_VERSION, "source": "manifold", "id": "m1",
        "question": "Will Bitcoin hit $100k by Friday?", "created_ms": created,
        "deadline_ms": deadline, "resolved_ms": resolved or deadline + DAY, "outcome": outcome,
        "points": points if points is not None else [[1 * DAY, .30], [20 * DAY, .40], [28 * DAY, .55]],
        "initial_prob": .25, "complete": complete, "covered_from_ms": created,
    }


class FeatureTests(unittest.TestCase):
    def test_question_flags(self):
        flags = features.question_flags("Will the Fed cut rates by 50 bps before December?")
        self.assertEqual(flags["q_macro"], 1.0)
        self.assertEqual(flags["q_by_deadline"], 1.0)
        self.assertEqual(flags["q_threshold"], 1.0)
        self.assertEqual(features.question_flags("Lakers vs Celtics")["q_match"], 1.0)

    def test_price_path_unknown_before_coverage(self):
        path = features.PricePath([[10, .4]], initial=.2, complete=False)
        self.assertIsNone(path.at(5))
        self.assertEqual(features.PricePath([[10, .4]], initial=.2, complete=True).at(5), .2)
        self.assertEqual(path.at(15), .4)

    def test_training_and_live_features_are_identical(self):
        h = fake_history()
        row = [r for r in features.historical_rows(h) if r["offset_days"] == 2][0]
        snap_time = h["deadline_ms"] - 2 * DAY
        path = features.PricePath(h["points"], h["initial_prob"], True)
        p = path.at(snap_time)
        live = features.live_features({
            "question": h["question"], "p_yes": p, "deadline_ms": h["deadline_ms"],
            "created_ms": h["created_ms"],
            "chg_1d": p - path.at(snap_time - DAY), "chg_7d": p - path.at(snap_time - 7 * DAY),
        }, snap_time)
        for name in features.FEATURES:
            self.assertAlmostEqual(row[name], live["features"][name], places=9, msg=name)

    def test_trades_after_the_snapshot_do_not_change_it(self):
        base = features.historical_rows(fake_history())
        later = fake_history(points=[[1 * DAY, .30], [20 * DAY, .40], [28 * DAY, .55], [29.9 * DAY, .99]])
        changed = features.historical_rows(later)
        first = {r["offset_days"]: r for r in base}
        for r in changed:
            if r["offset_days"] >= 1:
                self.assertEqual(r["p"], first[r["offset_days"]]["p"])

    def test_no_snapshot_after_resolution_or_before_creation(self):
        h = fake_history(created=25 * DAY, deadline=30 * DAY, resolved=27 * DAY)
        offsets = {r["offset_days"] for r in features.historical_rows(h)}
        self.assertTrue(all(30 - o < 27 for o in offsets))
        self.assertTrue(all(30 - o > 25 for o in offsets))

    def test_live_market_past_deadline_is_refused(self):
        self.assertIsNone(features.live_features({"p_yes": .5, "deadline_ms": 10}, 20))

    def test_early_closed_manifold_markets_have_no_known_deadline(self):
        h = dict(fake_history(), question="Will Bitcoin hit $100k soon?", closed_early=True)
        rows = features.historical_rows(h)
        self.assertTrue(rows)
        self.assertTrue(all(r["deadline_known"] is False for r in rows))
        self.assertTrue(all(r["deadline_known"] for r in features.historical_rows(fake_history())))

    def test_stated_dates(self):
        from datetime import datetime, timezone

        def ms(*args):
            return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)

        created = ms(2025, 1, 1)
        self.assertEqual(features.stated_deadline("Will X happen by March 31, 2025?", created), ms(2025, 3, 31, 23, 59))
        self.assertEqual(features.stated_deadline("Will Y ship before July 2025?", created), ms(2025, 7, 1, 23, 59))
        self.assertEqual(features.stated_deadline("CPI above 3% in September 2025?", created), ms(2025, 9, 30, 23, 59))
        self.assertEqual(features.stated_deadline("Will W happen before 2027?", created), ms(2026, 12, 31, 23, 59))
        self.assertEqual(features.stated_deadline("Will it happen by Feb 3?", ms(2025, 3, 1)), ms(2026, 2, 3, 23, 59))
        for vague in ("Will the price may 5x soon?", "Will the summary 5 pass?", "Will A beat B on Sunday?",
                      "Will it happen by 2020?"):
            self.assertIsNone(features.stated_deadline(vague, created), vague)

    def test_early_closed_market_with_a_stated_date_is_kept(self):
        # Question says 31 Jan 1970 (day 30); trading stopped at day 25 when it resolved.
        h = dict(fake_history(deadline=25 * DAY, resolved=25 * DAY), closed_early=True,
                 question="Will Bitcoin hit $100k by January 31, 1970?")
        rows = features.historical_rows(h)
        self.assertTrue(rows)
        self.assertTrue(all(r["deadline_known"] and r["deadline_source"] == "tanggal di pertanyaan" for r in rows))
        self.assertTrue(all(r["deadline_ms"] == 30 * DAY + 23 * 3600_000 + 59 * 60_000 for r in rows))
        self.assertTrue(all(30 - r["offset_days"] < 25 for r in rows))   # only while it still traded
        late = dict(h, resolved_ms=40 * DAY)                              # resolved long after the stated date
        self.assertFalse(any(r["deadline_known"] for r in features.historical_rows(late)))

    def test_polymarket_counts_back_from_the_scheduled_end(self):
        h = dict(fake_history(deadline=20 * DAY, resolved=20 * DAY), source="polymarket",
                 scheduled_deadline_ms=30 * DAY, closed_early=True)
        rows = features.historical_rows(h)
        self.assertTrue(all(r["deadline_known"] for r in rows))
        self.assertTrue(all(r["deadline_ms"] == 30 * DAY for r in rows))
        # trading stopped at day 20, so only snapshots 10+ days before the scheduled end exist
        self.assertTrue(all(r["offset_days"] > 10 for r in rows))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(store, "RAW", root / "raw"),
                        patch.object(history, "DATASET", root / "dataset.jsonl"),
                        patch.object(history, "DATASET_META", root / "dataset.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    @patch("src.ml.history._get")
    def test_manifold_pages_back_only_to_the_window(self, get):
        close = 100 * DAY

        def page(start, count, step):
            return [{"id": f"b{start - i}", "createdTime": start - i * step, "probAfter": .5,
                     "probBefore": .49} for i in range(count)]

        get.side_effect = [page(close, 1000, 60_000), page(close - 1000 * 60_000, 1000, 3 * DAY)]
        market = {"id": "abc", "question": "Q?", "createdTime": 0, "closeTime": close,
                  "resolutionTime": close + DAY, "resolution": "YES", "url": "u"}
        h = history.manifold_history(market, use_cache=False)
        self.assertEqual(get.call_count, 2)          # stopped once the window was covered
        self.assertFalse(h["complete"])
        self.assertIsNone(h["initial_prob"])
        self.assertEqual(h["outcome"], 1)
        self.assertFalse(h["closed_early"])

    @patch("src.ml.history._get")
    def test_polymarket_only_exact_resolutions(self, get):
        def m(prices, cid):
            return {"conditionId": cid, "question": "Q", "outcomes": '["Yes","No"]',
                    "outcomePrices": prices, "clobTokenIds": '["1","2"]',
                    "endDate": "2026-01-10T00:00:00Z", "startDate": "2026-01-01T00:00:00Z"}
        get.return_value = [m('["1","0"]', "a"), m('["0.5","0.5"]', "b"), m('["0","1"]', "c")]
        rows = history.polymarket_resolved(limit=10)
        self.assertEqual([(r["id"], r["outcome"]) for r in rows], [("a", 1), ("c", 0)])

    @patch("src.ml.history.manifold_resolved", side_effect=RuntimeError("down"))
    def test_build_uses_the_disk_cache_when_a_source_fails(self, _):
        store.write_json(store.RAW / "manifold" / "m1.json", fake_history())
        built = history.build(sources=("manifold",), max_markets=5)
        self.assertGreater(built["meta"]["rows"], 0)
        self.assertEqual(built["meta"]["sources"]["manifold"]["failed"], "down")

    @patch("src.ml.history.manifold_resolved", side_effect=RuntimeError("down"))
    def test_build_refuses_to_write_an_empty_dataset(self, _):
        with self.assertRaises(history.NotEnoughData):
            history.build(sources=("manifold",), max_markets=5)
        self.assertFalse(history.DATASET.exists())


def synthetic_rows(markets=500, signal=0.0, seed=3):
    """Rows priced by a market that ignores `q_crypto`. With signal > 0 the truth does not."""
    rng = random.Random(seed)
    rows = []
    for i in range(markets):
        crypto = i % 2 == 0
        question = "Will bitcoin close higher?" if crypto else "Will the council approve the budget?"
        p = min(.95, max(.05, rng.random()))
        truth = 1 / (1 + math.exp(-(math.log(p / (1 - p)) + (signal if crypto else -signal))))
        outcome = 1 if rng.random() < truth else 0
        deadline = (i + 10) * DAY
        for offset in (1, 3, 7):
            feats = features.snapshot_features(question, p, offset, 20, p, p)
            rows.append({"source": "manifold", "market_id": f"s{i}", "question": question,
                         "snapshot_ms": deadline - offset * DAY, "deadline_ms": deadline,
                         "offset_days": offset, "p": p, "y": outcome, **feats})
    return rows


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(model, "MODEL_PATH", root / "m.joblib"),
                        patch.object(model, "REPORT_PATH", root / "m.json"),
                        patch.object(store, "MODELS", root)]
        for p in self.patches:
            p.start()
        model._CACHE["mtime"] = None

    def tearDown(self):
        for p in self.patches:
            p.stop()
        model._CACHE.update(mtime=None, bundle=None, report=None)
        self.tmp.cleanup()

    def test_anchored_logit_with_no_freedom_returns_the_market(self):
        import numpy as np

        rows = synthetic_rows(200)
        X, y, p, _, w = model._arrays(rows)
        est = model.AnchoredLogit(alpha=1e6).fit(X, y, sample_weight=w)
        self.assertTrue(np.allclose(est.predict_proba(X)[:, 1], p, atol=1e-3))

    def test_real_signal_is_recognised(self):
        report = model.train(synthetic_rows(900, signal=1.2), {})
        self.assertTrue(report["beats_market"], report["verdict"])
        self.assertGreater(report["bootstrap"]["low"], 0)

    def test_no_signal_is_not_called_an_edge(self):
        report = model.train(synthetic_rows(900, signal=0.0, seed=9), {})
        self.assertFalse(report["beats_market"], report["verdict"])

    def test_too_little_data(self):
        with self.assertRaises(model.NotEnoughData):
            model.train(synthetic_rows(20), {})

    def test_unknown_deadlines_are_excluded_and_reported(self):
        rows = synthetic_rows(500, signal=0.5)
        for r in rows:
            if r["market_id"].endswith("7"):
                r["deadline_known"] = False
        report = model.train(rows, {})
        excluded = report["data"]["excluded_unknown_deadline"]
        self.assertEqual(excluded["markets"], 50)
        self.assertEqual(report["data"]["markets"], 450)
        kinds = {row["kind"] for row in report["deadline_effect"]}
        self.assertTrue({"semua", "lainnya"} <= kinds)
        self.assertIn("yes_gap_pp", report["deadline_effect"][0])

    def test_saved_model_predicts_live_markets_inside_its_range(self):
        model.train(synthetic_rows(400, signal=1.0), {})
        now = 1_000 * DAY
        snap = {"question": "Will bitcoin close higher?", "p_yes": .5, "deadline_ms": now + 2 * DAY,
                "created_ms": now - 10 * DAY, "chg_1d": 0.0, "chg_7d": 0.0}
        pred = model.predict_market(snap, now)
        self.assertTrue(pred["available"])
        self.assertEqual(pred["bucket"], "1–3 hari")
        self.assertIn(pred["direction"], ("naik", "turun", "datar"))
        far = dict(snap, deadline_ms=now + 90 * DAY)
        self.assertFalse(model.predict_market(far, now)["available"])


if __name__ == "__main__":
    unittest.main()
