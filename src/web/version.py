"""Which code a running server holds — so a second `python app.py` can tell the
user whether the server already on the port is current or stale.

On 17 September 2026 a server started six days earlier kept answering on port
5000 and hid a day of changes. Later the same day a *current* server, started in
the background, was reported as "probably an old server" and the user was left
guessing what to do. A server now reports its PID, start time, and the newest
modification time of the code it loaded, at `/api/version`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
APP_ID = "bloomberg-hub"
# What a restart changes: Python modules, templates (cached without BH_DEBUG), static files.
WATCHED = (("src", "*.py"), ("templates", "*.html"), ("static", "*"))
TOP_LEVEL = ("app.py", "config.py")

_STARTED: dict = {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.is_file() else 0.0
    except OSError:          # deleted while walking the tree
        return 0.0


def code_version(base: Path = BASE_DIR) -> int:
    """Newest modification time, in whole seconds, of the code a server would load."""
    newest = 0.0
    for folder, pattern in WATCHED:
        root = base / folder
        if root.is_dir():
            for path in root.rglob(pattern):
                if "__pycache__" not in path.parts:
                    newest = max(newest, _mtime(path))
    for name in TOP_LEVEL:
        newest = max(newest, _mtime(base / name))
    return int(newest)


def mark_started() -> dict:
    """Record the code this process serves. Called once, from `create_app()`."""
    if not _STARTED:
        _STARTED.update(app=APP_ID, pid=os.getpid(), started_at=int(time.time()),
                        code_version=code_version())
    return dict(_STARTED)


def snapshot() -> dict:
    return dict(_STARTED) if _STARTED else mark_started()
