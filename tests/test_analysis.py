"""Tests for the derived market reading, fixture matching and on-chain money.

The shared theme is abstention. Every one of these paths can produce a
confident-looking wrong answer — a favorite invented from a near-miss team name,
a "total USDC" summed over the twenty rows that happened to be displayed, a
market title attached to a condition id that belongs to someone else. The tests
below pin the cases where the right answer is "unknown".
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from eth_abi import encode

from src.markets.analysis import analyse, character, economics, lean, sector_brief
from src.markets.fixtures import (_series_market, _similarity, _soccer_1x2,
                                  _summarise, attach)
from src.markets.resolve import markets_by_condition
from src.polymarket.onchain import TOPIC_POSITION_SPLIT, USDC_E, PolygonReader
from src.sports.match_analysis import form_lean


def market(**over):
    base = {
        "question": "Will X happen?", "id": "0x" + "ab" * 32,
        "outcomes": [{"outcome": "Yes", "price": .65}, {"outcome": "No", "price": .35}],
        "volume24h": 1000, "liquidity": 50_000, "spread": .01,
        "change1h": .001, "change24h": .002, "change1w": .004,
        "end_date": "2099-01-01T00:00:00Z",
    }
    base.update(over)
    return base


class LeanTests(unittest.TestCase):
    def test_missing_prices_is_unknown_not_fifty_fifty(self):
        self.assertFalse(lean(market(outcomes=[]))["known"])

    def test_margin_and_band(self):
        side = lean(market())
        self.assertEqual(side["outcome"], "Yes")
        self.assertEqual(side["margin_pp"], 30.0)
        self.assertEqual(side["band"], "condong")

    def test_dead_heat_names_no_favorite(self):
        side = lean(market(outcomes=[{"outcome": "A", "price": .5},
                                     {"outcome": "B", "price": .5}]))
        self.assertIsNone(side["outcome"])
        self.assertEqual(side["band"], "seimbang")

    def test_multi_outcome_picks_the_top_price(self):
        side = lean(market(outcomes=[{"outcome": "A", "price": .2},
                                     {"outcome": "B", "price": .45},
                                     {"outcome": "C", "price": .35}]))
        self.assertEqual((side["outcome"], side["outcomes_count"]), ("B", 3))


class CharacterTests(unittest.TestCase):
    def test_hourly_jump_reads_as_repricing(self):
        traits = character(market(change1h=.08))
        self.assertTrue(traits["fresh_move"])
        self.assertEqual(traits["stability"], "repricing baru")

    def test_flat_across_every_horizon_reads_as_settled(self):
        traits = character(market(change1h=0, change24h=0, change1w=0))
        self.assertEqual(traits["stability"], "mapan")

    def test_thin_book_is_flagged(self):
        self.assertTrue(character(market(liquidity=900))["thin"])
        self.assertFalse(character(market(liquidity=90_000))["thin"])

    def test_absent_change_data_is_unknown_not_zero(self):
        traits = character(market(change1h=None, change24h=None, change1w=None))
        self.assertEqual(traits["stability"], "tidak diketahui")
        self.assertIsNone(traits["change_24h_pp"])


class EconomicsTests(unittest.TestCase):
    def test_breakeven_is_the_entry_price(self):
        econ = economics(market(best_ask=.7), lean(market()))
        self.assertEqual(econ["entry_pct"], "70.0%")
        self.assertEqual(econ["breakeven_pct"], "70.0%")
        self.assertAlmostEqual(econ["profit_pct_if_right"], 42.9, places=1)
        self.assertEqual(econ["loss_pct_if_wrong"], 100.0)

    def test_short_dated_market_is_not_annualised(self):
        """Annualising a two-day binary yields a number nobody can act on."""
        soon = market(end_date="2026-09-13T00:00:00Z")
        self.assertIsNone(economics(soon, lean(soon))["annualised_pct_if_right"])

    def test_price_outside_zero_to_one_is_refused(self):
        self.assertFalse(economics(market(), {"price": 1.0, "known": True})["known"])


class StrategyTests(unittest.TestCase):
    def test_mid_repricing_says_wait(self):
        plan = analyse(market(change1h=.09))["strategy"]
        self.assertEqual(plan["stance"], "tunggu")

    def test_near_certain_favorite_is_not_a_recommendation(self):
        plan = analyse(market(outcomes=[{"outcome": "Yes", "price": .97},
                                        {"outcome": "No", "price": .03}]))["strategy"]
        self.assertEqual(plan["stance"], "hati-hati")
        self.assertTrue(any("favorit" in p for p in plan["points"]))

    def test_every_plan_states_the_belief_it_requires(self):
        self.assertIn("impas", analyse(market())["strategy"]["breakeven"])


class SectorBriefTests(unittest.TestCase):
    def test_picks_busiest_mover_and_closest_to_even(self):
        rows = [
            market(question="A", volume24h=10, change24h=.01,
                   outcomes=[{"outcome": "Yes", "price": .51}, {"outcome": "No", "price": .49}]),
            market(question="B", volume24h=900, change24h=-.20,
                   outcomes=[{"outcome": "Yes", "price": .95}, {"outcome": "No", "price": .05}]),
        ]
        brief = sector_brief(rows, "tech")
        self.assertEqual(brief["busiest"]["question"], "B")
        self.assertEqual(brief["biggest_mover"]["question"], "B")
        self.assertEqual(brief["most_contested"]["question"], "A")
        self.assertEqual(brief["most_settled"]["question"], "B")
        self.assertEqual(brief["concentration_pct"], 98.9)

    def test_unpriced_sector_is_reported_not_faked(self):
        self.assertFalse(sector_brief([market(outcomes=[])], "tech")["known"])


class NameMatchTests(unittest.TestCase):
    def test_filler_words_do_not_create_a_match(self):
        """`Team Alpha` and `Team Beta` share only the noise word `Team`."""
        self.assertEqual(_similarity("Team Alpha", "Team Beta"), 0.0)

    def test_real_name_survives_different_filler(self):
        self.assertGreaterEqual(_similarity("Rennais", "Stade Rennais FC 1901"), .5)

    def test_all_noise_name_still_compares(self):
        self.assertGreater(_similarity("FC United", "United"), 0)


class SeriesMarketTests(unittest.TestCase):
    @staticmethod
    def event(markets):
        return {"title": "Dota 2: A vs B", "slug": "e", "markets": markets}

    def test_prop_and_per_game_markets_are_skipped(self):
        picked = _series_market(self.event([
            {"question": "Dota 2: A vs B - Game 1 Winner", "outcomes": '["A","B"]',
             "outcomePrices": '["0.9","0.1"]'},
            {"question": "Games Total: O/U 2.5", "outcomes": '["Over","Under"]',
             "outcomePrices": '["0.5","0.5"]'},
            {"question": "Game 1: Any Player Rampage?", "outcomes": '["Yes","No"]',
             "outcomePrices": '["0.1","0.9"]'},
            {"question": "Dota 2: A vs B (BO3) - Cup", "outcomes": '["A","B"]',
             "outcomePrices": '["0.7","0.3"]'},
        ]))
        self.assertEqual(picked["market"]["question"], "Dota 2: A vs B (BO3) - Cup")

    def test_closed_series_market_is_not_used(self):
        self.assertIsNone(_series_market(self.event([
            {"question": "Dota 2: A vs B (BO3)", "outcomes": '["A","B"]',
             "outcomePrices": '["0.7","0.3"]', "closed": True},
        ])))


class Soccer1x2Tests(unittest.TestCase):
    EVENT = {
        "title": "Alpha FC vs. Beta United", "slug": "m",
        "markets": [
            {"question": "Will Alpha FC win on 2026-09-11?", "outcomes": '["Yes","No"]',
             "outcomePrices": '["0.48","0.52"]'},
            {"question": "Will Alpha FC vs. Beta United end in a draw?",
             "outcomes": '["Yes","No"]', "outcomePrices": '["0.28","0.72"]'},
            {"question": "Will Beta United win on 2026-09-11?", "outcomes": '["Yes","No"]',
             "outcomePrices": '["0.24","0.76"]'},
        ],
    }

    def test_three_books_become_one_1x2(self):
        shape = _soccer_1x2(self.EVENT)
        self.assertEqual([o["outcome"] for o in shape["outcomes"]],
                         ["Alpha FC", "Draw", "Beta United"])
        self.assertTrue(shape["has_draw"])

    def test_overround_is_reported_not_normalised(self):
        """0.48 + 0.28 + 0.24 = 1.00, so the book is exactly fair here."""
        self.assertEqual(_soccer_1x2(self.EVENT)["overround_pct"], 0.0)

    def test_missing_away_leg_is_not_a_fixture(self):
        event = dict(self.EVENT, markets=self.EVENT["markets"][:2])
        self.assertIsNone(_soccer_1x2(event))

    def test_turnover_is_summed_across_all_three_books(self):
        """The home-win book alone understates a fixture's real turnover."""
        markets = [dict(m, volume24hr=100, liquidityNum=1000) for m in self.EVENT["markets"]]
        event = dict(self.EVENT, markets=markets)
        row = _summarise(event, _soccer_1x2(event))
        self.assertEqual(row["volume24h"], 300)
        self.assertEqual(row["liquidity"], 3000)


class AttachTests(unittest.TestCase):
    LISTED = [{"teams": ["Natus Vincere", "Zero Tenacity"], "event_title": "NaVi vs Z10",
               "favorite": "Natus Vincere", "favorite_pct": "87.5%", "margin_pp": 75.0,
               "tied": False, "outcomes": [], "volume24h": 1, "url": "u"}]

    def test_matching_fixture_gets_the_odds(self):
        rows = attach([{"team_a": "Natus Vincere", "team_b": "Zero Tenacity"}], self.LISTED)
        self.assertTrue(rows[0]["poly"]["matched"])
        self.assertEqual(rows[0]["poly"]["confidence"], 1.0)
        self.assertFalse(rows[0]["poly"]["sides_swapped"])

    def test_reversed_fixture_matches_and_says_so(self):
        rows = attach([{"team_a": "Zero Tenacity", "team_b": "Natus Vincere"}], self.LISTED)
        self.assertTrue(rows[0]["poly"]["sides_swapped"])

    def test_unrelated_fixture_gets_no_odds(self):
        """The worst failure here would be attaching another match's price."""
        rows = attach([{"team_a": "Recrent Club", "team_b": "VooDooSh Club"}], self.LISTED)
        self.assertFalse(rows[0]["poly"]["matched"])
        self.assertNotIn("favorite", rows[0]["poly"])

    def test_half_match_is_still_no_match(self):
        rows = attach([{"team_a": "Natus Vincere", "team_b": "Some Other Org"}], self.LISTED)
        self.assertFalse(rows[0]["poly"]["matched"])


class FormLeanTests(unittest.TestCase):
    CATALOG = [
        {"name": "Natus Vincere", "team_id": 1, "rating": 1500, "wins": 60, "losses": 40},
        {"name": "Natus Vincere US", "team_id": 2, "rating": 1200, "wins": 30, "losses": 30},
        {"name": "Team Spirit", "team_id": 3, "rating": 1600, "wins": 80, "losses": 20},
        {"name": "Rookies", "team_id": 4, "rating": 1400, "wins": 3, "losses": 2},
    ]

    @patch("src.sports.esports.Dota2.teams")
    def test_exact_name_beats_a_longer_lookalike(self, teams):
        teams.return_value = self.CATALOG
        row = form_lean("dota2", [{"team_a": "Natus Vincere", "team_b": "Team Spirit"}])[0]
        self.assertTrue(row["form"]["known"])
        self.assertEqual(row["form"]["favorite"], "Team Spirit")
        self.assertEqual(row["form"]["rating_gap"], 100.0)

    @patch("src.sports.esports.Dota2.teams")
    def test_thin_history_is_not_a_rating(self, teams):
        teams.return_value = self.CATALOG
        row = form_lean("dota2", [{"team_a": "Rookies", "team_b": "Team Spirit"}])[0]
        self.assertFalse(row["form"]["known"])

    @patch("src.sports.esports.Dota2.teams")
    def test_elo_expectation_matches_the_published_formula(self, teams):
        teams.return_value = self.CATALOG
        row = form_lean("dota2", [{"team_a": "Team Spirit", "team_b": "Natus Vincere"}])[0]
        self.assertAlmostEqual(row["form"]["favorite_pct"], 64.0, places=1)

    def test_game_without_a_ratings_source_abstains(self):
        row = form_lean("valorant", [{"team_a": "A", "team_b": "B"}])[0]
        self.assertFalse(row["form"]["known"])

    @patch("src.sports.esports.Dota2.teams", side_effect=RuntimeError("upstream down"))
    def test_source_failure_is_not_a_missing_team(self, _teams):
        row = form_lean("dota2", [{"team_a": "A", "team_b": "B"}])[0]
        self.assertIn("OpenDota", row["form"]["reason"])


class ResolveTests(unittest.TestCase):
    @patch("src.markets.resolve._get")
    def test_unknown_condition_id_stays_unresolved(self, get):
        get.return_value = [{"conditionId": "0x" + "aa" * 32, "question": "Known?",
                             "outcomes": '["Yes","No"]', "outcomePrices": '["0.7","0.3"]',
                             "events": [{"slug": "e"}]}]
        out = markets_by_condition(["0x" + "aa" * 32, "0x" + "bb" * 32])
        self.assertEqual((out["asked"], out["resolved"]), (2, 1))
        self.assertNotIn("0x" + "bb" * 32, out["markets"])
        self.assertEqual(out["markets"]["0x" + "aa" * 32]["leader_pct"], "70.0%")

    @patch("src.markets.resolve._get", side_effect=RuntimeError("gamma down"))
    def test_gamma_failure_degrades_to_unresolved(self, _get):
        out = markets_by_condition(["0x" + "aa" * 32])
        self.assertEqual(out["resolved"], 0)
        self.assertIn("gamma down", out["failed"])

    @patch("src.markets.resolve._get")
    def test_malformed_ids_are_never_sent(self, get):
        get.return_value = []
        markets_by_condition(["not-an-id", "0xshort", None])
        get.assert_not_called()


class SettlementMoneyTests(unittest.TestCase):
    """The counts always covered the window; the money used to cover 20 rows."""

    @staticmethod
    def split_log(index, amount):
        return {
            "data": encode(["address", "uint256[]", "uint256"], [USDC_E, [1, 2], amount]),
            "topics": [TOPIC_POSITION_SPLIT, "0x" + "00" * 12 + "12" * 20,
                       "0x" + "00" * 32, "0x" + "ab" * 32],
            "blockNumber": 100, "logIndex": index,
            "transactionHash": bytes.fromhex("ab" * 32), "address": "0x" + "11" * 20,
        }

    @patch("src.markets.resolve.markets_by_condition")
    def test_usdc_total_covers_every_event_not_just_the_shown_rows(self, resolve):
        resolve.return_value = {"markets": {}, "asked": 0, "resolved": 0,
                                "failed": None, "price_as_of": None, "note": ""}
        logs = [self.split_log(i, 1_000_000) for i in range(50)]   # 50 events of 1 USDC

        def get_logs(query):
            return logs if query["topics"] == [TOPIC_POSITION_SPLIT] else []

        reader = PolygonReader.__new__(PolygonReader)
        reader.w3 = SimpleNamespace(eth=SimpleNamespace(get_logs=get_logs, block_number=100))
        result = reader.settlement_activity(10)

        stat = result["stats"]["position_splits"]
        self.assertEqual(stat["count"], 50)
        self.assertEqual(len(result["details"]["position_splits"]), 20)   # display cap
        self.assertEqual(stat["usdc_total"], "50")                        # full window
        self.assertEqual(stat["usdc_median"], "1")
        self.assertEqual(stat["unique_wallets"], 1)

    @patch("src.markets.resolve.markets_by_condition")
    def test_failed_topic_reports_unknown_not_zero_money(self, resolve):
        resolve.return_value = {"markets": {}, "asked": 0, "resolved": 0,
                                "failed": None, "price_as_of": None, "note": ""}

        def get_logs(query):
            if query["topics"] == [TOPIC_POSITION_SPLIT]:
                raise RuntimeError("upstream down")
            return []

        reader = PolygonReader.__new__(PolygonReader)
        reader.w3 = SimpleNamespace(eth=SimpleNamespace(get_logs=get_logs, block_number=100))
        result = reader.settlement_activity(10)
        self.assertIsNone(result["summary"]["position_splits"])
        self.assertIn("position_splits", result["incomplete"])
        self.assertIsNone(result["stats"]["position_splits"]["usdc_total"])


class BoardFallbackTests(unittest.TestCase):
    """A dead schedule source must not take the odds down with it."""

    LISTED = [{"teams": ["Stray Club", "Rostik999 Club"], "event_title": "Dota 2: Stray vs Rostik999",
               "favorite": "Stray Club", "favorite_pct": "99.9%", "margin_pp": 99.9,
               "tied": False, "outcomes": [], "volume24h": 4929.0, "liquidity": 10.0,
               "start_date": "2026-09-11T00:19:31Z", "url": "u", "condition_id": "0x" + "d9" * 32}]

    @patch("src.sports.match_analysis.form_lean", side_effect=lambda game, rows, **k: rows)
    @patch("src.markets.fixtures.board")
    @patch("src.sports.esports.EsportsSchedules.upcoming",
           side_effect=RuntimeError("429 rate limited"))
    def test_liquipedia_failure_still_serves_priced_fixtures(self, _up, board, _form):
        from src.web import create_app

        board.return_value = self.LISTED
        with create_app().test_client() as client:
            payload = client.get("/api/esports/board?game=dota2&limit=10").get_json()

        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertEqual(data["schedule_source"], "Polymarket")
        self.assertEqual([f["source"] for f in data["failed"]], ["Liquipedia"])
        self.assertEqual(data["rows"][0]["team_a"], "Stray Club")
        self.assertTrue(data["rows"][0]["poly"]["matched"])
        self.assertIn("bukan seluruh jadwal turnamen", data["note"])


if __name__ == "__main__":
    unittest.main()
