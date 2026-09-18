"""Features for "will this market resolve YES?" at a moment before its deadline.

`snapshot_features()` builds every feature from inputs that exist both in a
historical price path and in a live market: the price now, the price a day and
a week earlier, the time left to the deadline, the market's age, and the words
of the question. Training rows and live predictions go through the same
function, so there is no second implementation to drift apart.

Deliberately *not* features:

  * **volume and trader counts** — a resolved market reports its final totals,
    which include trading after the snapshot. Using them leaks the future.
  * **news** — nobody archives what the headlines looked like at a past moment.
    News is logged next to each forward paper prediction instead and judged on
    those results (`paper.news_signal`).
  * **the source** — the model is trained mostly on Manifold and applied to both
    Manifold and Polymarket; a source flag could not be used honestly live.

"Deadline" means the *scheduled* end, the same thing a live market's countdown
shows: Polymarket's `endDate`, Manifold's `closeTime` for markets that ran to it,
and — for Manifold markets resolved early, whose `closeTime` was overwritten —
the date written in the question when there is one. Markets whose real deadline
cannot be recovered are flagged and left out of training (see
`historical_rows`).
"""
from __future__ import annotations

import bisect
import math
import re
from typing import Any

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
P_FLOOR = 0.01

# Snapshots taken before each resolved market's deadline, in days. Dense near
# the deadline because that is the question being studied.
OFFSETS_DAYS = (0.25, 0.5, 1, 2, 3, 5, 7, 10, 14)
MAX_OFFSET_DAYS = max(OFFSETS_DAYS)

FEATURES = (
    "logit_p", "extremity", "log_days_left", "inv_days_left", "logit_x_logdays",
    "chg_1d", "chg_7d", "abs_chg_1d", "log_age_days", "frac_elapsed",
    "q_by_deadline", "q_threshold", "q_negation", "q_match",
    "q_politics", "q_macro", "q_crypto", "q_tech",
)

LABELS = {
    "logit_p": "harga pasar (skala logit)",
    "extremity": "seberapa jauh harga dari 50%",
    "log_days_left": "sisa hari ke tenggat (log)",
    "inv_days_left": "kedekatan ke tenggat",
    "logit_x_logdays": "harga × sisa waktu",
    "chg_1d": "gerak harga 24 jam",
    "chg_7d": "gerak harga 7 hari",
    "abs_chg_1d": "besar gerak 24 jam",
    "log_age_days": "umur pasar (log)",
    "frac_elapsed": "porsi umur pasar yang sudah lewat",
    "q_by_deadline": "pertanyaan bertenggat (by/before/end of)",
    "q_threshold": "pertanyaan ambang angka (above/below/$/%)",
    "q_negation": "pertanyaan berbentuk negatif",
    "q_match": "pertandingan (vs/win/beat)",
    "q_politics": "topik politik",
    "q_macro": "topik makro (Fed/inflasi/suku bunga)",
    "q_crypto": "topik kripto",
    "q_tech": "topik teknologi/AI",
}

_FLAGS: dict[str, re.Pattern] = {
    "q_by_deadline": re.compile(
        r"\b(by|before|until|through|no later than|end of|by the end)\b"
        r"|\bin (19|20)\d\d\b|\bthis (week|month|year|season)\b", re.I),
    "q_threshold": re.compile(
        r"\b(above|below|over|under|at least|at most|more than|less than|higher than|lower than"
        r"|hit|hits|reach|reaches|exceed|exceeds|surpass|close above|close below)\b"
        r"|[$%€£]|\b\d+(\.\d+)?\s?(k|m|bn|b|million|billion|trillion|percent|bps)\b", re.I),
    "q_negation": re.compile(r"\b(not|no|never|fail|fails|without|lose|loses)\b", re.I),
    "q_match": re.compile(r"\bvs\.?(?=\s)|\bversus\b|\b(win|wins|beat|beats|defeat|defeats)\b", re.I),
    "q_politics": re.compile(
        r"\b(election|elected|president|presidential|senate|congress|governor|mayor|prime minister"
        r"|parliament|vote|votes|poll|polls|trump|biden|harris|vance|newsom|republican|democrat"
        r"|gop|nominee|primary|impeach\w*)\b", re.I),
    "q_macro": re.compile(
        r"\b(fed|fomc|interest rates?|rate cut|rate hike|inflation|cpi|pce|gdp|recession"
        r"|unemployment|jobs report|payrolls|treasury|yield|tariffs?|bps)\b", re.I),
    "q_crypto": re.compile(
        r"\b(bitcoin|btc|ethereum|eth|crypto\w*|solana|xrp|dogecoin|doge|stablecoin)\b", re.I),
    "q_tech": re.compile(
        r"\b(ai|openai|gpt[-\w]*|chatgpt|anthropic|claude|gemini|google|apple|nvidia|microsoft"
        r"|meta|tesla|spacex|llm|model|iphone)\b", re.I),
}


_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_FULL_DATE = re.compile(r"\b" + _MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I)
_MONTH_YEAR = re.compile(r"\b(before\s+|by\s+|end of\s+|in\s+|during\s+|through\s+)?" + _MONTH + r"\s+(\d{4})\b", re.I)
_YEAR = re.compile(r"\b(by|before|end of|in|during|through)\s+(?:the end of\s+)?((?:19|20)\d\d)\b", re.I)
_MONTH_NUM = {name: i for i, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def stated_deadline(question: str | None, created_ms: int | None) -> int | None:
    """The deadline written in a question, as the last minute of that day (UTC).

    "by March 31, 2025" → 31 Mar 2025 · "before July 2025" → 1 Jul 2025 ·
    "in September 2026" → 30 Sep 2026 · "by 2027" → 31 Dec 2027 ·
    "before 2027" → 31 Dec 2026. Anything else, or a date that is not after the
    market's creation, gives None rather than a guess.
    """
    import calendar
    from datetime import datetime, timezone

    text = question or ""
    if created_ms is None:
        return None
    created = datetime.fromtimestamp(created_ms / 1000, timezone.utc)

    def at(year: int, month: int, day: int) -> int | None:
        try:
            day = min(day, calendar.monthrange(year, month)[1])
            return int(datetime(year, month, day, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            return None

    found = None
    match = _FULL_DATE.search(text)
    if match:
        month = _MONTH_NUM[match.group(1)[:3].lower()]
        year = int(match.group(3)) if match.group(3) else created.year
        found = at(year, month, int(match.group(2)))
        if found is not None and not match.group(3) and found < created_ms:
            found = at(year + 1, month, int(match.group(2)))
    if found is None:
        match = _MONTH_YEAR.search(text)
        if match:
            month, year = _MONTH_NUM[match.group(2)[:3].lower()], int(match.group(3))
            before = (match.group(1) or "").strip().lower() == "before"
            found = at(year, month, 1) if before else at(year, month, calendar.monthrange(year, month)[1])
    if found is None:
        match = _YEAR.search(text)
        if match:
            year = int(match.group(2))
            found = at(year - 1 if match.group(1).lower() == "before" else year, 12, 31)
    if found is None or found <= created_ms or found > created_ms + 5 * 365 * DAY_MS:
        return None
    return found


def clip_p(value: float) -> float:
    return min(1 - P_FLOOR, max(P_FLOOR, float(value)))


def question_flags(question: str | None) -> dict[str, float]:
    text = question or ""
    return {name: 1.0 if pattern.search(text) else 0.0 for name, pattern in _FLAGS.items()}


def snapshot_features(question: str | None, p: float, days_left: float, age_days: float,
                      p_1d_ago: float | None, p_7d_ago: float | None) -> dict[str, float]:
    """Every model feature for one moment. Missing history means no move (0)."""
    p = clip_p(p)
    days_left = max(0.0, float(days_left))
    age = max(0.0, float(age_days))
    logit = math.log(p / (1 - p))
    log_days = math.log1p(days_left)
    chg_1d = p - clip_p(p_1d_ago) if p_1d_ago is not None else 0.0
    chg_7d = p - clip_p(p_7d_ago) if p_7d_ago is not None else 0.0
    return {
        "logit_p": logit,
        "extremity": abs(p - 0.5) * 2,
        "log_days_left": log_days,
        "inv_days_left": 1 / (1 + days_left),
        "logit_x_logdays": logit * log_days,
        "chg_1d": chg_1d,
        "chg_7d": chg_7d,
        "abs_chg_1d": abs(chg_1d),
        "log_age_days": math.log1p(age),
        "frac_elapsed": age / (age + days_left) if age + days_left > 0 else 1.0,
        **question_flags(question),
    }


class PricePath:
    """Step function of price over time, built from trades.

    `at(t)` is the price after the last trade at or before `t`. Before the first
    known trade it returns the opening price only when the path reaches back to
    the market's creation; otherwise the price there is unknown and it says so
    with None rather than guessing.
    """

    def __init__(self, points: list, initial: float | None = None,
                 complete: bool = False, covered_from_ms: int | None = None):
        clean = []
        for item in points or []:
            try:
                t, p = int(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(p) and 0 <= p <= 1:
                clean.append((t, p))
        clean.sort()
        self.times = [t for t, _ in clean]
        self.prices = [p for _, p in clean]
        self.initial = initial if initial is None else float(initial)
        self.complete = complete
        self.covered_from = covered_from_ms if covered_from_ms is not None else (
            self.times[0] if self.times else None)

    def at(self, t_ms: float) -> float | None:
        i = bisect.bisect_right(self.times, t_ms) - 1
        if i >= 0:
            return self.prices[i]
        if self.complete and self.initial is not None:
            return self.initial
        return None


def historical_rows(history: dict, offsets: tuple[float, ...] = OFFSETS_DAYS) -> list[dict]:
    """Training rows for one resolved market: one per snapshot offset.

    Snapshots count back from the *scheduled* deadline when the source keeps it
    (Polymarket's `endDate`). Manifold overwrites `closeTime` when a market is
    resolved early, so for those markets the real deadline is unknown and the
    rows are flagged `deadline_known: False`. Measured on 2.552 Manifold markets:
    "by <date>" questions that ran to their deadline were priced 30.1% three days
    out and resolved YES 24.2% of the time; early-closed ones, whose "deadline" is
    really the resolution moment, were priced 58.3% and resolved YES 78.9%.
    Mixing the two teaches the model that YES gets likelier near a deadline —
    the opposite of what the honest rows show.

    Excluding them is not neutral either: 35% of the early-closed "by <date>"
    markets whose date could be read resolved within 14 days of it (64 YES, 37
    NO), so dropping them all tilts the data toward NO. When the question states
    its date, that date becomes the deadline (`deadline_source`) and the market
    stays in.
    """
    outcome = history.get("outcome")
    created = history.get("created_ms")
    resolved = history.get("resolved_ms")
    if history.get("scheduled_deadline_ms"):
        deadline, deadline_known, deadline_source = history["scheduled_deadline_ms"], True, "jadwal"
    elif not history.get("closed_early"):
        deadline, deadline_known, deadline_source = history.get("deadline_ms"), True, "jadwal"
    else:
        # The close time was overwritten. If the question states its own date and
        # the market resolved no later than two days after it, that date is the
        # deadline; a resolution well after the stated date means the parse is
        # wrong or the question means something else, so it stays unknown.
        stated = stated_deadline(history.get("question"), created)
        if stated and resolved and resolved <= stated + 2 * DAY_MS:
            deadline, deadline_known, deadline_source = stated, True, "tanggal di pertanyaan"
        else:
            deadline, deadline_known, deadline_source = history.get("deadline_ms"), False, "tidak diketahui"
    if outcome not in (0, 1) or deadline is None or created is None:
        return []
    path = PricePath(history.get("points") or [], history.get("initial_prob"),
                     bool(history.get("complete")), history.get("covered_from_ms"))

    rows = []
    for offset in offsets:
        snap = deadline - offset * DAY_MS
        if snap <= created + HOUR_MS:
            continue
        if resolved and snap >= resolved:
            continue
        price = path.at(snap)
        if price is None:
            continue
        feats = snapshot_features(
            history.get("question"), price, offset, (snap - created) / DAY_MS,
            path.at(snap - DAY_MS), path.at(snap - 7 * DAY_MS))
        rows.append({
            "source": history.get("source"),
            "market_id": history.get("id"),
            "question": history.get("question"),
            "snapshot_ms": int(snap),
            "deadline_ms": int(deadline),
            "offset_days": offset,
            "deadline_known": deadline_known,
            "deadline_source": deadline_source,
            "p": round(price, 5),
            "y": int(outcome),
            **feats,
        })
    return rows


def live_features(snapshot: dict, now_ms: int) -> dict[str, Any] | None:
    """Features for a live market in the normalised deadline-snapshot shape.

    Needs `p_yes` and `deadline_ms`; `created_ms`, `chg_1d` and `chg_7d` are used
    when present. Returns None when the market cannot be described honestly.
    """
    p = snapshot.get("p_yes")
    deadline = snapshot.get("deadline_ms")
    if p is None or deadline is None:
        return None
    days_left = (deadline - now_ms) / DAY_MS
    if days_left < 0:
        return None
    created = snapshot.get("created_ms")
    age_days = (now_ms - created) / DAY_MS if created is not None else None
    imputed_age = age_days is None
    if imputed_age:
        age_days = max(1.0, days_left)
    chg_1d = snapshot.get("chg_1d")
    chg_7d = snapshot.get("chg_7d")
    feats = snapshot_features(
        snapshot.get("question"), p, days_left, age_days,
        (p - chg_1d) if chg_1d is not None else None,
        (p - chg_7d) if chg_7d is not None else None)
    return {"features": feats, "days_left": days_left, "imputed_age": imputed_age,
            "momentum_known": chg_1d is not None}


def deadline_bucket(days_left: float | None) -> str:
    if days_left is None:
        return "tidak diketahui"
    if days_left <= 1:
        return "≤1 hari"
    if days_left <= 3:
        return "1–3 hari"
    if days_left <= 7:
        return "3–7 hari"
    if days_left <= 14:
        return "7–14 hari"
    return ">14 hari"


BUCKET_ORDER = ("≤1 hari", "1–3 hari", "3–7 hari", "7–14 hari", ">14 hari", "tidak diketahui")
