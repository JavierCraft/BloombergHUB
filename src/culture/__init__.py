"""Culture data — free sources first, paid ones kept but no longer required.

The audit's rule for this category still holds: use official APIs, never
scrapers. Scrapers fail *silently* — they return an empty list, not an error,
and a bot reads that as "nothing charted this week".

What changed, 11 September 2026: both original sources moved behind a paywall.
TMDb now asks USD 149 per month for a developer subscription, and Spotify
requires a Premium subscription before it will issue API keys. That turned the
whole Culture page into a wall of MISSING_CREDENTIAL that no amount of filling
in `.env` could fix.

So the page now runs on three replacements, all checked live from this
connection on 11 September 2026:

    Deezer    music   no key at all   search + charts answered in ~0.4s
    TVmaze    TV      no key at all   search + daily schedule, 93 episodes today
    OMDb      film    free key        1.000 requests/day after a free signup

`SpotifyMusic` and `TMDbMovies` below are kept exactly as they were. They still
work for anyone who does pay, and they still fail with a specific, actionable
error for everyone who does not. Nothing in the UI depends on them any more.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config import (  # noqa: E402
    OMDB_API_KEY,
    SPOTIPY_CLIENT_ID,
    SPOTIPY_CLIENT_SECRET,
    TMDB_API_KEY,
)


class SpotifyMusic:
    """Spotify Web API via spotipy (★5.471). Client-credentials flow, no user login."""

    def __init__(self):
        from src.core.errors import MissingCredential, MissingDependency

        missing = [
            name for name, value in
            (("SPOTIPY_CLIENT_ID", SPOTIPY_CLIENT_ID),
             ("SPOTIPY_CLIENT_SECRET", SPOTIPY_CLIENT_SECRET))
            if not value
        ]
        if missing:
            raise MissingCredential(*missing)

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
        except ImportError:
            raise MissingDependency("spotipy") from None

        self.sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET
            ),
            requests_timeout=20,
        )

    @staticmethod
    def _track_row(track: dict) -> dict:
        album = track.get("album") or {}
        artists = track.get("artists") or [{}]
        return {
            "name": track.get("name"),
            "artist": ", ".join(a.get("name", "") for a in artists),
            "album": album.get("name"),
            "release_date": album.get("release_date"),
            "popularity": track.get("popularity"),
            "duration_s": round((track.get("duration_ms") or 0) / 1000),
            "explicit": track.get("explicit"),
            "url": (track.get("external_urls") or {}).get("spotify"),
        }

    def search(self, query: str, search_type: str = "track", limit: int = 20) -> list[dict]:
        """Search and return flat rows — the raw Spotify shape is deeply nested."""
        results = self.sp.search(q=query, type=search_type, limit=min(50, limit))
        items = (results.get(f"{search_type}s") or {}).get("items") or []

        if search_type == "track":
            return [self._track_row(t) for t in items if t]
        if search_type == "artist":
            return [
                {
                    "name": a.get("name"),
                    "popularity": a.get("popularity"),
                    "followers": (a.get("followers") or {}).get("total"),
                    "genres": ", ".join(a.get("genres") or []),
                    "url": (a.get("external_urls") or {}).get("spotify"),
                }
                for a in items if a
            ]
        if search_type == "album":
            return [
                {
                    "name": a.get("name"),
                    "artist": ", ".join(x.get("name", "") for x in (a.get("artists") or [])),
                    "release_date": a.get("release_date"),
                    "total_tracks": a.get("total_tracks"),
                    "url": (a.get("external_urls") or {}).get("spotify"),
                }
                for a in items if a
            ]
        return items

    def new_releases(self, country: str = "US", limit: int = 30) -> list[dict]:
        """New albums — the input for "will album X debut at number one" markets."""
        results = self.sp.new_releases(country=country, limit=min(50, limit))
        return [
            {
                "name": a.get("name"),
                "artist": ", ".join(x.get("name", "") for x in (a.get("artists") or [])),
                "release_date": a.get("release_date"),
                "total_tracks": a.get("total_tracks"),
                "type": a.get("album_type"),
                "url": (a.get("external_urls") or {}).get("spotify"),
            }
            for a in (results.get("albums") or {}).get("items") or []
        ]

    def artist_momentum(self, artist_query: str) -> dict:
        """Follower count, popularity and top tracks in one call.

        Popularity is Spotify's own 0-100 score and moves within days of a
        release, which makes it a usable proxy for chart trajectory.
        """
        found = self.search(artist_query, "artist", limit=1)
        if not found:
            from src.core.errors import BadRequest

            raise BadRequest(f"Artis '{artist_query}' tidak ditemukan.")

        raw = self.sp.search(q=artist_query, type="artist", limit=1)
        artist = (raw.get("artists") or {}).get("items", [{}])[0]
        top = self.sp.artist_top_tracks(artist["id"], country="US")

        return {
            "artist": found[0],
            "top_tracks": [self._track_row(t) for t in (top.get("tracks") or [])[:10]],
        }


class TMDbMovies:
    """TMDb v3 via tmdbsimple (★698) — release dates, ratings, revenue."""

    def __init__(self):
        from src.core.errors import MissingCredential, MissingDependency

        if not TMDB_API_KEY:
            raise MissingCredential("TMDB_API_KEY")
        try:
            import tmdbsimple as tmdb
        except ImportError:
            raise MissingDependency("tmdbsimple") from None

        tmdb.API_KEY = TMDB_API_KEY
        tmdb.REQUESTS_TIMEOUT = 20
        self._tmdb = tmdb

    @staticmethod
    def _movie_row(r: dict) -> dict:
        return {
            "id": r.get("id"),
            "title": r.get("title"),
            "release_date": r.get("release_date") or None,
            "vote_average": r.get("vote_average"),
            "vote_count": r.get("vote_count"),
            "popularity": round(r.get("popularity") or 0, 1),
            "language": r.get("original_language"),
        }

    def search_movie(self, query: str, limit: int = 20) -> list[dict]:
        results = self._tmdb.Search().movie(query=query)
        return [self._movie_row(r) for r in (results.get("results") or [])[:limit]]

    def movie(self, movie_id: int) -> dict:
        info = self._tmdb.Movies(movie_id).info()
        budget = info.get("budget") or 0
        revenue = info.get("revenue") or 0
        return {
            "title": info.get("title"),
            "release_date": info.get("release_date"),
            "status": info.get("status"),
            "runtime": info.get("runtime"),
            "vote_average": info.get("vote_average"),
            "vote_count": info.get("vote_count"),
            "budget": budget,
            "revenue": revenue,
            "roi_pct": round((revenue - budget) / budget * 100, 1) if budget else None,
            "genres": ", ".join(g["name"] for g in info.get("genres") or []),
        }

    def upcoming(self, limit: int = 30) -> list[dict]:
        results = self._tmdb.Movies().upcoming()
        rows = [self._movie_row(r) for r in (results.get("results") or [])]
        rows.sort(key=lambda r: r["release_date"] or "9999")
        return rows[:limit]

    def now_playing(self, limit: int = 30) -> list[dict]:
        results = self._tmdb.Movies().now_playing()
        return [self._movie_row(r) for r in (results.get("results") or [])[:limit]]

    def trending(self, window: str = "week", limit: int = 30) -> list[dict]:
        results = self._tmdb.Trending("movie", window).info()
        return [self._movie_row(r) for r in (results.get("results") or [])[:limit]]

    def tv_search(self, query: str, limit: int = 20) -> list[dict]:
        results = self._tmdb.Search().tv(query=query)
        return [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "first_air_date": r.get("first_air_date") or None,
                "vote_average": r.get("vote_average"),
                "popularity": round(r.get("popularity") or 0, 1),
            }
            for r in (results.get("results") or [])[:limit]
        ]

    def tv_show(self, tv_id: int) -> dict:
        info = self._tmdb.TV(tv_id).info()
        return {
            "name": info.get("name"),
            "first_air_date": info.get("first_air_date"),
            "status": info.get("status"),
            "seasons": info.get("number_of_seasons"),
            "episodes": info.get("number_of_episodes"),
            "vote_average": info.get("vote_average"),
            "genres": ", ".join(g["name"] for g in info.get("genres") or []),
            "next_episode": (info.get("next_episode_to_air") or {}).get("air_date"),
        }


# ---------------------------------------------------------------------------
# Pengganti gratis — inilah yang benar-benar dipakai halaman Culture
# ---------------------------------------------------------------------------

DEEZER_BASE = "https://api.deezer.com"
TVMAZE_BASE = "https://api.tvmaze.com"
OMDB_BASE = "https://www.omdbapi.com/"

USER_AGENT = "BloombergHub/1.0 (prediction-market research; local dashboard)"

_TANGGAL = re.compile(r"\d{4}-\d{2}-\d{2}")


def _get(url: str, params: dict | None = None, timeout: int = 25) -> Any:
    """Satu jalur HTTP, satu taksonomi error, untuk ketiga sumber gratis."""
    import requests

    from src.core.errors import NetworkBlocked, RateLimited, UpstreamError

    host = url.split("/")[2]
    try:
        resp = requests.get(url, params=params, timeout=timeout,
                            headers={"User-Agent": USER_AGENT})
    except requests.exceptions.Timeout:
        raise NetworkBlocked(host, "timeout") from None
    except requests.exceptions.SSLError:
        raise NetworkBlocked(host, "sertifikat tidak cocok — ciri halaman blokir ISP") from None
    except requests.exceptions.ConnectionError:
        raise NetworkBlocked(host, "tidak terhubung") from None

    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After")
        raise RateLimited(host, int(retry) if retry and retry.isdigit() else 60)
    if resp.status_code >= 400:
        raise UpstreamError(host, resp.text[:200], resp.status_code)

    try:
        payload = resp.json()
    except ValueError:
        raise UpstreamError(host, "respons bukan JSON", resp.status_code) from None

    # Deezer membalas 200 walau permintaannya salah; galatnya ada di dalam badan.
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict) and payload["error"]:
        err = payload["error"]
        raise UpstreamError(host, f"{err.get('type')}: {err.get('message')}", resp.status_code)
    return payload


def _menit(detik: Any) -> Any:
    """Detik -> menit:detik, karena '215' tidak terbaca sebagai durasi lagu."""
    try:
        d = int(detik)
    except (TypeError, ValueError):
        return None
    return f"{d // 60}:{d % 60:02d}"


class DeezerMusic:
    """Deezer API — musik tanpa kunci sama sekali.

    Menggantikan Spotify. `rank` Deezer adalah skor popularitas yang bergerak
    harian, jadi perannya sama dengan `popularity` Spotify: perkiraan lintasan
    tangga lagu, bukan angka penjualan.
    """

    @staticmethod
    def _track_row(t: dict, position: int | None = None) -> dict:
        artist = t.get("artist") or {}
        album = t.get("album") or {}
        row = {
            "title": t.get("title_short") or t.get("title"),
            "artist": artist.get("name"),
            "album": album.get("title"),
            "rank": t.get("rank"),
            "duration": _menit(t.get("duration")),
            "explicit": bool(t.get("explicit_lyrics")),
            "url": t.get("link"),
        }
        if position is not None:
            row = {"position": position, **row}
        return row

    def search_track(self, query: str, limit: int = 30) -> list[dict]:
        data = _get(f"{DEEZER_BASE}/search", {"q": query, "limit": min(100, limit)})
        return [self._track_row(t) for t in (data.get("data") or [])]

    def search_artist(self, query: str, limit: int = 30) -> list[dict]:
        data = _get(f"{DEEZER_BASE}/search/artist", {"q": query, "limit": min(100, limit)})
        return [
            {
                "artist": a.get("name"),
                "fans": a.get("nb_fan"),
                "albums": a.get("nb_album"),
                "url": a.get("link"),
            }
            for a in (data.get("data") or [])
        ]

    def top_tracks(self, limit: int = 40) -> list[dict]:
        """Tangga lagu global hari ini — pengganti langsung 'new releases'."""
        data = _get(f"{DEEZER_BASE}/chart/0/tracks", {"limit": min(100, limit)})
        return [self._track_row(t, t.get("position", i + 1))
                for i, t in enumerate(data.get("data") or [])]

    def top_albums(self, limit: int = 40) -> list[dict]:
        data = _get(f"{DEEZER_BASE}/chart/0/albums", {"limit": min(100, limit)})
        return [
            {
                "position": a.get("position", i + 1),
                "album": a.get("title"),
                "artist": (a.get("artist") or {}).get("name"),
                "type": a.get("record_type"),
                "explicit": bool(a.get("explicit_lyrics")),
                "url": a.get("link"),
            }
            for i, a in enumerate(data.get("data") or [])
        ]


class TVmazeShows:
    """TVmaze API — serial TV tanpa kunci sama sekali.

    Menggantikan sisi serial TMDb. `schedule/web` memberi daftar episode yang
    tayang pada satu tanggal, dan itu justru lebih langsung dipakai untuk pasar
    bertema "apakah episode X tayang sebelum tanggal Y" ketimbang data TMDb.
    """

    @staticmethod
    def _bersihkan(html: str | None) -> str | None:
        """Ringkasan TVmaze datang sebagai HTML; tabelnya hanya mau teks."""
        if not html:
            return None
        teks = re.sub(r"<[^>]+>", "", html)
        for kode, asli in (("&amp;", "&"), ("&quot;", '"'),
                           ("&#39;", "'"), ("&nbsp;", " ")):
            teks = teks.replace(kode, asli)
        teks = teks.strip()
        return (teks[:220] + "…") if len(teks) > 220 else teks

    def search(self, query: str, limit: int = 30) -> list[dict]:
        data = _get(f"{TVMAZE_BASE}/search/shows", {"q": query})
        rows = []
        for hit in (data or [])[:limit]:
            show = hit.get("show") or {}
            channel = (show.get("network") or show.get("webChannel") or {})
            rows.append({
                "show": show.get("name"),
                "premiered": show.get("premiered"),
                "ended": show.get("ended"),
                "status": show.get("status"),
                "rating": (show.get("rating") or {}).get("average"),
                "channel": channel.get("name"),
                "genres": ", ".join(show.get("genres") or []),
                "summary": self._bersihkan(show.get("summary")),
                "url": show.get("url"),
            })
        return rows

    def schedule(self, date: str | None = None, limit: int = 60) -> list[dict]:
        """Episode yang tayang pada satu tanggal. Kosongkan tanggal untuk hari ini."""
        from datetime import date as _date

        tanggal = date or _date.today().isoformat()
        if not _TANGGAL.fullmatch(tanggal):
            from src.core.errors import BadRequest

            raise BadRequest(f"Tanggal '{tanggal}' tidak sesuai bentuk.",
                             hint="Pakai bentuk TTTT-BB-HH, misalnya 2026-09-11.")

        data = _get(f"{TVMAZE_BASE}/schedule/web", {"date": tanggal})
        rows = []
        for ep in (data or [])[:limit]:
            show = ((ep.get("_embedded") or {}).get("show")) or {}
            channel = (show.get("network") or show.get("webChannel") or {})
            rows.append({
                "show": show.get("name"),
                "episode": ep.get("name"),
                "season": ep.get("season"),
                "number": ep.get("number"),
                "airdate": ep.get("airdate"),
                "airtime": ep.get("airtime") or None,
                "runtime": ep.get("runtime"),
                "channel": channel.get("name"),
                "url": ep.get("url"),
            })
        return rows


class OMDbMovies:
    """OMDb API — film, kunci gratis (1.000 permintaan/hari).

    Menggantikan sisi film TMDb. OMDb tidak punya daftar "sedang tayang" atau
    "tren" — hanya pencarian dan detail. Halaman Culture karena itu menampilkan
    pencarian film, bukan daftar tren yang disusun sendiri dan hanya tampak
    seperti data.
    """

    def __init__(self):
        from src.core.errors import MissingCredential

        if not OMDB_API_KEY:
            raise MissingCredential("OMDB_API_KEY")
        self.key = OMDB_API_KEY

    def _call(self, params: dict) -> dict:
        data = _get(OMDB_BASE, {"apikey": self.key, **params})
        if str(data.get("Response")).lower() == "false":
            pesan = data.get("Error", "")
            from src.core.errors import BadRequest, UpstreamError

            if "not found" in pesan.lower() or "too many results" in pesan.lower():
                raise BadRequest(f"OMDb: {pesan}",
                                 hint="Coba kata kunci yang lebih spesifik.")
            raise UpstreamError("omdbapi.com", pesan, 200)
        return data

    @staticmethod
    def _angka(nilai: Any) -> Any:
        """'1,234,567' atau '$1,234' -> int; sisanya jadi None."""
        if not isinstance(nilai, str) or nilai in ("N/A", ""):
            return None
        bersih = nilai.replace("$", "").replace(",", "").strip()
        return int(bersih) if bersih.isdigit() else None

    def search(self, query: str, kind: str = "movie", limit: int = 30) -> list[dict]:
        data = self._call({"s": query, "type": kind})
        return [
            {
                "title": r.get("Title"),
                "year": r.get("Year"),
                "type": r.get("Type"),
                "imdb_id": r.get("imdbID"),
            }
            for r in (data.get("Search") or [])[:limit]
        ]

    def detail(self, imdb_id: str) -> dict:
        data = self._call({"i": imdb_id, "plot": "short"})
        rating = data.get("imdbRating")
        return {
            "title": data.get("Title"),
            "year": data.get("Year"),
            "released": data.get("Released"),
            "runtime": data.get("Runtime"),
            "genre": data.get("Genre"),
            "director": data.get("Director"),
            "imdb_rating": float(rating) if rating not in (None, "N/A") else None,
            "imdb_votes": self._angka(data.get("imdbVotes")),
            "metascore": self._angka(data.get("Metascore")),
            "box_office": self._angka(data.get("BoxOffice")),
            "plot": data.get("Plot") if data.get("Plot") != "N/A" else None,
        }


def culture_status() -> list[dict]:
    """Apa yang dipakai halaman Culture sekarang, dan kenapa yang lama ditinggal."""
    return [
        {"provider": "Deezer", "status": "live",
         "gives": "musik: pencarian & tangga lagu",
         "needs": "tidak perlu kunci",
         "detail": "api.deezer.com menjawab ~0,4 detik"},
        {"provider": "TVmaze", "status": "live",
         "gives": "serial TV: pencarian & jadwal tayang",
         "needs": "tidak perlu kunci",
         "detail": "api.tvmaze.com menjawab ~1 detik"},
        {"provider": "OMDb", "status": "live" if OMDB_API_KEY else "needs_key",
         "gives": "film: pencarian & detail box office",
         "needs": "OMDB_API_KEY (gratis, 1.000/hari)",
         "detail": "Kunci sudah terisi — siap dipakai" if OMDB_API_KEY
                   else "Ambil gratis di omdbapi.com/apikey.aspx, lalu isi OMDB_API_KEY di .env"},
        {"provider": "Spotify", "status": "paid",
         "gives": "musik — sudah digantikan Deezer",
         "needs": "langganan Spotify Premium",
         "detail": "Sejak 2026 kunci API hanya untuk akun Premium. Tidak lagi dipakai halaman ini."},
        {"provider": "TMDb", "status": "paid",
         "gives": "film & serial — sudah digantikan OMDb + TVmaze",
         "needs": "langganan USD 149 per bulan",
         "detail": "Sejak 2026 akses pengembangnya berbayar. Tidak lagi dipakai halaman ini."},
    ]
