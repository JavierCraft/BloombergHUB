"""Three-question health probe for every source in the registry.

    1. Is the Python package importable?      -> MISSING_DEP
    2. Are the credentials present?           -> MISSING_CREDENTIAL
    3. Is the host reachable from *here*?     -> NETWORK_BLOCKED

Question 3 is the one the audit insists on. From this connection every
Polymarket subdomain resolves to an ISP block page, and the local probe added
two the audit had not caught: football-data.co.uk times out and fbref.com
answers 403. A dashboard that shows those as "available" is lying.

Probes run concurrently with a short timeout and are cached, because a blocked
host costs a full timeout every single time you ask.
"""
from __future__ import annotations

import concurrent.futures
import importlib.util
import time

from . import cache, registry
from .registry import Source

# Diukur di mesin ini: satu permintaan ke sumber yang sehat memakan 2–5,5 detik
# saat jaringan sedang lambat. Tenggat 6 detik membuat sumber yang sebenarnya
# hidup dilaporkan bermasalah, jadi diberi kelonggaran.
PROBE_TIMEOUT = 12
HEALTH_TTL = 300
MAX_WORKERS = 8
_UA = "BloombergHub/1.0 (research dashboard; local)"

# Status vocabulary, ordered worst to best for sorting in the UI.
BLOCKED = "blocked"
MISSING_DEP = "missing_dep"
NO_CREDENTIAL = "no_credential"
UPSTREAM_DOWN = "upstream_down"
RATE_LIMITED = "rate_limited"
NOT_CLONED = "not_cloned"
LIVE = "live"
READY = "ready"          # compute sources: nothing to reach, always usable

SEVERITY = {
    BLOCKED: 0,
    MISSING_DEP: 1,
    NO_CREDENTIAL: 2,
    UPSTREAM_DOWN: 3,
    NOT_CLONED: 4,
    RATE_LIMITED: 5,
    LIVE: 6,
    READY: 7,
}

LABELS = {
    BLOCKED: "Blocked",
    MISSING_DEP: "No package",
    NO_CREDENTIAL: "No key",
    UPSTREAM_DOWN: "Upstream down",
    RATE_LIMITED: "Rate limited",
    NOT_CLONED: "Not downloaded",
    LIVE: "Live",
    READY: "Ready",
}


def _has_module(name: str) -> bool:
    if not name:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _missing_secrets(src: Source) -> list[str]:
    if not src.secrets_required:
        return []
    import config

    return [k for k in src.secrets_required if not getattr(config, k, "")]


def _probe_http(src: Source) -> tuple[str, str, int | None, int]:
    """Return (status, detail, http_status, elapsed_ms)."""
    import requests

    timeout = src.probe_timeout or PROBE_TIMEOUT
    started = time.perf_counter()
    try:
        resp = requests.get(
            src.probe_url,
            timeout=timeout,
            headers={"User-Agent": _UA},
            allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        ms = int((time.perf_counter() - started) * 1000)
        # A timeout is only evidence of a block for a host the probe has already
        # confirmed blocked. Otherwise it is a slow server, and saying "BLOCKED"
        # would be exactly the kind of confident-and-wrong label this dashboard
        # exists to avoid.
        detail = f"Tidak menjawab dalam {timeout} detik"
        return (BLOCKED if src.blocked_here else UPSTREAM_DOWN), detail, None, ms
    except requests.exceptions.ConnectionError as exc:
        ms = int((time.perf_counter() - started) * 1000)
        text = str(exc).lower()
        reason = "Koneksi ditolak" if "refused" in text else "Tidak bisa terhubung"
        return (BLOCKED if src.blocked_here or "refused" in text else UPSTREAM_DOWN), reason, None, ms
    except requests.exceptions.RequestException as exc:
        ms = int((time.perf_counter() - started) * 1000)
        return UPSTREAM_DOWN, f"Gagal menghubungi ({type(exc).__name__})", None, ms

    ms = int((time.perf_counter() - started) * 1000)
    code = resp.status_code

    if code in src.probe_ok_status:
        return LIVE, "Bisa dihubungi", code, ms
    if code == 429:
        return RATE_LIMITED, "Sedang membatasi jumlah permintaan", code, ms
    if code in (401, 403):
        # 403 dari situs yang di-scrape berarti tembok; dari API berarti butuh kunci.
        if src.secrets_required:
            return NO_CREDENTIAL, "Kuncinya ditolak atau belum benar", code, ms
        return BLOCKED, f"Akses ditolak (kode {code})", code, ms
    return UPSTREAM_DOWN, f"Server mereka membalas kode {code}", code, ms


def check(src: Source, deep: bool = True) -> dict:
    """Health of one source. `deep=False` skips the network call."""
    result = {
        "id": src.id,
        "label": src.label,
        "category": src.category,
        "category_label": registry.CATEGORY_LABELS.get(src.category, src.category),
        "mode": src.mode,
        "money_movement": src.money_movement,
        "risk": src.risk,
        "upstream": src.upstream,
        "verdict": src.verdict,
        "stars": src.stars,
        "pushed_at": src.pushed_at,
        "notes": src.notes,
        "hosts": list(src.hosts),
        "status": READY,
        "label_status": LABELS[READY],
        "detail": "",
        "http_status": None,
        "elapsed_ms": 0,
        "checked_at": time.time(),
    }

    # 0. a confirmed block outranks everything below it. Reporting "NO KEY" for
    # a host this connection cannot reach would send the user to sign up for
    # credentials that cannot possibly help.
    if src.blocked_here:
        result.update(
            status=BLOCKED,
            detail=f"Alamat {src.hosts[0] if src.hosts else src.id} diblokir di jaringan ini",
            label_status=LABELS[BLOCKED],
        )
        return result

    # 1. package
    if src.module and not _has_module(src.module):
        result.update(
            status=MISSING_DEP,
            detail=f"pip install {src.pip}",  # sengaja perintah apa adanya
            label_status=LABELS[MISSING_DEP],
        )
        return result

    # 2. credentials
    missing = _missing_secrets(src)
    if missing:
        result.update(
            status=NO_CREDENTIAL,
            detail="Kunci belum diisi: " + ", ".join(missing),
            label_status=LABELS[NO_CREDENTIAL],
        )
        return result

    # 3. local data that has to be cloned first
    if src.id in {"electindex", "openelections"}:
        from pathlib import Path

        import config

        folder = "electindex" if src.id == "electindex" else "openelections-core"
        path = Path(config.REPOS_DIR) / folder
        if not path.exists():
            result.update(
                status=NOT_CLONED,
                detail="Datanya belum diunduh ke komputer ini",
                label_status=LABELS[NOT_CLONED],
            )
            return result

        # Sudah ada di cakram. Tanpa cabang ini pemeriksaan jatuh ke tahap 4,
        # yang — karena sumber ini memang tidak punya alamat untuk diketuk —
        # melaporkan "Belum dicek" padahal barusan dicek dan datanya ada.
        # Isi `.git` tidak dihitung: `git clone --depth 1` meninggalkan ribuan
        # berkas objek di sana, dan "2.648 berkas" untuk kumpulan data berisi
        # 46 berkas hanya akan menyesatkan.
        berkas = sum(1 for f in path.rglob("*")
                     if f.is_file() and ".git" not in f.parts)
        result.update(
            status=READY,
            detail=f"Sudah diunduh — {berkas} berkas di data/repos/{folder}",
            label_status=LABELS[READY],
        )
        return result

    # 4. network
    if not src.probe_url or not deep:
        result.update(
            status=READY, label_status=LABELS[READY],
            detail="Berjalan lokal, tidak perlu internet"
            if src.mode == registry.MODE_COMPUTE else "Belum dicek",
        )
        return result

    status, detail, code, ms = _probe_http(src)
    result.update(
        status=status,
        detail=detail,
        http_status=code,
        elapsed_ms=ms,
        label_status=LABELS[status],
    )
    return result


def check_all(deep: bool = True, ttl: int = HEALTH_TTL, force: bool = False) -> dict:
    """Probe every source concurrently. Cached — a blocked host costs a timeout."""
    if not force:
        hit = cache.get("health", {"deep": deep}, ttl)
        if hit is not None:
            payload, age = hit
            payload = dict(payload)
            payload["cached"] = True
            payload["age_s"] = round(age, 1)
            return payload

    started = time.perf_counter()
    results: list[dict] = []
    # Jumlah pekerja sengaja dibatasi, bukan disamakan dengan jumlah sumber.
    # Diukur di mesin ini: 19 sambungan HTTPS serentak membuat pengecekan
    # berikutnya gagal terhubung selama belasan detik — jaringannya kehabisan
    # napas, lalu enam sumber yang sebenarnya sehat dilaporkan bermasalah.
    # Dengan batas ini pengecekan berjalan dua gelombang dan hasilnya stabil.
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check, s, deep): s for s in registry.SOURCES}
        gelombang = -(-len(registry.SOURCES) // MAX_WORKERS)
        budget = max(s.probe_timeout for s in registry.SOURCES) * gelombang + PROBE_TIMEOUT
        try:
            selesai = list(concurrent.futures.as_completed(futures, timeout=budget))
        except concurrent.futures.TimeoutError:
            # Lewat tenggat: pakai yang sudah selesai, sisanya ditandai lambat.
            # Hasil sebagian jauh lebih berguna daripada satu halaman galat.
            selesai = [f for f in futures if f.done()]
        for future in selesai:
            src = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # a probe must never take the page down
                results.append(
                    {
                        "id": src.id,
                        "label": src.label,
                        "category": src.category,
                        "category_label": registry.CATEGORY_LABELS.get(src.category, src.category),
                        "mode": src.mode,
                        "money_movement": src.money_movement,
                        "risk": src.risk,
                        "upstream": src.upstream,
                        "verdict": src.verdict,
                        "stars": src.stars,
                        "pushed_at": src.pushed_at,
                        "notes": src.notes,
                        "hosts": list(src.hosts),
                        "status": UPSTREAM_DOWN,
                        "label_status": LABELS[UPSTREAM_DOWN],
                        "detail": f"probe gagal: {type(exc).__name__}",
                        "http_status": None,
                        "elapsed_ms": 0,
                        "checked_at": time.time(),
                    }
                )

    terjawab = {r["id"] for r in results}
    for src in registry.SOURCES:
        if src.id not in terjawab:
            results.append({
                "id": src.id, "label": src.label, "category": src.category,
                "category_label": registry.CATEGORY_LABELS.get(src.category, src.category),
                "mode": src.mode, "money_movement": src.money_movement, "risk": src.risk,
                "upstream": src.upstream, "verdict": src.verdict, "stars": src.stars,
                "pushed_at": src.pushed_at, "notes": src.notes, "hosts": list(src.hosts),
                "status": UPSTREAM_DOWN, "label_status": LABELS[UPSTREAM_DOWN],
                "detail": "Belum menjawab sampai batas waktu pengecekan",
                "http_status": None, "elapsed_ms": 0, "checked_at": time.time(),
            })

    results.sort(key=lambda r: (SEVERITY.get(r["status"], 9), r["category"], r["id"]))

    payload = {
        "checked_at": time.time(),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "cached": False,
        "age_s": 0.0,
        "summary": {
            "total": len(results),
            "live": len([r for r in results if r["status"] in (LIVE, READY)]),
            "blocked": len([r for r in results if r["status"] == BLOCKED]),
            "degraded": len(
                [
                    r
                    for r in results
                    if r["status"] in (MISSING_DEP, NO_CREDENTIAL, NOT_CLONED,
                                       RATE_LIMITED, UPSTREAM_DOWN)
                ]
            ),
        },
        "sources": results,
    }
    cache.put("health", {"deep": deep}, payload)
    return payload


def guard(source_id: str) -> Source:
    """Raise the specific, actionable error before a data call is attempted.

    This turns a 30-second hang into an instant, explained refusal.
    """
    from .errors import MissingCredential, MissingDependency, NetworkBlocked

    src = registry.assert_readonly(source_id)

    if src.module and not _has_module(src.module):
        raise MissingDependency(src.pip, src.module)

    missing = _missing_secrets(src)
    if missing:
        raise MissingCredential(*missing)

    if src.blocked_here:
        raise NetworkBlocked(src.hosts[0] if src.hosts else src.id, "diblokir ISP")

    return src
