"""Aplikasi pada pemasangan ramping — tanpa scikit-learn/numpy, disk hanya-baca.

Bentuk yang dipakai Vercel (`api/index.py`): paket berat tidak muat dalam batas
250 MB satu fungsi serverless, dan sistem berkasnya hanya-baca kecuali /tmp.
Dua hal itu pernah membuat aplikasi mati total, bukan berkurang fungsinya:

  * `BaseEstimator = ClassifierMixin = object` membuat `AnchoredLogit(object,
    object)` gagal dengan "duplicate base class", sehingga `import src.ml.model`
    meledak dan halaman Deadlines ikut mati;
  * `config.py` membuat folder data saat diimpor, yang gagal di disk hanya-baca.

Uji ini menjalankan penafsir terpisah karena scikit-learn memang terpasang di
mesin pengembangan; pemblokir impor meniru mesin yang tidak punya.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BLOCK = """
import sys

class Blocker:
    BLOCKED = {"sklearn", "numpy", "scipy", "pandas", "polars", "joblib", "web3"}

    def find_module(self, name, path=None):
        return self if name.split(".")[0] in self.BLOCKED else None

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BLOCKED:
            raise ImportError(f"{name} sengaja diblokir oleh uji")
        return None

sys.meta_path.insert(0, Blocker())
sys.path.insert(0, %r)
"""


def run_without_heavy_packages(body: str, data_dir: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, BH_DATA_DIR=data_dir, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", (BLOCK % str(ROOT)) + body],
                          capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=120)


class SlimInstall(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="bh-slim-test-")

    def test_model_module_imports_without_sklearn(self):
        """Tanpa scikit-learn, `AnchoredLogit` tetap punya dua basis yang berbeda."""
        done = run_without_heavy_packages(
            "from src.ml import model\n"
            "print('BASES', len(set(model.AnchoredLogit.__bases__)))\n",
            self.data_dir)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("BASES 2", done.stdout)

    def test_deadline_prediction_explains_itself_without_a_model(self):
        """Tanpa model terlatih, prediksi menjawab alasannya — bukan ImportError numpy."""
        done = run_without_heavy_packages(
            "from src.ml import model\n"
            "out = model.predict_market({'p_yes': 0.5, 'deadline_ms': 0})\n"
            "print('AVAILABLE', out['available'], bool(out.get('reason')))\n",
            self.data_dir)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("AVAILABLE False True", done.stdout)

    def test_every_page_renders_without_heavy_packages(self):
        done = run_without_heavy_packages(
            "from src.web import create_app\n"
            "from src.web.pages import NAV\n"
            "c = create_app().test_client()\n"
            "bad = [n['path'] for n in NAV if c.get(n['path']).status_code != 200]\n"
            "print('BAD', bad)\n",
            self.data_dir)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("BAD []", done.stdout)

    def test_missing_package_is_424_not_500(self):
        """Endpoint yang butuh paket berat menyebut paketnya, bukan 'kesalahan internal'."""
        done = run_without_heavy_packages(
            "from src.web import create_app\n"
            "r = create_app().test_client().get('/api/paper/summary')\n"
            "b = r.get_json()\n"
            "print('STATUS', r.status_code, b['error']['code'], b['error']['detail']['package'])\n",
            self.data_dir)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("STATUS 424 MISSING_DEP scikit-learn", done.stdout)

    def test_data_dir_follows_env(self):
        done = run_without_heavy_packages(
            "import config\n"
            "print('DATA', str(config.DATA_DIR))\n"
            "print('CACHE', str(config.ML_DIR.parent))\n",
            self.data_dir)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"DATA {self.data_dir}", done.stdout)

    def test_read_only_data_dir_does_not_break_import(self):
        """Folder data yang tidak bisa dibuat tidak boleh menggagalkan impor config."""
        blocked = str(Path(self.data_dir) / "tidak-ada-izin")
        done = run_without_heavy_packages(
            "import pathlib\n"
            "_real = pathlib.Path.mkdir\n"
            "pathlib.Path.mkdir = lambda *a, **k: (_ for _ in ()).throw(OSError('read-only'))\n"
            "import config\n"
            "print('OK', config.DATA_DIR.name)\n",
            blocked)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("OK tidak-ada-izin", done.stdout)


if __name__ == "__main__":
    unittest.main()
