"""`python app.py` on a busy port: say what is there, and what to do about it.

17 September 2026, twice: first a six-day-old server hid a day of changes; then
a current server started in the background was described as "probably an old
server" and the user had to ask for help. The guard now asks the server on the
port who it is and whether its code matches the files on disk.
"""
import logging
import os
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, jsonify  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import app as app_module  # noqa: E402
from src.web import version  # noqa: E402


logging.getLogger("werkzeug").setLevel(logging.ERROR)


@contextmanager
def serving(wsgi_app):
    server = make_server("127.0.0.1", 0, wsgi_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.socket.getsockname()[1]
    finally:
        server.shutdown()


def touch(path: Path, stamp: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (stamp, stamp))


class VersionTests(unittest.TestCase):
    def test_code_version_is_the_newest_code_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            touch(base / "src" / "ml" / "model.py", 1_000_000)
            touch(base / "templates" / "index.html", 2_000_000)
            touch(base / "static" / "js" / "ml.js", 1_500_000)
            touch(base / "app.py", 1_200_000)
            # Not code: caches and data change without a restart mattering.
            touch(base / "src" / "ml" / "__pycache__" / "model.cpython-314.py", 9_000_000)
            touch(base / "data" / "cache" / "x.json", 9_000_000)
            self.assertEqual(version.code_version(base), 2_000_000)
            touch(base / "config.py", 3_000_000)
            self.assertEqual(version.code_version(base), 3_000_000)

    def test_endpoint_reports_this_process(self):
        body = app_module.app.test_client().get("/api/version").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["app"], "bloomberg-hub")
        self.assertEqual(body["data"]["pid"], os.getpid())
        self.assertIsInstance(body["data"]["code_version"], int)


class PortTests(unittest.TestCase):
    def test_detects_a_listening_port(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertTrue(app_module.port_taken("127.0.0.1", port))
        self.assertFalse(app_module.port_taken("127.0.0.1", port))

    def test_reads_a_running_hub(self):
        with serving(app_module.app) as port:
            hub = app_module.running_hub("127.0.0.1", port)
        self.assertEqual(hub["pid"], os.getpid())
        self.assertEqual(hub["code_version"], version.snapshot()["code_version"])

    def test_older_hub_and_other_programs(self):
        legacy = Flask("legacy-hub")

        @legacy.errorhandler(404)
        def _not_found(_e):
            return jsonify(ok=False, data=None, meta={}, error={"code": "NOT_FOUND"}), 404

        other = Flask("someone-else")      # Werkzeug's own HTML 404
        with serving(legacy) as port:
            self.assertEqual(app_module.running_hub("127.0.0.1", port), {"app": "bloomberg-hub", "legacy": True})
        with serving(other) as port:
            self.assertIsNone(app_module.running_hub("127.0.0.1", port))

    def test_a_listener_that_never_answers_is_not_a_hub(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            self.assertIsNone(app_module.running_hub("127.0.0.1", server.getsockname()[1], timeout=0.3))


class MessageTests(unittest.TestCase):
    HUB = {"app": "bloomberg-hub", "pid": 70708, "started_at": 1_789_633_491, "code_version": 100}

    def text(self, hub, current=100):
        return "\n".join(app_module.port_message(5000, hub, current))

    def test_current_server_says_open_it_and_how_to_take_it_over(self):
        message = self.text(self.HUB)
        self.assertIn("SUDAH berjalan", message)
        self.assertIn("http://127.0.0.1:5000", message)
        self.assertIn("Stop-Process -Id 70708", message)
        self.assertNotIn("LAMA", message)
        self.assertNotIn("lama", message)

    def test_server_with_different_code_is_called_old(self):
        message = self.text(self.HUB, current=101)
        self.assertIn("LAMA", message)
        self.assertIn("Stop-Process -Id 70708", message)
        self.assertNotIn("Tidak perlu dijalankan", message)

    def test_legacy_hub_and_foreign_program(self):
        legacy = self.text({"app": "bloomberg-hub", "legacy": True})
        self.assertIn("versi lama", legacy)
        self.assertIn("Get-NetTCPConnection -LocalPort 5000 -State Listen | ForEach-Object { Stop-Process", legacy)
        foreign = self.text(None)
        self.assertIn("bukan Bloomberg Hub", foreign)
        self.assertIn("ForEach-Object { Get-Process -Id $_.OwningProcess }", foreign)
        self.assertIn("$env:BH_PORT=5001; python app.py", foreign)


if __name__ == "__main__":
    unittest.main()
