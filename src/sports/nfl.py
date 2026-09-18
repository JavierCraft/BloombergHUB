"""NFL data via nflreadpy.

Source: nflverse/nflreadpy (★204, pushed 2026-08-05) — the official replacement
for `nfl_data_py`, which GitHub reports as `archived: true` since 25 September
2025. Tutorials still say `pip install nfl_data_py`; they are out of date.

Two things the previous wrapper got wrong against nflreadpy 0.1.5:

  * `load_pfr_passing` / `load_pfr_rushing` / `load_pfr_receiving` do not exist.
    The real function is `load_pfr_advstats(stat_type=...)`.
  * every loader returns a **polars** DataFrame. Calling
    `.to_dict(orient="records")` on one raises TypeError, so the endpoint could
    never have returned a row. Normalisation lives in `src.core.frames`.
"""
from __future__ import annotations

from typing import Any

PFR_STAT_TYPES = ("pass", "rush", "rec", "def")


class NFLData:
    """Thin wrapper. Returns polars frames as-is; the API layer normalises them."""

    def __init__(self):
        try:
            import nflreadpy as nfl
        except ImportError as exc:
            raise ImportError(
                "pip install nflreadpy — menggantikan nfl_data_py yang sudah diarsipkan"
            ) from exc
        self._nfl = nfl

    # -- core tables --------------------------------------------------------

    def schedules(self, seasons: list[int] | None = None) -> Any:
        """Games with results, spreads and totals where available."""
        return self._nfl.load_schedules(seasons=seasons if seasons else True)

    def rosters(self, seasons: list[int] | None = None) -> Any:
        return self._nfl.load_rosters(seasons=seasons if seasons else True)

    def players(self) -> Any:
        return self._nfl.load_players()

    def player_stats(self, seasons: list[int] | None = None) -> Any:
        return self._nfl.load_player_stats(seasons=seasons if seasons else True)

    def team_stats(self, seasons: list[int] | None = None) -> Any:
        return self._nfl.load_team_stats(seasons=seasons if seasons else True)

    def injuries(self, seasons: list[int] | None = None) -> Any:
        """Injury reports — the single most price-moving input in NFL markets."""
        return self._nfl.load_injuries(seasons=seasons if seasons else True)

    def pfr_advstats(self, stat_type: str = "pass", seasons: list[int] | None = None) -> Any:
        """Pro Football Reference advanced stats.

        Args:
            stat_type: pass | rush | rec | def
        """
        if stat_type not in PFR_STAT_TYPES:
            raise ValueError(f"stat_type harus salah satu dari {PFR_STAT_TYPES}")
        return self._nfl.load_pfr_advstats(
            stat_type=stat_type, seasons=seasons if seasons else True
        )

    def draft_picks(self, seasons: list[int] | None = None) -> Any:
        return self._nfl.load_draft_picks(seasons=seasons if seasons else True)

    def combine(self) -> Any:
        return self._nfl.load_combine()

    # -- derived ------------------------------------------------------------

    def upcoming(self, season: int | None = None, limit: int = 25) -> Any:
        """Games not yet played — the ones a market can still be priced on."""
        import polars as pl

        frame = self.schedules(seasons=[season] if season else None)
        if "result" in frame.columns:
            frame = frame.filter(pl.col("result").is_null())
        keep = [
            c for c in
            ("game_id", "season", "week", "gameday", "gametime", "away_team",
             "home_team", "spread_line", "total_line", "away_moneyline", "home_moneyline")
            if c in frame.columns
        ]
        if keep:
            frame = frame.select(keep)
        if "gameday" in frame.columns:
            frame = frame.sort("gameday")
        return frame.head(limit)

    def results(self, season: int | None = None, limit: int = 25) -> Any:
        """Completed games, most recent first."""
        import polars as pl

        frame = self.schedules(seasons=[season] if season else None)
        if "result" in frame.columns:
            frame = frame.filter(pl.col("result").is_not_null())
        keep = [
            c for c in
            ("game_id", "season", "week", "gameday", "away_team", "away_score",
             "home_team", "home_score", "result", "total", "spread_line", "total_line")
            if c in frame.columns
        ]
        if keep:
            frame = frame.select(keep)
        if "gameday" in frame.columns:
            frame = frame.sort("gameday", descending=True)
        return frame.head(limit)
