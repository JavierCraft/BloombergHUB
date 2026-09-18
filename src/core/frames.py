"""Normalise anything tabular into JSON-safe records.

Three real problems this solves, all of which broke the previous code:

  1. `nflreadpy` returns **polars**, not pandas. `df.to_dict(orient="records")`
     raises TypeError on a polars frame — polars uses `to_dicts()`.
  2. pandas emits NaN / NaT / numpy scalars / Timestamps, none of which are valid
     JSON. `jsonify` either raises or silently produces `NaN`, which breaks
     `JSON.parse` in the browser.
  3. Some endpoints return 100k rows. Sending them all locks up the tab, so we
     truncate and say so in the envelope instead of pretending.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any

MAX_ROWS_DEFAULT = 500


def _scalar(value: Any) -> Any:
    """Coerce one cell into something `json.dumps` accepts."""
    if value is None:
        return None

    # numpy / pandas scalars expose .item()
    item = getattr(value, "item", None)
    if callable(item) and type(value).__module__.split(".")[0] in {"numpy", "pandas"}:
        try:
            value = value.item()
        except (ValueError, AttributeError):
            return str(value)

    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (list, tuple, set)):
        return [_scalar(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _scalar(v) for k, v in value.items()}

    # pandas NaT and friends
    text = str(value)
    if text in {"NaT", "nan", "<NA>", "None"}:
        return None
    return text


def to_records(
    frame: Any,
    limit: int | None = MAX_ROWS_DEFAULT,
    columns: list[str] | None = None,
) -> tuple[list[dict], list[str], bool]:
    """Return `(records, column_names, truncated)` from pandas / polars / list.

    `columns` selects a subset, silently skipping names the frame does not have —
    schemas drift upstream and a missing column should narrow the table, not 500.
    """
    if frame is None:
        return [], [], False

    # ---- polars ------------------------------------------------------------
    if hasattr(frame, "to_dicts") and hasattr(frame, "columns"):
        names = list(frame.columns)
        if columns:
            keep = [c for c in columns if c in names]
            if keep:
                frame = frame.select(keep)
                names = keep
        total = frame.height if hasattr(frame, "height") else len(frame)
        truncated = bool(limit and total > limit)
        if truncated:
            frame = frame.head(limit)
        rows = [{k: _scalar(v) for k, v in row.items()} for row in frame.to_dicts()]
        return rows, names, truncated

    # ---- pandas ------------------------------------------------------------
    if hasattr(frame, "to_dict") and hasattr(frame, "columns"):
        names = [str(c) for c in frame.columns]
        if columns:
            keep = [c for c in columns if c in frame.columns]
            if keep:
                frame = frame[keep]
                names = [str(c) for c in keep]
        total = len(frame)
        truncated = bool(limit and total > limit)
        if truncated:
            frame = frame.head(limit)
        # `.where(notna, None)` is unreliable across pandas 2/3 dtypes; go cell-wise.
        rows = [
            {str(k): _scalar(v) for k, v in record.items()}
            for record in frame.to_dict(orient="records")
        ]
        return rows, names, truncated

    # ---- pandas Series -----------------------------------------------------
    if hasattr(frame, "to_frame"):
        return to_records(frame.to_frame().reset_index(), limit, columns)

    # ---- plain list of dicts ----------------------------------------------
    if isinstance(frame, list):
        total = len(frame)
        truncated = bool(limit and total > limit)
        subset = frame[:limit] if truncated else frame
        rows = []
        names: list[str] = []
        for item in subset:
            if isinstance(item, dict):
                row = {str(k): _scalar(v) for k, v in item.items()}
                if columns:
                    row = {k: v for k, v in row.items() if k in columns}
            else:
                row = {"value": _scalar(item)}
            for k in row:
                if k not in names:
                    names.append(k)
            rows.append(row)
        return rows, names, truncated

    # ---- single dict -------------------------------------------------------
    if isinstance(frame, dict):
        row = {str(k): _scalar(v) for k, v in frame.items()}
        return [row], list(row.keys()), False

    return [{"value": _scalar(frame)}], ["value"], False


def json_safe(value: Any) -> Any:
    """Deep-clean an arbitrary structure for `jsonify`."""
    return _scalar(value)
