"""Fill the slow caches once, right after the server starts.

The machine-learning cards on Overview and the sector pages pull markets from
Limitless and Manifold and rebuild Manifold price momentum from trade history:
10–15 seconds on a cold cache, measured. Without this, the first person to open
a page after a restart watches skeleton rows for that long — and on 17 September
2026 "I see no changes" was exactly the complaint.

Requests go through Flask's test client, so they take the same path (and fill
the same cache entries) as a browser would. Runs once, in a daemon thread, only
from `python app.py` — never under tests.
"""
from __future__ import annotations

import threading
import time

PATHS = (
    "/api/ml/status",
    "/api/deadlines?days=3&limit=60",
    "/api/deadlines?sector=politics&days=14&limit=40",
    "/api/deadlines?sector=technology&days=14&limit=40",
    "/api/deadlines?sector=soccer&days=14&limit=40",
    "/api/deadlines?sector=esports&days=14&limit=40",
    "/api/deadlines?sector=culture&days=14&limit=40",
    "/api/markets/polymarket?sector=politics",
    "/api/markets/polymarket?sector=technology",
    "/api/datahub/coingecko-prices",
    "/api/datahub/gold-api",
    "/api/datahub/frankfurter",
    "/api/datahub/fear-greed",
    "/api/datahub/bmkg-gempa",
)

_STATE = {"started": False, "done": 0, "failed": [], "elapsed_s": None}


def status() -> dict:
    return dict(_STATE)


def _run(app, delay_s: float) -> None:
    time.sleep(delay_s)
    started = time.monotonic()
    with app.test_client() as client:
        for path in PATHS:
            try:
                response = client.get(path)
                if response.status_code >= 400:
                    _STATE["failed"].append({"path": path, "status": response.status_code})
            except Exception as exc:  # noqa: BLE001 — a warm-up must never take the server down
                _STATE["failed"].append({"path": path, "error": type(exc).__name__})
            _STATE["done"] += 1
    _STATE["elapsed_s"] = round(time.monotonic() - started, 1)


def start(app, delay_s: float = 3.0) -> bool:
    if _STATE["started"]:
        return False
    _STATE["started"] = True
    threading.Thread(target=_run, args=(app, delay_s), name="bh-warmup", daemon=True).start()
    return True
