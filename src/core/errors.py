"""Error taxonomy and the single JSON envelope every endpoint returns.

The point of the taxonomy: the UI must be able to tell the four failures apart,
because each one has a different fix and only one of them is our bug.

    MISSING_DEP        -> `pip install X`            (user action, one command)
    MISSING_CREDENTIAL -> set a key in .env          (user action, needs signup)
    NETWORK_BLOCKED    -> ISP/geo block, not fixable (needs VPS/VPN)
    UPSTREAM_ERROR     -> their server, not ours     (retry later)
    RATE_LIMITED       -> back off                   (retry later, with delay)
    BAD_REQUEST        -> caller sent nonsense       (fix the input)
    TRADING_DISABLED   -> money gate is closed       (deliberate, see registry)
    INTERNAL           -> our bug                    (fix the code)

Anything that reaches the UI as INTERNAL is a defect. Everything else is a
condition of the environment, and the UI says so in words the user can act on.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any


class HubError(Exception):
    """Base for every failure we can explain."""

    code = "INTERNAL"
    http_status = 500

    def __init__(self, message: str, hint: str = "", detail: Any = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail,
        }


class MissingDependency(HubError):
    code = "MISSING_DEP"
    http_status = 424

    def __init__(self, package: str, module: str = ""):
        super().__init__(
            f"Paket {package} belum terpasang di komputer ini.",
            hint=f"pip install {package}",
            detail={"package": package, "module": module or package},
        )


class MissingCredential(HubError):
    code = "MISSING_CREDENTIAL"
    http_status = 424

    def __init__(self, *keys: str, where: str = ".env"):
        joined = ", ".join(keys)
        super().__init__(
            f"Sumber ini butuh kunci yang belum diisi: {joined}.",
            hint=f"Tambahkan {joined} ke berkas {where}. Contoh isiannya ada di .env.example.",
            detail={"keys": list(keys)},
        )


class NetworkBlocked(HubError):
    code = "NETWORK_BLOCKED"
    http_status = 503

    def __init__(self, host: str, reason: str = "timeout"):
        super().__init__(
            f"{host} tidak bisa dihubungi dari koneksi internet ini ({reason}).",
            hint="Alamat ini diblokir di jaringan Anda. Perlu koneksi lain untuk membukanya. "
                 "Sumber lain yang bertanda Siap tetap bisa dipakai seperti biasa.",
            detail={"host": host, "reason": reason},
        )


class UpstreamError(HubError):
    code = "UPSTREAM_ERROR"
    http_status = 502

    def __init__(self, source: str, message: str = "", status: int | None = None):
        super().__init__(
            f"{source} sedang bermasalah." + (f" {message}" if message else ""),
            hint="Gangguan ada di server mereka, bukan di aplikasi ini. Coba lagi beberapa saat lagi.",
            detail={"source": source, "status": status},
        )


class RateLimited(HubError):
    code = "RATE_LIMITED"
    http_status = 429

    def __init__(self, source: str, retry_after: int | None = None):
        super().__init__(
            f"{source} sedang membatasi jumlah permintaan.",
            hint=f"Tunggu sekitar {retry_after or 60} detik lalu coba lagi. "
                 "Data yang sudah pernah dimuat tetap bisa dibuka.",
            detail={"source": source, "retry_after": retry_after},
        )


class BadRequest(HubError):
    code = "BAD_REQUEST"
    http_status = 400


class DataNotCloned(HubError):
    """A dataset repo has to be cloned before it can be read."""

    code = "NOT_CLONED"
    http_status = 424

    def __init__(self, name: str, path: str, command: str):
        super().__init__(
            f"Data {name} belum diunduh ke komputer ini.",
            hint=command,
            detail={"path": path, "command": command, "dataset": name},
        )


class TradingDisabled(HubError):
    code = "TRADING_DISABLED"
    http_status = 403

    def __init__(self, action: str = "eksekusi order"):
        super().__init__(
            f"Kunci transaksi masih tertutup, jadi {action} tidak dilakukan.",
            hint="Ini memang disengaja. Untuk membukanya, isi BH_ENABLE_TRADING=1 di berkas .env "
                 "dan gunakan dompet terpisah dengan saldo kecil.",
            detail={"action": action},
        )


@dataclass
class Meta:
    """Everything the UI needs to judge how much to trust a payload."""

    source: str = ""
    rows: int = 0
    cached: bool = False
    cache_age_s: float | None = None
    elapsed_ms: int = 0
    columns: list[str] = field(default_factory=list)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "source": self.source,
            "rows": self.rows,
            "cached": self.cached,
            "elapsed_ms": self.elapsed_ms,
            "columns": self.columns,
            "truncated": self.truncated,
            "notes": self.notes,
        }
        if self.cache_age_s is not None:
            d["cache_age_s"] = round(self.cache_age_s, 1)
        return d


def ok(data: Any, meta: Meta | None = None) -> dict:
    m = meta or Meta()
    if not m.rows and isinstance(data, list):
        m.rows = len(data)
    return {"ok": True, "data": data, "meta": m.to_dict(), "error": None}


def fail(err: BaseException, source: str = "", debug: bool = False) -> tuple[dict, int]:
    """Turn any exception into the envelope plus an HTTP status."""
    if isinstance(err, HubError):
        body = err.to_dict()
        status = err.http_status
    else:
        body = {
            "code": "INTERNAL",
            "message": f"{type(err).__name__}: {err}",
            "hint": "Ini kesalahan di aplikasi, bukan di sumber datanya. "
                    "Rinciannya ada di log server.",
            "detail": traceback.format_exc().splitlines()[-6:] if debug else None,
        }
        status = 500
    return (
        {"ok": False, "data": None, "meta": Meta(source=source).to_dict(), "error": body},
        status,
    )


class Timer:
    """Tiny elapsed-ms helper: `with Timer() as t: ...` then `t.ms`."""

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc) -> bool:
        self.ms = int((time.perf_counter() - self._t0) * 1000)
        return False
