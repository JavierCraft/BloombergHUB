"""Titik masuk Vercel: satu fungsi serverless yang melayani seluruh aplikasi.

Vercel menjalankan modul ini dan memakai variabel `app` (WSGI) sebagai handler;
`vercel.json` mengarahkan setiap path ke sini, jadi routing tetap milik Flask.

Dua hal yang berbeda dari `python app.py`:

  * Sistem berkasnya hanya-baca kecuali `/tmp`, jadi `BH_DATA_DIR` dipindah ke
    sana. `/tmp` bertahan selama satu instans masih hangat, sehingga lapisan
    cache disk tetap bekerja antar-permintaan dan hilang saat instans diganti —
    persis seperti cache yang memang boleh hilang.
  * Penjadwal paper test dan pemanasan cache TIDAK dijalankan. Keduanya hanya
    ada di `app.py __main__`; sebuah fungsi serverless tidak punya proses yang
    hidup terus untuk menjalankannya, dan buku besar paper test butuh disk yang
    permanen.

Paket berat (scikit-learn, pandas, web3, pustaka olahraga) sengaja tidak
dipasang di sini: lihat `api/requirements.txt`. Endpoint yang memerlukannya
menjawab `MISSING_DEP` (424) dengan nama paketnya, bukan berpura-pura kosong.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Harus diatur sebelum `config` diimpor: di sanalah folder data dibaca.
os.environ.setdefault("BH_DATA_DIR", "/tmp/bloomberg-hub-data")
os.environ.setdefault("BH_PAPER_AUTOSCAN_MIN", "0")

from src.web import create_app  # noqa: E402

app = create_app()
