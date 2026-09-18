"""Following the newest LLMs: releases as they are announced, from the labs.

Three layers, fastest-changing first:

  * **lab and tracker feeds** — OpenAI, Google DeepMind, Google AI, Hugging
    Face, NVIDIA, Meta, AWS, plus people who test new models within hours of
    release (Simon Willison) and AI news desks. Headlines are kept when they
    name a model family; those that also carry a release verb are flagged.
  * **Hugging Face** — open-weight releases from watched labs (see
    `ModelTracker.watch_authors`), already on the Tech page.
  * **Anthropic Models API** — the authoritative list of Claude models, read
    live when `ANTHROPIC_API_KEY` is set.

Anthropic and Mistral publish no RSS at the addresses tested (404), so their
announcements reach this radar through the trackers and news desks. That gap is
stated on the page rather than hidden.

Why it lives in a prediction-market tool: markets such as "Which company has
the best AI model at the end of September?" resolve on releases and
leaderboards. Knowing within hours that a model shipped is the edge.
"""
from __future__ import annotations

import concurrent.futures
import re
from datetime import datetime, timezone

LAB_FEEDS = ("openai", "deepmind", "google-ai", "hf-blog", "nvidia", "meta-eng", "aws-ml",
             "simonw", "decoder", "latentspace", "interconnects", "importai", "mittr", "techmeme",
             "verge", "arstechnica", "techcrunch", "gn-tech")

MODEL_FAMILIES = re.compile(
    r"\b(claude(?:\s+(?:opus|sonnet|haiku|fable|mythos))?(?:\s+\d+(?:\.\d+)?)?"
    r"|opus\s+\d+(?:\.\d+)?|sonnet\s+\d+(?:\.\d+)?|haiku\s+\d+(?:\.\d+)?"
    r"|gpt[-\s]?\d+(?:\.\d+)?(?:[-\s]?(?:mini|nano|pro|turbo|o))?|o[1-9]-(?:mini|pro)"
    r"|gemini(?:\s+\d+(?:\.\d+)?)?(?:\s+(?:pro|flash|ultra|nano|deep think))?|gemma\s*\d*"
    r"|llama\s*\d+(?:\.\d+)?|qwen\s*\d*(?:\.\d+)?|deepseek(?:[-\s]?(?:v|r)\d+(?:\.\d+)?)?"
    r"|grok\s*\d*(?:\.\d+)?|mistral(?:\s+(?:large|medium|small))?|mixtral|codestral|magistral"
    r"|phi-?\d+(?:\.\d+)?|nemotron|kimi(?:\s+k\d+)?|glm-?\d+(?:\.\d+)?|minimax(?:-m\d)?|olmo\s*\d*"
    r"|command\s+[ar]\+?|sora\s*\d*|veo\s*\d*|imagen\s*\d*)\b",
    re.I)

RELEASE_WORDS = re.compile(
    r"\b(release[sd]?|launch(?:es|ed)?|introduc(?:es|ed|ing)|announc(?:es|ed|ing)|unveil(?:s|ed)?"
    r"|debut(?:s|ed)?|ships?|rolls? out|rolling out|now available|available today|open[- ]weights?"
    r"|open[- ]sources?d?|preview|new model|benchmark)\b", re.I)


def _stamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def radar(hours: int = 336, limit: int = 120) -> dict:
    from src import news
    from src.news.sources import BY_CODE

    feeds = [BY_CODE[c] for c in LAB_FEEDS if c in BY_CODE]
    rows, failed = [], []

    def pull(feed):
        try:
            return feed, news.fetch_feed(feed)[0], None
        except Exception as exc:  # noqa: BLE001
            return feed, [], getattr(exc, "message", None) or str(exc)[:140]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for feed, items, error in pool.map(pull, feeds):
            if error:
                failed.append({"source": feed.name, "reason": error})
            for item in items:
                text = f"{item.get('title') or ''} {item.get('summary') or ''}"
                found = {m.group(0).strip() for m in MODEL_FAMILIES.finditer(item.get("title") or "")}
                if not found:
                    found = {m.group(0).strip() for m in MODEL_FAMILIES.finditer(text)}
                if not found:
                    continue
                rows.append({
                    "time": item.get("time"),
                    "lab_or_desk": feed.name,
                    "kind": feed.kind,
                    "title": item.get("title"),
                    "models": ", ".join(sorted(found, key=str.lower))[:120],
                    "release_signal": bool(RELEASE_WORDS.search(text)),
                    "url": item.get("url"),
                })

    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    seen, out = set(), []
    for row in sorted(rows, key=lambda r: _stamp(r["time"]), reverse=True):
        if _stamp(row["time"]) < cutoff:
            continue
        key = re.sub(r"[^a-z0-9]", "", (row["title"] or "").lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    families: dict[str, int] = {}
    for row in out:
        for name in row["models"].split(", "):
            base = re.split(r"[\s-]", name.lower())[0]
            families[base] = families.get(base, 0) + 1
    return {
        "hours": hours,
        "count": len(out),
        "rows": out[:limit],
        "families": sorted(({"family": k, "mentions": v} for k, v in families.items()),
                           key=lambda r: -r["mentions"])[:15],
        "feeds": [f.name for f in feeds],
        "failed": failed,
        "note": ("Judul dari blog lab dan pelacak AI yang menyebut nama keluarga model. 'Rilis' ditandai "
                 "bila judul juga memuat kata seperti launch/release/introducing. Anthropic dan Mistral "
                 "tidak menerbitkan RSS yang bisa dibaca dari sini, jadi kabar mereka datang lewat "
                 "pelacak dan media."),
    }
