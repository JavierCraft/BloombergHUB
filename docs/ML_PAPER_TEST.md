# EV, IEV, machine learning, paper test, dan sumber berita — 17 September 2026

Putaran ketiga. Semua angka di dokumen ini hasil pengukuran di mesin ini pada tanggal di atas,
bukan perkiraan.

| Permintaan | Pemenuhan | Di mana |
|-----------|-----------|---------|
| EV di semua sektor | Tabel EV per outcome di harga ask pada setiap pasar Politics, Tech, Culture, Sports, Esports, News; ringkasan kategori menampilkan EV model tertinggi | `src/markets/ev.py`, panel sektor |
| IEV (Indicative Equilibrium Volume) | IEP/IEV dengan aturan lelang praperdagangan, plus volume menuju nilai wajar model | `src/markets/orderbook.py`, tombol "Hitung IEP / IEV" |
| Machine learning untuk membuat/menganalisis berita | Relevansi TF-IDF, nada leksikon, pengelompokan cerita (ringkasan ekstraktif), kata yang sedang naik; opsional pendapat Claude | `src/ml/newsml.py`, `src/ml/llm.py` |
| ML yang bisa dipakai paper test | Model deadline + buku besar virtual yang diselesaikan dengan hasil sebenarnya | `src/ml/model.py`, `src/ml/paper.py`, halaman **Paper Test** |
| Tanggal pasar bertenggat mepet, benar-tidaknya ML, win rate | Halaman **Deadlines** (hitung mundur, tanggal posting, arah, EV) dan statistik per sisa waktu di **Paper Test** | `src/ml/deadlines.py` |
| Sumber berita selengkap mungkin, bisa untuk belajar | 157 feed teruji di 16 kategori + Hacker News, masing-masing dengan kegunaan dan peringatannya; korpus tersimpan untuk ML | `src/news/sources.py`, kartu **Source catalog** |
| Mengikuti LLM terbaru | Radar rilis LLM dari blog lab dan pelacak AI, daftar model Claude; integrasi memakai Claude Opus 5 | `src/tech/llm_radar.py`, halaman Tech |
| Memori untuk pengguna VS Code | `CLAUDE.md` di akar proyek (dimuat otomatis oleh CLI maupun ekstensi VS Code) | `CLAUDE.md` |

---

## 0. Putaran keempat — "tidak ada perubahan", ML tidak terlihat, sumber cuma 22

Tiga keluhan, tiga penyebab yang diukur:

| Keluhan | Penyebab sebenarnya | Perbaikan |
|---------|--------------------|-----------|
| Perubahan tidak terlihat | Server lama (dijalankan 11 September) masih hidup di port 5000. Di Windows dua server bisa mengikat port yang sama, dan yang lama menjawab semua permintaan — kode baru tidak pernah sampai ke browser. | Server lama dihentikan. `app.py` kini menolak jalan bila port terpakai dan menunjukkan perintah untuk menghentikan server lama. CSS/JS dimuat dengan versi waktu-ubah, jadi cache browser tidak menahan kode lama. |
| ML "tidak ada kabar" | Panel sektor hanya membaca Polymarket, yang diblokir — yang tampil hanya satu pesan galat, tanpa pasar, tanpa EV, tanpa model. | Pasar sektor diambil dari **Limitless** (uang sungguhan, USDC, 603 pasar aktif) dan **Manifold** saat Polymarket diblokir. Setiap halaman sektor dibuka dengan kartu **Machine learning · tenggat terdekat**. Overview menampilkan status model, paper test, dan pasar ≤ 3 hari dengan peluang model. |
| "Total sources 22" | Angka itu hanya menghitung integrasi di registry; 157 feed berita tidak ikut dihitung dan tidak terlihat. | 36 API data publik baru (dari 64 yang dicoba). Sources menghitung semuanya: **219** — 158 feed, 36 API, 25 integrasi — dalam satu tabel dengan status, kegunaan, filter, dan tombol "Check all now". |

Hasil yang diukur dari koneksi ini, 17 September 2026 (model `deadline-anchored_boost-20260917-0730`):

| Halaman | Pasar bertenggat ≤ 14 hari | Dinilai model | EV model positif |
|---------|---------------------------|---------------|------------------|
| Politics | 26 (Limitless 1, Manifold 25) | 26 | 15 |
| Tech | 27 (Limitless 3, Manifold 24) | 27 | 14 |
| Esports | 55 (Limitless 53, Manifold 2) | 55 | 20 |
| Sports | 29 (Limitless 24, Manifold 5) | 29 | 16 |
| Culture | 9 (Manifold 9) | 9 | 6 |
| Overview (≤ 3 hari, semua sumber) | 116 | 108 | 59 |

"EV model positif" berarti model menilai satu sisi lebih mungkin dari harga ask-nya — **bukan** sinyal:
model ini belum terbukti mengalahkan harga pasar (selisih Brier 0,1432 lawan 0,1462, interval 90%
menyentuh nol). Paper test yang memutuskan, dan kini berjalan otomatis tiap jam.

Waktu muat kartu ML sektor: ±30 detik pada percobaan pertama (tiga sumber diambil berurutan,
momentum Manifold dibangun ulang dari riwayat transaksi 1,5 detik per pasar). Sekarang ketiga sumber
diambil serentak, momentum disimpan 10 menit per pasar, dan `app.py` memanaskan simpanannya saat
server mulai: 9–10 detik pada simpanan kosong, ±2 detik sesudahnya, instan dari simpanan API.

**API data publik** (`src/datahub/`): CoinGecko, Crypto Fear & Greed, DefiLlama, Frankfurter (ECB),
ExchangeRate-API, Gold API, U.S. Treasury (utang harian dan bunga), BLS CPI, World Bank (Indonesia),
Federal Register (dokumen presiden AS), Wikipedia paling dibaca, Mastodon trending, OpenRouter,
Hugging Face papers, GitHub releases, PyPI, Lobsters, DEV, Apple Music/Podcasts, iTunes film,
TheSportsDB, OpenLigaDB, Jolpica F1, NHL, MLB, Steam, USGS, BMKG, GDACS, NASA EONET, Open-Meteo,
Launch Library. Masing-masing tampil di halaman yang memakainya, dengan catatan kegunaan untuk riset
pasar prediksi. Yang ditolak — Kalshi, SX Bet, PredictIt, Metaculus, Smarkets, Binance, Coinbase,
Kraken, ESPN, Reddit, Bluesky, GovTrack, CourtListener, IDX — dicatat alasannya di `catalog.py`.

**Kejujuran di pasar pertandingan.** Model dilatih dari pertanyaan "akan terjadi sebelum tanggal X",
hampir tanpa pertandingan. Pasar "A vs B" dari Limitless kini membawa catatan bahwa koreksi model
terhadap harganya paling lemah.

---

## 1. EV di semua sektor

EV dihitung pada harga yang benar-benar bisa didapat — **best ask**, bukan harga tengah:

```
EV per $1 = peluang ÷ harga masuk − 1
```

Dua peluang ditampilkan berdampingan, tidak pernah digabung:

- **EV di harga konsensus.** Selalu nol atau negatif, karena membeli di ask sementara harga
  wajarnya di tengah berarti kehilangan setengah spread. Ini biaya masuk yang ditulis sebagai EV.
- **EV model.** Hanya ada untuk pasar bertenggat ≤ 14 hari (rentang latih model), dan selalu
  ditemani rekam jejak model serta label "belum terbukti mengalahkan pasar" bila memang belum.

Gamma hanya mengirim best bid/ask untuk outcome pertama. Untuk pasar dua outcome, ask outcome kedua
adalah `1 − best bid` (membeli NO di q sama dengan menjual YES di 1 − q). Di Manifold, yang tidak
punya spread, dipakai asumsi biaya 1 poin — dan asumsinya ditulis di kolom sumber harga.

Di setiap pasar juga ada kolom **Perkiraan Anda (%)**: EV dihitung langsung di browser dari
perkiraan sendiri, dan nilainya bertahan saat panel menyegar tiap 30 detik.

## 2. IEP dan IEV

IEP/IEV adalah angka praperdagangan Bursa Efek Indonesia: harga yang mempertemukan volume
terbanyak bila semua pesanan dicocokkan sekarang, dan volume itu. Urutan aturannya: volume
terbesar → surplus terkecil → terdekat ke harga acuan (transaksi terakhir, atau harga tengah) →
harga terendah.

Pasar prediksi diperdagangkan terus-menerus. Di buku yang normal bid tertinggi selalu di bawah
ask terendah, sehingga **IEV yang jujur adalah 0** — dan itu yang ditampilkan, lengkap dengan
alasannya. IEV hanya positif saat buku bersilangan.

Yang tetap berguna ditampilkan terpisah: **volume menuju nilai wajar model** — berapa lembar di buku
yang lebih murah dari peluang model, biayanya, VWAP, dan untung harapannya. Diberi label sendiri
karena bergantung pada keyakinan model, bukan pada pesanan.

- Polymarket: buku dari CLOB `/book` (diblokir dari koneksi ini hari ini; kodenya lengkap dan diuji).
- Manifold: pesanan limit diubah dari mana ke **lembar** (`sisa ÷ p` untuk YES, `sisa ÷ (1 − p)`
  untuk NO) sebelum dilelang. Manifold juga punya AMM di luar buku, jadi IEV buku limit = 0 tidak
  berarti tidak ada transaksi.

## 3. Model deadline

### Data

Polymarket (gamma, clob, data-api) menolak koneksi ini dengan `SSLError` hari ini. Pelatihan
memakai **Manifold**: 2.552 pasar biner yang sudah selesai (≥ 15 trader, YES/NO), riwayat harganya
diambil mundur sampai 22 hari sebelum tenggat dan disimpan permanen di `data/ml/raw/`. Tiap pasar
menghasilkan snapshot pada 6 jam, 12 jam, 1, 2, 3, 5, 7, 10, dan 14 hari sebelum tenggat.
Kolektor Polymarket sudah lengkap dan langsung dipakai begitu alamatnya terjangkau.

Tiga temuan saat membangun data yang mengubah desain:

1. **`/bets?points=true` tidak lengkap.** Untuk pasar dengan 21.000 transaksi ia mengembalikan
   1.009 titik tanpa urutan. Dipakai `/bets` dengan `before=<id>`, yang terurut dan bisa dipaginasi.
2. **`search-markets` membatasi `offset ≤ 1000`**, dan 1.000 pasar selesai hanya mencakup sebulan.
   Daftar pasar dibaca lewat `/markets` dengan kursor.
3. **Manifold menimpa `closeTime` saat pasar diselesaikan lebih awal.** Ini yang paling penting:

| Pertanyaan "…by/before <tanggal>?", ≤ 3 hari sebelum "tenggat" | Harga YES rata-rata | YES terjadi |
|---|---|---|
| Berjalan sampai tenggat aslinya | 30,1% | 24,2% |
| Diselesaikan lebih awal (tenggat tertimpa) | 58,3% | 78,9% |

   Mencampur keduanya mengajari model bahwa YES makin mungkin menjelang tenggat — kebalikan dari
   data yang jujur. Tetapi membuang semuanya juga tidak netral: dari pasar yang tanggalnya bisa
   dibaca, 35% selesai dalam 14 hari sebelum tanggal itu (64 YES, 37 NO). Jalan tengahnya: bila
   pertanyaan menulis tanggalnya sendiri ("by March 31, 2025", "before July 2025", "in 2026"),
   tanggal itu menjadi tenggat; sisanya (702 pasar) tidak dipakai, dan jumlahnya dilaporkan.

### Cara menilai

- **Walk-forward**: pasar diurutkan menurut tenggat, dilatih pada blok lama dan diuji pada blok
  berikutnya, dengan jeda 30 hari.
- **Pembanding utamanya harga pasar itu sendiri.** Selisih Brier diberi interval bootstrap 90%
  yang di-resample per **pasar**. "Lebih tepat dari pasar" hanya bila seluruh interval di atas nol.
- Skor dihitung pada snapshot berharga 5–95%; snapshot yang sudah nyaris pasti membuat model mana
  pun tampak hebat.

### Model berjangkar harga pasar

Versi pertama memasukkan harga sebagai fitur biasa. Pada 150 pasar, regularisasi menyusutkan
koefisiennya, model hanyut ke base rate, bertaruh pada longshot, dan kalah dari pasar (Brier 0,156
lawan 0,132). Sekarang log-odds harga menjadi **offset tetap** dan model hanya mempelajari koreksi:
regularisasi kuat berarti "ikuti pasar". Empat kandidat berjangkar (tiga kekuatan regularisasi
logistik + gradient boosting yang mulai dari harga pasar) dibandingkan; yang log loss-nya terkecil
dipakai.

### Hasil — 1.579 pasar bertenggat jelas, 1.315 pasar uji, 6.549 snapshot 5–95%

| Ukuran | Model | Harga pasar |
|---|---|---|
| Brier (makin kecil makin tepat) | **0,1432** | 0,1462 |
| Log loss | **0,4399** | 0,4509 |
| Reliabilitas kalibrasi | **0,0007** | 0,0045 |
| Arah benar (naik/turun dari harga) | 68,5% | — |

Selisih Brier +0,0030, **rentang 90% [−0,0001; +0,0060]** — menyentuh nol dengan selisih tipis.
Vonisnya: *belum bisa dibedakan dari kebetulan*. Model tampil di mana-mana dengan label
**belum terbukti**, sampai paper test mengatakan lain.

### Menjawab hipotesis "harga naik karena mepet"

Diukur tanpa model, langsung dari harga dan hasil:

| Pertanyaan | Sisa waktu | YES: harga → terjadi | Favorit: harga → menang |
|---|---|---|---|
| Semua | 7–14 hari | 37,3% → 28,5% (−8,8 ± 1,3) | 75,7% → 80,2% (+4,5) |
| Semua | 3–7 hari | 38,7% → 31,6% (−7,2 ± 1,2) | 75,8% → 79,3% (+3,6) |
| Semua | 1–3 hari | 39,9% → 34,7% (−5,2 ± 1,2) | 75,8% → 78,2% (+2,4) |
| Semua | ≤ 1 hari | 41,5% → 38,4% (−3,0 ± 1,0) | 72,8% → 74,4% (+1,7) |
| "by/before <tanggal>" | 7–14 hari | 33,9% → 23,4% (−10,5 ± 1,7) | 77,7% → 83,0% (+5,3) |
| "by/before <tanggal>" | ≤ 1 hari | 29,0% → 24,5% (−4,5 ± 1,8) | 83,0% → 88,6% (+5,5) |

Artinya:

- **Ya, harga bergerak menuju hasilnya saat tenggat mendekat** — tetapi yang naik terutama harga
  **NO** dan harga **favorit**, karena YES rata-rata dihargai terlalu mahal (peluruhan waktu:
  kalau belum terjadi, peluang YES seharusnya turun setiap hari).
- **Salah harga paling besar 7–14 hari sebelum tenggat**, lalu mengecil. Di hari terakhir harga
  sudah hampir tepat. Backtest model pada prediksi uji mengatakan hal yang sama:

| Sisa waktu | Posisi | Win rate | Break-even | Selisih | ROI |
|---|---|---|---|---|---|
| ≤ 1 hari | 498 | 55,2% | 54,4% | +0,9 | −1,5% |
| 1–3 hari | 450 | 59,6% | 56,9% | +2,7 | +6,9% |
| 3–7 hari | 494 | 63,6% | 57,2% | +6,4 | +9,2% |
| 7–14 hari | 441 | 68,0% | 59,7% | +8,3 | +14,4% |
| **Semua** | **1.883** | **61,4%** | **57,0%** | **+4,5** | **+7,0%** |

  (EV ≥ 5% setelah biaya 1 poin, satu posisi per pasar per rentang.)

**Batas kesimpulan ini:** datanya Manifold, uang main. Kecenderungan "YES terlalu mahal" bisa khas
Manifold; pasar uang sungguhan bisa berbeda. Model dipilih dari empat kandidat pada data uji yang
sama, jadi skornya sedikit optimistis. Paper test di bawah ini adalah ujian yang sebenarnya.

## 4. Paper test

Setiap scan: **settle** posisi dan prediksi yang pasarnya sudah selesai → **prediksi** setiap pasar
bertenggat ≤ 14 hari (maksimal sekali per 12 jam per pasar, lengkap dengan fitur berita saat itu) →
**buka posisi virtual** bila EV model di harga ask ≥ 5%, stake datar $10.

Aturan yang menjaga hasilnya jujur:

- Sumber yang tidak bisa dihubungi saat settle **tidak pernah** diselesaikan dengan tebakan; posisi
  tetap terbuka dengan catatan.
- Win rate selalu ditampilkan **di samping break-even** (rata-rata harga masuk) dan nilai z-nya.
- **Win rate bayangan**: setiap prediksi yang sudah selesai dinilai seolah sisi yang dicondongi model
  dibeli di ask saat itu. Jadi "ML-nya benar atau tidak, dan win rate-nya" terjawab dari semua
  prediksi, tidak hanya dari posisi yang lolos ambang EV.
- Pasar pribadi Manifold ("Will I weigh…") dan pasar dengan < 15 trader tetap diprediksi, tetapi
  tidak dibuka posisinya — keduanya bukan bukti tentang pasar peristiwa nyata.
- Bila ≥ 80% posisi di satu sisi, halaman memperingatkan bahwa hasilnya **berkorelasi**.

**Status saat dokumen ini ditulis:** 66 prediksi tercatat, 10 posisi virtual terbuka — **semuanya
di sisi NO**, sebagian besar 7–14 hari sebelum tenggat (Brent crude ≥ $100 pada 30 September,
Menteri Energi Jerman, kursi United Russia di Duma, Blue Jays ke postseason, …). Karena satu arah,
10 posisi ini menguji satu pola, bukan sepuluh. Hasil pertama muncul saat pasar-pasar itu selesai.

Dua buku besar lama dari model yang dilatih sebelum bias tenggat ditemukan dipindah ke
`data/ml/paper/arsip-*` (tidak dihapus).

**Berjalan otomatis:** selama `python app.py` hidup, settle + scan berjalan **tiap 60 menit** (bawaan
sejak 17 September; `BH_PAPER_AUTOSCAN_MIN=0` di `.env` untuk mematikan, angka lain untuk mengubah
jedanya). Tanpa aplikasi web: jadwalkan `python run.py paper --scan` di Task Scheduler. Sumber scan
kini Polymarket, **Limitless**, dan Manifold; pasar Limitless tanpa buku pesanan (ask 0 atau spread
> 25 poin) tidak dibuka posisinya.

## 5. Berita

- **157 feed** lolos probe dari 188 kandidat: dunia, AS, politik, pasar, makro, **sumber resmi**
  (The Fed, BLS, BEA, ECB, BoE, SEC, CFTC, CDC, NHC, USGS, UN), kripto, teknologi, AI & LLM, sains,
  olahraga, esports, budaya, Asia, **Indonesia** (CNBC Indonesia, detikFinance, ANTARA, CNN Indonesia,
  Tempo, Republika, Okezone, Google News Indonesia), dan forecasting.
- Setiap sumber punya **jenis** (resmi / lab / media / agregator / analisis / riset / komunitas),
  **kegunaan** untuk riset pasar prediksi, dan **peringatan** bila condong atau sensasional.
- Ditolak dan dicatat alasannya: Reuters, AP, Yahoo (menolak koneksi), CNN (feed basi 3 tahun),
  WHO dan Calculated Risk (basi > 200 hari), Anthropic/Mistral/LMSYS (tidak ada RSS), dan lainnya.
- Tiap feed punya simpanan sendiri (3 menit untuk Google News, 30–60 menit untuk rilis resmi), dan
  permintaan menunggu maksimal 8 detik — feed lambat tidak menahan panel.
- Panel sektor sekarang mengambil feed kategorinya, bukan menyaring judul yang memuat kata
  "politics".
- Setiap pengambilan mengisi korpus `data/news/corpus-*.jsonl` — bahan belajar ML berita.

**News ML** (semuanya lokal, dapat dijelaskan): relevansi TF-IDF antara berita dan pertanyaan pasar,
nada leksikon Inggris + Indonesia dengan negasi, **pengelompokan cerita** (ringkasan ekstraktif yang
hanya mengulang judul yang benar-benar terbit, diurutkan menurut jumlah sumber berbeda), dan **kata
yang paling cepat naik** (percepatan liputan, bukan frekuensi). Apakah fitur berita menambah
informasi di atas model diuji otomatis setelah 60 prediksi selesai.

## 6. Claude (opsional)

Tombol "Minta pendapat Claude" mengirim pertanyaan, aturan, tenggat, harga, dan maksimal 20 judul
paling relevan. Keluaran JSON terstruktur: sikap (YES/NO/UNCLEAR), peluang, poin yang mengutip nomor
judul, apa yang bisa mengubah pembacaan, dan risiko aturan penyelesaian — dalam Bahasa Indonesia.

- Model bawaan **`claude-opus-5`** (ganti lewat `BH_LLM_MODEL`), dengan `fallbacks: "default"`
  (beta `server-side-fallback-2026-07-01`): permintaan yang ditolak pengaman model dijalankan ulang
  di model cadangan dalam panggilan yang sama.
- Penolakan (`stop_reason: "refusal"`) diperiksa sebelum isi dibaca; peluang di luar 0–1 dijepit;
  nomor judul yang tidak ada dibuang.
- Hasil disimpan sejam untuk pasar dan judul yang sama. Biaya perkiraan per analisis ikut
  ditampilkan (Opus 5: $5 / $25 per juta token masuk / keluar).
- Tanpa `ANTHROPIC_API_KEY`, tombolnya menjelaskan langkah pengisiannya; semua fitur lain tetap jalan.

## 7. Mengikuti LLM terbaru

Kartu **Latest LLMs** di halaman Tech membaca blog OpenAI, Google DeepMind, Google AI, Hugging Face,
NVIDIA, Meta, AWS, serta pelacak yang menguji model dalam hitungan jam (Simon Willison, The Decoder,
Latent Space, Interconnects, Import AI, Techmeme, dan lainnya). Judul yang menyebut keluarga model
(Claude, GPT, Gemini, Llama, Qwen, DeepSeek, Grok, Mistral, …) dikumpulkan, dan yang memuat kata
rilis ditandai. Kartu **Claude models** membaca Models API bila kunci terisi, atau daftar
terdokumentasi (Claude Fable 5.1, Opus 5, Sonnet 5, Haiku 4.5, Opus 4.8) bila tidak.

## 8. `CLAUDE.md` dan VS Code

Ekstensi VS Code menjalankan Claude Code yang sama dengan CLI, jadi memorinya sama:

- `CLAUDE.md` di akar proyek (baru dibuat) dimuat otomatis di awal setiap sesi — perintah uji,
  prinsip kejujuran, arsitektur, konvensi bahasa, dan kondisi jaringan.
- `CLAUDE.local.md` untuk catatan pribadi yang tidak ikut git; `~/.claude/CLAUDE.md` untuk semua proyek.
- *Auto memory* di `~/.claude/projects/<proyek>/memory/` diisi Claude sendiri (profil dan tujuan
  paper test sudah tercatat).
- `/memory` membuka dan mengedit berkas-berkas itu; `/context` menunjukkan mana yang sudah dimuat.

## 9. Keterbatasan yang sengaja dinyatakan

- Model dilatih dari Manifold (uang main) dan belum terbukti mengalahkan harga pasar.
- 702 pasar Manifold yang selesai lebih awal tanpa tanggal di pertanyaannya tidak dipakai; hasil YES
  menjelang tenggat bisa sedikit diremehkan karenanya.
- IEV Polymarket butuh CLOB, yang diblokir dari koneksi ini hari ini. IEV Limitless dan Manifold
  tersedia; buku Limitless sering tipis dan kadang hanya berisi pesanan 0,1¢/99,9¢.
- Nada berita adalah hitungan kata, bukan arah YES/NO; relevansi adalah kemiripan kata, bukan makna.
- Selama beberapa hari pertama paper test tidak punya hasil — pasar harus selesai dulu.

## 10. Validasi

```
python -m unittest discover -s tests     155 uji, lulus (35 baru: data publik, inventaris sumber,
                                         fallback Limitless/Manifold, kartu ML sektor, penjaga port)
python tests/browser_smoke.py            lulus — termasuk Overview (radar ML, snapshot, cerita, data
                                         publik), filter Sources, kartu ML sektor + detail, Deadlines,
                                         Paper Test, EV/IEV yang bertahan saat panel menyegar, lebar ponsel
node --check static/js/*.js              lulus
```

Uji baru mengunci hal yang paling mudah salah: IEV nol pada buku tak bersilangan, EV konsensus tidak
pernah positif, fitur latih identik dengan fitur live, transaksi setelah snapshot tidak mengubah
snapshot, sinyal sintetis dikenali dan noise tidak, pasar bertenggat tidak jelas dikeluarkan, tanggal
dari pertanyaan dibaca dengan benar, settle tidak menebak, posisi tidak dibuka dua kali, win rate
selalu bersama break-even, dan skema JSON Claude valid untuk structured outputs.

Satu kesalahan selama pengerjaan dicatat: uji API versi awal membersihkan simpanan
`polymarket_status` dan `manifold` di `data/cache` yang asli (simpanan respons ber-TTL 30–60 detik,
termasuk potret Polymarket 12 September). Ujinya sudah diperbaiki untuk memakai folder sementara.
