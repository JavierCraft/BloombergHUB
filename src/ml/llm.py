"""Optional: Claude reads the headlines for one market.

Everything else in this project runs without it. With `ANTHROPIC_API_KEY` set in
`.env`, a market gets one structured reading: stance (YES / NO / UNCLEAR), an
own probability, key points that cite the numbered headlines they came from,
what would change the reading, and risks in the resolution rules.

Choices that are deliberate:

  * **Grounded.** The model is told to use only the headlines and rules it is
    given. Its training data ends before these events; letting it "remember"
    would mix stale knowledge into a live market.
  * **Structured.** `output_config.format` with a JSON schema, so the answer
    parses every time instead of being scraped out of prose.
  * **Refusals handled.** Claude Opus 5 can decline a request with
    `stop_reason: "refusal"`; that is checked before reading any content, and the
    server-side `fallbacks: "default"` re-runs a declined request on Anthropic's
    recommended fallback model inside the same call.
  * **Cached.** The same market with the same headlines is answered from cache
    for an hour — a reading costs real money.
  * **Never a signal by itself.** The reading is logged next to paper
    predictions and judged on settled results like every other input.

Model: `claude-opus-5` by default (override with `BH_LLM_MODEL`). Effort:
the API default unless `BH_LLM_EFFORT` is set (low/medium/high/xhigh/max).
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import config
from src.core import cache
from src.core.errors import HubError, MissingCredential, NetworkBlocked, RateLimited, UpstreamError

# USD per million tokens, from Anthropic's published price list (cached 2026-06-24).
PRICING = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# The documented model line-up, shown when no key is available to ask the API.
KNOWN_MODELS = [
    {"id": "claude-fable-5-1", "name": "Claude Fable 5.1", "context": "1M", "price": "$10 / $50",
     "note": "Paling mampu yang dirilis luas; berpikir selalu aktif."},
    {"id": "claude-opus-5", "name": "Claude Opus 5", "context": "1M", "price": "$5 / $25",
     "note": "Bawaan proyek ini. Kuat untuk kerja agentik dan penalaran panjang."},
    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "context": "1M", "price": "$2 / $10",
     "note": "Seimbang antara kemampuan dan biaya."},
    {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "context": "200K", "price": "$1 / $5",
     "note": "Tercepat dan termurah."},
    {"id": "claude-opus-4-8", "name": "Claude Opus 4.8", "context": "1M", "price": "$5 / $25",
     "note": "Generasi sebelumnya; tujuan fallback untuk penolakan kategori siber."},
]
KNOWN_MODELS_AS_OF = "2026-06-24"

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1")
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
CACHE_TTL = 3600

SYSTEM_PROMPT = """You are a careful prediction-market analyst for a research dashboard used by an Indonesian trader.

You receive one market — its question, resolution rules, deadline, and current price — and a numbered list of recent headlines with source and time. Judge how these headlines bear on the market resolving YES by its deadline.

Rules:
- Use only the headlines and the rules provided. Your training data ends before these events, so do not fill gaps from memory. If the evidence is thin, off-topic, stale, or contradictory, the stance is UNCLEAR.
- Every key point cites the headline numbers it rests on.
- probability_yes is your own estimate from the evidence and the time left before the deadline. The current price is context, not an answer to copy; when the evidence is thin, stay close to the price and say that the evidence is thin.
- Consider the deadline explicitly: how much can still happen before it, and whether the resolution source is likely to publish in time.
- List resolution risks: ambiguous wording, which source decides, timing and time zones.
- Write every text field in Bahasa Indonesia. Be concise: the summary is at most four sentences, each list at most five short items."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["YES", "NO", "UNCLEAR"]},
        "probability_yes": {"type": "number"},
        "confidence": {"type": "string", "enum": ["rendah", "sedang", "tinggi"]},
        "summary": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "headlines": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["point", "headlines"],
                "additionalProperties": False,
            },
        },
        "what_would_change_it": {"type": "array", "items": {"type": "string"}},
        "deadline_note": {"type": "string"},
        "resolution_risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stance", "probability_yes", "confidence", "summary", "key_points",
                 "what_would_change_it", "deadline_note", "resolution_risks"],
    "additionalProperties": False,
}


def has_credentials() -> bool:
    return bool(config.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def status() -> dict:
    try:
        import anthropic
    except ImportError:
        return {"available": False, "model": config.LLM_MODEL,
                "reason": "Paket anthropic belum terpasang.", "hint": "pip install anthropic"}
    ready = has_credentials()
    price = PRICING.get(config.LLM_MODEL)
    return {
        "available": ready,
        "model": config.LLM_MODEL,
        "effort": config.LLM_EFFORT if config.LLM_EFFORT in EFFORTS else "bawaan API (high)",
        "sdk": getattr(anthropic, "__version__", None),
        "fallbacks": config.LLM_MODEL in FALLBACK_MODELS,
        "price_per_mtok": {"input": price[0], "output": price[1]} if price else None,
        "reason": None if ready else "ANTHROPIC_API_KEY belum diisi di .env — analisis LLM nonaktif.",
        "hint": None if ready else "Isi ANTHROPIC_API_KEY di .env lalu jalankan ulang aplikasi.",
    }


def _client():
    try:
        import anthropic
    except ImportError:
        from src.core.errors import MissingDependency

        raise MissingDependency("anthropic") from None
    if not has_credentials():
        raise MissingCredential("ANTHROPIC_API_KEY")
    kwargs: dict[str, Any] = {"timeout": 180.0, "max_retries": 2}
    if config.ANTHROPIC_API_KEY:
        kwargs["api_key"] = config.ANTHROPIC_API_KEY
    return anthropic, anthropic.Anthropic(**kwargs)


def _translate(anthropic, exc: Exception) -> Exception:
    """SDK exceptions → the project's error taxonomy, most specific first."""
    if isinstance(exc, anthropic.AuthenticationError):
        return MissingCredential("ANTHROPIC_API_KEY")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return HubError("Kunci Anthropic tidak punya izin untuk model ini.",
                        hint=f"Periksa akses model {config.LLM_MODEL} di Claude Console, "
                             "atau ganti BH_LLM_MODEL.")
    if isinstance(exc, anthropic.NotFoundError):
        return HubError(f"Model {config.LLM_MODEL} tidak dikenal oleh API.",
                        hint="Periksa BH_LLM_MODEL di .env.")
    if isinstance(exc, anthropic.RateLimitError):
        retry = None
        try:
            retry = int(exc.response.headers.get("retry-after", "60"))
        except (AttributeError, TypeError, ValueError):
            pass
        return RateLimited("Claude API", retry)
    if isinstance(exc, anthropic.BadRequestError):
        return HubError(f"Permintaan ke Claude ditolak: {getattr(exc, 'message', exc)}",
                        hint="Ini biasanya parameter yang tidak didukung model yang dipilih.")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return UpstreamError("Claude API", "", exc.status_code)
        return HubError(f"Claude API menjawab {exc.status_code}: {getattr(exc, 'message', exc)}")
    if isinstance(exc, anthropic.APIConnectionError):
        return NetworkBlocked("api.anthropic.com", "tidak bisa terhubung atau terlalu lama")
    return exc


def _prompt(market: dict, headlines: list[dict]) -> str:
    lines = [
        "MARKET",
        f"Question: {market.get('question') or '—'}",
        f"Current price of YES: {market.get('price_text') or '—'}",
        f"Deadline (UTC): {market.get('deadline') or '—'}",
        f"Time left: {market.get('time_left') or '—'}",
        f"Resolution rules: {(market.get('rules') or '—')[:2500]}",
        "",
        "HEADLINES (newest first)",
    ]
    for i, h in enumerate(headlines, 1):
        lines.append(f"[{i}] {h.get('time') or 'waktu tidak diketahui'} · {h.get('source') or '?'} · "
                     f"{h.get('title')}")
    if not headlines:
        lines.append("(no headlines matched this market)")
    return "\n".join(lines)


def _clean(result: dict, n_headlines: int) -> dict:
    try:
        prob = float(result.get("probability_yes"))
    except (TypeError, ValueError):
        prob = None
    if prob is not None:
        prob = prob / 100 if 1 < prob <= 100 else prob
        prob = min(0.99, max(0.01, prob))
    points = []
    for item in result.get("key_points") or []:
        cited = [n for n in (item.get("headlines") or []) if isinstance(n, int) and 1 <= n <= n_headlines]
        points.append({"point": str(item.get("point") or "")[:400], "headlines": cited})
    return {
        "stance": result.get("stance") if result.get("stance") in ("YES", "NO", "UNCLEAR") else "UNCLEAR",
        "probability_yes": round(prob, 4) if prob is not None else None,
        "confidence": result.get("confidence") if result.get("confidence") in ("rendah", "sedang", "tinggi") else "rendah",
        "summary": str(result.get("summary") or "")[:1200],
        "key_points": points[:6],
        "what_would_change_it": [str(x)[:300] for x in (result.get("what_would_change_it") or [])][:5],
        "deadline_note": str(result.get("deadline_note") or "")[:500],
        "resolution_risks": [str(x)[:300] for x in (result.get("resolution_risks") or [])][:5],
    }


def _cost(model: str, usage: Any) -> float | None:
    price = PRICING.get(model)
    if not price or usage is None:
        return None
    fresh = getattr(usage, "input_tokens", 0) or 0
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    dollars = (fresh + written * 1.25 + read * 0.1) * price[0] / 1e6 + out * price[1] / 1e6
    return round(dollars, 4)


def analyse(market: dict, headlines: list[dict]) -> dict:
    """One structured reading. `headlines` should already be relevance-ranked."""
    headlines = headlines[:25]
    key = hashlib.sha1(json.dumps({
        "model": config.LLM_MODEL, "effort": config.LLM_EFFORT, "q": market.get("question"),
        "rules": (market.get("rules") or "")[:500], "price": market.get("price_text"),
        "urls": [h.get("url") or h.get("title") for h in headlines],
    }, sort_keys=True).encode()).hexdigest()

    def call() -> dict:
        anthropic, client = _client()
        request: dict[str, Any] = {
            "model": config.LLM_MODEL,
            "max_tokens": 16000,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": _prompt(market, headlines)}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        }
        if config.LLM_EFFORT in EFFORTS:
            request["output_config"]["effort"] = config.LLM_EFFORT
        if config.LLM_MODEL in FALLBACK_MODELS:
            request["betas"] = [FALLBACK_BETA]
            request["extra_body"] = {"fallbacks": "default"}
        try:
            if "betas" in request:
                response = client.beta.messages.create(**request)
            else:
                response = client.messages.create(**request)
        except Exception as exc:  # noqa: BLE001 — translated into the project's taxonomy
            raise _translate(anthropic, exc) from None

        served_by = getattr(response, "model", config.LLM_MODEL)
        usage = getattr(response, "usage", None)
        meta = {
            "requested_model": config.LLM_MODEL,
            "served_by": served_by,
            "fallback_used": served_by != config.LLM_MODEL,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
            "cost_usd": _cost(served_by if served_by in PRICING else config.LLM_MODEL, usage),
            "request_id": getattr(response, "_request_id", None),
            "headlines_sent": len(headlines),
        }
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return {"ok": False, "refused": True,
                    "reason": "Claude menolak menganalisis permintaan ini.",
                    "category": getattr(details, "category", None), "meta": meta}
        if response.stop_reason == "max_tokens":
            return {"ok": False, "reason": "Jawaban terpotong (batas token).", "meta": meta}
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            parsed = json.loads(text)
        except ValueError:
            return {"ok": False, "reason": "Jawaban tidak berbentuk JSON yang sah.", "meta": meta}
        return {"ok": True, **_clean(parsed, len(headlines)), "meta": meta}

    result, cached, age = cache.cached_call("llm", key, CACHE_TTL, call, serve_stale_on_error=False)
    return {**result, "cached": cached, "cache_age_s": round(age) if cached else None,
            "headlines": [{k: h.get(k) for k in ("title", "url", "source", "time")} for h in headlines],
            "note": ("Dibaca Claude dari judul-judul bernomor di bawah saja. Pendapat model bahasa, "
                     "bukan fakta — cocokkan setiap poin dengan beritanya, dan nilai hasilnya di paper test.")}


def list_models() -> dict:
    """Live model list from the Models API when a key is set; the documented list otherwise."""
    if not has_credentials():
        return {"live": False, "as_of": KNOWN_MODELS_AS_OF, "models": KNOWN_MODELS,
                "note": ("Daftar terdokumentasi per tanggal di atas. Isi ANTHROPIC_API_KEY untuk "
                         "membaca daftar langsung dari API, termasuk model yang lebih baru.")}
    anthropic, client = _client()
    try:
        page = client.models.list(limit=100)
        rows = [{"id": m.id, "name": getattr(m, "display_name", m.id),
                 "created_at": str(getattr(m, "created_at", "") or "")} for m in page.data]
    except Exception as exc:  # noqa: BLE001
        raise _translate(anthropic, exc) from None
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return {"live": True, "as_of": None, "models": rows,
            "note": "Dibaca langsung dari Anthropic Models API dengan kunci Anda."}
