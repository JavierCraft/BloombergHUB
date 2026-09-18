"""Public data APIs — every one probed from this connection on 17 September 2026.

64 candidates were tried; 43 answered with JSON; the 36 below are wired into a
page. Rejected, with the reason, so nobody re-adds them blind:

  * other prediction markets — Kalshi and SX Bet (certificate mismatch: the ISP
    block page), PredictIt (403), Metaculus (API now needs an account),
    Smarkets (connection reset). Limitless answers and lives in `src/markets`.
  * Binance, Coinbase, Kraken (blocked); ESPN scoreboards (403); Reddit (403);
    Bluesky search (403); GovTrack (blocked); CourtListener (needs a token);
    IDX / Bursa Efek Indonesia (Cloudflare challenge); App Store chart (502).

`sectors` names the pages a source appears on. `good_for` is the learning note:
what the data is useful for when researching a prediction market.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

PROBED_AT = "2026-09-17"

CATEGORIES = {
    "Crypto": "Kripto",
    "FX & commodities": "Kurs & komoditas",
    "Macro": "Makroekonomi",
    "Official": "Dokumen resmi",
    "Attention": "Perhatian publik & tren",
    "AI & tech": "AI & teknologi",
    "Culture": "Budaya & hiburan",
    "Sports": "Jadwal olahraga",
    "Esports": "Esports & game",
    "Disasters & weather": "Bencana & cuaca",
    "Space": "Antariksa",
}

SECTOR_PAGES = ("overview", "news", "politics", "tech", "culture", "sports", "esports")


@dataclass(frozen=True)
class DataSource:
    code: str
    name: str
    category: str
    kind: str
    urls: tuple[str, ...]
    parser: str
    sectors: tuple[str, ...]
    good_for: str
    ttl: int = 900
    caution: str = ""
    docs: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["urls"] = list(self.urls)
        d["sectors"] = list(self.sectors)
        d["category_label"] = CATEGORIES.get(self.category, self.category)
        return d


def S(code, name, category, kind, urls, parser, sectors, good_for, **kw) -> DataSource:  # noqa: N802
    return DataSource(code, name, category, kind, tuple(urls) if isinstance(urls, (list, tuple)) else (urls,),
                      parser, tuple(sectors), good_for, **kw)


HOUR = 3600
GITHUB = "https://api.github.com/repos/{}/releases?per_page=2"
PYPI = "https://pypi.org/pypi/{}/json"
STEAM = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={}"
GOLD = "https://api.gold-api.com/price/{}"
WB = "https://api.worldbank.org/v2/country/IDN/indicator/{}?format=json&per_page=4"

DATA_SOURCES: tuple[DataSource, ...] = (
    # ------------------------------------------------------------------ crypto
    S("coingecko-prices", "CoinGecko · harga kripto", "Crypto", "agregator",
      "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,ripple,dogecoin,binancecoin"
      "&vs_currencies=usd,idr&include_24hr_change=true&include_market_cap=true",
      "coingecko_prices", ("overview", "news"),
      "Harga acuan untuk pasar ambang harga ('BTC di atas $X pada tanggal Y'): jarak harga sekarang ke ambang.",
      ttl=120, docs="https://docs.coingecko.com"),
    S("coingecko-trending", "CoinGecko · koin trending", "Crypto", "agregator",
      "https://api.coingecko.com/api/v3/search/trending", "coingecko_trending", ("news",),
      "Koin yang paling banyak dicari — sinyal awal untuk pasar peluncuran token dan pre-TGE.", ttl=900),
    S("fear-greed", "Crypto Fear & Greed Index", "Crypto", "data",
      "https://api.alternative.me/fng/?limit=7", "fear_greed", ("overview", "news"),
      "Suasana pasar kripto sepekan; pasar prediksi kripto sering bergerak searah sentimen.", ttl=HOUR),
    S("defillama-chains", "DefiLlama · TVL per blockchain", "Crypto", "data",
      "https://api.llama.fi/v2/chains", "defillama_chains", ("news",),
      "Dana yang terkunci per blockchain — konteks untuk pasar tentang ekosistem kripto.", ttl=HOUR),
    # ------------------------------------------------------------ fx & metals
    S("frankfurter", "Frankfurter · kurs ECB", "FX & commodities", "resmi",
      "https://api.frankfurter.app/latest?from=USD&to=IDR,EUR,JPY,GBP,CNY,SGD,AUD", "frankfurter",
      ("overview", "news"), "Kurs referensi Bank Sentral Eropa — termasuk USD/IDR untuk menghitung modal dalam rupiah.",
      ttl=HOUR),
    S("open-er-api", "ExchangeRate-API · kurs harian", "FX & commodities", "agregator",
      "https://open.er-api.com/v6/latest/USD", "open_er_api", ("news",),
      "Kurs kedua sebagai pembanding: selisih antar sumber menunjukkan seberapa pasti angka kurs itu.", ttl=HOUR),
    S("gold-api", "Gold API · emas & perak", "FX & commodities", "agregator",
      [GOLD.format("XAU"), GOLD.format("XAG"), GOLD.format("HG")], "gold_api", ("overview", "news"),
      "Harga emas, perak, tembaga — untuk pasar komoditas dan ukuran 'aset aman'.", ttl=600),
    # ------------------------------------------------------------------ macro
    S("treasury-debt", "U.S. Treasury · utang negara harian", "Macro", "resmi",
      "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny"
      "?sort=-record_date&page[size]=10", "treasury_debt", ("politics", "news"),
      "Sumber resolusi pasar 'utang AS melewati $X triliun'. Angka resmi Departemen Keuangan AS.", ttl=6 * HOUR),
    S("treasury-rates", "U.S. Treasury · rata-rata bunga surat utang", "Macro", "resmi",
      "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates"
      "?sort=-record_date&page[size]=40", "treasury_rates", ("news",),
      "Biaya utang pemerintah AS per jenis surat berharga — latar pasar suku bunga dan The Fed.", ttl=6 * HOUR),
    S("bls-cpi", "U.S. BLS · CPI (inflasi)", "Macro", "resmi",
      "https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0", "bls_cpi", ("politics", "news"),
      "Data resmi CPI-U — sumber resolusi pasar inflasi AS. Inflasi tahunan dihitung dari angkanya.",
      ttl=6 * HOUR, caution="API v1 tanpa kunci dibatasi 25 permintaan per hari; hasil disimpan 6 jam."),
    S("worldbank-idn", "World Bank · indikator Indonesia", "Macro", "resmi",
      [WB.format("NY.GDP.MKTP.CD"), WB.format("FP.CPI.TOTL.ZG"), WB.format("SL.UEM.TOTL.ZS")],
      "worldbank", ("politics", "news"),
      "PDB, inflasi, dan pengangguran Indonesia per tahun — bahan belajar membaca data makro resmi.", ttl=24 * HOUR),
    # --------------------------------------------------------------- official
    S("federal-register-presidential", "Federal Register · dokumen presiden AS", "Official", "resmi",
      "https://www.federalregister.gov/api/v1/documents.json?per_page=20&order=newest"
      "&conditions[type][]=PRESDOCU", "federal_register", ("politics", "news"),
      "Executive order, proklamasi, dan memorandum resmi — sumber resolusi pasar 'berapa executive order'.",
      ttl=HOUR),
    # -------------------------------------------------------------- attention
    S("wikipedia-mostread", "Wikipedia · artikel paling banyak dibaca", "Attention", "data",
      "https://api.wikimedia.org/feed/v1/wikipedia/en/featured/{yesterday}", "wikipedia_mostread",
      ("overview", "news", "politics", "culture"),
      "Apa yang sedang dicari dunia kemarin — perhatian publik sering mendahului pergerakan pasar budaya dan politik.",
      ttl=3 * HOUR),
    S("mastodon-links", "Mastodon · tautan berita trending", "Attention", "komunitas",
      "https://mastodon.social/api/v1/trends/links?limit=20", "mastodon_links", ("news", "politics"),
      "Berita yang paling banyak dibagikan di jaringan sosial terbuka.", ttl=900,
      caution="Pengguna Mastodon condong ke komunitas teknologi dan Eropa; bukan cermin seluruh publik."),
    S("mastodon-tags", "Mastodon · tagar trending", "Attention", "komunitas",
      "https://mastodon.social/api/v1/trends/tags?limit=20", "mastodon_tags", ("news", "culture"),
      "Topik yang sedang ramai dibicarakan, dengan jumlah akun yang memakainya hari ini.", ttl=900),
    # ------------------------------------------------------------- ai & tech
    S("openrouter-models", "OpenRouter · model LLM terbaru", "AI & tech", "agregator",
      "https://openrouter.ai/api/v1/models", "openrouter_models", ("tech",),
      "Daftar model LLM yang baru bisa dipakai lewat API, lengkap dengan harga dan panjang konteks — pelacak rilis tercepat.",
      ttl=HOUR),
    S("hf-daily-papers", "Hugging Face · makalah harian", "AI & tech", "riset",
      "https://huggingface.co/api/daily_papers?limit=30", "hf_papers", ("tech",),
      "Makalah AI yang dipilih komunitas hari ini — bahan belajar dan sinyal teknik baru.", ttl=HOUR),
    S("github-releases", "GitHub · rilis alat LLM", "AI & tech", "resmi",
      [GITHUB.format("ollama/ollama"), GITHUB.format("ggml-org/llama.cpp"), GITHUB.format("vllm-project/vllm"),
       GITHUB.format("huggingface/transformers")], "github_releases", ("tech",),
      "Versi terbaru alat untuk menjalankan LLM — dukungan model baru biasanya muncul di sini lebih dulu.",
      ttl=HOUR, caution="GitHub tanpa token dibatasi 60 permintaan per jam; hasil disimpan sejam."),
    S("pypi-llm-sdks", "PyPI · versi SDK LLM", "AI & tech", "resmi",
      [PYPI.format("anthropic"), PYPI.format("openai"), PYPI.format("google-genai"), PYPI.format("mistralai")],
      "pypi_versions", ("tech",),
      "Rilis SDK resmi Anthropic, OpenAI, Google, Mistral — fitur API baru terlihat dari versi SDK.", ttl=HOUR),
    S("lobsters", "Lobsters · diskusi teknologi", "AI & tech", "komunitas",
      "https://lobste.rs/hottest.json", "lobsters", ("tech",),
      "Topik teknologi yang sedang didiskusikan insinyur.", ttl=900),
    S("devto", "DEV Community · artikel populer", "AI & tech", "komunitas",
      "https://dev.to/api/articles?per_page=20&top=1", "devto", ("tech",),
      "Artikel pengembang paling populer hari ini.", ttl=HOUR),
    # ---------------------------------------------------------------- culture
    S("apple-music", "Apple Music · lagu paling banyak diputar (AS)", "Culture", "resmi",
      "https://rss.applemarketingtools.com/api/v2/us/music/most-played/25/songs.json", "apple_chart",
      ("culture",), "Tangga lagu resmi Apple — pembanding untuk pasar 'lagu nomor satu' dan tangga lagu.",
      ttl=HOUR),
    S("apple-podcasts", "Apple Podcasts · teratas (AS)", "Culture", "resmi",
      "https://rss.applemarketingtools.com/api/v2/us/podcasts/top/25/podcasts.json", "apple_chart",
      ("culture",), "Podcast paling populer — perhatian publik pada tokoh dan topik.", ttl=HOUR),
    S("itunes-movies", "iTunes · film teratas (AS)", "Culture", "resmi",
      "https://itunes.apple.com/us/rss/topmovies/limit=25/json", "itunes_movies", ("culture",),
      "Film yang paling banyak dibeli/disewa — sinyal permintaan di luar box office bioskop.", ttl=HOUR),
    # ----------------------------------------------------------------- sports
    S("thesportsdb-epl", "TheSportsDB · jadwal Premier League", "Sports", "komunitas",
      "https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php?id=4328", "thesportsdb", ("sports",),
      "Jadwal pertandingan Liga Inggris berikutnya untuk mencocokkan pasar pertandingan.", ttl=HOUR),
    S("openligadb-bundesliga", "OpenLigaDB · Bundesliga", "Sports", "komunitas",
      "https://api.openligadb.de/getmatchdata/bl1", "openligadb", ("sports",),
      "Jadwal dan skor Bundesliga pekan ini — termasuk hasil untuk memeriksa resolusi.", ttl=900),
    S("jolpica-f1", "Jolpica (Ergast) · Formula 1", "Sports", "komunitas",
      "https://api.jolpi.ca/ergast/f1/current/next.json", "jolpica_f1", ("sports",),
      "Balapan F1 berikutnya dan jadwal sesinya — pasar pemenang balapan.", ttl=HOUR),
    S("nhl-schedule", "NHL · jadwal pekan ini", "Sports", "resmi",
      "https://api-web.nhle.com/v1/schedule/now", "nhl_schedule", ("sports",),
      "Jadwal resmi NHL dari API liga.", ttl=HOUR),
    S("mlb-schedule", "MLB · jadwal & skor hari ini", "Sports", "resmi",
      "https://statsapi.mlb.com/api/v1/schedule?sportId=1", "mlb_schedule", ("sports",),
      "Pertandingan dan skor MLB hari ini dari API resmi liga.", ttl=600),
    # ---------------------------------------------------------------- esports
    S("steam-players", "Steam · pemain online per game", "Esports", "resmi",
      [STEAM.format(730), STEAM.format(570), STEAM.format(578080), STEAM.format(1172470), STEAM.format(252490)],
      "steam_players", ("esports",),
      "Jumlah pemain CS2, Dota 2, PUBG, Apex, dan Rust saat ini — ukuran kesehatan komunitas game esports.",
      ttl=900),
    # ---------------------------------------------------- disasters & weather
    S("usgs-quakes", "USGS · gempa M4.5+ 24 jam", "Disasters & weather", "resmi",
      "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson", "usgs_quakes", ("news",),
      "Sumber resolusi pasar gempa: magnitudo, lokasi, kedalaman, dan peringatan tsunami.", ttl=600),
    S("bmkg-gempa", "BMKG · gempa Indonesia", "Disasters & weather", "resmi",
      ["https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json", "https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json"],
      "bmkg_gempa", ("overview", "news"),
      "Gempa terkini dan gempa M5+ dari BMKG — sumber resmi Indonesia, termasuk potensi tsunami.", ttl=600),
    S("gdacs", "GDACS · peringatan bencana global", "Disasters & weather", "resmi",
      "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP", "gdacs", ("news",),
      "Siklon, banjir, gempa, dan letusan dengan tingkat peringatan PBB/Komisi Eropa.", ttl=HOUR),
    S("nasa-eonet", "NASA EONET · peristiwa alam", "Disasters & weather", "resmi",
      "https://eonet.gsfc.nasa.gov/api/v3/events?limit=25&status=open", "eonet", ("news",),
      "Kebakaran hutan, badai, gunung api yang sedang berlangsung, dipantau satelit NASA.", ttl=HOUR),
    S("open-meteo", "Open-Meteo · prakiraan suhu kota", "Disasters & weather", "data",
      "https://api.open-meteo.com/v1/forecast?latitude=40.71,51.51,35.68,-6.21,37.57&longitude=-74.01,-0.13,139.69,"
      "106.85,126.98&daily=temperature_2m_max,precipitation_sum&timezone=auto&forecast_days=3",
      "open_meteo", ("news",),
      "Prakiraan suhu maksimum New York, London, Tokyo, Jakarta, Seoul — Polymarket punya pasar suhu harian kota.",
      ttl=HOUR),
    # ------------------------------------------------------------------ space
    S("launch-library", "Launch Library · peluncuran roket", "Space", "komunitas",
      "https://ll.thespacedevs.com/2.3.0/launches/upcoming/?limit=15", "launches", ("tech", "news"),
      "Jadwal peluncuran SpaceX dan lainnya — pasar jumlah dan keberhasilan peluncuran.",
      ttl=2 * HOUR, caution="Tanpa kunci dibatasi 15 permintaan per jam; hasil disimpan 2 jam."),
)

BY_CODE = {s.code: s for s in DATA_SOURCES}


def for_page(page: str) -> list[DataSource]:
    return [s for s in DATA_SOURCES if page in s.sectors]
