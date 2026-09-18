# Deploy ke Vercel

Bloomberg Hub berjalan di Vercel sebagai **satu fungsi serverless** (`api/index.py`)
yang memuat aplikasi Flask yang sama dengan `python app.py`. Tidak ada cabang kode
khusus hosting; yang berbeda hanya dependensi yang dipasang dan tempat menulis data.

## Kenapa ini berguna

Dari koneksi rumah, `gamma-api.polymarket.com` dan `clob.polymarket.com` diblokir ISP
(`SSLError`). Jaringan Vercel tidak memblokirnya, jadi salinan yang berjalan di sana
adalah cara membaca Polymarket tanpa VPN.

## Yang ikut diunggah, dan yang tidak

`data/` berisi 357 MB simpanan yang dibuat sendiri oleh aplikasi — cache API,
riwayat harga pasar, dataset dan model ML, buku besar paper test, korpus berita,
dan repo yang diunduh. Semuanya bisa dibangun ulang dan tidak ada gunanya di
sistem berkas hanya-baca, jadi `.vercelignore` menahannya bersama `artifacts/`,
`docs/`, `tests/`, dan `.env`. Yang tersisa untuk diunggah sekitar 2,5 MB.

## Batas 250 MB, dan akibatnya

Satu fungsi serverless Vercel dibatasi 250 MB setelah dibuka. Diukur dari roda
`manylinux` untuk Python 3.12:

| paket | ukuran setelah dibuka |
|---|---|
| scipy | 118,4 MB |
| numpy | 57,8 MB |
| scikit-learn | 33,4 MB |
| **jumlah** | **209,6 MB** |

Angka itu belum termasuk berkas `.pyc` yang dibuat saat pemasangan maupun kode
aplikasi, jadi scikit-learn tidak muat dengan aman. `api/requirements.txt` karena
itu hanya memasang `flask`, `requests`, dan `python-dotenv`.

**Tetap berjalan penuh:** ke-13 halaman, katalog sumber, Polymarket Gamma/CLOB,
Limitless, Manifold, panel sektor, Deadlines (harga, arah, EV, IEV), berita
157 feed, dan 36 API Data Hub.

**Menjawab `MISSING_DEP` (424) beserta nama paketnya:** model tenggat dan paper
test (`scikit-learn`), analisis berita TF-IDF (`scikit-learn`), on-chain Polygon
(`web3`), dan halaman olahraga (`soccerdata`, `nba_api`, `nflreadpy`,
`statsbombpy`). Ini penerapan prinsip "status adalah hasil probe": endpointnya
menyebut paket yang kurang, bukan menampilkan nol atau galat internal.

Model tenggat memang tidak dikirim ke sana dengan sengaja — ia dilatih ulang di
mesin lokal, vonis terakhirnya "belum bisa dibedakan dari kebetulan", dan buku
besar paper test butuh disk permanen yang tidak dimiliki fungsi serverless.

## Disk

Sistem berkas Vercel hanya-baca kecuali `/tmp`. `api/index.py` mengatur
`BH_DATA_DIR=/tmp/bloomberg-hub-data` sebelum `config` diimpor, jadi seluruh
lapisan cache disk pindah ke sana: ia bertahan selama satu instans masih hangat
dan hilang saat instans diganti — persis sifat yang memang boleh dimiliki cache.
Penjadwal paper test dan pemanasan cache tidak dijalankan (keduanya hanya ada di
`app.py __main__`).

## Langkah deploy

Repo sudah berisi `vercel.json`, `.vercelignore`, dan `api/`. Pilih salah satu:

**Lewat dasbor (paling mudah, otomatis tiap `git push`)**

1. Buka <https://vercel.com/new>, pilih repo `JavierCraft/BloombergHUB`.
2. Framework Preset: **Other**. Build/Output/Install Command dikosongkan.
3. Deploy.

**Lewat CLI**

```powershell
npm i -g vercel
vercel login            # membuka browser
vercel --prod
```

## Environment Variables (semua opsional)

Isi di Project Settings → Environment Variables. Tanpa satu pun kunci, aplikasi
tetap jalan; endpoint yang butuh kunci menjawab `MISSING_CREDENTIAL` (424).

| variabel | akibat bila diisi |
|---|---|
| `ANTHROPIC_API_KEY` | analisis berita dengan Claude aktif |
| `OMDB_API_KEY`, `TMDB_API_KEY` | sisi film halaman Culture |
| `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET` | sisi musik halaman Culture |
| `HF_TOKEN` | batas laju Hugging Face lebih longgar |
| `BH_SECRET_KEY` | ganti nilai bawaan `bloomberg-hub-local` |

`BH_ENABLE_TRADING` **jangan** diisi. Gerbang uang tertutup secara bawaan dan
`py-clob-client` memang tidak dipasang di sini, jadi jalur order tidak ada.

## Batas waktu

`vercel.json` menyetel `maxDuration: 60`. Panggilan terlama yang terukur adalah
`/api/deadlines?source=limitless` pada 16,4 detik dari koneksi rumah; bawaan
10 detik akan memotongnya.

## Yang dijaga oleh uji

`tests/test_deploy_slim.py` menjalankan aplikasi di penafsir terpisah dengan
pemblokir impor untuk `sklearn`, `numpy`, `scipy`, `pandas`, `polars`, `joblib`,
dan `web3`, lalu memastikan ke-13 halaman tetap 200, endpoint berat menjawab 424
dengan nama paketnya, dan `config` tetap bisa diimpor saat `mkdir` gagal. Jalankan
uji itu setelah menambah impor tingkat-modul di `src/` — impor berat harus tetap
berada di dalam fungsi.
