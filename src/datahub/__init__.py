"""Data publik selain berita — harga, kurs, angka resmi, jadwal, bencana.

Daftarnya ada di `catalog.py` (36 API, semuanya dicoba dari koneksi ini), cara
membaca tiap jawaban ada di `parsers.py`. Modul ini hanya mengambil:

  * `fetch(code)` — satu sumber, lewat simpanan dengan TTL sumber itu. Sumber
    yang punya beberapa alamat (emas + perak + tembaga, lima game Steam)
    diambil serentak; kalau sebagian gagal, sisanya tetap tampil dan yang gagal
    disebut di `partial`.
  * `inventory()` — semua sumber yang dipakai aplikasi: sumber terdaftar,
    feed berita, dan API data, dengan status terakhir yang diketahui.
  * `probe_all()` — cek semuanya sekarang (pekerjaan latar, bisa semenit).
    Hasilnya ditulis ke `data/datahub/status.json` supaya halaman Sources tidak
    perlu mengecek ulang setiap dibuka.
"""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import config
from src.core import cache
from src.core.errors import BadRequest, NetworkBlocked, RateLimited, UpstreamError
from src.datahub.catalog import BY_CODE, CATEGORIES, DATA_SOURCES, PROBED_AT, DataSource, for_page
from src.datahub.parsers import PARSERS

USER_AGENT = "BloombergHub/1.0 (research dashboard; local; +https://github.com)"
TIMEOUT = 15
MAX_WORKERS = 8          # sama dengan berita: lebih banyak sambungan HTTPS serentak justru memperlambat
API_CANDIDATES = 64      # dicoba pada PROBED_AT; lihat docstring catalog.py

STATUS_DIR = Path(config.DATA_DIR) / "datahub"
STATUS_FILE = STATUS_DIR / "status.json"
_STATUS_LOCK = threading.Lock()

__all__ = ["BY_CODE", "CATEGORIES", "DATA_SOURCES", "PROBED_AT", "DataSource", "catalog", "counts", "fetch",
           "for_page", "inventory", "probe_all"]


def urls_for(src: DataSource, now: datetime | None = None) -> list[str]:
    """Alamat sumber dengan penanda tanggal sudah diisi (Wikipedia: kemarin, UTC)."""
    yesterday = ((now or datetime.now(timezone.utc)) - timedelta(days=1)).strftime("%Y/%m/%d")
    return [url.replace("{yesterday}", yesterday) for url in src.urls]


def _get_json(url: str, label: str) -> Any:
    import requests

    host = url.split("/")[2] if "//" in url else url
    try:
        resp = requests.get(url, timeout=TIMEOUT,
                            headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    except requests.exceptions.SSLError:
        raise NetworkBlocked(host, "sertifikat tidak cocok — ciri halaman pemblokir") from None
    except requests.exceptions.Timeout:
        raise NetworkBlocked(host, "tidak menjawab tepat waktu") from None
    except requests.exceptions.ConnectionError:
        raise NetworkBlocked(host, "tidak bisa terhubung") from None

    if resp.status_code == 429:
        raise RateLimited(label, int(resp.headers.get("Retry-After", "60") or 60)
                          if str(resp.headers.get("Retry-After", "60")).isdigit() else 60)
    if resp.status_code >= 400:
        raise UpstreamError(label, f"HTTP {resp.status_code}", resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise UpstreamError(label, "jawaban bukan JSON", resp.status_code) from None


def _source(code: str) -> DataSource:
    src = BY_CODE.get(code)
    if src is None:
        raise BadRequest(f"Sumber data '{code}' tidak dikenal.",
                         hint="Daftar kodenya ada di /api/datahub/catalog.")
    return src


def _pull(src: DataSource, urls: list[str], getter: Callable[[str, str], Any]) -> dict:
    payloads: list[Any] = [None] * len(urls)
    errors: list[tuple[int, BaseException]] = []
    if len(urls) == 1:
        payloads[0] = getter(urls[0], src.name)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(urls), 5)) as pool:
            futures = {pool.submit(getter, url, src.name): i for i, url in enumerate(urls)}
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                try:
                    payloads[i] = future.result()
                except Exception as exc:  # noqa: BLE001 — satu alamat gagal, sisanya tetap dipakai
                    errors.append((i, exc))
        if len(errors) == len(urls):
            raise min(errors, key=lambda e: e[0])[1]

    rows = PARSERS[src.parser](payloads)
    if not rows and errors:
        raise errors[0][1]
    return {
        "code": src.code,
        "name": src.name,
        "rows": rows,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "partial": [{"url": urls[i], "reason": getattr(exc, "message", None) or str(exc)[:140]}
                    for i, exc in sorted(errors, key=lambda e: e[0])],
    }


def fetch(code: str, getter: Callable[[str, str], Any] | None = None) -> tuple[dict, bool, float]:
    """Rows for one data source. Returns `(payload, was_cached, age_seconds)`."""
    src = _source(code)
    urls = urls_for(src)
    return cache.cached_call("datahub", [code, urls], src.ttl,
                             lambda: _pull(src, urls, getter or _get_json))


def catalog() -> dict:
    by_category: dict[str, int] = {}
    for src in DATA_SOURCES:
        by_category[src.category] = by_category.get(src.category, 0) + 1
    return {
        "probed_at": PROBED_AT,
        "total": len(DATA_SOURCES),
        "categories": [{"id": k, "label": v, "count": by_category.get(k, 0)} for k, v in CATEGORIES.items()],
        "sources": [s.to_dict() for s in DATA_SOURCES],
    }


# ---------------------------------------------------------------------------
# Inventaris semua sumber
# ---------------------------------------------------------------------------

def _error_status(exc: BaseException) -> str:
    if isinstance(exc, NetworkBlocked):
        return "blocked"
    if isinstance(exc, RateLimited):
        return "rate_limited"
    return "upstream_down"


def _read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_status(payload: dict) -> None:
    with _STATUS_LOCK:
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATUS_FILE)


def _cached_rows(namespace: str, key: Any, max_age: float = 7 * 86400) -> tuple[int, float] | None:
    hit = cache.stale(namespace, key, max_age)
    if hit is None:
        return None
    value, age = hit
    rows = value.get("rows") if isinstance(value, dict) else value
    return (len(rows) if isinstance(rows, list) else 0), age


def _registry_rows() -> list[dict]:
    from src.core import health, registry

    checked = cache.stale("health", {"deep": True}, 7 * 86400)
    by_id = {r["id"]: r for r in (checked[0]["sources"] if checked else [])}
    age = checked[1] if checked else None
    rows = []
    for src in registry.SOURCES:
        row = by_id.get(src.id) or {}
        status = row.get("status") or ("ready" if src.mode == registry.MODE_COMPUTE else "unchecked")
        rows.append({
            "type": "registry", "type_label": "Integrasi", "code": src.id, "name": src.label,
            "category": registry.CATEGORY_LABELS.get(src.category, src.category),
            "kind": "transaksi" if src.money_movement else ("komputasi" if src.mode == registry.MODE_COMPUTE
                                                            else "baca saja"),
            "status": status, "detail": row.get("detail") or ("" if status != "unchecked" else "Belum dicek"),
            "rows": None, "checked_age_s": round(age, 1) if age is not None and row else None,
            "url": src.probe_url or (f"https://github.com/{src.upstream}" if src.upstream else ""),
            "good_for": src.notes or src.verdict,
            "status_label": health.LABELS.get(status, "Unchecked"),
        })
    return rows


def _feed_rows(probe: dict) -> list[dict]:
    from src.news.sources import FEEDS_CATALOG, HACKER_NEWS

    results = probe.get("feeds") or {}
    rows = []
    for feed in FEEDS_CATALOG:
        last = results.get(feed.code)
        cached = _cached_rows("feed", feed.url)
        if last:
            status, detail, n = last["status"], last.get("detail", ""), last.get("rows")
        elif cached:
            status, detail, n = ("live" if cached[0] else "empty"), f"Terakhir diambil {int(cached[1] // 60)} menit lalu", cached[0]
        else:
            status, detail, n = "unchecked", "Belum pernah diambil", None
        rows.append({"type": "feed", "type_label": "Feed berita", "code": feed.code, "name": feed.name,
                     "category": feed.category, "kind": feed.kind, "lang": feed.lang,
                     "status": status, "detail": detail, "rows": n, "url": feed.url,
                     "good_for": feed.good_for, "caution": feed.caution})
    hn = HACKER_NEWS if isinstance(HACKER_NEWS, dict) else vars(HACKER_NEWS)
    last = results.get("hn")
    rows.append({"type": "feed", "type_label": "Feed berita", "code": "hn", "name": hn.get("name", "Hacker News"),
                 "category": hn.get("category", "Tech"), "kind": hn.get("kind", "komunitas"), "lang": "en",
                 "status": last["status"] if last else "unchecked",
                 "detail": (last or {}).get("detail", "Diambil saat panel berita dibuka"),
                 "rows": (last or {}).get("rows"), "url": "https://news.ycombinator.com",
                 "good_for": hn.get("good_for", ""), "caution": ""})
    return rows


def _api_rows(probe: dict) -> list[dict]:
    results = probe.get("apis") or {}
    rows = []
    for src in DATA_SOURCES:
        last = results.get(src.code)
        cached = _cached_rows("datahub", [src.code, urls_for(src)])
        if last:
            status, detail, n = last["status"], last.get("detail", ""), last.get("rows")
        elif cached:
            status, detail, n = ("live" if cached[0] else "empty"), f"Terakhir diambil {int(cached[1] // 60)} menit lalu", cached[0]
        else:
            status, detail, n = "unchecked", "Belum pernah diambil", None
        rows.append({"type": "api", "type_label": "API data", "code": src.code, "name": src.name,
                     "category": CATEGORIES.get(src.category, src.category), "kind": src.kind,
                     "status": status, "detail": detail, "rows": n, "url": src.docs or src.urls[0],
                     "good_for": src.good_for, "caution": src.caution, "pages": list(src.sectors)})
    return rows


def counts() -> dict:
    """How many sources of each type the app uses — no network, no status."""
    from src.core import registry
    from src.news.sources import FEEDS_CATALOG

    feeds = len(FEEDS_CATALOG) + 1          # + Hacker News (Algolia)
    return {"all": len(registry.SOURCES) + feeds + len(DATA_SOURCES), "registry": len(registry.SOURCES),
            "feed": feeds, "api": len(DATA_SOURCES)}


STATUS_LABELS = {"live": "Live", "ready": "Ready", "empty": "Empty", "blocked": "Blocked",
                 "rate_limited": "Rate limited", "upstream_down": "Upstream down", "unchecked": "Unchecked",
                 "missing_dep": "No package", "no_credential": "No key", "not_cloned": "Not downloaded"}
USABLE = {"live", "ready", "empty"}


def inventory() -> dict:
    """Every source the app uses, in one list, with the last known status."""
    from src.news.sources import REJECTED

    probe = _read_status()
    rows = _registry_rows() + _feed_rows(probe) + _api_rows(probe)
    for row in rows:
        row["status_label"] = STATUS_LABELS.get(row["status"], row.get("status_label") or row["status"])

    def count(kind: str | None = None) -> dict:
        subset = [r for r in rows if kind is None or r["type"] == kind]
        return {
            "total": len(subset),
            "usable": sum(1 for r in subset if r["status"] in USABLE),
            "problem": sum(1 for r in subset if r["status"] not in USABLE | {"unchecked"}),
            "unchecked": sum(1 for r in subset if r["status"] == "unchecked"),
        }

    return {
        "totals": {"all": count(), "registry": count("registry"), "feed": count("feed"), "api": count("api")},
        "rejected": {"feeds": len(REJECTED), "apis": API_CANDIDATES - len(DATA_SOURCES),
                     "note": "Dicoba tetapi tidak dipakai: diblokir di jaringan ini, butuh akun, tidak lagi "
                             "menjawab, atau isinya sama dengan sumber lain. Alasannya dicatat di "
                             "src/news/sources.py dan src/datahub/catalog.py."},
        "last_probe": {k: probe.get(k) for k in ("started_at", "finished_at", "elapsed_s")} if probe else None,
        "sources": rows,
    }


# ---------------------------------------------------------------------------
# Cek semuanya
# ---------------------------------------------------------------------------

def _outcome(fn: Callable[[], int]) -> dict:
    started = time.perf_counter()
    try:
        n = fn()
        status, detail = ("live" if n else "empty"), (f"{n} baris" if n else "Menjawab, tapi kosong")
    except Exception as exc:  # noqa: BLE001 — setiap kegagalan jadi baris status, bukan halaman galat
        n, status = None, _error_status(exc)
        detail = (getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}")[:160]
    return {"status": status, "detail": detail, "rows": n,
            "elapsed_ms": int((time.perf_counter() - started) * 1000)}


def probe_all(progress: Callable[[str, int, int, str], None] | None = None,
              include_registry: bool = True) -> dict:
    """Fetch every feed and every data API once (through their caches), record the outcome."""
    from src import news
    from src.news.sources import FEEDS_CATALOG

    progress = progress or (lambda *a, **k: None)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.perf_counter()
    tasks: list[tuple[str, str, Callable[[], int]]] = []
    for feed in FEEDS_CATALOG:
        tasks.append(("feeds", feed.code, lambda f=feed: len(news.fetch_feed(f)[0])))
    tasks.append(("feeds", "hn", lambda: len(news._hacker_news(None, 30))))  # noqa: SLF001
    for src in DATA_SOURCES:
        tasks.append(("apis", src.code, lambda c=src.code: len(fetch(c)[0]["rows"])))

    result: dict[str, Any] = {"started_at": started_at, "feeds": {}, "apis": {}}
    total = len(tasks) + (1 if include_registry else 0)
    done = 0
    progress("mengecek feed berita dan API data", 0, total, "")
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_outcome, fn): (group, code) for group, code, fn in tasks}
        for future in concurrent.futures.as_completed(futures):
            group, code = futures[future]
            result[group][code] = future.result()
            done += 1
            progress("mengecek feed berita dan API data", done, total, code)

    if include_registry:
        from src.core import health

        progress("mengecek sumber terdaftar", done, total, "")
        health.check_all(deep=True, force=True)
        done += 1

    result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["elapsed_s"] = round(time.perf_counter() - t0, 1)
    _write_status(result)
    progress("selesai", done, total, "")
    return result


def probe_summary(result: dict) -> dict:
    out = {"elapsed_s": result.get("elapsed_s")}
    for group in ("feeds", "apis"):
        items = (result.get(group) or {}).values()
        out[group] = {"total": len(items), "live": sum(1 for r in items if r["status"] in USABLE),
                      "problem": sum(1 for r in items if r["status"] not in USABLE)}
    return out
