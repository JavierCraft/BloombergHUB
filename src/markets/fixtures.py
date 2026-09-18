"""Link a scheduled match to the Polymarket market that prices it.

The schedule and the market come from two systems that have never agreed on how
to spell a team. Liquipedia says `Natus Vincere`, a Polymarket handicap market
says `Na`Vi`; ESPN says `Stade Rennais FC 1901`, Polymarket says the same thing
with different filler words. So the join is done on normalised token sets, and
the score that produced it is reported alongside the answer.

That reported score is the whole point. A wrong join here is worse than no join:
it would put one match's odds next to another match's teams and look completely
convincing. So:

  * **both** sides must match, and the confidence is the weaker of the two;
  * below `MIN_CONFIDENCE` the fixture comes back unmatched, with the best
    candidate named so a human can check it;
  * the matched names are always returned next to the fixture names, so a
    mismatch is visible on screen rather than buried in a score.

Two market shapes are handled, because the two sports are listed differently:

  * **esports** — one market whose two outcomes *are* the team names
    (`["Stray Club", "Rostik999 Club"]`). Per-game and prop markets on the same
    event (Game 1 Winner, Handicap, Games Total, "Any Player Rampage?") are
    skipped; the series market is the one that answers "who wins".
  * **soccer** — three separate Yes/No markets per event (home win, draw, away
    win). They are reassembled into a 1X2 here, and the overround is reported
    rather than silently normalised away.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any, Iterable

from src.markets import POLYMARKET_GAMMA, _get

# Liquipedia's game key -> Polymarket's tag slug. Probed 11 September 2026, and
# the obvious slug is the wrong one twice: `cs2` and `lol` exist but hold only
# futures and prop markets ("Will FaZe win a Tier 1 event in 2026?"), with zero
# head-to-head fixtures. The per-match events live under the long slugs.
GAME_TAGS = {
    "dota2": "dota-2",
    "counterstrike": "counter-strike-2",
    "leagueoflegends": "league-of-legends",
    "valorant": "valorant",
}
SOCCER_TAG = "soccer"

# Tokens that carry no identity: every other org has them.
NOISE = {
    "esports", "esport", "esports club", "gaming", "team", "club", "the", "gg",
    "academy", "fc", "cf", "sc", "ac", "afc", "cfc", "ss", "as", "us", "sv",
    "fk", "sk", "bk", "if", "aik", "futbol", "football", "de", "del", "la",
    "le", "los", "saudi", "pro", "official",
}

# Below this, the fixture is reported unmatched rather than joined on a guess.
MIN_CONFIDENCE = 0.5

# Markets on an esports event that are not "who wins the series".
NOT_SERIES = re.compile(
    r"\b(game\s*\d|map\s*\d|handicap|total|o/u|over|under|first\s|rampage|"
    r"ultra\s*kill|roshan|barracks|daytime|correct\s*score|prop)\b",
    re.I,
)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def _tokens(name: Any) -> set[str]:
    """Normalise a team name to the words that actually identify it."""
    text = _strip_accents(str(name or "")).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = {w for w in text.split() if w and w not in NOISE}
    # A name made entirely of filler ("FC United") must not become empty.
    return words or {w for w in text.split() if w}


def _similarity(a: Any, b: Any) -> float:
    """Overlap over the shorter side, so `Rennais` still matches
    `Stade Rennais FC 1901` without rewarding long names."""
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    shared = left & right
    if shared:
        return len(shared) / min(len(left), len(right))
    # No shared whole word: allow one clear prefix hit ("navi" vs "naviesports").
    for x in left:
        for y in right:
            if len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x)):
                return 0.5
    return 0.0


def _prices(market: dict) -> list[dict]:
    try:
        labels = market.get("outcomes") or []
        prices = market.get("outcomePrices") or []
        labels = json.loads(labels) if isinstance(labels, str) else labels
        prices = json.loads(prices) if isinstance(prices, str) else prices
        if len(labels) != len(prices):
            return []
        rows = []
        for label, price in zip(labels, prices):
            value = float(price)
            if not math.isfinite(value) or not 0 <= value <= 1:
                return []
            rows.append({"outcome": str(label), "price": value})
        return rows
    except (TypeError, ValueError):
        return []


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Market shapes
# ---------------------------------------------------------------------------


def _series_market(event: dict) -> dict | None:
    """The one esports market whose outcomes are the two team names."""
    best = None
    for market in event.get("markets") or []:
        if market.get("closed") or not market.get("active", True):
            continue
        question = str(market.get("question") or "")
        if NOT_SERIES.search(question):
            continue
        rows = _prices(market)
        if len(rows) != 2:
            continue
        labels = {r["outcome"].lower() for r in rows}
        if labels & {"yes", "no", "over", "under"}:
            continue
        # Prefer the explicit best-of market when the event has several.
        score = (2 if "(bo" in question.lower() else 0,
                 _num(market.get("volume24hr")) or 0)
        if best is None or score > best[0]:
            best = (score, market, rows)
    if not best:
        return None
    _score, market, rows = best
    return {"market": market, "outcomes": rows,
            "teams": [r["outcome"] for r in rows]}


def _soccer_1x2(event: dict) -> dict | None:
    """Reassemble home/draw/away from three separate Yes/No markets."""
    title = str(event.get("title") or "")
    parts = re.split(r"\s+vs\.?\s+", title, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()

    legs: dict[str, dict] = {}
    for market in event.get("markets") or []:
        if market.get("closed") or not market.get("active", True):
            continue
        rows = _prices(market)
        if len(rows) != 2 or {r["outcome"].lower() for r in rows} != {"yes", "no"}:
            continue
        yes = next((r["price"] for r in rows if r["outcome"].lower() == "yes"), None)
        if yes is None:
            continue
        question = str(market.get("question") or "")
        low = question.lower()
        if "draw" in low:
            legs["draw"] = {"price": yes, "question": question, "raw": market,
                            "id": market.get("conditionId")}
        elif re.match(r"^will\s+(.+?)\s+win\b", low):
            who = re.match(r"^will\s+(.+?)\s+win\b", question, re.I).group(1)
            key = "home" if _similarity(who, home) >= _similarity(who, away) else "away"
            legs.setdefault(key, {"price": yes, "question": question, "raw": market,
                                  "label": who.strip(), "id": market.get("conditionId")})

    if "home" not in legs or "away" not in legs:
        return None

    outcomes = [
        {"outcome": legs["home"].get("label") or home, "price": legs["home"]["price"]},
        {"outcome": "Draw", "price": legs["draw"]["price"]} if "draw" in legs else None,
        {"outcome": legs["away"].get("label") or away, "price": legs["away"]["price"]},
    ]
    outcomes = [o for o in outcomes if o]
    total = sum(o["price"] for o in outcomes)
    return {
        "market": legs["home"], "outcomes": outcomes, "teams": [home, away],
        # Turnover for a football fixture is spread across all three books, so
        # the home-win market's own volume understates the fixture. They are
        # summed here; `_summarise` reads this instead of one leg.
        "legs": [leg["raw"] for leg in legs.values() if leg.get("raw")],
        # Three independent Yes/No books rarely sum to exactly 1. Reporting the
        # overround is more honest than scaling it away without saying so.
        "overround_pct": round((total - 1) * 100, 2),
        "has_draw": "draw" in legs,
    }


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------


# Gamma caps a single /events answer at 100 rows whatever `limit` asks for, so
# the busy tags (counter-strike-2 alone carries 300+ active events) need paging.
PAGE = 100
MAX_PAGES = 3


def _events(tag: str, limit: int = 300) -> list[dict]:
    """Active events for a tag, soonest-to-settle first.

    Ordering matters more than it looks, and both obvious choices are wrong.

    Ascending `startDate` returns the *oldest* events, and an esports event stays
    `closed: false` for months after the match is over because its novelty props
    ("Map 1: Odd/Even Total Kills?") never settle — paging that way returned 300
    finished April fixtures whose series markets were all closed. Descending
    `startDate` overshoots the other way into fixtures scheduled days out that
    nobody has traded yet, so every outcome still sits at the 0.5 default: 100
    soccer events, only 32 with a real price.

    Ascending `endDate` with `end_date_min=now` asks the question that actually
    matters — what settles soonest — and returned 88 of 100 priced.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        batch = _get(f"{POLYMARKET_GAMMA}/events", {
            "tag_slug": tag, "active": "true", "closed": "false",
            "limit": PAGE, "offset": page * PAGE, "end_date_min": now,
            "order": "endDate", "ascending": "true",
        }, "Polymarket") or []
        rows.extend(batch)
        if len(batch) < PAGE or len(rows) >= limit:
            break
    return rows[:limit]


def _summarise(event: dict, shape: dict) -> dict:
    market = shape["market"]
    ranked = sorted(shape["outcomes"], key=lambda o: -o["price"])
    top, runner = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    margin = round((top["price"] - runner["price"]) * 100, 2) if runner else None
    tied = margin is not None and abs(margin) < 0.5

    # A single-market fixture reports its own turnover; a soccer fixture reports
    # the sum across its home/draw/away books.
    books = shape.get("legs") or [market]
    volume = [v for v in (_num(b.get("volume24hr")) for b in books) if v is not None]
    depth = [v for v in (_num(b.get("liquidityNum") or b.get("liquidity")) for b in books)
             if v is not None]

    return {
        "event_title": event.get("title"),
        "url": f"https://polymarket.com/event/{event.get('slug')}"
               if event.get("slug") else None,
        "condition_id": market.get("conditionId") or market.get("id"),
        "teams": shape["teams"],
        "outcomes": [{"outcome": o["outcome"], "price": round(o["price"], 4),
                      "pct": f"{o['price'] * 100:.1f}%"} for o in ranked],
        "favorite": None if tied else top["outcome"],
        "favorite_pct": None if tied else f"{top['price'] * 100:.1f}%",
        "favorite_price": None if tied else round(top["price"], 4),
        "margin_pp": margin,
        "tied": tied,
        "volume24h": round(sum(volume), 2) if volume else None,
        "liquidity": round(sum(depth), 2) if depth else None,
        "start_date": event.get("startDate"),
        "end_date": event.get("endDate"),
        "overround_pct": shape.get("overround_pct"),
        "has_draw": shape.get("has_draw"),
    }


def board(kind: str, game: str = "", limit: int = 300) -> list[dict]:
    """Every priced fixture Polymarket currently lists for a game or for soccer."""
    if kind == "soccer":
        tag, shaper = SOCCER_TAG, _soccer_1x2
    else:
        if game not in GAME_TAGS:
            raise ValueError(f"Game harus salah satu dari {list(GAME_TAGS)}")
        tag, shaper = GAME_TAGS[game], _series_market

    rows = []
    for event in _events(tag, limit):
        shape = shaper(event)
        if shape and shape["outcomes"]:
            rows.append(_summarise(event, shape))
    return rows


def attach(fixtures: list[dict], listed: list[dict],
           home_key: str = "team_a", away_key: str = "team_b") -> list[dict]:
    """Put each fixture next to the Polymarket market that prices it.

    Mutates and returns the fixture rows, adding `poly` (the match, or the
    reason there isn't one). A fixture that cannot be matched confidently keeps
    its best candidate under `poly.best_candidate` so the miss is inspectable.
    """
    for fixture in fixtures:
        home, away = fixture.get(home_key), fixture.get(away_key)
        best, best_score, best_flip = None, 0.0, False

        for market in listed:
            names = market["teams"]
            if len(names) < 2:
                continue
            straight = min(_similarity(home, names[0]), _similarity(away, names[1]))
            flipped = min(_similarity(home, names[1]), _similarity(away, names[0]))
            score, flip = (straight, False) if straight >= flipped else (flipped, True)
            if score > best_score:
                best, best_score, best_flip = market, score, flip

        if best and best_score >= MIN_CONFIDENCE:
            fixture["poly"] = dict(
                best,
                matched=True,
                confidence=round(best_score, 2),
                sides_swapped=best_flip,
                note=("Dicocokkan dari nama tim, bukan dari nomor pertandingan resmi. "
                      "Periksa tanggal dan turnamennya sebelum memakai odds ini."),
            )
        else:
            fixture["poly"] = {
                "matched": False,
                "confidence": round(best_score, 2),
                "best_candidate": best["event_title"] if best else None,
                "reason": ("Tidak ada pasar Polymarket yang cocok dengan kedua nama tim "
                           "di atas ambang keyakinan. Tidak ditebak."),
            }
    return fixtures


def summary(fixtures: Iterable[dict]) -> dict:
    rows = list(fixtures)
    matched = [f for f in rows if f.get("poly", {}).get("matched")]
    return {
        "fixtures": len(rows),
        "matched": len(matched),
        "unmatched": len(rows) - len(matched),
        "min_confidence": MIN_CONFIDENCE,
        "note": (
            "Pencocokan memakai nama tim yang dinormalkan; skor keyakinan "
            "ditampilkan per baris. Pertandingan tanpa pasar yang cocok dibiarkan "
            "kosong, bukan diisi pasar terdekat."
        ),
    }
