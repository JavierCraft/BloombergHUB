"""Bloomberg Hub — web entry point.

    python app.py            run the dashboard on 127.0.0.1:5000
    BH_DEBUG=1 python app.py reload on edit

Routes live in `src/web/`; this file only wires them up.
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from src.web import create_app  # noqa: E402

app = create_app()


def _local(host: str) -> str:
    return "127.0.0.1" if host in ("", "0.0.0.0") else host


def port_taken(host: str, port: int) -> bool:
    """True when something already answers on host:port.

    On Windows two servers can bind the same port (Werkzeug sets SO_REUSEADDR),
    and the older one keeps receiving the requests: on 17 September 2026 a
    server started days earlier answered every page, so none of the new code
    was visible. A connect test catches that case; a bind test does not.
    """
    try:
        with socket.create_connection((_local(host), port), timeout=0.5):
            return True
    except OSError:
        return False


def running_hub(host: str, port: int, timeout: float = 2.0) -> dict | None:
    """The Bloomberg Hub server on host:port as it describes itself, or None.

    A Bloomberg Hub from before `/api/version` existed answers that path with
    its own 404 envelope, and is reported as `{"app": ..., "legacy": True}` —
    old by definition. Anything else on the port is not ours.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a proxy
    try:
        with opener.open(f"http://{_local(host)}:{port}/api/version", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        finally:
            exc.close()
        envelope = isinstance(body, dict) and {"ok", "meta", "error"} <= set(body)
        return {"app": "bloomberg-hub", "legacy": True} if envelope else None
    except (OSError, ValueError):
        return None
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) and data.get("app") == "bloomberg-hub" else None


def port_message(port: int, hub: dict | None, current_version: int) -> list[str]:
    """What to tell someone whose `python app.py` found the port busy."""
    if hub is None:
        return ["", f"  Port {port} sudah dipakai program lain (bukan Bloomberg Hub).", "",
                "  Lihat programnya (PowerShell):",
                f"    Get-NetTCPConnection -LocalPort {port} -State Listen | "
                "ForEach-Object { Get-Process -Id $_.OwningProcess }",
                "  Tutup program itu, atau jalankan Bloomberg Hub di port lain:",
                "    $env:BH_PORT=5001; python app.py", ""]
    if hub.get("legacy") or not hub.get("pid"):
        return ["", f"  Server Bloomberg Hub versi lama masih berjalan di port {port} — "
                    "browser akan melihat kode lama.", "",
                "  Hentikan dulu (PowerShell):",
                f"    Get-NetTCPConnection -LocalPort {port} -State Listen | "
                "ForEach-Object { Stop-Process -Id $_.OwningProcess }",
                "  lalu jalankan lagi:  python app.py", ""]
    pid = hub["pid"]
    since = time.strftime("%d %b %H:%M", time.localtime(hub.get("started_at") or 0))
    if hub.get("code_version") == current_version:
        return ["", f"  Bloomberg Hub SUDAH berjalan di port {port} dengan kode terbaru "
                    f"(PID {pid}, sejak {since}).",
                f"  Tidak perlu dijalankan lagi — buka http://127.0.0.1:{port}", "",
                "  Ingin menjalankannya dari terminal ini (log terlihat, berhenti dengan Ctrl+C)?",
                f"    Stop-Process -Id {pid}",
                "    python app.py", ""]
    return ["", f"  Server Bloomberg Hub LAMA masih berjalan di port {port} (PID {pid}, sejak {since}).",
            "  Kodenya berbeda dari berkas sekarang, jadi browser akan melihat versi lama.", "",
            "  Hentikan dulu, lalu jalankan lagi:",
            f"    Stop-Process -Id {pid}",
            "    python app.py", ""]


if __name__ == "__main__":
    # The reloader's child inherits the parent's listening socket, so it must not refuse.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true" and port_taken(config.HOST, config.PORT):
        from src.web import version

        hub = running_hub(config.HOST, config.PORT)
        print("\n".join(port_message(config.PORT, hub, version.code_version())))
        sys.exit(1)
    # Paper test settles and scans every BH_PAPER_AUTOSCAN_MIN minutes (default 60,
    # 0 = off). Started here, not in create_app, so tests never trigger a scan.
    from src.ml import scheduler
    from src.web import warmup

    scheduler.start()
    # Fill the ML-card caches once so the first page view is not a 15-second wait.
    # Under BH_DEBUG=1 only the reloader's child serves requests, so only it warms up.
    if not config.DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        warmup.start(app)
    banner = [
        "",
        "  BLOOMBERG HUB",
        f"  http://{config.HOST}:{config.PORT}",
        "",
        f"  trading gate : {'OPEN — hati-hati' if config.ENABLE_TRADING else 'CLOSED (default)'}",
        f"  paper test   : {f'scan otomatis tiap {config.PAPER_AUTOSCAN_MIN} menit (uang virtual)' if config.PAPER_AUTOSCAN_MIN > 0 else 'scan otomatis mati'}",
        f"  polygon rpc  : {config.POLYGON_RPC}",
        "  health       : /api/health   sumber: /sources",
        "",
    ]
    print("\n".join(banner))
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
