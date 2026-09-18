"""Background loop: settle and scan every N minutes.

`BH_PAPER_AUTOSCAN_MIN` sets N (default 60; 0 turns it off). A paper test only means
something if predictions are logged before the outcome is known, on a regular
rhythm, including on days nobody opens the page — that is what this is for.

Under `BH_DEBUG=1` Flask's reloader runs the app twice; the loop starts only in
the child process that actually serves requests, so scans are not doubled.
The same can be done without the web app from Windows Task Scheduler:

    python run.py paper --scan
"""
from __future__ import annotations

import os
import threading
import time

import config
from src.ml import store

_STATE: dict = {"started": False, "interval_min": 0, "last_run": None, "last_result": None,
                "last_error": None, "next_run": None, "runs": 0}
_LOCK = threading.Lock()


def status() -> dict:
    with _LOCK:
        return dict(_STATE)


def _loop(minutes: int) -> None:
    time.sleep(90)   # let the app finish starting before the first network burst
    while True:
        from src.ml import model, paper

        started = time.monotonic()
        try:
            if model.status()["trained"]:
                result = paper.scan()
                summary = {k: result.get(k) for k in ("ok", "scanned", "predictions_logged", "opened_count")}
                summary["settled"] = (result.get("settled") or {}).get("settled_positions")
            else:
                result = paper.settle()
                summary = {"ok": True, "settle_only": True, "settled": result.get("settled_positions")}
            with _LOCK:
                _STATE.update(last_run=store.now_iso(), last_result=summary, last_error=None)
        except Exception as exc:  # noqa: BLE001 — the loop must survive a bad tick
            with _LOCK:
                _STATE.update(last_run=store.now_iso(),
                              last_error=getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}")
        with _LOCK:
            _STATE["runs"] += 1
        wait = max(60.0, minutes * 60 - (time.monotonic() - started))
        with _LOCK:
            _STATE["next_run"] = store.ms_to_iso(store.now_ms() + wait * 1000)
        time.sleep(wait)


def start() -> bool:
    minutes = int(config.PAPER_AUTOSCAN_MIN or 0)
    if minutes <= 0:
        return False
    if config.DEBUG and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False
    with _LOCK:
        if _STATE["started"]:
            return False
        _STATE.update(started=True, interval_min=minutes,
                      next_run=store.ms_to_iso(store.now_ms() + 90_000))
    threading.Thread(target=_loop, args=(minutes,), name="bh-paper-autoscan", daemon=True).start()
    return True
