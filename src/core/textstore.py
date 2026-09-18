"""Penyimpanan sementara berbasis berkas teks.

Dipakai lapisan berita: setiap kali kabar diambil, hasilnya ditulis ke berkas
`.txt` di `data/news/`. Gunanya tiga:

  * kalau sumbernya sedang mati atau membatasi permintaan, tampilan tetap punya
    isi — dibaca dari berkas terakhir, dengan umurnya dinyatakan apa adanya;
  * isinya bisa dibuka dan dibaca manusia dengan Notepad, bukan basis data yang
    perlu alat khusus;
  * riwayatnya menumpuk, jadi bisa dilihat apa yang berubah dari waktu ke waktu.

Formatnya sengaja sederhana: satu berkas ringkasan yang bisa dibaca orang, plus
satu berkas `.jsonl` berisi data yang sama untuk dibaca program. Berkas dipangkas
otomatis supaya folder tidak tumbuh tanpa batas.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_LOCK = threading.RLock()
_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "news"

MAX_LINES = 4000          # batas baris per berkas jsonl sebelum dipangkas
MAX_SNAPSHOT_BYTES = 600_000


def _folder() -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    return _ROOT


def _safe(name: str) -> str:
    keep = "-_."
    return "".join(c if (c.isalnum() or c in keep) else "-" for c in name)[:60] or "tanpa-nama"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_snapshot(topic: str, rows: Iterable[dict], fields: list[str]) -> Path:
    """Tulis ringkasan yang enak dibaca manusia. Menimpa berkas sebelumnya."""
    rows = list(rows)
    path = _folder() / f"{_safe(topic)}.txt"
    header = [
        f"# {topic}",
        f"# fetched : {now_iso()}",
        f"# rows    : {len(rows)}",
        "#" + "-" * 68,
        "",
    ]
    body: list[str] = []
    for i, row in enumerate(rows, 1):
        body.append(f"[{i:>3}] " + str(row.get(fields[0], ""))[:160])
        for field in fields[1:]:
            value = row.get(field)
            if value in (None, ""):
                continue
            body.append(f"      {field}: {str(value)[:200]}")
        body.append("")

    text = "\n".join(header + body)
    if len(text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        text = text[: MAX_SNAPSHOT_BYTES // 2] + "\n\n… dipotong …\n"

    with _LOCK:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return path


def append_records(topic: str, rows: Iterable[dict]) -> int:
    """Tambahkan baris ke `.jsonl`, lewati yang sudah pernah tercatat.

    Kunci pembeda memakai `url` bila ada, jika tidak memakai judulnya. Ini yang
    membuat berkas jadi riwayat, bukan sekadar salinan terakhir.
    """
    rows = list(rows)
    if not rows:
        return 0

    path = _folder() / f"{_safe(topic)}.jsonl"
    seen: set[str] = set()
    existing: list[str] = []

    with _LOCK:
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8").splitlines()
                for line in existing:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    key = item.get("url") or item.get("title") or ""
                    if key:
                        seen.add(key)
            except OSError:
                existing = []

        fresh = []
        for row in rows:
            key = row.get("url") or row.get("title") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            fresh.append(json.dumps({**row, "_stored_at": now_iso()}, ensure_ascii=False))

        if not fresh:
            return 0

        lines = existing + fresh
        if len(lines) > MAX_LINES:
            lines = lines[-MAX_LINES:]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)

    return len(fresh)


def read_records(topic: str, limit: int = 200) -> list[dict]:
    """Baca kembali riwayat tersimpan, terbaru lebih dulu."""
    path = _folder() / f"{_safe(topic)}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with _LOCK:
            lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def search_records(term: str, limit: int = 60) -> list[dict]:
    """Cari di seluruh riwayat yang tersimpan di semua berkas.

    Inilah yang membuat pencarian bisa menemukan kabar yang sudah lewat dari
    tampilan tapi masih ada di simpanan.
    """
    term = (term or "").strip().lower()
    if not term:
        return []

    hits: list[dict] = []
    seen: set[str] = set()
    for path in sorted(_folder().glob("*.jsonl"), key=lambda p: -p.stat().st_mtime):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if term not in line.lower():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            key = item.get("url") or item.get("title") or ""
            if key in seen:
                continue
            seen.add(key)
            item["_file"] = path.stem
            hits.append(item)
            if len(hits) >= limit:
                return hits
    return hits


def stats() -> dict:
    folder = _folder()
    files = sorted(folder.glob("*"), key=lambda p: -p.stat().st_mtime)
    return {
        "folder": str(folder),
        "file_count": len(files),
        "total_bytes": sum(f.stat().st_size for f in files),
        "files": [
            {
                "name": f.name,
                "bytes": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
                            .isoformat(timespec="seconds"),
            }
            for f in files[:40]
        ],
    }


def clear(topic: str | None = None) -> int:
    removed = 0
    folder = _folder()
    pattern = f"{_safe(topic)}.*" if topic else "*"
    with _LOCK:
        for path in folder.glob(pattern):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
