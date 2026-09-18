"""Files under `data/ml/`. JSON and JSONL only, written atomically.

Layout:

    data/ml/raw/<source>/<id>.json   price path of one resolved market (immutable)
    data/ml/dataset.jsonl            training rows, rebuilt by each training run
    data/ml/models/deadline.joblib   the fitted estimator
    data/ml/models/deadline.json     its report: data, out-of-sample metrics, verdict
    data/ml/paper/positions.jsonl    paper positions (rewritten on settlement)
    data/ml/paper/predictions.jsonl  every prediction the scanner made
    data/ml/paper/ringkasan.txt      the same, readable in Notepad
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import config

ROOT = Path(config.ML_DIR)
RAW = ROOT / "raw"
MODELS = ROOT / "models"
PAPER = ROOT / "paper"
LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_to_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # Manifold sends milliseconds; guard against seconds sneaking in.
        return int(value if value > 10**11 else value * 1000)
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def ms_to_iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:120] or "_"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False, default=str) for r in rows]
    with LOCK:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(path)
    return len(lines)


def read_jsonl(path: Path) -> list[dict]:
    try:
        with LOCK:
            text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)
