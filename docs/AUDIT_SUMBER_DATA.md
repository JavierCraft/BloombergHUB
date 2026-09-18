# Audit Sumber Data — Dump Referensi

Sumber: audit 40 repo (GitHub REST API + git tree rekursif + Polygon RPC), 5 September 2026.
Ditambah **probe lokal** dari mesin ini, 6 September 2026 — hasil probe ada di §4 dan itulah yang
dipakai `src/core/registry.py` sebagai basis kode.

Aturan yang lahir dari audit ini, dan yang dikodekan di repo ini:

> Cek `archived` dan `pushed_at` dari GitHub API sebelum `pip install` apa pun.
> README berbohong; file tree tidak.

---

## 1. Klasifikasi kapabilitas

| Kode | Arti | Konsekuensi di kode |
|------|------|---------------------|
| `TRADE` | ada modul signing / order builder — bisa memindahkan uang | wajib di balik gate `BH_ENABLE_TRADING` |
| `ANALISA` | baca & hitung, tidak bisa memasang order | boleh dipanggil bebas |
| `COMPUTE` | murni matematika, nol panggilan jaringan | selalu tersedia, tidak pernah gagal |
| `DAFTAR` | tidak ada logika jalan | tidak di-wrap |

---

## 2. Repo HIJAU — yang diimplementasikan di sini

Hanya yang hijau (dan yang lolos probe lokal) yang di-wrap jadi modul.

### Infrastruktur

| Repo | ★ | Update | Peran | Status di repo ini |
|------|---|--------|-------|--------------------|
| `Polymarket/py-sdk` | 118 | 2026-09-04 | TRADE — signing EIP-712, order builder, stream CLOB | di-wrap, **gated**; host diblokir ISP |
| `Polymarket/py-clob-client-v2` | 166 | 2026-08-17 | TRADE — akses CLOB langsung | di-wrap, **gated**; pengganti v1 yang diarsipkan |
| `nautechsystems/nautilus_trader` | 28.399 | 2026-09-05 | TRADE — engine + adapter Polymarket | tidak di-wrap (berat, di luar cakupan) |
| `warproxxx/poly-maker` | 1.482 | 2026-07-09 | TRADE — market maker dua sisi | dipakai sebagai **rujukan pola**, bukan dependensi |
| `evan-kolberg/prediction-market-backtesting` | 1.188 | 2026-05-16 | ANALISA — 254 file, **nol file signing** | pola direplikasi di `src/backtest`; branch default `v4.1-alpha`, bukan `main` |

### Politics

| Repo | ★ | Peran | Status |
|------|---|-------|--------|
| `ElectIndex/26_us_forecast_data` | 0 | ANALISA — 547 MB, 47 file input siklus 2026 | di-wrap (`src/politics`), perlu clone manual |
| `openelections/openelections-core` | 193 | ANALISA — hasil pemilu terstandardisasi | di-wrap sebagai pembaca CSV |
| `alex9smith/gdelt-doc-api` | 228 | ANALISA — volume & tone berita global | **tidak dipakai sebagai dependensi** (basi 16 bln); API GDELT v2 dipanggil langsung lewat `requests` |

Catatan `gcb_polls.csv`: generic congressional ballot adalah variabel tunggal paling menentukan
harga market "partai mana menguasai House". `pollster_ratings.csv` punya kolom `banned` yang
menandai pollster fabrikasi — buang dulu sebelum merata-rata.

### Sports + Esports

| Repo | ★ | Update | Peran | Status |
|------|---|--------|-------|--------|
| `machina-sports/sports-skills` | 211 | 2026-09-04 | 26 skill, 1 eksekusi berpagar | **pola `catalog.json`-nya direplikasi** di `src/core/registry.py` |
| `probberechts/soccerdata` | 2.056 | 2026-08-21 | ANALISA — 8 sumber | di-wrap; sumber odds-nya mati dari koneksi ini (lihat §4) |
| `swar/nba_api` | 3.761 | 2026-08-16 | ANALISA — stats.nba.com | di-wrap, jalan |
| `statsbomb/statsbombpy` | 741 | 2026-09-01 | ANALISA — event data | di-wrap, open-data jalan |
| `odota/core` | 1.628 | 2026-08-21 | ANALISA — Dota 2, API publik | di-wrap, jalan, tanpa key |
| `nflverse/nflreadpy` | 204 | 2026-08-05 | ANALISA — pengganti `nfl_data_py` | di-wrap; **mengembalikan polars**, bukan pandas |
| `sportsdataverse/sportsdataverse-py` | 116 | 2026-09-04 | ANALISA — multi-liga | opsional, belum terpasang |

### Tech

| Repo | ★ | Peran | Status |
|------|---|-------|--------|
| `huggingface/huggingface_hub` | 3.871 | ANALISA — deteksi rilis model | di-wrap, jalan |

### Culture

| Repo | ★ | Peran | Status |
|------|---|-------|--------|
| `spotipy-dev/spotipy` | 5.471 | ANALISA — Spotify Web API resmi | di-wrap, perlu key |
| `celiao/tmdbsimple` | 698 | ANALISA — TMDb v3 resmi | di-wrap, perlu key |

---

## 3. Daftar MERAH — jangan dipakai, sudah dikeluarkan dari `requirements.txt`

| Repo / paket | Alasan |
|--------------|--------|
| `Polymarket/py-clob-client` (v1) | **diarsipkan** — diganti v2 |
| `Polymarket/agents` | diarsipkan 11 Mei 2026 |
| `nflverse/nfl_data_py` | **diarsipkan** 25 Sep 2025 → pakai `nflreadpy` |
| `fivethirtyeight/data` | 538 dibubarkan, arsip sejarah |
| `guoguo12/billboard-charts` | scraper HTML, basi 25 bulan → anggap rusak |
| `tjwaterman99/boxofficemojo-scraper` | CI berhenti Jan 2025 |
| `gigobyte/HLTV` | scraper CS2, basi 18 bulan |
| `otrofimo/polymarket_data` | klaim 1,1 M record, repo 95 KB — datanya tidak ada |
| `tatn/awesome-ai-benchmarks` | isi lengkap: 2 file README |
| `NYTEMODEONLY/polyterm` | eksekusi cuma `docs/EXECUTION_ROADMAP.md` — intel saja, bukan bot |
| paket pip `gdelt` (linwoodc3) | bukan repo yang diaudit; audit merujuk `gdeltdoc` |

---

## 4. Probe lokal — 6 September 2026

Dijalankan dari mesin ini. Inilah yang menentukan default UI, bukan asumsi.
Ulangi kapan saja dengan `python run.py health` atau buka `/health` di web.

### Hidup

```
api.github.com                200   GitHub API
polygon.publicnode.com        200   Polygon RPC (penyelesaian trade Polymarket)
api.opendota.com              200   Dota 2, tanpa key
huggingface.co/api            200   deteksi rilis model
site.api.espn.com             200   jadwal & skor
understat.com                 200   xG sepak bola
stats.nba.com                 200   NBA
raw.githubusercontent.com     200   StatsBomb open-data
liquipedia.net/api.php        200   jadwal esports
api.themoviedb.org            401   hidup, perlu TMDB_API_KEY
accounts.spotify.com          405   hidup, perlu SPOTIPY_CLIENT_*
api.gdeltproject.org          429   hidup, kena rate limit — bukan blokir
```

### Mati dari koneksi ini

```
gamma-api.polymarket.com      timeout   dibelokkan ISP ke 202.137.1.74
clob.polymarket.com           timeout   idem
data-api.polymarket.com       timeout   idem
www.football-data.co.uk       timeout   TEMUAN BARU — odds bandar ikut terblokir
fbref.com                     403       Cloudflare
api.clubelo.com               502       upstream error
```

**Konsekuensi yang mengubah rencana:** audit menyarankan kalibrasi model ke odds bandar lewat
`soccerdata.FootballData`. Jalur itu **tertutup dari sini**. Yang tersisa untuk sepak bola adalah
Understat (xG) dan ESPN (jadwal/hasil) — cukup untuk membangun model, tidak cukup untuk kalibrasi
terhadap bandar. Kalibrasi harus menunggu akses (VPS/VPN) atau memakai arsip odds unduhan manual.

---

## 5. Bug runtime yang ditemukan saat implementasi

Semua ini nyata di environment ini (Python 3.14.2, numpy 2.4.3, pandas 3.0.2, web3 8.0.0) dan
sudah diperbaiki:

| Lokasi lama | Bug | Perbaikan |
|-------------|-----|-----------|
| `src/sports/betting.py` | `np.math.factorial` — `np.math` dihapus di numpy 2.x | `math.factorial`, plus rekurensi PMF Poisson yang stabil |
| `src/sports/betting.py` | anotasi `-> pd.DataFrame` sebelum `import pandas` di baris terakhir file | pandas dihapus dari modul; murni stdlib, jadi benar-benar `COMPUTE` |
| `src/polymarket/onchain.py` | `.hex()` di hexbytes 2.0 **tidak lagi berprefiks `0x`** — semua perbandingan topic gagal senyap | helper `_hex()` yang menormalkan |
| `src/polymarket/onchain.py` | deteksi fill lewat `"OrderFilled" in str(topic0)` — topic itu hash, tidak pernah cocok | bandingkan ke konstanta `ORDER_FILLED_TOPIC` + hitung data word |
| `src/polymarket/onchain.py` | `NEG_RISK_CTF_EXCHANGE` = alamat yang sama dengan v2 | dipisah ke alamat NegRisk yang benar |
| `src/sports/nfl.py` | `load_pfr_passing/rushing/receiving` tidak ada di nflreadpy 0.1.5 | `load_pfr_advstats(stat_type=...)` |
| `src/sports/nfl.py` | nflreadpy mengembalikan **polars**, dikirim ke `.to_dict(orient=)` | normalizer `src/core/frames.py` |
| `src/politics` | `import gdelt` = paket linwoodc3, bukan repo yang diaudit, dan tidak terpasang | klien HTTP langsung ke GDELT v2 doc API |
| `app.py` | tiap request meng-import ulang modul berat, tanpa cache, tanpa timeout | cache TTL + registry + envelope error |

---

## 6. Aturan desain yang diambil dari audit

1. **Pisahkan paket read-only dari paket pemegang kunci.** Ini pola `sports-skills`: 26 skill,
   hanya 1 yang `money_movement: true`, ditandai `risk: critical`, paket terpisah. Repo yang
   mencampur "baca harga" dan "pasang order" membuat setiap bug pembacaan berpotensi jadi order
   tak disengaja.
2. **Uji dengan Brier score, bukan PnL.** Model beruntung dan model terkalibrasi terlihat sama di
   kurva PnL pendek. Hanya yang kedua bertahan.
3. **Hindari scraper untuk apa pun yang menentukan uang.** Scraper gagal *diam-diam* — kembali
   daftar kosong tanpa error. Itu bentuk kegagalan paling berbahaya buat bot.
4. **Jangan cantumkan angka yang tidak bisa diverifikasi.** PnL per kategori tidak ada di audit
   karena `data-api` tak terjangkau; jangan dikarang di UI.
