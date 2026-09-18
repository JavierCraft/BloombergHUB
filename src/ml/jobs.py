"""Background jobs with progress, for work longer than an HTTP request.

Training pulls hundreds of price histories and can take minutes. The request
that starts it returns at once; the page polls `/api/ml/status` and draws the
progress this module records. One job per kind at a time — pressing "Train"
twice attaches to the running job instead of starting a second one that would
hammer the same API.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable

from src.ml import store

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def get(kind: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(kind)
        return _public(job) if job else None


def running(kind: str) -> bool:
    with _LOCK:
        job = _JOBS.get(kind)
        return bool(job and job["state"] in ("queued", "running"))


def start(kind: str, fn: Callable[..., Any], summarise: Callable[[Any], Any] | None = None,
          **kwargs) -> dict:
    """Start `fn(progress=..., **kwargs)` in a daemon thread, unless one is running."""
    with _LOCK:
        current = _JOBS.get(kind)
        if current and current["state"] in ("queued", "running"):
            return {**_public(current), "attached": True}
        job = {
            "id": uuid.uuid4().hex[:10], "kind": kind, "state": "queued",
            "started_at": store.now_iso(), "finished_at": None,
            "stage": "menunggu", "done": 0, "total": 0, "message": "", "elapsed_s": 0.0,
            "result": None, "error": None, "_t0": time.monotonic(),
        }
        _JOBS[kind] = job

    def progress(stage: str, done: int, total: int, note: str = "") -> None:
        with _LOCK:
            job.update(stage=stage, done=done, total=total, message=note,
                       elapsed_s=round(time.monotonic() - job["_t0"], 1))

    def run() -> None:
        with _LOCK:
            job["state"] = "running"
        try:
            result = fn(progress=progress, **kwargs)
            with _LOCK:
                job.update(state="done", result=summarise(result) if summarise else result)
        except Exception as exc:  # noqa: BLE001 — the error is the job's result
            with _LOCK:
                job.update(state="failed",
                           error=getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}",
                           trace=traceback.format_exc().splitlines()[-4:])
        finally:
            with _LOCK:
                job.update(finished_at=store.now_iso(),
                           elapsed_s=round(time.monotonic() - job["_t0"], 1))

    threading.Thread(target=run, name=f"bh-job-{kind}", daemon=True).start()
    return _public(job)
