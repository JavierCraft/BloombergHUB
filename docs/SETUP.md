> **Pembaruan monitor 12 September 2026:** pencarian, transaksi publik, dan pencarian balik `condition_ids` Polymarket berhasil diakses pada uji sesi terbaru. Catatan pemblokiran di bawah adalah hasil audit sebelumnya. Lihat [fitur, cara membaca analisis, dan batas datanya](PERUBAHAN_MONITOR.md).

# Panduan melengkapi — langkah demi langkah

Aplikasi ini **sudah bisa dipakai tanpa satu kunci pun**. Dokumen ini untuk melengkapi bagian
yang masih tertutup.

Cek kondisi Anda kapan saja:

```bash
python run.py setup
```

Perintah itu menunjukkan apa yang sudah siap, apa yang masih kurang, dan — yang sama
pentingnya — apa yang **tidak perlu** diambil sekarang.

---

## Ringkasan: apa yang perlu dilengkapi

Diurutkan dari yang paling banyak membuka fitur. Dua yang pertama gratis; dua yang dicoret
sudah tidak.

| Urutan | Yang dilengkapi | Membuka | Waktu | Biaya |
|--------|-----------------|---------|-------|-------|
| 1 | **Data ElectIndex 2026** | 4 endpoint di **Politics** | 10 menit, 562 MB | gratis |
| 2 | **OMDb API key** | kartu **Films** di halaman **Culture** | 3 menit | gratis |
| 3 | HuggingFace token *(opsional)* | tidak membuka apa pun — hanya menaikkan batas permintaan | 2 menit | gratis |
| — | ~~TMDb API key~~ | halaman **Culture** (film) | — | **USD 149/bulan** |
| — | ~~Spotify client ID~~ | halaman **Culture** (musik) | — | **perlu Spotify Premium** |

> **Koreksi 11 September 2026.** Versi pertama panduan ini menulis TMDb dan Spotify "gratis".
> Itu salah. Keduanya sudah menutup akses gratisnya: TMDb kini meminta langganan pengembang
> **USD 149 per bulan**, dan Spotify mewajibkan **Spotify Premium** untuk membuat kunci API.
> Jangan bayar — lihat bagian [Pengganti gratis](#pengganti-gratis-untuk-halaman-culture)
> di bawah.

Sudah diperiksa dari koneksi ini pada 11 September 2026: `git clone` ke GitHub berfungsi,
dan seluruh pengganti gratis di bawah bisa dibuka.

---

## Langkah 0 — siapkan berkas `.env`

Semua kunci disimpan di satu berkas bernama `.env` di folder proyek. Berkas itu **tidak pernah
dikirim ke browser** dan **tidak pernah masuk log** — halaman Sources hanya menampilkan
sudah-diisi atau belum.

```bash
python run.py setup --buat-env
```

Perintah ini menyalin `.env.example` menjadi `.env`. Kalau `.env` sudah ada, ia menolak menimpa.

Buka `.env` dengan Notepad atau editor teks apa pun. Isinya berpasangan `NAMA=nilai`, satu per
baris, **tanpa tanda kutip dan tanpa spasi** di sekitar tanda sama dengan:

```
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxx
```

Setelah mengisi, **hentikan dan jalankan ulang** `python app.py`. Berkas `.env` hanya dibaca
sekali saat aplikasi menyala.

> Jangan bagikan berkas `.env` ke siapa pun dan jangan unggah ke GitHub. Isinya setara kata sandi.

---

## Pengganti gratis untuk halaman Culture — sudah terpasang

TMDb dan Spotify sudah berbayar, jadi halaman **Culture** sekarang berjalan di atas ketiga
sumber di bawah, dan **dua dari tiga tidak perlu kunci sama sekali**. Semuanya diuji langsung
dari koneksi ini pada 11 September 2026.

| Kebutuhan | Pengganti | Perlu kunci? | Hasil uji |
|-----------|-----------|--------------|-----------|
| Musik | **Deezer API** | tidak | pencarian dan tangga lagu menjawab, ~1,1 detik |
| Serial TV | **TVmaze API** | tidak | pencarian dan jadwal menjawab; 93 episode tayang hari ini |
| Film | **OMDb API** | ya, tapi gratis | halaman kuncinya menyatakan gratis, batas 1.000 permintaan/hari |

### Deezer — musik, tanpa kunci

Tidak ada yang perlu didaftarkan. Contoh yang sudah diuji:

```
https://api.deezer.com/search?q=dua+lipa&limit=5
https://api.deezer.com/chart/0/albums?limit=10
```

Hasil ujinya mengembalikan judul lagu, nama musisi, dan peringkat popularitas — cukup untuk
menggantikan pencarian lagu dan musisi dari Spotify.

### TVmaze — serial TV, tanpa kunci

Juga tanpa pendaftaran:

```
https://api.tvmaze.com/search/shows?q=severance
https://api.tvmaze.com/schedule/web?date=2026-09-11
```

Memberi nama serial, tanggal tayang perdana, penilaian, status, dan jadwal episode per tanggal.

### OMDb — film, kunci gratis

Ini satu-satunya yang perlu didaftarkan, dan gratis:

1. Buka <https://www.omdbapi.com/apikey.aspx>.
2. Pilih **FREE! (1,000 daily limit)**.
3. Isi email dan keterangan singkat pemakaian, lalu kirim.
4. Buka email Anda, klik tautan aktivasinya. Kuncinya ada di email itu.
5. Simpan ke `.env`:

   ```
   OMDB_API_KEY=paste_kunci_di_sini
   ```

> **Sudah tersambung, 11 September 2026.** Halaman Culture kini memanggil Deezer, TVmaze,
> dan OMDb. Musik dan serial TV langsung jalan begitu aplikasi dinyalakan — tanpa mengisi apa
> pun. Hanya kartu **Films** yang menunggu `OMDB_API_KEY`, dan sampai diisi ia mengatakannya
> terus terang alih-alih menampilkan tabel kosong.
>
> Kode Spotify dan TMDb tidak dihapus. Keduanya masih ada di `src/culture/__init__.py` dan
> tetap berfungsi bagi yang berlangganan; yang berubah hanya ini — tidak ada halaman yang
> bergantung padanya lagi.

---

## Langkah 1 — Data ElectIndex 2026 (10 menit, 562 MB)

Membuka: survei nasional, daftar lembaga survei bermasalah, dan isi lengkap dataset di halaman
**Politics**.

Ini bukan kunci API melainkan kumpulan data yang harus diunduh. Isinya seluruh bahan mentah
sebuah model prakiraan pemilu AS 2026 — survei, penilaian mutu lembaga survei, penggalangan
dana, hasil per kabupaten.

1. Pastikan `git` sudah terpasang. Cek dengan:

   ```bash
   git --version
   ```

   Kalau belum ada, unduh dari <https://git-scm.com/download/win> dan pasang dengan pengaturan
   bawaan.
2. Dari folder proyek, jalankan:

   ```bash
   git clone --depth 1 https://github.com/ElectIndex/26_us_forecast_data.git data/repos/electindex
   ```

   `--depth 1` mengambil hanya versi terbaru tanpa seluruh riwayatnya. Tanpa itu, unduhannya
   jauh lebih besar.
3. Tunggu sampai selesai. Pada koneksi biasa sekitar 5–15 menit.

**Cara memastikan berhasil:**

```bash
python run.py setup
python run.py politics --electindex
```

Perintah kedua akan menampilkan daftar berkasnya. Lalu buka halaman **Politics** — tab
"Generic ballot" dan "Banned pollsters" akan terisi.

> Dataset ini tidak mencantumkan lisensi. Perlakukan sebagai bahan riset, bukan sumber
> kebenaran — dan silang-periksa sebelum dipakai mempertaruhkan uang.

---

## Langkah 2 — HuggingFace token (opsional, 2 menit)

**Halaman Tech sudah berfungsi penuh tanpa ini.** Token hanya menaikkan batas jumlah permintaan
per jam. Ambil kalau Anda sering menekan tombol muat ulang di halaman Tech.

1. Buka <https://huggingface.co/join> dan daftar, lalu verifikasi email.
2. Buka <https://huggingface.co/settings/tokens>.
3. Klik **Create new token**.
4. Pilih tipe **Read**. Beri nama, misalnya `bloomberg-hub`.
5. Klik **Create token**, lalu salin nilainya — diawali `hf_`.

   Nilai ini **hanya ditampilkan sekali**. Kalau tertutup sebelum disalin, buat token baru.
6. Salin ke `.env`:

   ```
   HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
   ```

---

## Yang sebaiknya **tidak** Anda ambil sekarang

Empat hal ini sering dikira perlu. Tidak.

| Hal | Kenapa tidak perlu |
|-----|--------------------|
| **TMDb dan Spotify** | Sudah berbayar sejak 2026. Jangan bayar — pakai Deezer, TVmaze, dan OMDb di atas. |
| **Riot API key** | Kelas LoL ada di `src/sports/esports.py`, tapi belum ada endpoint maupun halaman yang memanggilnya. Mengambil kuncinya sekarang tidak membuka apa pun. Kunci pengembang Riot juga kedaluwarsa tiap 24 jam. |
| **Data OpenElections** | Kelas pembacanya ada, endpoint-nya belum. Sama seperti di atas. |
| **Repo backtesting** | Tidak perlu di-clone (161 MB dihemat). Halaman **Backtest** memakai simulator lokal di `src/backtest` yang sudah lengkap — Brier score, kurva ekuitas, drawdown, semuanya. |
| **Kunci Polymarket** | Alamatnya diblokir dari koneksi ini. Sejak 6 September 2026 permintaannya ditolak di tahap sertifikat. Kunci selengkap apa pun tidak akan membuat pesanan sampai. Buku pesanan di halaman **News & Markets** memakai Manifold Markets sebagai gantinya, dan itu tidak perlu kunci sama sekali. |

Kalau Anda ingin Riot atau OpenElections benar-benar tersambung ke halaman, bilang saja —
pekerjaannya menambah endpoint dan panel, bukan mengambil kunci.

---

## Sudah berjalan tanpa dilengkapi apa pun

Supaya jelas apa yang sudah Anda punya sekarang:

| Halaman | Sumber | Perlu kunci? |
|---------|--------|--------------|
| **Overview** | pengecekan kondisi semua sumber | tidak |
| **Overview** | pengecekan kondisi semua sumber | tidak |
| **Edge** | seluruh hitungan peluang, berjalan lokal | tidak, bahkan tanpa internet |
| **News & Markets** | Google News, BBC, Al Jazeera, CNBC, NPR, Hacker News, Manifold Markets | tidak |
| **On-Chain** | Polygon RPC | tidak |
| **Sports** | Understat, ESPN, NBA, NFL, StatsBomb | tidak |
| **Esports** | OpenDota, Liquipedia | tidak |
| **Politics** | GDELT + ElectIndex | tidak — datanya sudah diunduh |
| **Tech** | HuggingFace Hub | tidak (token hanya menaikkan batas) |
| **Backtest** | simulator lokal | tidak, bahkan tanpa internet |
| **Sources** | katalog internal | tidak |
| **Culture** | Deezer, TVmaze (OMDb untuk film) | tidak untuk musik & serial; film perlu kunci gratis OMDb |

Tidak ada halaman yang terhenti. Satu-satunya bagian yang masih menunggu adalah kartu
**Films** di halaman Culture, yang butuh kunci gratis OMDb — tiga menit, tanpa kartu kredit.

---

## Kalau ada yang tidak berjalan

| Gejala | Kemungkinan sebabnya |
|--------|----------------------|
| Kunci sudah diisi tapi masih "No key" | Aplikasi belum dijalankan ulang. `.env` hanya dibaca saat menyala. |
| Masih "No key" setelah dijalankan ulang | Ada tanda kutip atau spasi di sekitar `=`. Tulis `OMDB_API_KEY=abc`, bukan `OMDB_API_KEY = "abc"`. |
| `git clone` berhenti di tengah | Ulangi perintahnya setelah menghapus folder `data/repos/electindex` yang tidak lengkap. |
| Satu sumber bertanda "Upstream down" padahal biasanya sehat | Jaringan sedang lambat. Tekan **Re-check** sekali lagi sebelum menyimpulkan sumbernya rusak — hasil pengecekan ikut dipengaruhi kondisi jaringan saat itu. |
