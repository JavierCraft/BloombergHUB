"""Soccer data via soccerdata (★2.056) plus StatsBomb open data (★741).

`soccerdata` fronts eight providers. From this connection they do not all work,
and the probe on 6 September 2026 says which:

    Understat        200   xG per shot and per match      -> USE
    ESPN             200   fixtures, results, live        -> USE
    FBref            403   Cloudflare wall                -> dead here
    ClubElo          502   upstream error                 -> dead today
    Football-Data    timeout  ISP block                   -> dead here

That last line is the one that changes the plan. The audit's advice was to
calibrate your model against bookmaker odds from Football-Data.co.uk before ever
pricing a Polymarket market — "if you cannot beat the bookmaker, do not
continue". That host is blocked from here, exactly like Polymarket's own API.

So this module builds the *prediction* layer (xG in, probabilities out) and is
explicit that the *calibration* layer is unavailable until you have network
access elsewhere. It does not silently substitute a worse source and call it
calibration.

League naming, fixed 11 September 2026: soccerdata 1.9 takes standardised names
like `ENG-Premier League`. The previous code passed Understat's own short codes
(`EPL`) and ESPN's slugs (`eng.1`), which the library rejects outright — both
soccer tabs returned a 500 on every load. The names are now taken from the
library itself via `available_leagues()`, so a future rename shows up as a
smaller league list rather than a crash.
"""
from __future__ import annotations

from typing import Any

# Nama baku soccerdata 1.9. Understat dan ESPN kebetulan menerima daftar yang
# sama; `liga_tersedia()` di bawah menanyakannya langsung ke pustakanya.
LEAGUES = (
    "ENG-Premier League",
    "ESP-La Liga",
    "GER-Bundesliga",
    "ITA-Serie A",
    "FRA-Ligue 1",
)
DEFAULT_LEAGUE = "ENG-Premier League"
DEFAULT_SEASON = "2025-2026"

BLOCKED_PROVIDERS = {
    "FootballData": "www.football-data.co.uk timeout — diblokir ISP, jalur odds bandar tertutup",
    "FBref": "fbref.com 403 — dinding Cloudflare",
    "ClubElo": "api.clubelo.com 502 — error di sisi mereka",
}

# Kolom yang berguna dibaca orang, diurutkan. Sisanya (id internal, url) dibuang
# supaya tabelnya tidak penuh nomor yang tidak berarti apa-apa.
UNDERSTAT_COLUMNS = [
    "date", "home_team", "away_team",
    "home_goals", "away_goals", "home_xg", "away_xg",
]
ESPN_COLUMNS = ["date", "home_team", "away_team", "game_id"]


def _normalise(league: str | None) -> str:
    """Terima nama baku, juga kode lama, supaya tautan lama tidak mati."""
    if not league:
        return DEFAULT_LEAGUE
    if league in LEAGUES:
        return league
    alias = {
        "EPL": "ENG-Premier League", "eng.1": "ENG-Premier League",
        "La_liga": "ESP-La Liga", "esp.1": "ESP-La Liga",
        "Bundesliga": "GER-Bundesliga", "ger.1": "GER-Bundesliga",
        "Serie_A": "ITA-Serie A", "ita.1": "ITA-Serie A",
        "Ligue_1": "FRA-Ligue 1", "fra.1": "FRA-Ligue 1",
    }
    if league in alias:
        return alias[league]

    from src.core.errors import BadRequest

    raise BadRequest(
        f"Liga '{league}' tidak dikenal.",
        hint="Pilih salah satu: " + ", ".join(LEAGUES),
    )


class SoccerDataSource:
    """Wrapper over soccerdata that refuses to pretend a blocked source works."""

    def __init__(self):
        try:
            import soccerdata as sd
        except ImportError as exc:
            raise ImportError("pip install soccerdata") from exc
        self.sd = sd

    # -- providers that answer from this connection -------------------------

    def liga_tersedia(self) -> list[str]:
        """Tanyakan langsung ke pustakanya, jangan mengandalkan daftar tetap."""
        try:
            return sorted(set(self.sd.Understat.available_leagues())
                          & set(self.sd.ESPN.available_leagues()))
        except Exception:
            return list(LEAGUES)

    def understat_schedule(self, league: str | None = None,
                           seasons: list[str] | None = None) -> Any:
        """Match list with xG for both sides — the model input that matters."""
        us = self.sd.Understat(leagues=_normalise(league),
                               seasons=seasons or [DEFAULT_SEASON])
        frame = us.read_schedule().reset_index()
        return self._pilih_kolom(frame, UNDERSTAT_COLUMNS)

    def understat_team_stats(self, league: str | None = None,
                             seasons: list[str] | None = None) -> Any:
        us = self.sd.Understat(leagues=_normalise(league),
                               seasons=seasons or [DEFAULT_SEASON])
        return us.read_team_match_stats().reset_index()

    def espn_schedule(self, league: str | None = None,
                      seasons: list[str] | None = None) -> Any:
        """Fixtures and results from ESPN — reachable, and updates fast."""
        espn = self.sd.ESPN(leagues=_normalise(league),
                            seasons=seasons or [DEFAULT_SEASON])
        frame = espn.read_schedule().reset_index()
        return self._pilih_kolom(frame, ESPN_COLUMNS)

    @staticmethod
    def _pilih_kolom(frame: Any, wanted: list[str]) -> Any:
        """Ambil kolom yang berguna saja, lewati yang tidak ada.

        Skema upstream berubah dari waktu ke waktu; kolom yang hilang harus
        mempersempit tabel, bukan menggagalkan permintaannya.
        """
        keep = [c for c in wanted if c in frame.columns]
        return frame[keep] if keep else frame

    # -- providers confirmed dead from here ---------------------------------

    def football_data_odds(self, league: str = "", seasons: list | None = None) -> Any:
        """Bookmaker odds. Blocked from this connection — fails loudly, on purpose."""
        from src.core.errors import NetworkBlocked

        raise NetworkBlocked("www.football-data.co.uk", BLOCKED_PROVIDERS["FootballData"])

    def club_elo(self, *_args, **_kwargs) -> Any:
        from src.core.errors import UpstreamError

        raise UpstreamError("ClubElo", BLOCKED_PROVIDERS["ClubElo"], status=502)

    def fbref_match(self, *_args, **_kwargs) -> Any:
        from src.core.errors import NetworkBlocked

        raise NetworkBlocked("fbref.com", BLOCKED_PROVIDERS["FBref"])

    @staticmethod
    def provider_status() -> list[dict]:
        """What the UI shows on the Soccer page, so nobody clicks a dead button."""
        return [
            {"provider": "Understat", "status": "live", "gives": "xG per match", "detail": "HTTP 200"},
            {"provider": "ESPN", "status": "live", "gives": "jadwal & hasil", "detail": "HTTP 200"},
            {"provider": "StatsBomb", "status": "live", "gives": "event data (open)", "detail": "HTTP 200"},
            {"provider": "FBref", "status": "blocked", "gives": "stats lanjutan", "detail": BLOCKED_PROVIDERS["FBref"]},
            {"provider": "ClubElo", "status": "upstream_down", "gives": "rating Elo", "detail": BLOCKED_PROVIDERS["ClubElo"]},
            {"provider": "Football-Data", "status": "blocked", "gives": "ODDS BANDAR", "detail": BLOCKED_PROVIDERS["FootballData"]},
        ]


class StatsBombSoccer:
    """StatsBomb open data — free event-level football, served from GitHub raw.

    Reachable here precisely because it is hosted on raw.githubusercontent.com,
    which is not blocked. Paid competitions need credentials.
    """

    def __init__(self, creds: dict | None = None):
        try:
            from statsbombpy import sb
        except ImportError as exc:
            raise ImportError("pip install statsbombpy") from exc
        self.sb = sb
        self.creds = creds or {}

    def competitions(self) -> Any:
        return self.sb.competitions(creds=self.creds) if self.creds else self.sb.competitions()

    def matches(self, competition_id: int, season_id: int) -> Any:
        return self.sb.matches(competition_id=competition_id, season_id=season_id)

    def events(self, match_id: int) -> Any:
        """Event stream for one match. `include_360_metrics` was never a real kwarg."""
        return self.sb.events(match_id=match_id)

    def lineups(self, match_id: int) -> Any:
        return self.sb.lineups(match_id=match_id)


def xg_to_probabilities(home_xg: float, away_xg: float, total_line: float = 2.5) -> dict:
    """Bridge: turn Understat xG into market-comparable probabilities.

    This is the join between the data layer and the betting layer — take xG from
    a real match, get 1X2 / totals / BTTS priced, then compare against a market.
    """
    from .betting import match_probabilities

    return match_probabilities(home_xg, away_xg, total_line=total_line)
