"""Two-tier TTL cache: process memory in front of a JSON file store.

Why this exists rather than `functools.lru_cache`:

  * stats.nba.com and GDELT rate-limit aggressively — GDELT already answered 429
    during the audit probe. A cache is the difference between a usable dashboard
    and a dashboard that bans you.
  * the disk tier survives a Flask reload, so hitting save on a template does not
    re-hammer every upstream.
  * `stale()` lets a caller serve expired data with an honest age label when the
    upstream is down. Showing 10-minute-old NBA numbers marked "stale" beats
    showing an error.

Keys are hashed, so callers can pass any JSON-able argument tuple.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

_LOCK = threading.RLock()
_MEM: dict[str, tuple[float, Any]] = {}

_DISK_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"


def _key(namespace: str, payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    return f"{namespace}.{digest}"


def _disk_path(key: str) -> Path:
    return _DISK_DIR / f"{key}.json"


def get(namespace: str, args: Any, ttl: float) -> tuple[Any, float] | None:
    """Return `(value, age_seconds)` if a fresh entry exists, else None."""
    key = _key(namespace, args)
    now = time.time()

    with _LOCK:
        hit = _MEM.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1], now - hit[0]

    path = _disk_path(key)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stamp = float(raw["t"])
            if now - stamp < ttl:
                with _LOCK:
                    _MEM[key] = (stamp, raw["v"])
                return raw["v"], now - stamp
        except (OSError, ValueError, KeyError):
            pass
    return None


def stale(namespace: str, args: Any, max_age: float = 86_400) -> tuple[Any, float] | None:
    """Return an *expired* entry (value, age) — for degraded serving only."""
    key = _key(namespace, args)
    now = time.time()

    with _LOCK:
        hit = _MEM.get(key)
    if hit and now - hit[0] < max_age:
        return hit[1], now - hit[0]

    path = _disk_path(key)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stamp = float(raw["t"])
            if now - stamp < max_age:
                return raw["v"], now - stamp
        except (OSError, ValueError, KeyError):
            pass
    return None


def put(namespace: str, args: Any, value: Any) -> None:
    key = _key(namespace, args)
    stamp = time.time()
    with _LOCK:
        _MEM[key] = (stamp, value)
    try:
        _DISK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _disk_path(key).with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"t": stamp, "ns": namespace, "v": value}, default=str),
            encoding="utf-8",
        )
        tmp.replace(_disk_path(key))
    except (OSError, TypeError, ValueError):
        # A cache that cannot write to disk is still a working memory cache.
        pass


def cached_call(
    namespace: str,
    args: Any,
    ttl: float,
    fn: Callable[[], Any],
    serve_stale_on_error: bool = True,
) -> tuple[Any, bool, float]:
    """Run `fn` unless a fresh entry exists.

    Returns `(value, was_cached, age_seconds)`. On failure, falls back to a stale
    entry when one exists rather than showing the user nothing.
    """
    hit = get(namespace, args, ttl)
    if hit is not None:
        return hit[0], True, hit[1]

    try:
        value = fn()
    except Exception:
        if serve_stale_on_error:
            old = stale(namespace, args)
            if old is not None:
                return old[0], True, old[1]
        raise

    put(namespace, args, value)
    return value, False, 0.0


def clear(namespace: str | None = None) -> int:
    """Drop cache entries. Returns how many were removed."""
    removed = 0
    with _LOCK:
        for key in list(_MEM):
            if namespace is None or key.startswith(f"{namespace}."):
                del _MEM[key]
                removed += 1
    if _DISK_DIR.exists():
        for path in _DISK_DIR.glob("*.json"):
            if namespace is None or path.name.startswith(f"{namespace}."):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def stats() -> dict:
    files = list(_DISK_DIR.glob("*.json")) if _DISK_DIR.exists() else []
    return {
        "memory_entries": len(_MEM),
        "disk_entries": len(files),
        "disk_bytes": sum(f.stat().st_size for f in files),
        "dir": str(_DISK_DIR),
    }
