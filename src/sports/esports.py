"""Esports data — Dota 2 via OpenDota, schedules via Liquipedia.

Both confirmed reachable in the 6 September 2026 probe (HTTP 200), and neither
needs an API key, which makes esports the cheapest live category to work in from
this connection.

Counter-Strike is deliberately absent. The audit's finding stands: the popular
`gigobyte/HLTV` scraper was last pushed March 2025, and an HTML scraper left
idle for eighteen months against an actively developed site should be assumed
broken. Wiring it in would produce empty lists that look like real answers —
the silent-failure mode the audit calls the most dangerous one for a bot.
"""
from __future__ import annotations

from typing import Any

OPENDOTA_BASE = "https://api.opendota.com/api"
LIQUIPEDIA_BASE = "https://liquipedia.net"

# Liquipedia's terms require a descriptive User-Agent; generic ones get blocked.
USER_AGENT = "BloombergHub/1.0 (prediction-market research; local dashboard)"

LIQUIPEDIA_GAMES = {
    "dota2": "Dota 2",
    "leagueoflegends": "League of Legends",
    "counterstrike": "Counter-Strike",
    "valorant": "Valorant",
}


def _request(url: str, timeout: int = 20, params: dict | None = None) -> Any:
    """One HTTP path, one error taxonomy, for every esports call."""
    import requests

    from src.core.errors import NetworkBlocked, RateLimited, UpstreamError

    try:
        resp = requests.get(
            url, timeout=timeout, params=params, headers={"User-Agent": USER_AGENT}
        )
    except requests.exceptions.Timeout:
        raise NetworkBlocked(url.split("/")[2], "timeout") from None
    except requests.exceptions.ConnectionError:
        raise NetworkBlocked(url.split("/")[2], "tidak terhubung") from None

    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After")
        raise RateLimited(url.split("/")[2], int(retry) if retry and retry.isdigit() else 60)
    if resp.status_code >= 400:
        raise UpstreamError(url.split("/")[2], resp.text[:200], resp.status_code)

    try:
        return resp.json()
    except ValueError:
        raise UpstreamError(url.split("/")[2], "respons bukan JSON", resp.status_code) from None


class Dota2:
    """OpenDota — free, no key, ~60 requests/minute on the free tier.

    Source: odota/core (★1.628, pushed 2026-08-21).
    """

    def hero_stats(self) -> list[dict]:
        """Every hero with pro pick/ban/win counts — team-form input for markets."""
        return _request(f"{OPENDOTA_BASE}/heroStats")

    def pro_matches(self, limit: int = 50) -> list[dict]:
        return _request(f"{OPENDOTA_BASE}/proMatches", params={"limit": limit})

    def pro_players(self) -> list[dict]:
        return _request(f"{OPENDOTA_BASE}/proPlayers")

    def teams(self) -> list[dict]:
        """Team ratings and win/loss — the closest thing to a power ranking here."""
        return _request(f"{OPENDOTA_BASE}/teams")

    def team_matches(self, team_id: int) -> list[dict]:
        return _request(f"{OPENDOTA_BASE}/teams/{team_id}/matches")

    def match(self, match_id: int) -> dict:
        return _request(f"{OPENDOTA_BASE}/matches/{match_id}")

    def search_player(self, name: str) -> list[dict]:
        return _request(f"{OPENDOTA_BASE}/search", params={"q": name})

    def hero_win_rates(self, top: int = 25) -> list[dict]:
        """Pro win rate per hero, ordered by pick count. Small samples excluded."""
        rows = []
        for hero in self.hero_stats():
            picks = hero.get("pro_pick") or 0
            if picks < 10:
                continue
            wins = hero.get("pro_win") or 0
            rows.append({
                "hero": hero.get("localized_name"),
                "pro_pick": picks,
                "pro_win": wins,
                "win_rate": round(wins / picks, 4),
                "win_rate_pct": f"{wins / picks * 100:.1f}%",
                "pro_ban": hero.get("pro_ban") or 0,
                "contest_rate": picks + (hero.get("pro_ban") or 0),
            })
        rows.sort(key=lambda r: -r["contest_rate"])
        return rows[:top]

    def team_strength(self, top: int = 25) -> list[dict]:
        """Rating plus win rate, filtered to teams with a real match history."""
        rows = []
        for team in self.teams():
            played = (team.get("wins") or 0) + (team.get("losses") or 0)
            if played < 20:
                continue
            rows.append({
                "team": team.get("name") or f"id {team.get('team_id')}",
                "team_id": team.get("team_id"),
                "rating": round(team.get("rating") or 0, 1),
                "wins": team.get("wins"),
                "losses": team.get("losses"),
                "win_rate_pct": f"{(team.get('wins') or 0) / played * 100:.1f}%",
                "last_match_time": team.get("last_match_time"),
            })
        rows.sort(key=lambda r: -r["rating"])
        return rows[:top]


class EsportsSchedules:
    """Upcoming matches from the Liquipedia MediaWiki API.

    `liquipediapy` (★69) wraps this, but it is a thin wrapper over an API that is
    stable and public, so calling it directly removes a dependency that was last
    touched in April 2026 without losing anything.
    """

    def upcoming(self, game: str = "dota2", limit: int = 20) -> list[dict]:
        if game not in LIQUIPEDIA_GAMES:
            raise ValueError(f"Game harus salah satu dari {list(LIQUIPEDIA_GAMES)}")

        data = _request(
            f"{LIQUIPEDIA_BASE}/{game}/api.php",
            params={
                "action": "parse",
                "page": "Liquipedia:Matches",
                "format": "json",
                "prop": "text",
            },
        )
        html = (data.get("parse", {}) or {}).get("text", {}).get("*", "")
        return self._parse_matches(html, game, limit)

    @staticmethod
    def _parse_matches(html: str, game: str, limit: int) -> list[dict]:
        """Pull team pairs out of the match ticker.

        Written against the live markup on 6 September 2026: each fixture is a
        `div.match-info` holding a `data-timestamp`, two
        `div.match-info-header-opponent` blocks whose team link carries the clean
        name in `title`, and a `match-info-tournament-name`.

        Deliberately shallow. If Liquipedia changes its markup this returns fewer
        rows rather than wrong ones, and `count` in the envelope makes that
        visible instead of letting an empty list pass as "no matches today".
        """
        import datetime as dt
        import html as html_mod
        import re

        blocks = re.split(r'(?=<div class="match-info">)', html)
        rows: list[dict] = []

        for block in blocks:
            if 'class="match-info-header-opponent' not in block:
                continue

            teams = re.findall(
                r'class="match-info-header-opponent[^"]*">.*?'
                r'class="team-template-image-icon"><a[^>]*title="([^"]+)"',
                block,
                re.S,
            )
            if len(teams) < 2:
                continue

            stamp = re.search(r'data-timestamp="(\d+)"', block)
            best_of = re.search(r'match-info-header-scoreholder-lower">\(?([^)<]*)\)?<', block)
            tournament = re.search(
                r'class="match-info-tournament-name"><a[^>]*title="([^"]+)"', block
            )

            starts = int(stamp.group(1)) if stamp else None
            rows.append({
                "game": LIQUIPEDIA_GAMES[game],
                "team_a": html_mod.unescape(teams[0].strip()),
                "team_b": html_mod.unescape(teams[1].strip()),
                "starts_unix": starts,
                "starts_utc": (
                    dt.datetime.fromtimestamp(starts, dt.timezone.utc).isoformat()
                    if starts else None
                ),
                "format": (best_of.group(1).strip() if best_of else None),
                "tournament": (
                    html_mod.unescape(tournament.group(1).split("#")[0].strip())
                    if tournament else None
                ),
            })
            if len(rows) >= limit:
                break

        return rows

    @staticmethod
    def games() -> list[dict]:
        return [{"id": k, "name": v} for k, v in LIQUIPEDIA_GAMES.items()]


class LoL:
    """League of Legends via Riot's API (cassiopeia).

    Not installed here and needs a Riot key, so it raises a specific error rather
    than a bare ImportError from deep inside a call stack.
    """

    def __init__(self, riot_api_key: str = ""):
        import os

        from src.core.errors import MissingCredential, MissingDependency

        key = riot_api_key or os.getenv("RIOT_API_KEY", "")
        if not key:
            raise MissingCredential("RIOT_API_KEY")
        try:
            import cassiopeia as cass
        except ImportError:
            raise MissingDependency("cassiopeia") from None

        cass.set_riot_api_key(key)
        cass.set_default_region("NA")
        self._cass = cass

    def summoner(self, name: str):
        return self._cass.Summoner(name=name)

    def match_history(self, name: str):
        return self.summoner(name).match_history
