"""Katalog sumber berita — semuanya diuji dari koneksi ini.

Setiap feed di bawah lolos probe pada 17 September 2026: dijawab 200, bisa
diurai sebagai RSS/Atom, dan memuat minimal satu item. Dari 188 kandidat yang
diuji, 157 dipasang di sini. Yang tidak lolos dicatat di `REJECTED` beserta
alasannya, supaya tidak ada yang menambahkannya lagi tanpa sadar:

  * menolak koneksi ini — Reuters (404), AP (403), Yahoo Finance (timeout),
    Metaculus (403), Science (403), Times of Israel (403), IMF (403);
  * alamat feed tidak ada lagi — Anthropic, Mistral, LMSYS, Goal, White House,
    U.S. Treasury, Jakarta Post, Liputan6, Suara;
  * masih menjawab tetapi basi — CNN (berhenti 2023), WHO dan Calculated Risk
    (item terbaru berumur lebih dari 200 hari). Feed basi lebih berbahaya dari
    feed mati: ia tampak hidup.

Kolom `good_for` adalah bagian "untuk belajar": satu kalimat tentang apa
gunanya sumber itu untuk riset pasar prediksi. `kind` membedakan sumber
primer (resmi, lab) dari media dan agregator — pasar diselesaikan oleh sumber
primer, bukan oleh media yang mengutipnya.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

GN_SEARCH_EN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
GN_SEARCH_ID = "https://news.google.com/rss/search?q={q}&hl=id&gl=ID&ceid=ID:id"
GN_TOPIC = "https://news.google.com/rss/headlines/section/topic/{t}?hl=en-US&gl=US&ceid=US:en"
CNBC = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id={id}"

PROBED_AT = "2026-09-17"

CATEGORIES: dict[str, str] = {
    "World": "Kabar dunia",
    "US": "Amerika Serikat",
    "Politics": "Politik & pemilu",
    "Markets": "Pasar & bisnis",
    "Macro": "Makroekonomi",
    "Official": "Sumber resmi & data primer",
    "Crypto": "Kripto",
    "Tech": "Teknologi",
    "AI": "AI & LLM",
    "Science": "Sains & kesehatan",
    "Sports": "Olahraga",
    "Esports": "Esports & game",
    "Culture": "Budaya & hiburan",
    "Asia": "Asia",
    "Indonesia": "Indonesia",
    "Forecasting": "Forecasting & pasar prediksi",
}

KINDS: dict[str, str] = {
    "resmi": "Lembaga yang menerbitkan data atau keputusan itu sendiri — sering jadi sumber resolusi.",
    "lab": "Pengumuman langsung dari pembuat model/produk.",
    "media": "Redaksi berita yang meliput dan mengutip sumber primer.",
    "agregator": "Mengumpulkan judul dari banyak media; cepat tetapi tanpa penyuntingan sendiri.",
    "analisis": "Blog/newsletter analisis; berguna untuk kerangka berpikir, bukan fakta pertama.",
    "riset": "Makalah dan jurnal; lambat tetapi paling dalam.",
    "komunitas": "Diskusi komunitas; sinyal awal yang belum terverifikasi.",
}

# Sector monitor pages ask for categories, not for the literal word "politics"
# inside a headline. The old behaviour filtered titles by the sector's name.
SECTOR_CATEGORIES: dict[str, tuple[str, ...]] = {
    "politics": ("Politics", "US", "World", "Official"),
    "technology": ("Tech", "AI"),
    "culture": ("Culture",),
    "soccer": ("Sports",),
    "sports": ("Sports",),
    "esports": ("Esports",),
    "markets": ("Markets", "Macro", "Official", "Crypto"),
    "crypto": ("Crypto",),
    "science": ("Science",),
    "indonesia": ("Indonesia",),
    "forecasting": ("Forecasting",),
}


@dataclass(frozen=True)
class Feed:
    code: str
    name: str
    category: str
    lang: str
    kind: str
    url: str
    good_for: str
    search_url: str = ""      # template with {q}; empty = not keyword-searchable
    core: bool = False        # part of the default mix on /news
    max_items: int = 40       # some feeds carry hundreds of items; keep the mix balanced
    ttl: int = 300            # seconds a fetched copy stays fresh
    caution: str = ""         # known slant or reliability issue, said out loud

    @property
    def searchable(self) -> bool:
        return bool(self.search_url)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["searchable"] = self.searchable
        d["category_label"] = CATEGORIES.get(self.category, self.category)
        d["kind_label"] = KINDS.get(self.kind, "")
        return d


def F(code, name, category, lang, kind, url, good_for, **kw) -> Feed:  # noqa: N802 — table helper
    return Feed(code, name, category, lang, kind, url, good_for, **kw)


SLOW = 1800   # official releases and blogs publish a few times a day at most
DAILY = 3600

FEEDS_CATALOG: tuple[Feed, ...] = (
    # ------------------------------------------------------------ general/world
    F("google", "Google News", "World", "en", "agregator",
      "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
      "Titik awal tercepat: judul dari ribuan media, dan satu-satunya yang bisa dicari dengan kata kunci apa pun.",
      search_url=GN_SEARCH_EN, core=True, ttl=180),
    F("gn-world", "Google News · World", "World", "en", "agregator", GN_TOPIC.format(t="WORLD"),
      "Rangkuman dunia yang sudah dikelompokkan Google per peristiwa.", ttl=180),
    F("gn-nation", "Google News · U.S.", "US", "en", "agregator", GN_TOPIC.format(t="NATION"),
      "Kabar dalam negeri AS — bahan utama pasar politik Polymarket.", ttl=180),
    F("bbc", "BBC World", "World", "en", "media", "https://feeds.bbci.co.uk/news/world/rss.xml",
      "Liputan dunia yang relatif netral; bagus untuk memastikan sebuah peristiwa memang terjadi.", core=True),
    F("aljazeera", "Al Jazeera", "World", "en", "media", "https://www.aljazeera.com/xml/rss/all.xml",
      "Kuat di Timur Tengah dan konflik — pasar perang dan gencatan senjata.", core=True),
    F("npr", "NPR", "US", "en", "media", "https://feeds.npr.org/1001/rss.xml",
      "Kabar AS yang tenang, cocok untuk konteks kebijakan.", core=True),
    F("guardian-world", "The Guardian · World", "World", "en", "media", "https://www.theguardian.com/world/rss",
      "Liputan dunia yang dalam, sering memuat latar belakang."),
    F("guardian-us", "The Guardian · US", "US", "en", "media", "https://www.theguardian.com/us-news/rss",
      "Politik dan kebijakan AS dari sudut pandang luar negeri."),
    F("nyt-world", "New York Times · World", "World", "en", "media",
      "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
      "Liputan dunia dari salah satu redaksi terbesar AS."),
    F("wapo-world", "Washington Post · World", "World", "en", "media", "https://feeds.washingtonpost.com/rss/world",
      "Liputan dunia; lambat dijawab dari sini (±10 detik).", ttl=900),
    F("wsj-world", "WSJ · World", "World", "en", "media",
      "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
      "Kabar dunia dengan sudut pandang pasar dan bisnis."),
    F("dw", "Deutsche Welle", "World", "en", "media", "https://rss.dw.com/rdf/rss-en-all",
      "Eropa dan dunia dari media publik Jerman."),
    F("france24", "France 24", "World", "en", "media", "https://www.france24.com/en/rss",
      "Eropa, Afrika, dan Timur Tengah dari media publik Prancis."),
    F("euronews", "Euronews", "World", "en", "media",
      "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
      "Politik Uni Eropa — pemilu dan kebijakan Eropa."),
    F("sky-world", "Sky News · World", "World", "en", "media", "https://feeds.skynews.com/feeds/rss/world.xml",
      "Kabar cepat dari Inggris dan dunia."),
    F("independent", "The Independent · World", "World", "en", "media",
      "https://www.independent.co.uk/news/world/rss", "Volume tinggi, banyak kabar kilat."),
    F("abc-au", "ABC Australia", "World", "en", "media", "https://www.abc.net.au/news/feed/51120/rss.xml",
      "Asia-Pasifik dari media publik Australia."),
    F("un", "UN News", "World", "en", "resmi", "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
      "Pernyataan dan laporan PBB — resolusi, sanksi, krisis kemanusiaan.", ttl=SLOW),
    F("moscowtimes", "The Moscow Times", "World", "en", "media", "https://www.themoscowtimes.com/rss/news",
      "Rusia dari media independen — pasar perang Rusia-Ukraina."),
    F("kyivindependent", "The Kyiv Independent", "World", "en", "media",
      "https://kyivindependent.com/news-archive/rss/", "Perang dari sudut pandang Ukraina.",
      caution="Pihak dalam konflik; bandingkan dengan sumber lain."),
    F("mee", "Middle East Eye", "World", "en", "media", "https://www.middleeasteye.net/rss",
      "Timur Tengah — Gaza, Iran, Suriah, Teluk."),
    # ---------------------------------------------------------------- US & politics
    F("abc", "ABC News", "US", "en", "media", "https://abcnews.go.com/abcnews/topstories",
      "Berita utama AS dari jaringan TV besar."),
    F("cbs", "CBS News", "US", "en", "media", "https://www.cbsnews.com/latest/rss/main",
      "Berita utama AS dari jaringan TV besar."),
    F("nbc", "NBC News", "US", "en", "media", "https://feeds.nbcnews.com/nbcnews/public/news",
      "Berita utama AS dari jaringan TV besar."),
    F("fox", "Fox News", "US", "en", "media", "https://moxie.foxnews.com/google-publisher/latest.xml",
      "Kabar AS dari media yang dekat dengan basis pemilih Partai Republik.",
      caution="Condong kanan; baca berdampingan dengan media lain."),
    F("fox-politics", "Fox News · Politics", "Politics", "en", "media",
      "https://moxie.foxnews.com/google-publisher/politics.xml",
      "Politik AS dari sudut pandang kanan — berguna untuk membaca sentimen basis Republik.",
      caution="Condong kanan."),
    F("nyt-politics", "New York Times · Politics", "Politics", "en", "media",
      "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
      "Politik AS, Kongres, Gedung Putih.", core=True),
    F("wapo-politics", "Washington Post · Politics", "Politics", "en", "media",
      "https://feeds.washingtonpost.com/rss/politics", "Politik Washington; lambat dijawab dari sini.", ttl=900),
    F("politico", "Politico", "Politics", "en", "media", "https://rss.politico.com/politics-news.xml",
      "Detail proses legislasi dan kampanye — sering lebih dulu dari media umum.", core=True),
    F("thehill", "The Hill", "Politics", "en", "media", "https://thehill.com/news/feed/",
      "Kongres dan survei; banyak kabar kecil yang menggerakkan pasar politik."),
    F("axios", "Axios", "Politics", "en", "media", "https://api.axios.com/feed/",
      "Kabar singkat dan cepat tentang politik, bisnis, dan teknologi.", core=True, max_items=40),
    F("rollcall", "Roll Call", "Politics", "en", "media", "https://rollcall.com/feed/",
      "Kongres AS — jadwal voting dan RUU."),
    F("scotusblog", "SCOTUSblog", "Politics", "en", "analisis", "https://www.scotusblog.com/feed/",
      "Mahkamah Agung AS — putusan yang sering jadi pasar prediksi.", ttl=SLOW),
    F("ballotpedia", "Ballotpedia News", "Politics", "en", "analisis", "https://news.ballotpedia.org/feed/",
      "Kalender pemilu, kandidat, dan hasil — bagus untuk resolusi pasar pemilu.", ttl=SLOW),
    F("semafor", "Semafor", "Politics", "en", "media", "https://www.semafor.com/rss.xml",
      "Politik dan bisnis global dengan analisis singkat.", max_items=40),
    F("natesilver", "Silver Bulletin (Nate Silver)", "Politics", "en", "analisis", "https://www.natesilver.net/feed",
      "Model dan survei pemilu dari pembuat FiveThirtyEight — pembanding langsung harga pasar.", ttl=SLOW),
    F("cnbc-politics", "CNBC · Politics", "Politics", "en", "media", CNBC.format(id=10000113),
      "Politik dengan dampak pasar keuangan."),
    F("bloomberg-politics", "Bloomberg · Politics", "Politics", "en", "media",
      "https://feeds.bloomberg.com/politics/news.rss", "Politik global dari sudut pandang pasar."),
    # ------------------------------------------------------------------- markets
    F("cnbc", "CNBC", "Markets", "en", "media", CNBC.format(id=100003114),
      "Berita pasar AS sepanjang hari.", core=True),
    F("cnbc-finance", "CNBC · Finance", "Markets", "en", "media", CNBC.format(id=10000664),
      "Bank, suku bunga, dan pasar modal."),
    F("gn-business", "Google News · Business", "Markets", "en", "agregator", GN_TOPIC.format(t="BUSINESS"),
      "Rangkuman bisnis dari banyak media sekaligus.", ttl=180),
    F("wsj-markets", "WSJ · Markets", "Markets", "en", "media",
      "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
      "Pasar saham, obligasi, dan komoditas dari Wall Street Journal.", core=True),
    F("marketwatch", "MarketWatch · Top stories", "Markets", "en", "media",
      "https://feeds.content.dowjones.io/public/rss/mw_topstories", "Pasar dan keuangan pribadi."),
    F("bloomberg-markets", "Bloomberg · Markets", "Markets", "en", "media",
      "https://feeds.bloomberg.com/markets/news.rss", "Pasar global — kabar yang menggerakkan harga.", core=True),
    F("ft", "Financial Times", "Markets", "en", "media", "https://www.ft.com/rss/home",
      "Keuangan global dan kebijakan ekonomi."),
    F("investing", "Investing.com", "Markets", "en", "media", "https://www.investing.com/rss/news.rss",
      "Kabar pasar kilat, kalender ekonomi."),
    F("seekingalpha", "Seeking Alpha · Market Currents", "Markets", "en", "media",
      "https://seekingalpha.com/market_currents.xml", "Kabar emiten dan pasar dalam hitungan menit."),
    F("fortune", "Fortune", "Markets", "en", "media", "https://fortune.com/feed/", "Bisnis dan perusahaan besar."),
    F("businessinsider", "Business Insider", "Markets", "en", "media",
      "https://feeds.businessinsider.com/custom/all", "Bisnis, teknologi, dan pasar."),
    F("nasdaq", "Nasdaq · Markets", "Markets", "en", "media",
      "https://www.nasdaq.com/feed/rssoutbound?category=Markets", "Pasar saham AS dan emiten."),
    F("nyt-business", "New York Times · Business", "Markets", "en", "media",
      "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "Bisnis dan ekonomi AS."),
    F("guardian-biz", "The Guardian · Business", "Markets", "en", "media",
      "https://www.theguardian.com/uk/business/rss", "Bisnis Inggris dan global."),
    F("zerohedge", "ZeroHedge", "Markets", "en", "analisis", "https://feeds.feedburner.com/zerohedge/feed",
      "Cepat mengangkat isu pasar yang belum diliput media besar.",
      caution="Sensasional dan partisan; jangan dipakai sebagai satu-satunya konfirmasi."),
    # --------------------------------------------------------------------- macro
    F("bloomberg-economics", "Bloomberg · Economics", "Macro", "en", "media",
      "https://feeds.bloomberg.com/economics/news.rss", "Data ekonomi, bank sentral, inflasi."),
    F("cnbc-economy", "CNBC · Economy", "Macro", "en", "media", CNBC.format(id=20910258),
      "Rilis data ekonomi AS dan reaksi pasar."),
    F("economist-finance", "The Economist · Finance & economics", "Macro", "en", "analisis",
      "https://www.economist.com/finance-and-economics/rss.xml",
      "Analisis ekonomi mendalam — kerangka berpikir, bukan kabar kilat.", ttl=SLOW),
    F("fredblog", "FRED Blog (St. Louis Fed)", "Macro", "en", "analisis", "https://fredblog.stlouisfed.org/feed/",
      "Cara membaca data ekonomi resmi dengan grafik — bahan belajar yang baik.", ttl=DAILY),
    # ------------------------------------------------------------------ official
    F("fed-monetary", "Federal Reserve · Monetary policy", "Official", "en", "resmi",
      "https://www.federalreserve.gov/feeds/press_monetary.xml",
      "Keputusan FOMC dan pernyataan kebijakan — sumber resolusi pasar suku bunga The Fed.",
      core=True, ttl=600),
    F("fed-all", "Federal Reserve · Press releases", "Official", "en", "resmi",
      "https://www.federalreserve.gov/feeds/press_all.xml",
      "Semua rilis The Fed: kebijakan, regulasi bank, stress test.", ttl=600),
    F("fed-speeches", "Federal Reserve · Speeches", "Official", "en", "resmi",
      "https://www.federalreserve.gov/feeds/speeches.xml",
      "Pidato pejabat The Fed — petunjuk arah suku bunga sebelum rapat.", ttl=SLOW),
    F("ecb", "European Central Bank · Press", "Official", "en", "resmi", "https://www.ecb.europa.eu/rss/press.html",
      "Keputusan suku bunga ECB dan pernyataan resmi.", ttl=SLOW),
    F("boe", "Bank of England · News", "Official", "en", "resmi", "https://www.bankofengland.co.uk/rss/news",
      "Keputusan suku bunga Bank of England.", ttl=SLOW),
    F("bls", "U.S. BLS · Latest releases", "Official", "en", "resmi", "https://www.bls.gov/feed/bls_latest.rss",
      "Rilis CPI, lapangan kerja, dan upah — sumber resolusi pasar inflasi & tenaga kerja.", ttl=SLOW),
    F("bea", "U.S. BEA · Releases", "Official", "en", "resmi", "https://apps.bea.gov/rss/rss.xml",
      "PDB dan PCE — sumber resolusi pasar pertumbuhan ekonomi AS.", ttl=SLOW),
    F("sec", "SEC · Press releases", "Official", "en", "resmi", "https://www.sec.gov/news/pressreleases.rss",
      "Keputusan SEC — ETF kripto, penegakan hukum, aturan pasar.", ttl=SLOW),
    F("cftc", "CFTC · Press releases", "Official", "en", "resmi", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
      "Regulator derivatif dan pasar prediksi di AS.", ttl=SLOW),
    F("cdc", "CDC · Newsroom", "Official", "en", "resmi", "https://tools.cdc.gov/api/v2/resources/media/316422.rss",
      "Wabah dan kesehatan masyarakat AS — pasar outbreak.", ttl=SLOW),
    F("nhc", "NOAA National Hurricane Center", "Official", "en", "resmi", "https://www.nhc.noaa.gov/index-at.xml",
      "Status badai Atlantik — sumber resolusi pasar badai.", ttl=900),
    F("usgs-quakes", "USGS · Earthquakes M4.5+", "Official", "en", "resmi",
      "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.atom",
      "Gempa ≥ M4.5 sepekan terakhir — sumber resolusi pasar gempa.", ttl=900, max_items=30),
    F("nasa", "NASA · News releases", "Science", "en", "resmi", "https://www.nasa.gov/news-release/feed/",
      "Peluncuran dan misi — pasar luar angkasa.", ttl=SLOW),
    # -------------------------------------------------------------------- crypto
    F("coindesk", "CoinDesk", "Crypto", "en", "media", "https://www.coindesk.com/arc/outboundfeeds/rss/",
      "Harga kripto, ETF, dan regulasi.", core=True),
    F("cointelegraph", "Cointelegraph", "Crypto", "en", "media", "https://cointelegraph.com/rss",
      "Kabar kripto bervolume tinggi."),
    F("decrypt", "Decrypt", "Crypto", "en", "media", "https://decrypt.co/feed", "Kripto, Web3, dan AI."),
    F("theblock", "The Block", "Crypto", "en", "media", "https://www.theblock.co/rss.xml",
      "Data dan kabar industri kripto."),
    F("bitcoinmagazine", "Bitcoin Magazine", "Crypto", "en", "media", "https://bitcoinmagazine.com/.rss/full/",
      "Khusus Bitcoin.", caution="Media advokasi Bitcoin."),
    # ---------------------------------------------------------------------- tech
    F("gn-tech", "Google News · Technology", "Tech", "en", "agregator", GN_TOPIC.format(t="TECHNOLOGY"),
      "Rangkuman teknologi dari banyak media.", ttl=180),
    F("techcrunch", "TechCrunch", "Tech", "en", "media", "https://techcrunch.com/feed/",
      "Startup, pendanaan, dan peluncuran produk.", core=True),
    F("verge", "The Verge", "Tech", "en", "media", "https://www.theverge.com/rss/index.xml",
      "Produk dan platform teknologi konsumen.", core=True),
    F("arstechnica", "Ars Technica", "Tech", "en", "media", "https://feeds.arstechnica.com/arstechnica/index",
      "Liputan teknis yang cermat."),
    F("wired", "Wired", "Tech", "en", "media", "https://www.wired.com/feed/rss", "Teknologi dan dampaknya."),
    F("engadget", "Engadget", "Tech", "en", "media", "https://www.engadget.com/rss.xml", "Gadget dan produk."),
    F("theregister", "The Register", "Tech", "en", "media", "https://www.theregister.com/headlines.atom",
      "TI perusahaan, keamanan, dan cloud."),
    F("zdnet", "ZDNET", "Tech", "en", "media", "https://www.zdnet.com/news/rss.xml", "Teknologi bisnis."),
    F("ieee", "IEEE Spectrum", "Tech", "en", "riset", "https://spectrum.ieee.org/feeds/feed.rss",
      "Rekayasa dan riset teknologi.", ttl=SLOW),
    F("techmeme", "Techmeme", "Tech", "en", "agregator", "https://www.techmeme.com/feed.xml",
      "Peristiwa teknologi terpenting yang sedang dibicarakan — penentu 'apa yang besar hari ini'."),
    F("9to5mac", "9to5Mac", "Tech", "en", "media", "https://9to5mac.com/feed/", "Apple — peluncuran dan rumor.",
      max_items=30),
    F("cnbc-tech", "CNBC · Technology", "Tech", "en", "media", CNBC.format(id=19854910),
      "Big Tech dan dampaknya ke pasar."),
    F("bloomberg-tech", "Bloomberg · Technology", "Tech", "en", "media",
      "https://feeds.bloomberg.com/technology/news.rss", "Big Tech, chip, dan AI dari sudut pandang pasar."),
    F("nyt-tech", "New York Times · Technology", "Tech", "en", "media",
      "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "Teknologi dan kebijakannya."),
    F("guardian-tech", "The Guardian · Technology", "Tech", "en", "media",
      "https://www.theguardian.com/uk/technology/rss", "Teknologi dan regulasi."),
    # ------------------------------------------------------------------ AI & LLM
    F("openai", "OpenAI · News", "AI", "en", "lab", "https://openai.com/news/rss.xml",
      "Pengumuman model dan produk OpenAI langsung dari sumbernya.", ttl=900, max_items=30),
    F("deepmind", "Google DeepMind · Blog", "AI", "en", "lab", "https://deepmind.google/blog/rss.xml",
      "Riset dan rilis model Gemini dari DeepMind.", ttl=900, max_items=30),
    F("google-ai", "Google · AI blog", "AI", "en", "lab", "https://blog.google/technology/ai/rss/",
      "Peluncuran produk AI Google.", ttl=900),
    F("hf-blog", "Hugging Face · Blog", "AI", "en", "lab", "https://huggingface.co/blog/feed.xml",
      "Rilis model terbuka dan tutorial.", ttl=900, max_items=30),
    F("nvidia", "NVIDIA · Blog", "AI", "en", "lab", "https://blogs.nvidia.com/feed/",
      "Chip, pusat data, dan model NVIDIA.", ttl=SLOW),
    F("meta-eng", "Meta · Engineering", "AI", "en", "lab", "https://engineering.fb.com/feed/",
      "Rekayasa dan riset AI Meta.", ttl=DAILY),
    F("aws-ml", "AWS · Machine Learning blog", "AI", "en", "lab",
      "https://aws.amazon.com/blogs/machine-learning/feed/", "Model yang tersedia di AWS Bedrock.", ttl=SLOW),
    F("mittr", "MIT Technology Review", "AI", "en", "media", "https://www.technologyreview.com/feed/",
      "Analisis AI dan dampaknya."),
    F("decoder", "The Decoder", "AI", "en", "media", "https://the-decoder.com/feed/",
      "Kabar AI harian — rilis model dan benchmark."),
    F("simonw", "Simon Willison's Weblog", "AI", "en", "analisis", "https://simonwillison.net/atom/everything/",
      "Uji langsung LLM baru dalam hitungan jam setelah rilis — pelacak LLM paling cepat.", ttl=900),
    F("importai", "Import AI (Jack Clark)", "AI", "en", "analisis", "https://importai.substack.com/feed",
      "Newsletter riset AI mingguan.", ttl=DAILY),
    F("latentspace", "Latent Space", "AI", "en", "analisis", "https://www.latent.space/feed",
      "Wawancara dan analisis industri AI.", ttl=DAILY),
    F("interconnects", "Interconnects (Nathan Lambert)", "AI", "en", "analisis",
      "https://www.interconnects.ai/feed", "Analisis model terbuka dan post-training.", ttl=DAILY),
    F("arxiv-cl", "arXiv · cs.CL", "AI", "en", "riset", "https://rss.arxiv.org/rss/cs.CL",
      "Makalah model bahasa terbaru — ratusan per hari.", ttl=DAILY, max_items=25),
    F("arxiv-ai", "arXiv · cs.AI", "AI", "en", "riset", "https://rss.arxiv.org/rss/cs.AI",
      "Makalah AI terbaru.", ttl=DAILY, max_items=25),
    F("arxiv-lg", "arXiv · cs.LG", "AI", "en", "riset", "https://rss.arxiv.org/rss/cs.LG",
      "Makalah machine learning terbaru.", ttl=DAILY, max_items=25),
    # ------------------------------------------------------------------- science
    F("gn-science", "Google News · Science", "Science", "en", "agregator", GN_TOPIC.format(t="SCIENCE"),
      "Rangkuman sains.", ttl=600),
    F("gn-health", "Google News · Health", "Science", "en", "agregator", GN_TOPIC.format(t="HEALTH"),
      "Rangkuman kesehatan — wabah dan kebijakan kesehatan.", ttl=600),
    F("nature", "Nature · News", "Science", "en", "riset", "https://www.nature.com/nature.rss",
      "Jurnal sains terkemuka.", ttl=DAILY, max_items=30),
    F("statnews", "STAT News", "Science", "en", "media", "https://www.statnews.com/feed/",
      "Farmasi, FDA, dan kesehatan — pasar persetujuan obat."),
    F("spacecom", "Space.com", "Science", "en", "media", "https://www.space.com/feeds/all",
      "Peluncuran roket dan misi antariksa."),
    # -------------------------------------------------------------------- sports
    F("espn", "ESPN", "Sports", "en", "media", "https://www.espn.com/espn/rss/news",
      "Olahraga AS — NFL, NBA, MLB.", core=True),
    F("gn-sports", "Google News · Sports", "Sports", "en", "agregator", GN_TOPIC.format(t="SPORTS"),
      "Rangkuman olahraga.", ttl=180),
    F("bbc-sport", "BBC Sport", "Sports", "en", "media", "https://feeds.bbci.co.uk/sport/rss.xml",
      "Olahraga Inggris dan dunia."),
    F("bbc-football", "BBC Sport · Football", "Sports", "en", "media",
      "https://feeds.bbci.co.uk/sport/football/rss.xml", "Sepak bola — cedera dan susunan pemain."),
    F("guardian-football", "The Guardian · Football", "Sports", "en", "media",
      "https://www.theguardian.com/football/rss", "Sepak bola Eropa."),
    F("skysports", "Sky Sports", "Sports", "en", "media", "https://www.skysports.com/rss/12040",
      "Sepak bola Inggris dan transfer."),
    F("cbssports", "CBS Sports", "Sports", "en", "media", "https://www.cbssports.com/rss/headlines/",
      "Olahraga AS."),
    F("pft", "ProFootballTalk", "Sports", "en", "media", "https://profootballtalk.nbcsports.com/feed/",
      "NFL — cedera dan transaksi pemain."),
    F("f1", "Formula 1", "Sports", "en", "resmi", "https://www.formula1.com/content/fom-website/en/latest/all.xml",
      "Situs resmi F1 — pasar pemenang balapan.", ttl=900),
    # ------------------------------------------------------------------- esports
    F("hltv", "HLTV · CS2", "Esports", "en", "media", "https://www.hltv.org/rss/news",
      "Counter-Strike: roster, turnamen, dan hasil.", core=True),
    F("dotesports", "Dot Esports", "Esports", "en", "media", "https://dotesports.com/feed",
      "LoL, Valorant, Dota 2, CS2."),
    F("dexerto", "Dexerto", "Esports", "en", "media", "https://www.dexerto.com/feed/",
      "Esports, streamer, dan game."),
    F("gamespot", "GameSpot", "Esports", "en", "media", "https://www.gamespot.com/feeds/news/",
      "Industri game — rilis dan penghargaan."),
    F("ign", "IGN", "Esports", "en", "media", "https://feeds.feedburner.com/ign/all", "Game dan hiburan."),
    # ------------------------------------------------------------------- culture
    F("gn-ent", "Google News · Entertainment", "Culture", "en", "agregator", GN_TOPIC.format(t="ENTERTAINMENT"),
      "Rangkuman hiburan.", ttl=300),
    F("variety", "Variety", "Culture", "en", "media", "https://variety.com/feed/",
      "Film, TV, dan box office — pasar penghargaan.", core=True),
    F("thr", "The Hollywood Reporter", "Culture", "en", "media", "https://www.hollywoodreporter.com/feed/",
      "Industri film dan TV."),
    F("deadline", "Deadline", "Culture", "en", "media", "https://deadline.com/feed/",
      "Box office akhir pekan dan kesepakatan studio."),
    F("billboard", "Billboard", "Culture", "en", "media", "https://www.billboard.com/feed/",
      "Tangga lagu — sumber resolusi pasar musik."),
    F("rollingstone", "Rolling Stone", "Culture", "en", "media", "https://www.rollingstone.com/feed/",
      "Musik dan budaya pop."),
    F("pitchfork", "Pitchfork · News", "Culture", "en", "media", "https://pitchfork.com/rss/news/",
      "Rilis album dan tur."),
    F("indiewire", "IndieWire", "Culture", "en", "media", "https://www.indiewire.com/feed/",
      "Film dan musim penghargaan."),
    F("tmz", "TMZ", "Culture", "en", "media", "https://www.tmz.com/rss.xml", "Selebriti — sering paling dulu.",
      caution="Tabloid; tunggu konfirmasi sebelum menganggap fakta."),
    F("screenrant", "Screen Rant", "Culture", "en", "media", "https://screenrant.com/feed/",
      "Film, serial, dan waralaba."),
    # ---------------------------------------------------------------------- asia
    F("japantimes", "The Japan Times", "Asia", "en", "media", "https://www.japantimes.co.jp/feed/",
      "Jepang — politik dan Bank of Japan."),
    F("scmp", "South China Morning Post", "Asia", "en", "media", "https://www.scmp.com/rss/91/feed",
      "Tiongkok dan Hong Kong."),
    F("toi", "Times of India", "Asia", "en", "media", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
      "India — politik dan kriket."),
    F("cna", "CNA (Channel NewsAsia)", "Asia", "en", "media", "https://www.channelnewsasia.com/rssfeeds/8395986",
      "Asia Tenggara dari Singapura."),
    F("straits", "The Straits Times · World", "Asia", "en", "media",
      "https://www.straitstimes.com/news/world/rss.xml", "Asia Tenggara dan dunia."),
    F("nikkei", "Nikkei Asia", "Asia", "en", "media", "https://asia.nikkei.com/rss/feed/nar",
      "Bisnis dan politik Asia."),
    # ----------------------------------------------------------------- indonesia
    F("google-id", "Google News Indonesia", "Indonesia", "id", "agregator",
      "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id",
      "Judul utama Indonesia dari banyak media; bisa dicari dengan kata kunci berbahasa Indonesia.",
      search_url=GN_SEARCH_ID, ttl=180),
    F("gn-id-bisnis", "Google News Indonesia · Bisnis", "Indonesia", "id", "agregator",
      "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=id&gl=ID&ceid=ID:id",
      "Bisnis dan ekonomi Indonesia.", ttl=300),
    F("cnbc-id", "CNBC Indonesia · News", "Indonesia", "id", "media", "https://www.cnbcindonesia.com/news/rss",
      "Ekonomi dan kebijakan Indonesia.", core=True, max_items=40),
    F("cnbc-id-market", "CNBC Indonesia · Market", "Indonesia", "id", "media",
      "https://www.cnbcindonesia.com/market/rss", "IHSG, rupiah, dan komoditas — termasuk sesi praperdagangan.",
      max_items=40),
    F("detik-finance", "detikFinance", "Indonesia", "id", "media", "https://finance.detik.com/rss",
      "Ekonomi dan bisnis Indonesia.", max_items=40),
    F("antara", "ANTARA · Terkini", "Indonesia", "id", "media", "https://www.antaranews.com/rss/terkini.xml",
      "Kantor berita negara — pernyataan resmi pemerintah.", max_items=40),
    F("antara-en", "ANTARA · English", "Indonesia", "en", "media", "https://en.antaranews.com/rss/news.xml",
      "Kabar Indonesia dalam bahasa Inggris."),
    F("cnn-id-nasional", "CNN Indonesia · Nasional", "Indonesia", "id", "media",
      "https://www.cnnindonesia.com/nasional/rss", "Politik dan hukum Indonesia.", max_items=40),
    F("cnn-id-ekonomi", "CNN Indonesia · Ekonomi", "Indonesia", "id", "media",
      "https://www.cnnindonesia.com/ekonomi/rss", "Ekonomi Indonesia.", max_items=40),
    F("tempo-nasional", "Tempo · Nasional", "Indonesia", "id", "media", "https://rss.tempo.co/nasional",
      "Investigasi dan politik nasional.", ttl=900),
    F("tempo-bisnis", "Tempo · Bisnis", "Indonesia", "id", "media", "https://rss.tempo.co/bisnis",
      "Bisnis dan ekonomi.", ttl=900),
    F("republika", "Republika", "Indonesia", "id", "media", "https://www.republika.co.id/rss",
      "Kabar nasional."),
    F("okezone", "Okezone", "Indonesia", "id", "media", "https://sindikasi.okezone.com/index.php/rss/0/RSS2.0",
      "Kabar nasional bervolume tinggi."),
    # --------------------------------------------------------------- forecasting
    F("manifold-news", "Manifold · News", "Forecasting", "en", "komunitas", "https://news.manifold.markets/feed",
      "Tulisan komunitas pasar prediksi Manifold.", ttl=DAILY),
    F("acx", "Astral Codex Ten", "Forecasting", "en", "analisis", "https://www.astralcodexten.com/feed",
      "Forecasting, kalibrasi, dan tinjauan pasar prediksi.", ttl=DAILY),
    F("overcoming", "Overcoming Bias (Robin Hanson)", "Forecasting", "en", "analisis",
      "https://www.overcomingbias.com/feed", "Penggagas pasar prediksi — teori dan kritik.", ttl=DAILY),
)

# Hacker News is not RSS — it is read through the Algolia API in src/news.
HACKER_NEWS = {
    "code": "hn", "name": "Hacker News", "category": "Tech", "lang": "en", "kind": "komunitas",
    "good_for": "Apa yang sedang ramai di kalangan insinyur — sering lebih dulu dari media teknologi.",
    "searchable": True, "core": True,
}

REJECTED: tuple[tuple[str, str], ...] = (
    ("Reuters", "404 dari koneksi ini (juga feed agensinya)"),
    ("Associated Press", "403"),
    ("Yahoo Finance / Yahoo Sports", "tidak menjawab (timeout)"),
    ("CNN", "masih menjawab, tetapi item terbarunya berumur 3 tahun"),
    ("WHO", "item terbaru berumur 203 hari"),
    ("Calculated Risk", "item terbaru berumur 238 hari"),
    ("Anthropic / Mistral / LMSYS", "tidak menerbitkan RSS di alamat yang diuji (404)"),
    ("Microsoft AI blog", "410 — feed ditutup"),
    ("VentureBeat AI", "429 — membatasi permintaan"),
    ("MarkTechPost / CryptoSlate / Science / ATP / IMF / Metaculus / Times of Israel / Bisnis.com", "403"),
    ("White House / U.S. Treasury / Goal.com", "404"),
    ("Jakarta Post / Jakarta Globe / Liputan6 / Suara", "404"),
    ("detik (indeks utama)", "koneksi diputus server; detikFinance tetap jalan"),
    ("Kontan", "XML rusak, tidak bisa diurai"),
    ("Esports Insider / Polymarket The Oracle", "sertifikat tidak cocok — ciri halaman pemblokir"),
)

BY_CODE: dict[str, Feed] = {f.code: f for f in FEEDS_CATALOG}


def by_categories(categories: tuple[str, ...] | list[str] | None) -> list[Feed]:
    if not categories:
        return [f for f in FEEDS_CATALOG if f.core]
    wanted = set(categories)
    return [f for f in FEEDS_CATALOG if f.category in wanted]


def for_sector(sector: str) -> list[Feed]:
    return by_categories(SECTOR_CATEGORIES.get((sector or "").lower(), ()))


def catalog() -> dict:
    rows = [f.to_dict() for f in FEEDS_CATALOG]
    rows.append({**HACKER_NEWS, "url": "https://hn.algolia.com/api/v1/search",
                 "category_label": CATEGORIES["Tech"], "kind_label": KINDS["komunitas"]})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return {
        "probed_at": PROBED_AT,
        "total": len(rows),
        "categories": [{"code": c, "label": label, "feeds": counts.get(c, 0)}
                       for c, label in CATEGORIES.items()],
        "kinds": KINDS,
        "feeds": rows,
        "rejected": [{"source": s, "reason": r} for s, r in REJECTED],
        "note": ("Diuji dari koneksi ini pada tanggal di atas. Sumber resmi menentukan penyelesaian "
                 "pasar; media dan agregator mempercepat Anda tahu. Keduanya tidak saling menggantikan."),
    }
