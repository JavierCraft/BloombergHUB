"""NBA data via nba_api (★3.761) — the official stats.nba.com endpoints.

stats.nba.com rate-limits aggressively and will start returning empty payloads
rather than errors if you hammer it, so every call here is short-timeout and the
API layer caches the result. That is not politeness, it is the difference
between a working dashboard and a silently empty one.
"""
from __future__ import annotations

from typing import Any

DEFAULT_SEASON = "2025-26"
STAT_CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK", "FG_PCT", "FG3_PCT", "EFF")
TIMEOUT = 30


class NBAData:
    def __init__(self, season: str = DEFAULT_SEASON):
        try:
            import nba_api  # noqa: F401
        except ImportError:
            from src.core.errors import MissingDependency

            raise MissingDependency("nba_api") from None
        self.season = season

    # -- static reference (no network) --------------------------------------

    def teams(self) -> list[dict]:
        from nba_api.stats.static import teams

        return teams.get_teams()

    def players(self, active_only: bool = True) -> list[dict]:
        from nba_api.stats.static import players

        return players.get_active_players() if active_only else players.get_players()

    # -- live endpoints ------------------------------------------------------

    def league_leaders(self, stat: str = "PTS", season: str | None = None) -> Any:
        if stat not in STAT_CATEGORIES:
            raise ValueError(f"stat harus salah satu dari {STAT_CATEGORIES}")
        from nba_api.stats.endpoints import leagueleaders

        return leagueleaders.LeagueLeaders(
            stat_category_abbreviation=stat,
            season=season or self.season,
            timeout=TIMEOUT,
        ).get_data_frames()[0]

    def team_stats(self, season: str | None = None) -> Any:
        from nba_api.stats.endpoints import leaguedashteamstats

        return leaguedashteamstats.LeagueDashTeamStats(
            season=season or self.season, timeout=TIMEOUT
        ).get_data_frames()[0]

    def standings(self, season: str | None = None) -> Any:
        """Conference standings — the base rate for series and title markets."""
        from nba_api.stats.endpoints import leaguestandingsv3

        return leaguestandingsv3.LeagueStandingsV3(
            season=season or self.season, timeout=TIMEOUT
        ).get_data_frames()[0]

    def season_games(self, season: str | None = None) -> Any:
        from nba_api.stats.endpoints import leaguegamefinder

        return leaguegamefinder.LeagueGameFinder(
            season_nullable=season or self.season, timeout=TIMEOUT
        ).get_data_frames()[0]

    def recent_games(self, season: str | None = None, limit: int = 40) -> Any:
        """Pertandingan terakhir yang sudah selesai, terbaru lebih dulu.

        Ini pengganti papan skor langsung. Papan skor itu dilayani
        `cdn.nba.com`, yang menolak koneksi ini dengan 403, sementara
        `stats.nba.com` tetap terbuka — jadi tab yang dulu selalu gagal
        sekarang diisi data yang benar-benar bisa diambil.
        """
        frame = self.season_games(season)
        if "GAME_DATE" in frame.columns:
            frame = frame.sort_values("GAME_DATE", ascending=False)
        keep = [c for c in ("GAME_DATE", "MATCHUP", "WL", "PTS", "REB", "AST",
                            "TEAM_NAME", "FG_PCT") if c in frame.columns]
        if keep:
            frame = frame[keep]
        return frame.head(limit)

    def today_scoreboard(self) -> Any:
        """Today's games from the live endpoint.

        This one is served by `cdn.nba.com`, not `stats.nba.com`, and the CDN
        answers 403 from this connection regardless of User-Agent. nba_api then
        tries to parse the HTML error page and raises `JSONDecodeError` — a
        confusing failure that looks like our bug. Translate it into the truth.
        """
        from src.core.errors import NetworkBlocked

        try:
            from nba_api.live.nba.endpoints import scoreboard

            games = scoreboard.ScoreBoard().games.get_dict()
        except Exception as exc:
            raise NetworkBlocked(
                "cdn.nba.com",
                f"HTTP 403 — CDN skor langsung menolak koneksi ini ({type(exc).__name__})",
            ) from None
        return [
            {
                "game_id": g.get("gameId"),
                "status": g.get("gameStatusText"),
                "away": (g.get("awayTeam") or {}).get("teamTricode"),
                "away_score": (g.get("awayTeam") or {}).get("score"),
                "home": (g.get("homeTeam") or {}).get("teamTricode"),
                "home_score": (g.get("homeTeam") or {}).get("score"),
                "period": g.get("period"),
                "clock": g.get("gameClock"),
                "start_utc": g.get("gameTimeUTC"),
            }
            for g in games
        ]
