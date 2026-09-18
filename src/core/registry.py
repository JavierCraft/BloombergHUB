"""Machine-readable source catalog.

This is the pattern the audit singled out as worth copying: `machina-sports/
sports-skills` publishes `skills/catalog.json`, where every skill declares in
data — not in prose — whether it can move money. Twenty-six skills, exactly one
with `money_movement: true`, marked `critical`, in its own package.

The value is that the gate becomes checkable. `assert_readonly()` below is the
enforcement point, and it reads this table rather than trusting a caller.

Fields carry the audit's own findings so the UI can show provenance next to data:
stars, last push, verdict, and — the field the audit says decides everything —
whether the host is reachable from *this* connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# The policy block, transcribed from the pattern in sports-skills/catalog.json.
DEFAULT_POLICY = {
    "default_to_read_only": True,
    "never_handle_private_keys_in_chat": True,
    "never_log_secrets": True,
    "require_confirmation_for": ["financial_execution"],
    "separate_package_for_money_movement": "src/polymarket/client.py",
}

MODE_READ_ONLY = "read_only"
MODE_COMPUTE = "compute"
MODE_EXECUTION = "financial_execution"


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    category: str                      # infra|politics|sports|esports|tech|culture|compute
    mode: str                          # read_only | compute | financial_execution
    upstream: str                      # owner/repo as audited
    verdict: str                       # the audit's one-line judgement
    money_movement: bool = False
    risk: str = "low"                  # none|low|medium|high|critical
    secrets_required: tuple[str, ...] = ()
    module: str = ""                   # import name that must exist
    package: str = ""                  # pip name, when it differs from module
    hosts: tuple[str, ...] = ()
    probe_url: str = ""
    probe_ok_status: tuple[int, ...] = (200,)
    stars: int = 0
    pushed_at: str = ""
    blocked_here: bool = False         # confirmed unreachable in the 6 Sep probe
    probe_timeout: int = 6             # some hosts are slow without being blocked
    ttl: int = 900                     # cache seconds for this source
    notes: str = ""

    @property
    def pip(self) -> str:
        return self.package or self.module

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hosts"] = list(self.hosts)
        d["secrets_required"] = list(self.secrets_required)
        d["probe_ok_status"] = list(self.probe_ok_status)
        d["pip"] = self.pip
        return d


# --------------------------------------------------------------------------
# The catalog. Only green-verdict repos from the audit are wrapped as modules.
# Red ones are recorded in docs/AUDIT_SUMBER_DATA.md and deliberately absent.
# --------------------------------------------------------------------------
SOURCES: tuple[Source, ...] = (
    # ---- compute: always available, no network, cannot fail ----------------
    Source(
        id="betting",
        label="Betting math",
        category="compute",
        mode=MODE_COMPUTE,
        upstream="machina-sports/sports-skills",
        verdict="Pola dipakai — skill `betting` bertanda mode: compute, nol panggilan jaringan",
        risk="none",
        stars=211,
        pushed_at="2026-09-04",
        ttl=0,
        notes="Semua hitungan peluang, ukuran taruhan, dan ketepatan model. Berjalan di komputer ini tanpa internet.",
    ),
    Source(
        id="ml",
        label="Deadline model & paper test",
        category="compute",
        mode=MODE_COMPUTE,
        upstream="scikit-learn/scikit-learn",
        verdict="Dilatih lokal dari pasar yang sudah selesai; dinilai melawan harga pasar",
        module="sklearn",
        package="scikit-learn",
        risk="none",
        ttl=0,
        notes="Model peluang untuk pasar bertenggat dekat, analisis berita (TF-IDF), dan buku besar "
              "paper test. Pelatihan butuh internet untuk mengambil riwayat harga; prediksi berjalan lokal.",
    ),
    Source(
        id="backtest",
        label="Backtest & calibration",
        category="compute",
        mode=MODE_COMPUTE,
        upstream="evan-kolberg/prediction-market-backtesting",
        verdict="Backtest terbaik — 254 file, nol file signing",
        risk="none",
        stars=1188,
        pushed_at="2026-05-16",
        ttl=0,
        notes="Menguji seberapa tepat perkiraan Anda, bukan sekadar untung-rugi. Berjalan tanpa internet.",
    ),

    # ---- infra: on-chain reading is the one Polymarket path still open -----
    Source(
        id="polygon",
        label="Polygon RPC",
        category="infra",
        mode=MODE_READ_ONLY,
        upstream="Polymarket/ctf-exchange-v2",
        verdict="Terbuka — penyelesaian trade Polymarket terjadi di Polygon",
        module="web3",
        hosts=("polygon.publicnode.com",),
        probe_url="https://polygon.publicnode.com",
        probe_ok_status=(200, 405),
        risk="low",
        ttl=30,
        notes="Satu-satunya jalur ke data Polymarket yang masih terbuka dari koneksi ini.",
    ),
    Source(
        id="polymarket_clob",
        label="Polymarket CLOB",
        category="infra",
        mode=MODE_EXECUTION,
        upstream="Polymarket/py-clob-client-v2",
        verdict="Pakai ini — v1 sudah diarsipkan",
        money_movement=True,
        risk="critical",
        secrets_required=("POLY_API_KEY", "POLY_SECRET", "POLY_PASSPHRASE", "POLY_PRIVATE_KEY"),
        module="py_clob_client",
        package="py-clob-client",
        hosts=("clob.polymarket.com", "gamma-api.polymarket.com"),
        probe_url="https://clob.polymarket.com/ok",
        blocked_here=True,
        stars=166,
        pushed_at="2026-08-17",
        ttl=15,
        notes="Terblokir di jaringan ini. Satu-satunya bagian yang boleh menyimpan kunci dompet, dan terkunci secara bawaan.",
    ),

    # ---- politics ----------------------------------------------------------
    Source(
        id="electindex",
        label="ElectIndex 2026 forecast",
        category="politics",
        mode=MODE_READ_ONLY,
        upstream="ElectIndex/26_us_forecast_data",
        verdict="Temuan terbaik — 0 bintang, 547 MB, di-push kemarin",
        risk="low",
        stars=0,
        pushed_at="2026-09-04",
        ttl=3600,
        notes="Kumpulan data mentah sebuah model prakiraan pemilu, sekitar 547 MB. Perlu diunduh dulu. Tanpa lisensi resmi, jadi pakai sebagai bahan riset saja.",
    ),
    Source(
        id="gdelt",
        label="GDELT news tone",
        category="politics",
        mode=MODE_READ_ONLY,
        upstream="alex9smith/gdelt-doc-api",
        verdict="Basi 16 bulan — API-nya stabil, pustakanya tidak dirawat",
        risk="medium",
        hosts=("api.gdeltproject.org",),
        probe_url="https://api.gdeltproject.org/api/v2/doc/doc?query=test&mode=artlist&format=json&maxrecords=1",
        probe_ok_status=(200, 429),
        probe_timeout=20,
        stars=228,
        pushed_at="2025-04-22",
        ttl=3600,
        notes="Volume dan nada pemberitaan dunia. Membatasi permintaan per alamat IP dengan keras — terukur: tiga panggilan berjarak tujuh detik pun ditolak, dan baru pulih setelah diam sekitar tiga menit. Karena itu hasilnya disimpan satu jam dan panggilannya diberi jeda.",
    ),
    Source(
        id="openelections",
        label="OpenElections results",
        category="politics",
        mode=MODE_READ_ONLY,
        upstream="openelections/openelections-core",
        verdict="Base rate — hasil pemilu terstandardisasi",
        risk="low",
        stars=193,
        pushed_at="2026-05-20",
        ttl=86400,
        notes="Hasil pemilu masa lalu sebagai pembanding. Perlu diunduh dulu.",
    ),

    # ---- sports ------------------------------------------------------------
    Source(
        id="soccer",
        label="Soccer",
        category="sports",
        mode=MODE_READ_ONLY,
        upstream="probberechts/soccerdata",
        verdict="Pakai ini — 8 sumber, termasuk odds bandar",
        module="soccerdata",
        hosts=("understat.com", "site.api.espn.com"),
        probe_url="https://understat.com/league/EPL",
        risk="low",
        stars=2056,
        pushed_at="2026-08-21",
        ttl=3600,
        notes="Understat dan ESPN bisa dibuka. Tiga penyedia lain tidak, termasuk arsip odds bandar.",
    ),
    Source(
        id="nba",
        label="NBA",
        category="sports",
        mode=MODE_READ_ONLY,
        upstream="swar/nba_api",
        verdict="Pakai ini — API resmi NBA.com",
        module="nba_api",
        hosts=("stats.nba.com",),
        probe_url="https://stats.nba.com/stats/leagueleaders?LeagueID=00&PerMode=PerGame"
                  "&Scope=S&Season=2025-26&SeasonType=Regular+Season&StatCategory=PTS",
        probe_timeout=15,
        risk="low",
        stars=3761,
        pushed_at="2026-08-16",
        ttl=900,
        notes="Klasemen dan statistik pemain bisa dibuka. Skor langsung tidak, karena alamatnya berbeda dan terblokir.",
    ),
    Source(
        id="nfl",
        label="NFL",
        category="sports",
        mode=MODE_READ_ONLY,
        upstream="nflverse/nflreadpy",
        verdict="Pakai yang ini — nfl_data_py sudah diarsipkan",
        module="nflreadpy",
        hosts=("github.com", "raw.githubusercontent.com"),
        probe_url="https://raw.githubusercontent.com/nflverse/nflverse-data/master/README.md",
        risk="low",
        stars=204,
        pushed_at="2026-08-05",
        ttl=3600,
        notes="Jadwal, hasil, dan laporan cedera NFL. Menggantikan paket lama yang sudah diarsipkan pemiliknya.",
    ),
    Source(
        id="statsbomb",
        label="StatsBomb",
        category="sports",
        mode=MODE_READ_ONLY,
        upstream="statsbomb/statsbombpy",
        verdict="Pakai ini — event data sepak bola",
        module="statsbombpy",
        hosts=("raw.githubusercontent.com",),
        probe_url="https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json",
        risk="low",
        stars=741,
        pushed_at="2026-09-01",
        ttl=86400,
        notes="Data rinci setiap kejadian dalam pertandingan. Yang gratis bisa langsung dipakai.",
    ),

    # ---- esports -----------------------------------------------------------
    Source(
        id="dota2",
        label="Dota 2",
        category="esports",
        mode=MODE_READ_ONLY,
        upstream="odota/core",
        verdict="Esports terbaik — API publik, tanpa key",
        hosts=("api.opendota.com",),
        probe_url="https://api.opendota.com/api/heroStats",
        risk="low",
        stars=1628,
        pushed_at="2026-08-21",
        ttl=600,
        notes="Data Dota 2 lengkap tanpa perlu kunci API. Dibatasi 60 permintaan per menit.",
    ),
    Source(
        id="liquipedia",
        label="Esports schedules",
        category="esports",
        mode=MODE_READ_ONLY,
        upstream="c00kie17/liquipediapy",
        verdict="Kecil tapi hidup",
        hosts=("liquipedia.net",),
        probe_url="https://liquipedia.net/dota2/api.php?action=query&format=json",
        risk="medium",
        stars=69,
        pushed_at="2026-04-27",
        ttl=1800,
        notes="Jadwal turnamen Dota 2, CS2, LoL, dan Valorant.",
    ),

    # ---- tech --------------------------------------------------------------
    Source(
        id="huggingface",
        label="Model releases",
        category="tech",
        mode=MODE_READ_ONLY,
        upstream="huggingface/huggingface_hub",
        verdict="Sumber resolusi — deteksi rilis model",
        module="huggingface_hub",
        package="huggingface-hub",
        hosts=("huggingface.co",),
        probe_url="https://huggingface.co/api/models?limit=1",
        risk="low",
        stars=3871,
        pushed_at="2026-09-05",
        ttl=300,
        notes="Mendeteksi model AI baru beberapa menit setelah diunggah, sering sebelum pengumuman resminya.",
    ),

    # ---- culture -----------------------------------------------------------
    # Spotify dan TMDb di bawah tetap dicatat karena kodenya masih ada dan masih
    # jalan bagi yang berlangganan. Tiga di bawah inilah yang dipakai halamannya.
    Source(
        id="deezer",
        label="Music (Deezer)",
        category="culture",
        mode=MODE_READ_ONLY,
        upstream="deezer/api (resmi, tanpa pustaka pihak ketiga)",
        verdict="Pakai ini — API resmi, tanpa kunci sama sekali",
        module="requests",
        hosts=("api.deezer.com",),
        probe_url="https://api.deezer.com/chart/0/tracks?limit=1",
        risk="low",
        pushed_at="2026-09-11",
        ttl=900,
        notes="Pencarian lagu, pencarian musisi, dan tangga lagu global. Tidak perlu mendaftar apa pun.",
    ),
    Source(
        id="tvmaze",
        label="TV shows (TVmaze)",
        category="culture",
        mode=MODE_READ_ONLY,
        upstream="tvmaze/api (resmi, tanpa pustaka pihak ketiga)",
        verdict="Pakai ini — API resmi, tanpa kunci sama sekali",
        module="requests",
        hosts=("api.tvmaze.com",),
        probe_url="https://api.tvmaze.com/shows/1",
        risk="low",
        pushed_at="2026-09-11",
        ttl=900,
        notes="Pencarian serial dan jadwal episode per tanggal. Tidak perlu mendaftar apa pun.",
    ),
    Source(
        id="omdb",
        label="Films (OMDb)",
        category="culture",
        mode=MODE_READ_ONLY,
        upstream="omdbapi.com (resmi, tanpa pustaka pihak ketiga)",
        verdict="Pakai ini — pengganti gratis TMDb, kuncinya gratis",
        module="requests",
        secrets_required=("OMDB_API_KEY",),
        hosts=("www.omdbapi.com",),
        probe_url="https://www.omdbapi.com/?i=tt3896198",
        probe_ok_status=(200, 401),
        risk="low",
        pushed_at="2026-09-11",
        ttl=900,
        notes="Pencarian film dan detail box office. Kuncinya gratis di omdbapi.com/apikey.aspx, batas 1.000 permintaan per hari.",
    ),
    Source(
        id="spotify",
        label="Music (Spotify)",
        category="culture",
        mode=MODE_READ_ONLY,
        upstream="spotipy-dev/spotipy",
        verdict="Ditinggalkan — kunci API kini hanya untuk akun Premium",
        module="spotipy",
        secrets_required=("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"),
        hosts=("accounts.spotify.com", "api.spotify.com"),
        probe_url="https://accounts.spotify.com/api/token",
        probe_ok_status=(200, 400, 405),
        risk="low",
        stars=5471,
        pushed_at="2026-06-29",
        ttl=900,
        notes="Sudah tidak dipakai halaman mana pun sejak 11 September 2026: Spotify mewajibkan langganan Premium untuk membuat kunci API. Digantikan Deezer, yang tidak perlu kunci.",
    ),
    Source(
        id="tmdb",
        label="Movies & TV (TMDb)",
        category="culture",
        mode=MODE_READ_ONLY,
        upstream="celiao/tmdbsimple",
        verdict="Ditinggalkan — akses pengembang berbayar USD 149/bulan",
        module="tmdbsimple",
        secrets_required=("TMDB_API_KEY",),
        hosts=("api.themoviedb.org",),
        probe_url="https://api.themoviedb.org/3/configuration",
        probe_ok_status=(200, 401),
        risk="low",
        stars=698,
        pushed_at="2026-07-30",
        ttl=900,
        notes="Sudah tidak dipakai halaman mana pun sejak 11 September 2026: TMDb menutup akses gratisnya. Digantikan OMDb untuk film dan TVmaze untuk serial.",
    ),
    # ---- berita & pasar prediksi lain --------------------------------------
    Source(
        id="news",
        label="News feeds",
        category="news",
        mode=MODE_READ_ONLY,
        upstream="google/news-rss",
        verdict="Tujuh sumber terbuka, disimpan ke berkas teks",
        hosts=("news.google.com", "feeds.bbci.co.uk", "hn.algolia.com"),
        probe_url="https://hn.algolia.com/api/v1/search?query=test&hitsPerPage=1",
        risk="low",
        ttl=300,
        notes="157 feed dari 16 kategori (dunia, politik, pasar, sumber resmi seperti The Fed dan BLS, "
              "kripto, AI, olahraga, esports, budaya, Indonesia) plus Hacker News — semuanya diuji dari "
              "koneksi ini pada 17 September 2026. Hasilnya disimpan ke data/news sebagai korpus belajar.",
    ),
    Source(
        id="anthropic",
        label="Claude API (analisis berita)",
        category="news",
        mode=MODE_READ_ONLY,
        upstream="anthropics/anthropic-sdk-python",
        verdict="Opsional — membaca judul berita untuk satu pasar dan memberi pendapat terstruktur",
        module="anthropic",
        secrets_required=("ANTHROPIC_API_KEY",),
        hosts=("api.anthropic.com",),
        probe_url="https://api.anthropic.com/v1/models",
        probe_ok_status=(200, 401),
        risk="low",
        ttl=3600,
        notes="Berbayar per pemakaian (Claude Opus 5: $5/$25 per juta token). Tanpa kunci, semua fitur "
              "lain tetap berjalan; analisis berita memakai TF-IDF lokal.",
    ),
    Source(
        id="manifold",
        label="Manifold Markets",
        category="news",
        mode=MODE_READ_ONLY,
        upstream="manifoldmarkets/manifold",
        verdict="Pengganti Polymarket yang bisa dibuka dari sini",
        hosts=("api.manifold.markets",),
        probe_url="https://api.manifold.markets/v0/markets?limit=1",
        risk="low",
        ttl=60,
        notes="Harga, buku pesanan, dan taruhan satu per satu — tanpa kunci. "
              "Dipakai sebagai pembanding karena Polymarket terblokir.",
    ),
    Source(
        id="limitless",
        label="Limitless Exchange",
        category="news",
        mode=MODE_READ_ONLY,
        upstream="limitless-exchange/api",
        verdict="Pasar prediksi uang sungguhan (USDC) yang terbuka dari koneksi ini",
        hosts=("api.limitless.exchange",),
        probe_url="https://api.limitless.exchange/categories",
        risk="low",
        ttl=60,
        notes="603 pasar aktif saat diuji 17 September 2026: sepak bola, esports, politik, kripto, saham "
              "harian. Mengisi panel sektor, radar tenggat, IEV, dan paper test selama Polymarket diblokir. "
              "Baca saja — tidak ada kunci, tidak ada order.",
    ),
    Source(
        id="github",
        label="GitHub",
        category="infra",
        mode=MODE_READ_ONLY,
        upstream="github/rest-api",
        verdict="Terbuka penuh — dasar seluruh audit",
        hosts=("api.github.com",),
        probe_url="https://api.github.com/rate_limit",
        risk="none",
        ttl=3600,
        notes="Dipakai untuk memastikan sebuah sumber masih dirawat sebelum dipasang.",
    ),
)

BY_ID: dict[str, Source] = {s.id: s for s in SOURCES}

CATEGORIES = (
    ("compute", "Compute"),
    ("infra", "Infrastructure"),
    ("politics", "Politics"),
    ("sports", "Sports"),
    ("esports", "Esports"),
    ("tech", "Tech"),
    ("culture", "Culture"),
    ("news", "News & Markets"),
)

# Dipakai antarmuka untuk menampilkan nama kategori pada baris status.
CATEGORY_LABELS = dict(CATEGORIES)


def get(source_id: str) -> Source:
    try:
        return BY_ID[source_id]
    except KeyError:
        raise KeyError(f"Sumber tidak dikenal: {source_id}") from None


def by_category(category: str) -> list[Source]:
    return [s for s in SOURCES if s.category == category]


def money_sources() -> list[Source]:
    """Every source that can move money. Should stay very short."""
    return [s for s in SOURCES if s.money_movement]


def assert_readonly(source_id: str) -> Source:
    """Refuse to serve a money-moving source through a read-only path.

    Called at the top of every data endpoint. The catalog, not the caller,
    decides — that is the whole point of writing the risk metadata down.
    """
    from .errors import TradingDisabled

    src = get(source_id)
    if src.money_movement or src.mode == MODE_EXECUTION:
        raise TradingDisabled(f"membaca '{src.id}' lewat jalur read-only")
    return src


def catalog() -> dict:
    """The whole thing, JSON-ready — served at /api/registry and shown in the UI."""
    return {
        "default_policy": DEFAULT_POLICY,
        "generated_from": "docs/AUDIT_SUMBER_DATA.md",
        "counts": {
            "total": len(SOURCES),
            "money_movement": len(money_sources()),
            "blocked_here": len([s for s in SOURCES if s.blocked_here]),
        },
        "sources": [s.to_dict() for s in SOURCES],
    }
