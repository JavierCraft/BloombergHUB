# Implementasi — apa yang berubah dan kenapa

Dokumen ini mencatat modifikasi kode yang dikerjakan pada 6 September 2026, berdasarkan
[audit 40 repo](AUDIT_SUMBER_DATA.md) plus probe langsung dari mesin ini.

Prinsip yang memandu semuanya:

> Sebuah dashboard yang menampilkan sumber mati sebagai "tersedia" itu berbohong.
> Setiap status di UI ini adalah hasil probe, bukan asumsi.

---

## 1. Bug runtime yang ditemukan dan diperbaiki

Semua nyata di environment ini — Python 3.14.2, numpy 2.4.3, pandas 3.0.2, web3 8.0.0,
hexbytes 2.0.0, huggingface_hub 1.30, nflreadpy 0.1.5. Semuanya **gagal senyap atau
membingungkan**, bukan gagal jelas.

| # | Lokasi | Bug | Perbaikan |
|---|--------|-----|-----------|
| 1 | `src/sports/betting.py` | `np.math.factorial` — `np.math` dihapus di numpy 2.x, jadi **setiap** panggilan Poisson melempar AttributeError | rekurensi PMF `p(k) = p(k-1)·λ/k`, tanpa faktorial sama sekali |
| 2 | `src/sports/betting.py` | anotasi `-> pd.DataFrame` sementara `import pandas` ada di baris terakhir file | pandas dihapus total dari modul — sekarang benar-benar `COMPUTE`, murni stdlib |
| 3 | `src/polymarket/onchain.py` | hexbytes 2.0 mengubah `.hex()` jadi **tanpa prefiks `0x`** → semua perbandingan topic jadi `False` diam-diam | helper `_hex()` yang menormalkan |
| 4 | `src/polymarket/onchain.py` | deteksi fill lewat `"OrderFilled" in str(topic0)` — topic0 itu hash keccak, tidak pernah memuat teks itu, jadi cek ini **tidak pernah bisa menyala** | bandingkan ke konstanta `TOPIC_ORDER_FILLED` + hitung data word |
| 5 | `src/polymarket/onchain.py` | `NEG_RISK_CTF_EXCHANGE` diisi alamat yang sama persis dengan `CTF_EXCHANGE_V2` | dipisahkan; semua alamat diverifikasi punya bytecode di rantai |
| 6 | `src/polymarket/onchain.py` | konstanta `TOPIC_POSITION_SPLIT` salah di 20 karakter terakhirnya | seluruh 8 konstanta topic diverifikasi ulang lewat `Web3.keccak` — satu ketahuan salah |
| 7 | `src/sports/nfl.py` | `load_pfr_passing/rushing/receiving` tidak ada di nflreadpy 0.1.5 | `load_pfr_advstats(stat_type=...)` |
| 8 | `src/sports/nfl.py` | nflreadpy mengembalikan **polars**, dikirim ke `.to_dict(orient="records")` → TypeError; endpoint ini tidak pernah bisa mengembalikan satu baris pun | normalizer `src/core/frames.py` |
| 9 | `src/tech/models.py` | `list_models(direction=-1)` — argumen itu dihapus di huggingface_hub 1.30 | dihapus; urut pakai `sort="createdAt"` |
| 10 | `src/politics/__init__.py` | `import gdelt` = paket linwoodc3, **bukan** repo yang diaudit, dan tidak terpasang | klien HTTP langsung ke GDELT v2 doc API |
| 11 | `src/sports/nba.py` | `today_scoreboard()` melempar `JSONDecodeError` yang tampak seperti bug kita | `cdn.nba.com` menolak koneksi ini (403); sekarang melempar `NetworkBlocked` yang jujur |
| 12 | `app.py` | tiap request meng-import ulang modul berat, tanpa cache, tanpa timeout, tanpa taksonomi error | app factory + blueprint + cache TTL + amplop error |

---

## 2. Temuan on-chain yang memperkuat — dan mengoreksi — audit

Diverifikasi langsung ke Polygon, blok 93.282.644.

**Yang audit benar soal itu, dan sekarang bisa dinamai:**
CTF Exchange `0x4bFb41d5…B8982E` hanya mengeluarkan satu jenis event dalam blok-blok terakhir:
topic `0xbc9a2432…` dengan 0 data word. Audit menyimpulkan "ini bukan fill" dari bentuknya.
Hash-nya cocok dengan **`TokenRegistered(uint256,uint256,bytes32)`** — jadi memang pendaftaran
token, persis seperti dugaan audit.

**Yang harus dinyatakan lebih hati-hati dari audit:**
Audit menyimpulkan "matching order sudah pindah ke kontrak v2". Yang bisa saya buktikan lebih
sempit dari itu:

```
OrderFilled dalam 5.000 blok:
  CTF Exchange        0x4bFb41d5…B8982E   0
  NegRisk CTF Exchange 0xC5d563A3…20f80a   0
```

**Nol di keduanya.** Saya tidak menemukan alamat "v2" yang mengeluarkan `OrderFilled`, jadi saya
tidak mengklaimnya. Pernyataan yang jujur: *tidak satu pun dari dua alamat exchange yang dikenal
mengeluarkan fill, dan aktivitas harus dibaca di lapisan token.*

**Dan di situlah aktivitasnya terlihat** — ini jadi fitur baru, bukan sekadar catatan:

```
per 200 blok:
  ConditionalTokens 0x4D97…6045    35.377 log   TransferSingle, TransferBatch, PositionSplit
  NegRisk adapter   0xd91E…5296     1.433 log   PayoutRedemption
```

`PolygonReader.settlement_activity()` membaca ini, dan halaman Terminal menampilkannya.
Artinya: **volume Polymarket tetap terpantau meski API-nya gelap.** Yang tetap tidak bisa —
dan tidak dipura-purakan — adalah memetakan token id ke judul market; pemetaan itu hanya ada
di Gamma.

---

## 3. Yang ditambahkan

### `src/core/` — lapisan yang sebelumnya tidak ada

| Modul | Isi |
|-------|-----|
| `errors.py` | taksonomi 9 kode error + amplop JSON tunggal. UI bisa membedakan "kamu butuh `pip install`" dari "kamu butuh API key" dari "ISP-mu memblokir ini" dari "ini bug kami" |
| `cache.py` | cache TTL dua tingkat (memori + disk JSON). Bertahan melewati reload Flask. `stale()` menyajikan data kedaluwarsa berlabel umur ketika upstream mati — 10 menit lama lebih berguna daripada layar error |
| `frames.py` | normalisasi polars / pandas / list → record aman-JSON. Menangani NaN, NaT, skalar numpy, Timestamp — semuanya tidak valid di JSON dan semuanya muncul di sumber-sumber ini |
| `registry.py` | katalog risiko yang bisa dibaca mesin — **replikasi pola `catalog.json` milik `sports-skills`** |
| `health.py` | probe 3 pertanyaan per sumber, konkuren dan di-cache |

### Katalog risiko (`registry.py`)

Ini bagian yang paling langsung meniru audit. `sports-skills` menerbitkan 26 skill di mana
tepat **satu** bertanda `money_movement: true`, ditandai `critical`, di paket terpisah. Di sini:
17 sumber, **satu** yang bisa menggerakkan uang.

Nilainya bukan dokumentasi — nilainya adalah gerbang itu jadi bisa dicek:

```python
def assert_readonly(source_id):
    src = get(source_id)
    if src.money_movement or src.mode == MODE_EXECUTION:
        raise TradingDisabled(...)
    return src
```

Dipanggil di puncak setiap endpoint data. **Tabelnya yang memutuskan, bukan pemanggilnya.**

### Probe kesehatan (`health.py`)

Tiga pertanyaan, dijawab konkuren, di-cache 5 menit:

1. Paket Python-nya bisa di-import? → `MISSING_DEP` + perintah `pip install` yang bisa disalin
2. Kredensialnya terisi? → `MISSING_CREDENTIAL` + nama variabel yang mana
3. Host-nya terjangkau **dari sini**? → `NETWORK_BLOCKED`

Dua koreksi penting yang lahir saat menguji probe ini sendiri:

- **Timeout ≠ diblokir.** Versi pertama melabeli NBA dan GDELT `BLOCKED` karena lambat. Keduanya
  hidup. Sekarang timeout hanya berarti "diblokir" untuk host yang memang sudah terkonfirmasi
  diblokir; selebihnya `UPSTREAM_DOWN`. Melabeli sesuatu BLOCKED dengan yakin dan salah persis
  kesalahan yang dashboard ini ada untuk menghindarinya.
- **Blokir mengalahkan "tidak ada kunci".** Polymarket sempat dilabeli `NO KEY` — itu menyuruh
  pengguna mendaftar API key yang tidak mungkin berguna. Sekarang blokir dicek lebih dulu.

### Kalkulator edge & backtest — inti platformnya

Sebelumnya `betting.py` cuma punya de-vig multiplicative, Kelly dengan tanda tangan yang
keliru, dan Poisson yang crash. Sekarang:

- **De-vig tiga metode** — multiplicative, power, Shin — ditampilkan berdampingan.
  Di pasar dua sisi yang seimbang ketiganya sepakat; di longshot mereka berbeda, dan justru
  di situ edge prediction market berada. Metode multiplicative yang biasa dipakai orang itu
  bias: dia memotong longshot terlalu banyak.
- **Kelly dari probabilitas eksplisit** (`kelly_from_prob`). API lama menerima "edge" lalu
  merekonstruksi `p = 1/odds + edge` — diam-diam mengasumsikan harga yang ditawarkan adalah
  harga wajar pasar, yang salah setiap kali ada vig, yang berarti selalu.
- **`market_edge()`** — perhitungan yang jadi alasan platform ini ada: model bilang 62%,
  pasar bilang 55¢, ada edge tidak, dan berapa taruhannya. Default setengah-Kelly.
- **`calibration_table()`** — dekomposisi Murphy: `Brier = reliability − resolution + uncertainty`.
- **`Simulator.run()`** — replay bet dengan batas taruhan, kurva ekuitas, drawdown, Sharpe,
  dan vonis yang **menilai kalibrasi dulu, profit belakangan**:

  > "Brier 0,2409 tidak lebih baik dari menebak base rate. Model ini belum punya informasi —
  > profit apa pun di atas adalah keberuntungan."

### Jadwal esports tanpa scraper HLTV

Audit mencatat CS2 tidak tercakup, dan satu-satunya jalur populer (`gigobyte/HLTV`, ★497)
sudah basi 18 bulan. Parser API MediaWiki Liquipedia memberi jadwal **Dota 2, CS2, LoL, dan
Valorant** tanpa menyentuh scraper mana pun. Yang tetap tidak ada: statistik pemain CS2 —
itu memang butuh HLTV.

---

## 4. Antarmuka web

Ditulis ulang dua kali. Versi pertama bergaya terminal — gelap, padat, penuh paragraf
penjelasan. Versi ini menggantinya dengan tampilan yang lebih tenang dan lebih mudah dipakai.

**Arah desain.** Terang secara bawaan, lapang, dan hemat warna. Hampir semua elemen memakai
tinta hitam-abu, sehingga satu-satunya hal berwarna di layar adalah data dan status — itu yang
membuat status langsung terlihat tanpa halaman jadi ramai. Mode gelap dirancang tersendiri,
bukan hasil pembalikan otomatis.

**Bahasa.** Semua istilah teknis diganti dengan padanan yang wajar dibaca:

| Sebelum | Sesudah |
|---------|---------|
| Terminal · Edge · Backtest · Sources | Ringkasan · Peluang · Uji Model · Sumber Data |
| "Gate uang tertutup" | "Kunci transaksi masih tertutup" |
| "Kredensial belum diisi: X" | "Sumber ini butuh kunci yang belum diisi: X" |
| "Bankroll", "Fraksi Kelly" | "Modal", "Keberanian bertaruh" |
| "De-vig", "Overround" | "Buang margin bandar", "Margin bandar" |
| "Brier score" | "Skor ketepatan", dengan pembanding "asal tebak" |
| "diprobe", "host", "cache" | "dicek", "alamat", "simpanan" |
| "NO KEY", "NOT CLONED", "BLOCKED" | "Perlu kunci", "Perlu unduh", "Terblokir" |

Nama sumber dan catatannya juga ditulis ulang: `registry.py` sekarang menyimpan keterangan
yang bisa dibaca siapa pun, bukan singkatan internal.

**Lebih ringkas.** Paragraf penjelasan panjang dipindahkan ke dalam pengungkap yang tertutup
secara bawaan ("Kenapa ketepatan lebih penting daripada untung"). Halaman jadi ringkas bagi
yang sudah paham, tanpa menghilangkan penjelasan bagi yang belum.

**Palet grafik.** Tetap memakai palet data-viz yang sudah lolos uji di kedua latar:

```
gelap  (latar #1A1A1E): rentang terang LOLOS · kroma LOLOS · buta warna ΔE 9,4 LOLOS · kontras LOLOS
terang (latar #FFFFFF): rentang terang LOLOS · kroma LOLOS · buta warna ΔE 9,2 LOLOS · kontras PERINGATAN¹
```

¹ satu warna di bawah rasio 3:1 pada latar terang — aturan penggantinya terpenuhi: setiap grafik
punya tabel pendamping berisi angka yang sama.

**Empat grafik SVG tulisan tangan**, semuanya dengan garis bidik dan keterangan saat disentuh:
volume pemberitaan, nada pemberitaan (dua kutub di sekitar nol), perjalanan modal, dan ketepatan
perkiraan.

**Keadaan gagal punya bentuk sendiri:** ikon, judul singkat, penjelasan, dan — kalau bisa
diperbaiki sendiri — perintah yang tinggal disalin.

**Lainnya:** pencarian halaman (Ctrl+K), tabel bisa diurutkan, disaring, dan diunduh sebagai CSV.

**Dua perbaikan kecepatan yang lahir dari pengujian tampilan:**

- Panel aktivitas pasar semula butuh **35 detik**: ia menarik seluruh catatan kontrak
  ConditionalTokens, sekitar 170 catatan per blok. Sekarang setiap jenis kejadian diminta
  terpisah dan berbarengan, dan arus perpindahan token yang paling besar dilewati — hasilnya
  turun ke sekitar 5 detik untuk cakupan yang sama.
- Ikon dalam tombol tidak punya ukuran, sehingga tombol ganti tema tampil sebagai lingkaran
  kosong. Ukurannya sekarang ditetapkan di CSS.

## 5. Status akhir — diprobe, bukan diklaim

```
12 live · 4 degraded · 1 blocked   dari 17 sumber
```

| Status | Sumber |
|--------|--------|
| **LIVE** | Polygon RPC, GitHub, OpenDota, Liquipedia, HuggingFace, soccerdata (Understat + ESPN), NBA, NFL, StatsBomb, GDELT, + 2 compute |
| **NO KEY** | Spotify, TMDb — isi `.env` dan langsung hidup |
| **BELUM DI-CLONE** | ElectIndex, OpenElections — perintah `git clone`-nya ada di UI, bisa disalin |
| **BLOCKED** | Polymarket CLOB — dan tidak ada yang bisa memperbaikinya dari koneksi ini |

---

## 6. Yang sengaja **tidak** dikerjakan

- **Eksekusi order dari UI web.** Gate uang tertutup secara default, dan bahkan ketika dibuka,
  endpoint order menolak dengan penjelasan. Order harus dipasang dari proses terpisah yang
  memegang kunci, di mesin yang bisa menjangkau `clob.polymarket.com`. Mencampur "baca harga"
  dan "pasang order" di satu antarmuka membuat setiap bug pembacaan berpotensi jadi order tak
  disengaja — itu justru pola yang audit puji `sports-skills` karena menghindarinya.
- **Menebak judul market dari token id.** Butuh Gamma. Diblokir. Dibiarkan kosong.
- **PnL wallet per kategori.** Alasan yang sama dengan audit: tidak bisa diverifikasi, jadi
  tidak dicantumkan.
- **Scraper apa pun untuk data yang menentukan uang.** Billboard, Box Office Mojo, HLTV,
  Cinemagoer — semuanya ditinggalkan, alasannya di `AUDIT_SUMBER_DATA.md` §3.


---

## 7. Penambahan 6 September 2026 — berita, pasar, dan antarmuka

### Yang ditambahkan

| Permintaan | Cara pemenuhannya |
|-----------|-------------------|
| Logo + kutipan acak setiap membuka | Layar pembuka dengan 16 kutipan Jim Simons dalam bahasa aslinya, diacak. Kunjungan pertama dalam satu sesi menampilkan kutipan; perpindahan halaman berikutnya hanya logo — kutipan di setiap klik akan cepat mengganggu. |
| Layar memuat saat halaman berpindah | Layar yang sama, mode `memuat`. Digambar lewat CSS sebaris di `<head>` supaya sudah tampil sebelum berkas gaya selesai diunduh. |
| Jam Amerika dan Indonesia | New York dan Jakarta, dihitung browser lewat `Intl.DateTimeFormat` — tanpa jaringan, tanpa mengurus pergantian musim panas sendiri. |
| Pencarian mendalam | Satu kotak, empat sumber: halaman, **isi halaman yang sedang dibuka**, berita langsung, dan pasar prediksi. Dua yang pertama tanpa jeda, dua sisanya ditunda 400 md setelah berhenti mengetik. Alamat `?cari=…` membuat hasil pencarian bisa dibagikan. |
| Tombol buka-tutup menu samping | Menu menyempit jadi ikon, bukan hilang — arah halaman tetap terlihat. Pilihannya diingat. |
| Berita realtime, disimpan ke txt | Tujuh sumber diambil bersamaan: Google News, BBC, Al Jazeera, CNBC, NPR, Hacker News. Sekitar 2 detik. Hasilnya ditulis ke `data/news/*.txt` (bisa dibaca orang) dan `*.jsonl` (bisa dibaca program). |
| Buku pesanan pasar prediksi | Harga, pesanan limit yang menunggu, jarak harga beli-jual, dan aliran taruhan satu per satu. |

### Soal poin ketujuh: Polymarket

Polymarket tetap tidak bisa dibuka, dan bentuk kegagalannya **berubah** sejak audit sebelumnya:
dulu permintaannya kehabisan waktu, sekarang ditolak di tahap sertifikat (`SSLError`) — ciri khas
halaman pemblokir yang menyamar sebagai tujuan. Kalshi terkena hal yang sama.

Klien Polymarket tetap ditulis lengkap di `src/markets/__init__.py` dan diuji setiap kali halaman
Berita dibuka, jadi langsung bekerja dari jaringan lain tanpa perubahan kode. Tidak ada jalan
pintas untuk menembus pemblokirannya yang disediakan.

Yang dipakai sebagai gantinya: **Manifold Markets** — pasar prediksi terbuka yang bisa dibuka dari
sini tanpa kunci. Yang ditampilkan bukan tiruan, melainkan pesanan limit sungguhan yang sedang
menunggu, disusun jadi buku pesanan:

```
Beli tertinggi 35,0%  ·  jarak 18,0%  ·  jual terendah 53,0%
```

Pemetaan arahnya perlu ketelitian: di Manifold `limitProb` selalu dinyatakan sebagai peluang YES,
jadi pesanan YES adalah permintaan beli dan pesanan NO adalah penawaran jual.

Satu batas yang tidak ditutup-tutupi: Manifold tidak mengirim nama pemasang taruhan, hanya nomor
penggunanya. Yang ditampilkan adalah kode pendek dari nomor itu — bukan nama karangan. Kalau kode
yang sama muncul berulang, satu orang sedang menumpuk posisi.

---

## 8. Audit ulang — lima bug ditemukan dan diperbaiki

Setelah ketujuh permintaan berjalan, seluruhnya diperiksa ulang. Semua bug di bawah **lolos dari
pengujian jalur normal** dan hanya muncul di kondisi tepi.

| # | Bug | Akibatnya | Perbaikan |
|---|-----|-----------|-----------|
| 1 | `as_completed(timeout=…)` melempar `TimeoutError` di luar blok penangkap | Halaman Ringkasan gagal total dengan galat 500 ketika jaringan lambat | Tenggat ditangkap; sumber yang belum menjawab tetap ditampilkan dengan tanda "belum menjawab" |
| 2 | Masalah sama di pencarian mendalam | Satu sumber lambat menjatuhkan seluruh hasil pencarian | Sama; bagian yang sudah selesai tetap ditampilkan |
| 3 | `new Date(x).toISOString()` pada waktu kosong | `RangeError` yang menjatuhkan **seluruh** tabel taruhan, bukan satu barisnya | Penanggalan menerima teks ISO dan angka milidetik; nilai tak masuk akal jadi teks kosong |
| 4 | Tanpa kata kunci, pasar dicari dengan kata "news" | Yang muncul pasar yang kebetulan membahas kata "news", bukan yang teramai | Tanpa kata kunci menampilkan pasar dengan likuiditas terbesar |
| 5 | Nomor pasar divalidasi dengan `isalnum()` | Nomor bertanda hubung ditolak tanpa sebab yang jelas | Pola yang menerima huruf, angka, tanda hubung, dan garis bawah |

### Temuan tambahan: jumlah pekerja pengecekan

Saat menaikkan jumlah pekerja pengecekan kesehatan menjadi satu per sumber, hasilnya **memburuk**,
bukan membaik. Diukur langsung:

```
 4 pekerja  23,4s  hidup 14/19  gagal terhubung 0
 6 pekerja  32,8s  hidup 14/19  gagal terhubung 0
 8 pekerja  22,8s  hidup 14/19  gagal terhubung 0
12 pekerja  34,2s  hidup  8/19  gagal terhubung 6
19 pekerja  44,9s  hidup  8/19  gagal terhubung 6
```

Sembilan belas sambungan HTTPS serentak membuat jaringan mesin ini kehabisan napas, lalu enam
sumber yang sebenarnya sehat dilaporkan bermasalah. Batasnya dikembalikan ke 8.

Pengukuran berurutan juga menunjukkan satu permintaan ke sumber sehat memakan 2–5,5 detik saat
jaringan sedang lambat — tenggat lama 6 detik terlalu ketat dan ikut menyebabkan salah lapor.
Dinaikkan ke 12 detik. Setelah kedua perbaikan: **14 hidup, nol gagal terhubung.**

Ini juga jadi catatan jujur tentang alatnya sendiri: hasil pengecekan kesehatan ikut dipengaruhi
kondisi jaringan saat itu. Kalau ada sumber bertanda "Bermasalah" padahal biasanya sehat, tekan
"Cek ulang" sekali lagi sebelum menyimpulkan sumbernya yang rusak.

### Hasil pemeriksaan akhir

```
11 halaman  · rusak 0
35 endpoint · berhasil 29 · gagal-dengan-penjelasan 6 · gagal tak terjelaskan 0
```

Enam yang gagal semuanya disengaja dan menjelaskan diri: dua butuh kunci API, dua butuh unduhan
data, satu gerbang transaksi yang memang terkunci, dan satu masukan sengaja dibuat salah untuk
menguji penolakannya.

Kasus tepi yang ikut diuji dan semuanya mengembalikan amplop yang benar: kueri satu huruf, kueri
300 huruf, batas negatif, batas berlebihan, nomor pasar berkarakter aneh, topik berisi `../../`
(dibersihkan — tidak ada berkas yang lolos keluar folder), metode HTTP salah, rute tak dikenal,
dan enam permintaan penulisan berkas teks secara bersamaan.

---

# Putaran 11 September 2026 — sapuan menyeluruh aplikasi yang berjalan

Seluruh rute (11 halaman + 51 endpoint) diketuk langsung di `http://127.0.0.1:5000`, lalu
setiap kegagalan ditelusuri sampai penyebabnya. Ringkasan hasilnya: **dua error 500 yang nyata,
satu halaman yang benar-benar mati, dan empat cacat yang membuat aplikasi salah melaporkan
keadaan dirinya sendiri.** Semuanya di bawah ini.

## 1. Dua error 500 — nama liga sepak bola

Ini satu-satunya `INTERNAL` di seluruh aplikasi, artinya satu-satunya yang benar-benar bug kita.

```
/api/sports/soccer/understat?league=EPL    500  ValueError: Invalid league 'EPL'.
/api/sports/soccer/espn?league=eng.1       500  ValueError: Invalid league 'eng.1'
```

Penyebabnya: `soccerdata` 1.9 memakai nama baku lintas penyedia, sementara kode ini masih
mengirim kode internal Understat (`EPL`) dan siput URL ESPN (`eng.1`). Ditanyakan langsung ke
pustakanya, keduanya ternyata menerima daftar yang sama persis:

```python
sorted(set(sd.Understat.available_leagues()) & set(sd.ESPN.available_leagues()))
# ['ENG-Premier League', 'ESP-La Liga', 'FRA-Ligue 1', 'GER-Bundesliga', 'ITA-Serie A']
```

| Berkas | Perubahan |
|--------|-----------|
| `src/sports/soccer.py` | konstanta `LEAGUES`, `DEFAULT_LEAGUE`, `DEFAULT_SEASON`; `_normalise()` yang **tetap menerima kode lama** lewat tabel alias sehingga tautan lama tidak mati; nama yang benar-benar asing kini jadi `BAD_REQUEST` yang menyebut pilihan yang sah, bukan 500 |
| `src/sports/soccer.py` | `liga_tersedia()` menanyakan daftarnya ke pustakanya, bukan memakai daftar tetap — kalau nanti ada liga yang berganti nama, gejalanya jadi "daftar liga memendek", bukan "halaman rusak" |
| `src/sports/soccer.py` | `_pilih_kolom()` menyaring kolom berguna saja; kolom yang hilang **mempersempit** tabel, bukan menggagalkan permintaan |
| `src/web/api.py` | endpoint baru `/api/sports/soccer/leagues`; kedua endpoint jadwal tidak lagi memaksakan liga bawaan sendiri |
| `templates/sports.html` | pemilih liga berisi 5 liga, diisi dari endpoint di atas; tabelnya memakai judul kolom Inggris dan menampilkan gol serta xG kedua tim |

Hasil sesudahnya — dan sengaja diuji juga dengan kode lama serta kode karangan:

```
/api/sports/soccer/understat                     ok rows=380  date,home_team,away_team,home_goals,away_goals,home_xg,away_xg
/api/sports/soccer/espn                          ok rows=380  date,home_team,away_team,game_id
/api/sports/soccer/understat?league=EPL          ok rows=380  (alias lama, tetap jalan)
/api/sports/soccer/espn?league=eng.1             ok rows=380  (alias lama, tetap jalan)
/api/sports/soccer/understat?league=ESP-La Liga  ok rows=380
/api/sports/soccer/understat?league=Mars         BAD_REQUEST  Liga 'Mars' tidak dikenal.
```

## 2. Tab NBA "Today" yang tidak mungkin berhasil

Tab itu memanggil `cdn.nba.com`, yang menolak koneksi ini dengan 403 secara permanen. Perilakunya
sudah jujur — melempar `NETWORK_BLOCKED`, bukan menyamar sukses — tapi sebuah tab yang **tidak
akan pernah** berisi data tidak pantas dipajang di antarmuka.

Yang penting: `stats.nba.com` tetap terbuka dari sini. Jadi tabnya diganti, bukan dihapus.

- `src/sports/nba.py` — `recent_games()` memakai `LeagueGameFinder` (2.805 baris tersedia),
  diurutkan dari yang terbaru, kolomnya dipangkas ke yang terbaca orang.
- `src/web/api.py` — endpoint `/api/sports/nba/recent`.
- `templates/sports.html` — tab **Today** diganti **Recent games**.
- `/api/sports/nba/today` **tidak dihapus**: catatan blokirnya tetap bisa dibaca lewat API dan
  halaman Sources, hanya tidak lagi dipajang sebagai tombol yang pasti gagal.

## 3. Halaman Culture: dipindahkan ke sumber yang masih gratis

Sebelum putaran ini, seluruh halaman Culture adalah dinding `MISSING_CREDENTIAL` yang **tidak
bisa diperbaiki dengan mengisi kunci apa pun** — karena kedua sumbernya pindah ke model berbayar
(TMDb USD 149/bulan, Spotify mewajibkan Premium). Ini kegagalan yang berbeda jenis dari yang
lain: bukan rusak, melainkan mati.

Penggantinya dipilih dengan syarat yang sama seperti sumber lain di aplikasi ini — API resmi,
bukan pembaca halaman web — dan ketiganya diuji langsung dari koneksi ini:

| Kebutuhan | Pengganti | Kunci | Hasil uji |
|-----------|-----------|-------|-----------|
| Musik | Deezer | tidak perlu | pencarian + tangga lagu, ~0,4 detik |
| Serial TV | TVmaze | tidak perlu | pencarian + jadwal, 93 episode hari ini |
| Film | OMDb | gratis, 1.000/hari | belum diisi — melapor apa adanya |

Berkas yang berubah: `src/culture/__init__.py` (kelas `DeezerMusic`, `TVmazeShows`,
`OMDbMovies`, plus `culture_status()`), `src/web/api.py` (8 endpoint Culture disusun ulang),
`templates/culture.html` (ditulis ulang), `src/core/registry.py` (3 sumber baru),
`config.py` dan `.env.example` (`OMDB_API_KEY`), `run.py` dan `docs/SETUP.md`.

Tiga keputusan yang sengaja diambil:

1. **Kode Spotify dan TMDb tidak dihapus.** Keduanya masih ada dan masih berfungsi bagi yang
   berlangganan. Yang berubah hanya: tidak ada halaman yang bergantung padanya lagi. Di katalog
   Sources keduanya sekarang bervonis "Ditinggalkan", dengan alasannya tertulis.
2. **Kartu Films tidak dipalsukan.** OMDb tidak punya daftar "sedang tayang" atau "tren", jadi
   halaman ini menampilkan pencarian film — bukan daftar susunan sendiri yang hanya *tampak*
   seperti data resmi. Selama `OMDB_API_KEY` kosong, kartunya mengatakannya terus terang.
3. **Tangga album Deezer memang hanya lima entri** — `total` di responsnya ikut mengatakan 5
   berapa pun limit yang diminta. Itu ditulis sebagai catatan di atas tabel, supaya tabel pendek
   tidak terbaca sebagai pengambilan data yang gagal. `serve()` diberi parameter `note=` untuk ini.

```
/api/culture/status                       ok rows=5
/api/culture/music/chart?type=tracks      ok rows=40
/api/culture/music/search?q=dua lipa      ok rows=30
/api/culture/tv/schedule                  ok rows=60
/api/culture/tv/search?q=severance        ok rows=3
/api/culture/movies/search?q=dune         MISSING_CREDENTIAL  OMDB_API_KEY
/api/culture/movies/detail?id=bukanid     BAD_REQUEST  harus berupa id IMDb
/api/culture/music/chart?type=xyz         BAD_REQUEST  hanya menerima tracks atau albums
```

## 4. GDELT: satu diagnosis yang salah, dan satu kebiasaan buruk kita sendiri

Halaman Politics melaporkan ketiga kartunya gagal. Ditelusuri, ada **dua** masalah terpisah, dan
salah satunya kita sendiri penyebabnya.

**Yang pertama — kita menembak berbarengan.** Ketiga kartu berangkat serentak, jadi ketiganya
tiba di GDELT dalam detik yang sama dan ketiganya ditolak, termasuk yang sebenarnya masih berhak
lewat.

**Yang kedua — batasnya jauh lebih ketat dari yang mereka tulis.** Pesan penolakannya berbunyi
*"Please limit requests to one every 5 seconds"*. Diukur langsung:

```
jeda  0 detik antar panggilan          ->  0/3 berhasil
jeda  2 detik                          ->  0/3 berhasil
jeda  5 detik                          ->  1/3 berhasil
jeda  7 detik, setelah diam 75 detik   ->  0/3 berhasil
diam 180 detik penuh, satu panggilan   ->  200, dan panggilan itu memakan 37 detik
```

Baris terakhir itu membuka cacat ketiga: batas waktu di kode adalah **30 detik**, sementara
panggilan yang berhasil memakan **37 detik**. Artinya panggilan yang sebenarnya baik-baik saja
dilaporkan sebagai `NETWORK_BLOCKED` — menyuruh pemakainya mencari masalah di jaringannya sendiri
padahal GDELT-nya saja yang lambat. Itu persis jenis kebohongan yang dilarang prinsip di atas.

| Berkas | Perubahan |
|--------|-----------|
| `src/politics/__init__.py` | gerbang laju setingkat modul: kunci bersama + jeda minimum 7 detik, jadi tiga kartu **mengantre**, tidak berebut |
| `src/politics/__init__.py` | batas waktu 30 → **75 detik**; lambat tidak lagi salah dilaporkan sebagai diblokir |
| `src/politics/__init__.py` | `RateLimited` menyebut 300 detik, bukan 60 — sesuai jendela penalti yang terukur |
| `src/core/registry.py` | TTL simpanan 1800 → **3600 detik**, supaya satu pengambilan yang berhasil menutupi seluruh jendela penalti |
| `templates/politics.html` | ketiga kartu dimuat **berurutan**, dengan pembatalan kalau pencarian baru menyusul |
| `templates/politics.html` | bagian penjelasan baru: apa arti pesannya, angka pengukurannya, dan kenapa menekan Search berkali-kali justru memperburuk |

Yang tidak bisa diperbaiki dari sini: GDELT membatasi per alamat IP, dan alamat ini sedang
dibatasi keras. Yang bisa dikerjakan sudah dikerjakan — tidak pernah menembak berbarengan,
mendiagnosis dengan benar, dan menyimpan hasil cukup lama. Saat GDELT menolak, halaman
menampilkan data tersimpan terakhir beserta umurnya, bukan grafik kosong.

## 5. Pemeriksaan kesehatan yang berkata "Belum dicek" padahal sudah

Data ElectIndex sudah ada di cakram — 46 berkas, endpoint-nya menjawab — tapi halaman Sources
menulis detailnya "Belum dicek". Penyebabnya: sumber ini tidak punya alamat untuk diketuk, jadi
setelah lolos pemeriksaan berkas ia jatuh ke cabang jaringan dan mengambil teks bawaan di sana.

`src/core/health.py` — cabang data lokal sekarang berhenti di tempatnya sendiri dan melaporkan
jumlah berkas yang benar-benar ada: `Sudah diunduh — 46 berkas di data/repos/electindex`.

## 6. 2.573 berkas kumpulan data yang tidak pernah ditampilkan — termasuk seluruh prakiraan per balapan

Ini temuan terbesar putaran ini, dan ditemukan justru saat memperbaiki hal kecil di nomor 12.
Saat memeriksa hitungan berkas, angkanya tidak masuk akal: pemeriksaan kesehatan menghitung
**2.619** berkas, sementara tab **Files** di halaman Politics menampilkan **46**.

Keduanya benar, dan itulah masalahnya. `list_files()` memakai `iterdir()`, yang hanya melihat
akar folder. Isi sebenarnya:

```
data/repos/electindex/                46 berkas   (yang selama ini ditampilkan)
                     /output/          29 berkas
                     /output/historical/  2.038 berkas  — potret model tiap hari sejak 1 Januari
                     /output/races/       506 berkas  — prakiraan per balapan
                                        ---------
                                        2.619 berkas, 205,5 MB
```

Jadi halaman yang berjudul "seluruh bahan mentah sebuah model prakiraan" sebenarnya
memperlihatkan **1,7 persen** isinya.

Dan yang tidak terlihat itu justru bagian paling berguna. Tiap berkas di `output/races/`
adalah satu balapan dengan **satu baris per hari**:

```
$ OH-SEN.csv  ->  253 baris x 71 kolom
       date  days_out  dem_prob  rep_prob  margin   rating  polling_avg  poll_count
 2026-09-09        55      51.5      48.5    0.27  Toss-up         5.00          18
 2026-09-10        54      52.6      47.4    0.54  Toss-up         5.16          19
```

Itu persis bentuk yang dibutuhkan untuk pekerjaan inti dashboard ini: **peluang model per hari,
untuk diadu dengan harga pasar per hari.** Datanya sudah ada di cakram sejak diunduh, dan tidak
ada satu pun endpoint yang menyentuhnya.

| Berkas | Perubahan |
|--------|-----------|
| `src/politics/__init__.py` | `list_files()` berjalan rekursif dan menyertakan kolom `folder`; isi `.git` tidak dihitung |
| `src/politics/__init__.py` | `races()` — 506 balapan dengan keadaan terakhirnya, plus kolom turunan `toss_up` (jarak dari 50 persen), diurutkan dari yang paling ketat. Seluruh 506 berkas terbaca dalam 1,5 detik, lalu disimpan |
| `src/politics/__init__.py` | `race(code)` — deret harian satu balapan, 10 kolom yang terbaca orang dari 71 yang ada |
| `src/web/api.py` | `/api/politics/electindex/races` dan `/api/politics/electindex/race?code=…`; batas `files` dinaikkan 100 → 3.000 supaya daftarnya tidak terpotong sejak awal |
| `templates/politics.html` | kartu **Race forecasts** dengan saringan All / Senate / Governor / House, dan kartu grafik yang terbuka saat satu baris diklik |

Soal keamanan masukan: nama berkas disusun **setelah** kodenya lolos pola
`[A-Z]{2}-(?:SEN|GOV|[0-9]{1,2})`, jadi tidak ada masukan yang bisa menunjuk ke luar folder.
Diuji langsung:

```
?code=OH-SEN            ok rows=253
?code=oh-sen            ok rows=253   (huruf kecil diterima)
?code=PA-SEN            BAD_REQUEST   Balapan 'PA-SEN' tidak ada di kumpulan data ini
                                      (benar — Pennsylvania memang tidak punya
                                       pemilihan Senat pada 2026)
?code=../../etc/passwd  BAD_REQUEST   Kode balapan tidak sesuai bentuk
(tanpa code)            BAD_REQUEST   Parameter 'code' wajib
```

Lima balapan paling ketat per 10 September 2026, langsung dari endpoint barunya:

```
TX-15   House 15   Toss-up   Dem 48,6   Rep 51,4   margin -0,22
FL-09   House 9    Toss-up   Dem 48,5   Rep 51,5   margin -0,26
OH-SEN  Senate     Toss-up   Dem 52,6   Rep 47,4   margin +0,54
AZ-02   House 2    Toss-up   Dem 52,8   Rep 47,2   margin +0,59
ME-SEN  Senate     Toss-up   Dem 53,1   Rep 46,9   margin +0,72
```

Daftar berkasnya sekarang menampilkan **2.619 baris di 256 folder**, bukan 46.

## 7. Hasil sapuan akhir

```
62 rute diketuk
11 halaman   · rusak 0
51 endpoint  · berhasil 45 · gagal-dengan-penjelasan 6 · gagal tak terjelaskan 0
```

Enam yang gagal semuanya menjelaskan diri dan tidak satu pun bug: dua menunggu kunci gratis OMDb,
tiga dibatasi GDELT, satu diblokir permanen oleh `cdn.nba.com`.

Pemeriksaan tambahan yang ikut dijalankan:

- **Seluruh blok `<script>` di 12 template plus `hub.js`** diperiksa `node --check` — 0 salah sintaks.
- **Setiap `BH.xxx` yang dipanggil halaman** dicocokkan dengan daftar ekspor `hub.js` —
  0 pemanggilan ke fungsi yang tidak ada.

## 8. Satu hal di luar aplikasi yang perlu Anda tahu

Cakram **C: penuh — 0 byte tersisa** dari 275 GB. Ini bukan akibat aplikasi ini (seluruh
datanya ada di D:, 12,6 GB terpakai dari 200 GB), tapi cepat atau lambat akan menggigit:
Windows, pip, dan berkas sementara semuanya menulis ke C:. Satu penulisan berkas sementara
dalam sesi ini memang sudah gagal karena itu.
