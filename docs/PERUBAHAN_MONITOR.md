# Pembaruan News, Markets, dan aktivitas — 12 September 2026

## Penyempurnaan kontrol dan akurasi tampilan

- Pilihan topik, Enter, tombol Cari, dan analisis pertandingan langsung memperbarui headline sektor sekaligus pasar.
- Leaderboard mengenali kata AI, mengabaikan waktu publikasi di masa depan, dan menampilkan hingga tiga contoh berita dengan tautan sumber untuk topik terpilih. Peringkat tetap berdasarkan frekuensi judul dalam sampel, bukan statistik pencarian publik.
- Respons headline yang terlambat tidak menimpa pencarian terbaru; URL ganda dalam satu respons hanya ditampilkan sekali.
- Status headline menampilkan waktu pengambilan data, membedakan cache lama, serta menandai kegagalan sebagian sumber.
- Nilai uang dan perubahan harga yang tidak tersedia ditampilkan sebagai tanda kosong, bukan nol.

Validasi tambahan: 54 uji unit; uji browser mencakup pilihan AI, detail leaderboard dan perpindahan headline sesuai keyword, selain cakupan halaman sebelumnya. API pada uji browser menggunakan fixture lokal.

Putaran kedua. Catatan putaran pertama (11 September) ada di bagian bawah.

## Yang baru di putaran ini

### Berita mengalir, bukan dimuat ulang

`Latest headlines` di **News & Markets** dan di panel sektor (Politics, Tech, Culture, Soccer,
Esports) sekarang berjalan sebagai **aliran**. Tiap 15 detik panel menanyakan sumber, lalu
**menambahkan judul yang belum pernah tampil di bagian atas daftar** — daftarnya tidak digambar
ulang. Akibatnya posisi gulir, judul yang sedang dibaca, dan bagian yang sedang dibuka tetap di
tempatnya. Judul yang baru tiba ditandai garis hijau selama enam detik supaya perubahannya
terlihat.

Sebelumnya panel ini memakai jalur muat biasa, yang **mengosongkan lalu menggambar ulang**
seluruh daftar tiap 30 detik. Itu benar untuk tabel dan salah untuk feed: tiap pembaruan
membuang daftar yang sedang dibaca dan mengembalikan gulirnya ke atas.

Aliran ini punya kepala status sendiri: titik hidup, waktu pengecekan terakhir, jumlah yang baru
masuk, dan jumlah yang sedang di layar. Kalau sumber gagal, titiknya merah, keterangan
kegagalannya muncul, dan **isi yang sudah ada tetap dipertahankan**. Daftar dibatasi 150 judul
supaya tab yang ditinggal semalaman tidak tumbuh tanpa batas. Pilihan `Auto refresh` di News &
Markets mengatur irama aliran ini, termasuk mematikannya.

`Related markets`, `Order book`, dan `Polymarket` ikut diperbarui pada irama yang sama. Ketiganya
memakai jalur muat biasa yang mempertahankan isi dan bagian yang terbuka saat pembaruan gagal.

### Analisis pasar: kenapa condong ke sana, dan apa artinya untuk masuk

Tiap pasar Polymarket di panel sektor kini membawa pembacaan turunan — `src/markets/analysis.py`.
Semuanya aritmetika atas angka yang memang dikirim Gamma; tidak ada sumber baru yang ditarik.

| Bagian | Isinya |
|--------|--------|
| **Kecondongan** | sisi teratas, harganya, selisih poin dari sisi kedua, dan labelnya (seimbang / condong / dominan / sangat dominan) |
| **Sifat** | mapan, tenang, bergerak, atau **repricing baru** — dibaca dari perubahan 1 jam, 24 jam, dan sepekan |
| **Buku** | tebal atau tipis, dari likuiditas; plus spread sebagai biaya masuk |
| **Hitungan masuk** | harga masuk, untung jika benar, rugi jika salah (selalu 100%), sisa hari, biaya bolak-balik |
| **Strategi** | sikap bersyarat (periksa / hati-hati / tunggu / hindari) beserta alasan yang menghasilkannya |

Yang dipisahkan dengan sengaja: pasar 87% yang tidak bergerak sepekan di buku tebal, dan pasar
87% yang baru melompat 20 poin dalam sejam di buku tipis, **adalah harga yang sama dan situasi
yang sama sekali berbeda**. Membedakan keduanya adalah tugas bagian "Sifat".

Panel sektor juga mendapat **ringkasan kategori**: jumlah pasar, total volume 24 jam, seberapa
terkonsentrasi volumenya di satu pasar, lalu pasar tersibuk, penggerak terbesar, yang paling
berimbang, yang paling mapan, dan yang paling cepat selesai.

**Yang tidak dilakukan modul ini:** menyebut *sebab* harga bergerak. Itu butuh model kausal yang
belum ada di proyek ini, dan kecocokan kata kunci antara judul berita dan pertanyaan pasar bukan
model kausal. Berita di panel sebelah tetap berlabel **kandidat** penyebab.

Angka setara-per-tahun hanya ditampilkan bila penyelesaiannya masih **tujuh hari atau lebih**.
Menahunkan biner yang selesai empat hari lagi menghasilkan angka seperti "1.792% per tahun":
benar secara aritmetika, tidak berguna sebagai pembanding, karena taruhannya tidak bisa diulang
90 kali.

### Esports: odds pasar dan indikasi form di dalam jadwal

`Match schedule` kini satu tabel dari tiga sumber — endpoint baru `/api/esports/board`.

* **Logo game** di tiap baris, monokrom, terbaca di mode terang maupun gelap.
* **Model lean** — harapan Elo dari rating OpenDota. Hanya Dota 2; game lain menyatakan bahwa
  sumber ratingnya belum tersambung, bukan menebak.
* **Polymarket** — favorit, persentasenya, selisih poin, volume 24 jam, bar proporsi, tautan
  pasar, dan **skor keyakinan pencocokan nama**.

Pencocokan jadwal ke pasar dilakukan atas nama tim yang dinormalkan (`src/markets/fixtures.py`).
Kegagalan terburuk di sini adalah menempelkan odds pertandingan lain ke nama tim yang benar, jadi
**kedua** nama harus cocok, keyakinannya adalah yang terlemah dari keduanya, dan di bawah 0,5
barisnya dinyatakan tidak punya pasar — lengkap dengan kandidat terdekatnya untuk diperiksa
manusia. Sekitar sepertiga jadwal Liquipedia punya pasar; sisanya turnamen tier bawah yang memang
tidak dihargai Polymarket.

Dua slug tag Polymarket ternyata bukan yang terduga: `cs2` dan `lol` hanya berisi pasar futures
dan prop ("Will FaZe win a Tier 1 event in 2026?") tanpa satu pun pertandingan. Pasar per-laga ada
di `counter-strike-2` dan `league-of-legends`.

### Soccer: kartu `Match odds`

Polymarket memecah satu pertandingan sepak bola menjadi **tiga pasar Yes/No terpisah** — menang
tuan rumah, seri, menang tamu. Endpoint baru `/api/sports/soccer/fixtures` menyusunnya kembali
menjadi satu baris 1X2, diurutkan dari yang paling cepat selesai.

Jumlah ketiga harga jarang tepat 100%. Selisihnya **dilaporkan sebagai overround**, bukan
dinormalkan diam-diam — angka 0,5 poin yang muncul pada sebagian besar baris justru bukti bahwa
penyusunan ulangnya benar.

### On-Chain: waktu, uang, dan nama pasarnya

Tiga perubahan pada `Market activity`.

**1. Cap waktu yang selama ini kosong.** Polygon adalah rantai proof-of-authority: `extraData`-nya
105 byte, bukan 32. Tanpa middleware POA, tiap panggilan `get_block` melempar
`ExtraDataLengthError` — dan karena panggilan itu dibungkus `except` kosong, kegagalannya muncul
sebagai `time_utc: null` di **setiap** event, bukan sebagai error. Middleware-nya kini dipasang,
dan waktunya terbaca.

**2. Uangnya dihitung dari seluruh rentang.** Jumlah event selalu mencakup seluruh jendela blok,
tetapi nilainya dulu tidak dihitung sama sekali. Sekarang **semua** log didekode — bukan hanya 20
yang ditampilkan — untuk menghasilkan total USDC, terbesar, terkecil, median, rata-rata, jumlah
wallet berbeda, jumlah pasar berbeda, dan laju event per menit. Menjumlahkan 20 baris yang
kebetulan tampil lalu menyebutnya "total" adalah angka salah yang berpakaian angka presisi.

**3. Nama pasarnya muncul.** Event ConditionalTokens hanya membawa `conditionId`. Gamma menjawab
lagi dari koneksi ini dan menerima `condition_ids` sebagai parameter berulang, jadi id itu kini
dicari balik ke pertanyaan pasar yang diselesaikannya, beserta harga outcome saat pencarian.
`0x2bfe…b906c7 · 5 USDC` sekarang terbaca sebagai
*Counter-Strike: B8 vs Nuclear TigeRES — B8 60,5%*.

Tiga aturan kejujuran yang dipegang pencarian ini: id yang tidak dikenal Gamma **dibiarkan kosong**
(ConditionalTokens kontrak bersama Gnosis — layanan lain memakainya juga, dan "tidak ketemu"
adalah jawaban); harga yang ditampilkan adalah harga **saat pencarian**, bukan harga eksekusi
event — event split/merge/redeem memang tidak memuat harga sama sekali; dan kegagalan Gamma
menurunkan hasilnya jadi "tidak dikenali", tidak pernah jadi label yang salah.

### Tiga perbaikan kecil yang ikut ditemukan

**Ikon peringatan sebesar layar.** Bar "sumber sedang bermasalah" menyisipkan SVG inline yang
punya `viewBox` tanpa ukuran intrinsik. Sebagai anak flex ia tumbuh sebesar ruang yang tersedia —
satu jam raksasa setinggi viewport. Tiap tempat lain yang menyisipkan ikon serupa sudah
memberinya ukuran; yang ini tidak pernah, karena jalurnya hanya menyala ketika sumber mati **dan**
cache-nya sudah lewat TTL. Kini diberi ukuran, dan diuji di browser.

**Volume sepak bola selalu kosong.** Nilai perputaran diambil dari pasar "menang tuan rumah" saja,
padahal satu pertandingan tersebar di tiga buku. Sekarang dijumlahkan dari ketiganya.

**Jadwal esports jatuh bersama Liquipedia.** Liquipedia membatasi permintaan cukup ketat sehingga
TTL 60 detik mengubah perpindahan tab biasa jadi 503. TTL dinaikkan ke 300 detik — tickernya
bergerak dalam hitungan jam, bukan detik — dan ketika Liquipedia tetap tidak menjawab, daftar
diisi dari pertandingan yang sedang dihargai Polymarket, **dengan label** bahwa itu bukan seluruh
jadwal turnamen. Odds tidak ikut mati bersama jadwalnya.

## Cara membaca analisis

Harga outcome adalah probabilitas tersirat, bukan persentase orang yang memasang taruhan. Arus BUY
menunjukkan tekanan pembelian dalam **sampel**, bukan seluruh posisi crowd. Harga, arus transaksi,
win rate historis, dan selisih rating adalah empat ukuran yang berbeda.

`breakeven` bukan ramalan. Ia harga yang membuat taruhan impas: perkiraan peluang Anda sendiri
harus melampauinya, dan program ini tidak punya pendapat tentang berapa perkiraan Anda seharusnya.
Karena itu keluaran bagian Strategi selalu **bersyarat** — sikap plus kondisi yang harus berlaku,
tidak pernah "beli ini".

Model lean Dota adalah harapan Elo **per pertandingan** dari selisih rating, bukan peluang
memenangkan seri Bo3/Bo5, dan belum mengoreksi roster, patch, draft, atau kualitas lawan terakhir.
Tim yang tidak cocok secara unik di OpenDota, atau yang ratingnya berasal dari kurang dari 20
pertandingan, dibiarkan kosong.

Pasar hasil pencocokan pertandingan tetap **kandidat**: cocokkan kedua tim, waktu, turnamen, dan
aturan resolusinya sebelum memakai odds-nya.

Angka ConditionalTokens adalah batas atas aktivitas Polymarket, bukan angka pasti. Kegagalan RPC
ditampilkan sebagai tidak tersedia, bukan nol. Token collateral yang desimalnya belum diketahui
tetap memakai jumlah mentah dan tidak ikut dijumlahkan ke dolar.

## Validasi

```powershell
python -m unittest discover -s tests -v
node --check static/js/hub.js
node --check static/js/monitor.js
python tests/browser_smoke.py
```

52 uji unit lulus. Yang baru menutup: pembacaan kecondongan dan deteksi repricing, ambang buku
tipis, breakeven dan penekanan angka tahunan di bawah tujuh hari, pemilihan ringkasan kategori,
pencocokan nama yang menolak kecocokan dari kata pengisi, penolakan pasar prop/per-game saat
mencari pasar seri, penyusunan ulang 1X2 beserta overround-nya, penolakan pencocokan separuh,
presedensi nama persis pada rating OpenDota, abstain saat ambigu maupun saat sumber gagal, id
condition yang tidak dikenal, dan — yang paling penting — bahwa total USDC dihitung dari 50 event
pada rentang, bukan dari 20 baris yang ditampilkan.

Uji browser memakai Selenium/Firefox dengan API contoh: enam halaman, lebar ponsel, aliran berita
yang menambahkan judul baru tanpa menggambar ulang, isi yang dipertahankan saat sumber gagal,
blok analisis dan strategi, odds/form/logo pada jadwal esports, 1X2 sepak bola, serta nama pasar
dan statistik pada On-Chain.

Uji jaringan langsung sesi ini: Gamma, Data API, OpenDota, Liquipedia, dan Polygon RPC menjawab;
pencocokan jadwal menghasilkan 10/30 (Dota 2), 11/30 (CS2), 4/30 (LoL), 4/30 (Valorant); 31
pertandingan sepak bola berharga; dan pemindaian settlement 12 blok mengenali 30 dari 50 condition
ID. Keberhasilan satu sesi tidak menjamin sumber selalu tersedia.

---

# Putaran pertama — 11 September 2026

## Yang tersedia

- **News & Markets:** pilihan AI, Trump, Elon Musk, Bitcoin, Election, Outbreak, War, dan Inflation; leaderboard topik; headlines, Related markets, buku pesanan terpilih, dan Polymarket diperbarui otomatis.
- **Politics, Tech, Culture, Soccer, Esports:** panel berita dan Polymarket bersama. Keyword awal/kosong mengambil kategori Polymarket; keyword lain mencari lintas kategori. Daftar kategori diurutkan menurut volume 24 jam dalam hasil yang diambil.
- **Leaderboard:** frekuensi kata dalam maksimal 30 judul yang diambil, dibatasi 24 jam terakhir; minimal muncul dalam dua judul. Ini sampel topik pemberitaan, **bukan volume pencarian publik**, dan belum mengukur percepatan tren.
- **Polymarket:** semua outcome dan persentase harganya, volume total/24 jam, likuiditas, spread, perubahan harga outcome pertama, waktu sumber, tenggat, dan aturan resolusi.
- **Arus taruhan:** tombol di setiap pasar membaca maksimal 100 transaksi taker dari Data API. Menampilkan BUY/SELL per outcome, porsi nilai BUY, wallet dalam sampel, rentang waktu, dan rincian transaksi. Perhitungannya `price × size`; penjualan tidak masuk denominator porsi BUY.
- **On-Chain:** tiap jenis event memiliki maksimal 20 detail terbaru: block/log index, wallet/oracle, collateral, jumlah mentah dan unit USDC, condition/question ID, partition/payout vector, hash transaksi, dan tautan PolygonScan.
- **Esports:** empat logo game lokal monokrom. Tombol Analisis membuka perbandingan form Dota dari OpenDota. Riwayat dibatasi maksimal 20 game dalam 90 hari; minimal lima game per tim.

## Referensi implementasi

- [Polymarket market data](https://docs.polymarket.com/market-data/overview)
- [Pencarian keyword](https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles)
- [Kategori events](https://docs.polymarket.com/api-reference/events/list-events)
- [Transaksi publik dan parameter takerOnly](https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets)
- [ABI ConditionalTokens](https://github.com/gnosis/conditional-tokens-contracts/blob/master/contracts/ConditionalTokens.sol)
- [OpenDota API](https://docs.opendota.com/)
- [Middleware POA web3.py](https://web3py.readthedocs.io/en/stable/middleware.html#proof-of-authority)
- Logo: lihat `static/img/games/ATTRIBUTION.md`.
