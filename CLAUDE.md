# Bloomberg Hub — instruksi untuk Claude Code

Terminal riset pasar prediksi (Flask + JS tanpa framework). Berkas ini dimuat otomatis di setiap
sesi Claude Code — CLI maupun ekstensi VS Code. Rincian perubahan ada di `docs/`; di sini hanya
aturan yang harus dipegang setiap kali mengubah kode.

## Menjalankan dan menguji

```powershell
python app.py                                   # http://127.0.0.1:5000
python -m unittest discover -s tests            # semua uji unit (tanpa jaringan)
python tests/browser_smoke.py                   # Firefox headless, API di-mock
node --check static/js/hub.js; node --check static/js/ml.js; node --check static/js/monitor.js; node --check static/js/datahub.js
python run.py health                            # kondisi sumber dari koneksi ini
python run.py ml --train --markets 600          # latih model deadline
python run.py paper --scan                      # paper test: settle, prediksi, posisi virtual
```

- Windows: skrip yang mencetak karakter non-ASCII butuh `PYTHONIOENCODING=utf-8`.
- Uji unit mengikuti pola `tests/test_*.py`; tidak ada `tests/__init__.py`, jadi pakai `discover`.
- Uji **tidak boleh** menyentuh `data/`: patch `cache._DISK_DIR`, `store.RAW`, `paper.POSITIONS`,
  `datahub.STATUS_FILE`, dan sejenisnya ke folder sementara. `cache.clear()` pada disk asli
  menghapus simpanan pengguna.
- **"Perubahan tidak terlihat"**: cek dulu apakah server lama masih hidup di port 5000
  (`Get-NetTCPConnection -LocalPort 5000 -State Listen`). Di Windows dua server bisa mengikat port
  yang sama dan yang lama tetap menerima permintaan; `app.py` kini menolak jalan bila port terpakai
  dan membaca `/api/version` server itu (PID, waktu mulai, versi kode = waktu ubah terbaru berkas
  kode) untuk menjelaskan apakah server tersebut Bloomberg Hub terbaru, versi lama, atau program lain.
  Jangan menjalankan server di latar belakang untuk pengguna tanpa memberi tahu cara menghentikannya.
  Template Jinja di-cache tanpa `BH_DEBUG=1`, jadi server harus dijalankan ulang setelah mengubahnya.
- Penjadwal paper test (`scheduler.start`) dan pemanasan cache (`src/web/warmup.py`) hanya
  dijalankan dari `app.py` `__main__`, bukan dari `create_app()`, supaya uji tidak memicu scan.

## Prinsip yang tidak boleh dilanggar

1. **Status adalah hasil probe, bukan asumsi.** Sumber yang tidak menjawab dilaporkan apa adanya.
2. **Tidak diketahui ≠ nol.** Harga, volume, atau perubahan yang tidak dikirim sumber tampil
   sebagai "—"/None, tidak pernah 0. Awas nilai falsy: cek `is None`, bukan `if not x`.
3. **Jangan menebak label.** Pencocokan nama/ID yang ragu dibiarkan kosong beserta alasannya.
4. **Model dinilai melawan harga pasar.** Setiap EV model tampil bersama rekam jejak dan vonisnya
   ("belum terbukti" bila interval bootstrap selisih Brier menyentuh nol).
5. **Gerbang uang tertutup.** Hanya `src/polymarket/client.py` boleh memegang kunci; paper test
   memakai uang virtual dan tidak pernah memanggil jalur order.

## Arsitektur

- `src/web/api.py` — semua endpoint lewat `serve()` → amplop `{ok, data, meta, error}`.
  Input salah → `BadRequest` (400), bukan 500. Validasi `num()` di luar `serve()` harus ditangkap.
- `src/core/errors.py` — taksonomi galat; `registry.py` — katalog sumber (+ `health.guard`).
- `src/markets/` — Polymarket Gamma/CLOB, Manifold, `limitless.py` (Limitless Exchange, USDC, terbuka
  dari sini), `sector.py` (panel sektor: Polymarket → Limitless + Manifold), `analysis.py`
  (kecondongan/strategi), `ev.py` (EV per outcome di harga ask), `orderbook.py` (IEP/IEV lelang +
  volume ke nilai wajar).
- `src/datahub/` — `catalog.py` 36 API data publik (kode, URL, parser, halaman, kegunaan, TTL);
  `parsers.py` satu fungsi per API → baris; `__init__.py` `fetch()` lewat cache, `inventory()` semua
  219 sumber dengan status, `probe_all()` (pekerjaan latar, hasil di `data/datahub/status.json`).
- `src/news/` — `sources.py` katalog 157 feed teruji (kategori, jenis, kegunaan); `__init__.py`
  simpanan per feed, tenggat `budget_s`, korpus belajar `data/news/corpus-*.jsonl`.
- `src/ml/` — `history.py` (riwayat harga pasar selesai, cache permanen `data/ml/raw/`),
  `features.py` (SATU fungsi fitur untuk latih dan live), `model.py` (model berjangkar harga pasar,
  walk-forward), `deadlines.py`, `paper.py` (buku besar `data/ml/paper/`), `newsml.py` (TF-IDF,
  nada, cerita, kata naik), `llm.py` (Claude opsional), `jobs.py`, `scheduler.py`.
- `src/ml/deadlines.py` — `collect()` (semua sumber) dan `sector_deadlines()` (per halaman sektor)
  mengambil Polymarket, Limitless, dan Manifold **serentak** (`_gather`); momentum Manifold disimpan
  10 menit per pasar (`manifold_changes`) — tanpa itu satu kartu sektor makan ±30 detik.
- Frontend: `static/js/hub.js` (BH.el/load/renderTable/grafik), `ml.js` (EV/IEV/model/berita/LLM,
  plus `radar()`/`marketDetail()` bersama untuk Deadlines, Overview, dan kartu ML sektor),
  `datahub.js` (panel `data-datahub="<halaman>"` memasang dirinya sendiri), `monitor.js` (panel
  sektor). Template Jinja di `templates/`; aset dimuat lewat `asset()` (versi = waktu ubah berkas).
- Flask mengurutkan kunci JSON. Urutan kolom tabel diambil dari `meta.columns`, bukan dari
  `Object.keys(row)`.

## Konvensi

- Bahasa UI: label, judul, tombol, nama kolom dalam istilah Inggris; kalimat penjelas, catatan,
  dan pesan galat dalam Bahasa Indonesia.
- Teks dari API selalu lewat `textContent` (`BH.el(..., {text})`), tidak pernah `innerHTML`.
- Warna status (good/warning/critical) hanya untuk status. Arah data (naik/turun) memakai
  `--pole-pos`/`--pole-neg`.
- Hasil yang dibuka di dalam panel yang menyegar sendiri disimpan di memo `ml.js` supaya tidak
  hilang saat panel digambar ulang.
- Komentar kode menjelaskan *kenapa*, termasuk angka hasil pengukuran bila keputusan didasarkan
  padanya. Pertahankan gaya ini.

## Jaringan dari mesin ini (diukur, bisa berubah)

- Polymarket (gamma, clob, data-api) sering diblokir ISP: gejalanya `SSLError`. Kode Polymarket
  tetap lengkap dan diuji dengan fixture; laporkan blokir sebagai `NETWORK_BLOCKED`.
- Manifold terbuka. `search-markets` membatasi `offset ≤ 1000`; `/bets?points=true` TIDAK lengkap
  untuk pasar besar — pakai `/bets` dengan `before=<id>`. Topik per sektor diperiksa lewat
  `/v0/group/<slug>` (mis. `politics-default`, `technology-default`, `gaming`).
- Limitless terbuka (`api.limitless.exchange`). Harga kadang dalam sen (`[50, 50]`); ask/bid 0 atau
  1 berarti tidak ada yang memasang harga — diperlakukan sebagai "tanpa buku", bukan harga. Ukuran
  buku dalam sepersejuta lembar. Kategori "Sports" ikut memuat esports.
- Dari 64 API publik yang dicoba, 36 dipakai; yang ditolak dan alasannya ada di docstring
  `src/datahub/catalog.py`.
- GDELT sangat membatasi laju (jeda ≥ 7 detik). Maksimal ±8 koneksi HTTPS serentak.

## Machine learning

- Model: `AnchoredLogit` / boosting dengan log-odds harga pasar sebagai offset tetap. Regularisasi
  kuat = mengikuti pasar. Jangan kembali ke harga sebagai fitur biasa (terbukti kalah dari pasar).
- Latih ulang setelah mengubah `features.py`; naikkan `HISTORY_VERSION` di `history.py` bila bentuk
  riwayat mentah berubah.
- Model hanya menilai pasar dengan sisa ±5 jam sampai 21 hari. Data latih saat ini hanya Manifold
  (Polymarket diblokir); pasar pertandingan ("A vs B") diberi catatan bahwa koreksinya paling lemah.
- LLM: bawaan `claude-opus-5` dengan `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`),
  output terstruktur JSON schema, cek `stop_reason == "refusal"` sebelum membaca isi.
