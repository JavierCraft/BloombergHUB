"""Politics data: ElectIndex 2026 forecast inputs, OpenElections, GDELT.

US midterms fall on 3 November 2026 — the highest-volume prediction-market
category of the year, and the one where the best source has the least attention.

GDELT note. The audit recommends `alex9smith/gdelt-doc-api` (★228) but records
its last push as April 2025, sixteen months stale. The underlying API is stable
and public, so this module calls GDELT v2 directly over `requests` rather than
taking a dependency on an unmaintained wrapper. That also avoids the confusion
with the unrelated pip package named `gdelt` (linwoodc3), which the previous
code imported and which is not the audited repo.

GDELT rate-limits hard, and measurement on 11 September 2026 says harder than
its own message admits. The body it returns reads "Please limit requests to one
every 5 seconds", but three calls spaced *seven* seconds apart, after a 75-second
idle, were all still refused. The limiter is per source IP and its penalty window
outlasts the interval it advertises.

Two consequences, both handled below:

  * We never burst. `_call` holds a process-wide lock and enforces a minimum gap
    between requests, so three cards loading at once queue instead of racing.
    This does not make a penalised IP work again; it stops us being the cause.
  * A 429 is reported as RATE_LIMITED, never as our bug, and the page keeps
    whatever it had. Every call also goes through the shared TTL cache, so a
    successful fetch keeps the page alive across the whole penalty window.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config import DATA_DIR  # noqa: E402

REPOS_DIR = DATA_DIR / "repos"
ELECTINDEX_DIR = REPOS_DIR / "electindex"
OPENELECTIONS_DIR = REPOS_DIR / "openelections-core"

ELECTINDEX_CLONE = (
    "git clone --depth 1 https://github.com/ElectIndex/26_us_forecast_data.git "
    f"{ELECTINDEX_DIR}"
)


class ElectIndexForecast:
    """The whole input layer of a running 2026 election forecast, published raw.

    Zero stars, 547 MB, pushed the day before the audit. Zero stars also means
    zero public scrutiny and no stated licence — treat it as a research input and
    cross-check against OpenElections before staking anything on it.
    """

    FILES = {
        "generic_ballot": "gcb_polls.csv",
        "approval": "approval_polls.csv",
        "race_polls": "race_polls.csv",
        "pollster_ratings": "pollster_ratings.csv",
        "fundraising": "fundraising.csv",
        "county_results_2024": "county_results_2024.csv",
        "candidate_strength": "leg_candidate_strength.csv",
        "third_parties": "third_parties.csv",
        "economic": "fred_cache.json",
    }

    def __init__(self, data_dir: Path = ELECTINDEX_DIR):
        self.dir = Path(data_dir)
        if not self.dir.exists():
            from src.core.errors import DataNotCloned

            raise DataNotCloned("ElectIndex 2026", str(self.dir), ELECTINDEX_CLONE)

    def _read(self, name: str) -> Any:
        import pandas as pd

        path = self.dir / name
        if not path.exists():
            from src.core.errors import HubError

            available = [f.name for f in self.dir.iterdir() if f.is_file()][:20]
            raise HubError(
                f"{name} tidak ada di dataset ElectIndex.",
                hint="Skema upstream mungkin berubah. File yang tersedia ada di detail.",
                detail={"available": available},
            )
        if name.endswith(".json"):
            return pd.read_json(path)
        return pd.read_csv(path, low_memory=False)

    def table(self, key: str) -> Any:
        if key not in self.FILES:
            raise ValueError(f"Tabel tidak dikenal: {key}. Pilih {list(self.FILES)}")
        return self._read(self.FILES[key])

    def generic_ballot(self, drop_banned: bool = True) -> Any:
        """Generic congressional ballot — the single variable that prices House control.

        `drop_banned=True` removes pollsters flagged in `pollster_ratings.csv`.
        The dataset carries that column because fabricated polls are a real
        problem in this cycle; averaging without filtering imports the fabrication.
        """
        frame = self._read("gcb_polls.csv")
        if not drop_banned:
            return frame

        try:
            ratings = self._read("pollster_ratings.csv")
        except Exception:
            return frame

        if "banned" not in ratings.columns:
            return frame

        banned_col = next(
            (c for c in ("pollster", "pollster_name", "display_name") if c in ratings.columns),
            None,
        )
        target_col = next(
            (c for c in ("pollster", "pollster_name", "display_name") if c in frame.columns),
            None,
        )
        if not banned_col or not target_col:
            return frame

        banned = set(ratings.loc[ratings["banned"].astype(bool), banned_col].astype(str))
        if not banned:
            return frame
        return frame[~frame[target_col].astype(str).isin(banned)]

    def banned_pollsters(self) -> Any:
        ratings = self._read("pollster_ratings.csv")
        if "banned" not in ratings.columns:
            return ratings.head(0)
        return ratings[ratings["banned"].astype(bool)]

    def approval_polls(self) -> Any:
        return self._read("approval_polls.csv")

    def race_polls(self) -> Any:
        return self._read("race_polls.csv")

    def pollster_ratings(self) -> Any:
        return self._read("pollster_ratings.csv")

    def fundraising(self) -> Any:
        return self._read("fundraising.csv")

    def county_results_2024(self) -> Any:
        return self._read("county_results_2024.csv")

    def candidate_strength(self) -> Any:
        return self._read("leg_candidate_strength.csv")

    def third_parties(self) -> Any:
        return self._read("third_parties.csv")

    def economic_fundamentals(self) -> Any:
        return self._read("fred_cache.json")

    def list_files(self) -> list[dict]:
        """Seluruh berkas datanya, bukan hanya yang di akar folder.

        Versi sebelumnya memakai `iterdir()` dan karena itu hanya melihat 46
        berkas di akar — sementara `output/` menyimpan 2.573 berkas lagi,
        termasuk seluruh prakiraan per balapan. Dashboard yang memperlihatkan
        1,7 persen isi sebuah kumpulan data sambil menyebutnya "isi dataset"
        menyesatkan pemakainya.
        """
        rows = []
        for f in self.dir.rglob("*"):
            if not f.is_file() or ".git" in f.parts:
                continue
            ukuran = f.stat().st_size
            rows.append({
                "name": f.name,
                "folder": f.parent.relative_to(self.dir).as_posix() or ".",
                "bytes": ukuran,
                "mb": round(ukuran / 1e6, 2),
            })
        return sorted(rows, key=lambda d: -d["bytes"])

    # -- prakiraan per balapan ------------------------------------------------
    #
    # Inilah bagian kumpulan data yang paling langsung bisa diadu dengan harga
    # pasar: 506 balapan, masing-masing satu baris per hari, lengkap dengan
    # peluang menang kedua pihak. Sebelumnya tidak ada satu pun endpoint yang
    # menyentuhnya.

    RACE_COLUMNS = [
        "date", "days_out", "dem_prob", "rep_prob", "margin",
        "rating", "dem_pct", "rep_pct", "polling_avg", "poll_count",
    ]

    @property
    def races_dir(self):
        return self.dir / "output" / "races"

    def races(self) -> list[dict]:
        """Daftar balapan beserta keadaan terakhirnya — urut dari yang paling ketat."""
        import pandas as pd

        from src.core.errors import UpstreamError

        folder = self.races_dir
        if not folder.exists():
            raise UpstreamError(
                "electindex",
                "Folder output/races tidak ada di salinan data ini. "
                "Unduh ulang dengan perintah clone di docs/SETUP.md.",
                404,
            )

        rows = []
        for f in sorted(folder.glob("*.csv")):
            try:
                frame = pd.read_csv(
                    f, usecols=["date", "dem_prob", "rep_prob", "margin", "rating"])
            except (ValueError, OSError):
                continue          # satu berkas cacat tidak boleh mematikan daftarnya
            if frame.empty:
                continue
            akhir = frame.iloc[-1]
            kode = f.stem
            negara, _, jenis = kode.partition("-")
            dem = float(akhir["dem_prob"])
            rows.append({
                "race": kode,
                "state": negara,
                # "House 09" terbaca janggal; nomor distriknya dibaca sebagai angka.
                "office": {"SEN": "Senate", "GOV": "Governor"}.get(
                    jenis, f"House {int(jenis)}" if jenis.isdigit() else jenis),
                "as_of": akhir["date"],
                "dem_prob": round(dem, 1),
                "rep_prob": round(float(akhir["rep_prob"]), 1),
                "margin": round(float(akhir["margin"]), 2),
                "rating": akhir["rating"],
                # Jarak dari 50 persen: angka paling berguna untuk menyaring,
                # karena balapan paling ketat itulah yang harganya paling sering
                # meleset di pasar prediksi.
                "toss_up": round(abs(dem - 50.0), 1),
            })
        return sorted(rows, key=lambda r: r["toss_up"])

    def race(self, code: str) -> Any:
        """Deret harian satu balapan — masukan langsung untuk mengadu dengan harga pasar."""
        import re

        import pandas as pd

        from src.core.errors import BadRequest

        kode = (code or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}-(?:SEN|GOV|[0-9]{1,2})", kode):
            raise BadRequest(
                f"Kode balapan '{code}' tidak sesuai bentuk.",
                hint="Bentuknya dua huruf negara bagian, tanda hubung, lalu SEN, GOV, "
                     "atau nomor distrik. Contoh: PA-SEN, TX-GOV, CA-22.",
            )

        # Nama berkas disusun dari kode yang sudah lolos pola di atas, jadi tidak
        # ada jalan bagi masukan untuk menunjuk ke luar folder ini.
        path = self.races_dir / f"{kode}.csv"
        if not path.exists():
            raise BadRequest(
                f"Balapan '{kode}' tidak ada di kumpulan data ini.",
                hint="Daftar lengkapnya ada di /api/politics/electindex/races.",
            )

        frame = pd.read_csv(path)
        keep = [c for c in self.RACE_COLUMNS if c in frame.columns]
        return frame[keep] if keep else frame


class OpenElections:
    """Standardised historical US election results — the base rate layer.

    Not pip-installable; the project publishes per-state CSV repos.
    """

    def __init__(self, data_dir: Path = OPENELECTIONS_DIR):
        self.dir = Path(data_dir)
        if not self.dir.exists():
            from src.core.errors import DataNotCloned

            raise DataNotCloned(
                "OpenElections", str(self.dir),
                f"git clone https://github.com/openelections/openelections-core.git {self.dir}",
            )

    def load_state(self, state: str, year: int) -> Any:
        import pandas as pd

        folder = self.dir / "data" / state.lower() / str(year)
        if not folder.exists():
            from src.core.errors import HubError

            raise HubError(f"Tidak ada data untuk {state.upper()} {year}.",
                           hint="Cek struktur folder repo OpenElections.")
        frames = [pd.read_csv(f, low_memory=False) for f in folder.glob("*.csv")]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# GDELT meminta jeda satu permintaan per lima detik. Diambil tujuh: batas yang
# mereka sebutkan diukur di sisi mereka, dan selisih dua detik jauh lebih murah
# daripada satu penalti yang mematikan halaman selama berpuluh menit.
_GDELT_JEDA = 7.0
_gdelt_kunci = threading.Lock()
_gdelt_terakhir = 0.0


class GDELT:
    """GDELT v2 Document API — global news volume and tone.

    The audit's read is right: non-US political markets (resignations, coups,
    ceasefires) are where the mispricing lives, because few people follow them.
    Tone and volume are a fast proxy for "is this story accelerating".
    """

    BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
    MODES = ("artlist", "timelinevol", "timelinetone", "tonechart")

    def _call(self, params: dict) -> Any:
        import requests

        from src.core.errors import RateLimited, UpstreamError, NetworkBlocked

        global _gdelt_terakhir

        # Antre, jangan berebut. Tiga kartu halaman Politics memuat bersamaan;
        # tanpa gerbang ini ketiganya tiba di GDELT dalam satu detik yang sama
        # dan ketiganya ditolak — termasuk yang sebenarnya masih berhak lewat.
        with _gdelt_kunci:
            sisa = _GDELT_JEDA - (time.monotonic() - _gdelt_terakhir)
            if sisa > 0:
                time.sleep(sisa)
            _gdelt_terakhir = time.monotonic()

        try:
            resp = requests.get(
                self.BASE,
                params=params,
                # Terukur 11 September 2026: satu panggilan yang berhasil memakan
                # 37 detik. Dengan batas 30 detik hasilnya dilaporkan sebagai
                # "diblokir ISP" — salah diagnosis, dan menyuruh orang mencari
                # masalah di jaringannya padahal GDELT-nya saja yang lambat.
                timeout=75,
                headers={"User-Agent": "BloombergHub/1.0 (research)"},
            )
        except requests.exceptions.Timeout:
            raise NetworkBlocked("api.gdeltproject.org", "timeout") from None
        except requests.exceptions.ConnectionError:
            raise NetworkBlocked("api.gdeltproject.org", "tidak terhubung") from None

        if resp.status_code == 429:
            raise RateLimited("GDELT", 300)
        if resp.status_code >= 400:
            raise UpstreamError("GDELT", resp.text[:200], resp.status_code)

        text = resp.text.strip()
        if not text:
            return {}
        if not text.startswith(("{", "[")):
            # GDELT answers rate limits and bad queries with plain prose.
            if "rate limit" in text.lower() or "too many" in text.lower():
                # GDELT membalas 200 dengan prosa saat membatasi; ini tetap 429.
                raise RateLimited("GDELT", 300)
            raise UpstreamError("GDELT", text[:200], resp.status_code)
        try:
            return resp.json()
        except ValueError:
            raise UpstreamError("GDELT", "respons bukan JSON valid") from None

    def articles(self, query: str, max_records: int = 50, timespan: str = "7d") -> list[dict]:
        """Matching articles with source, tone-bearing title and language."""
        data = self._call({
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": max(1, min(250, max_records)),
            "timespan": timespan,
            "sort": "hybridrel",
        })
        return [
            {
                "title": a.get("title"),
                "url": a.get("url"),
                "domain": a.get("domain"),
                "language": a.get("language"),
                "country": a.get("sourcecountry"),
                "seen": a.get("seendate"),
            }
            for a in (data.get("articles") or [])
        ]

    def volume_timeline(self, query: str, timespan: str = "30d") -> list[dict]:
        """Share of global coverage over time — the acceleration signal."""
        data = self._call({
            "query": query, "mode": "timelinevol", "format": "json", "timespan": timespan,
        })
        series = (data.get("timeline") or [{}])[0].get("data", [])
        return [{"date": p.get("date"), "value": p.get("value")} for p in series]

    def tone_timeline(self, query: str, timespan: str = "30d") -> list[dict]:
        """Average tone over time. Negative = hostile coverage."""
        data = self._call({
            "query": query, "mode": "timelinetone", "format": "json", "timespan": timespan,
        })
        series = (data.get("timeline") or [{}])[0].get("data", [])
        return [{"date": p.get("date"), "tone": p.get("value")} for p in series]

    def query(self, keywords: str, mode: str = "artlist", max_records: int = 50) -> Any:
        """Back-compatible entry point for the old CLI and routes."""
        if mode == "timelinevol":
            return self.volume_timeline(keywords)
        if mode == "timelinetone":
            return self.tone_timeline(keywords)
        return self.articles(keywords, max_records=max_records)


class Congress:
    """US Congress bills and members via the OpenStates API."""

    def __init__(self):
        try:
            from openstates import api
        except ImportError:
            from src.core.errors import MissingDependency

            raise MissingDependency("openstates-python", "openstates") from None
        self.api = api

    def legislators(self, state: str = "us", active: bool = True) -> list:
        return list(self.api.legislators(state=state, active=active))

    def search_bills(self, query: str, jurisdiction: str = "us", per_page: int = 20) -> list:
        return list(self.api.bills(jurisdiction=jurisdiction, q=query, per_page=per_page))
