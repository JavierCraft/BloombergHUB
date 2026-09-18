"""Machine learning over news: relevance, tone, stories, and rising terms.

Four tools, all local, all explainable, none pretending to understand meaning:

  * **relevance** — TF-IDF cosine similarity between a market question and each
    headline. It ranks which headlines are *about* the market. It does not know
    whether a headline makes YES more or less likely.
  * **tone** — a lexicon score in [-1, 1] (English and Indonesian word lists,
    with negation). "Fed cuts rates" and "Fed refuses to cut" are both about the
    Fed; tone tells them apart only as far as words like *refuses* carry it.
    Tone is not direction: a positive headline can make a "Will X fail?" market
    less likely.
  * **stories** — headlines grouped into events by similarity, each with a
    representative title, sources, time span and key terms. This is the "create
    news" half done without a language model: an extractive digest that only
    ever repeats what the sources said.
  * **rising terms** — words whose share of recent headlines grew fastest against
    the previous window. The old leaderboard counted frequency; this measures
    acceleration, which is what "trending" actually means.

The corpus these learn from is `data/news/corpus-*.jsonl`, filled by every feed
refresh (see `src/news`). The optional LLM reading lives in `llm.py`.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

STOPWORDS = frozenset("""
a about above after again against all also am amid an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he
her here hers herself him himself his how i if in into is it its itself just me more most my myself new no nor
not now of off on once only or other our ours out over own same says said she should so some such than that the
their theirs them themselves then there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your yours yourself yourselves live latest update
updates breaking news report reports video watch photos opinion analysis today week year years day days time
first last one two three how why what may might get gets got make makes made take takes via amid per us u.s
will would could should dan yang di ke dari untuk pada dengan ini itu dalam akan juga tidak ada atau karena
oleh sebagai bisa saat telah sudah masih lebih hingga para kata jadi agar bagi setelah antara tersebut begini
begitu harus soal usai jelang kini baru tahun hari
""".split())

POSITIVE = frozenset("""
gain gains gained rise rises rising rose surge surges surged soar soars soared rally rallies rallied jump jumps
jumped climb climbs climbed record high highs beat beats boost boosts boosted strong stronger strength growth grow
grows grew win wins won victory lead leads leading ahead approve approves approved approval pass passes passed
agree agrees agreed deal deals breakthrough success successful succeed succeeds recover recovers recovery rebound
upgrade upgraded optimism optimistic confident confidence support supports backed bullish expand expands expanded
profit profits positive improve improves improved peace ceasefire launch launches launched secure secured
naik menguat melonjak meroket tumbuh untung laba surplus positif setuju disetujui menang unggul pulih membaik
optimistis rekor tertinggi sukses berhasil damai
""".split())

NEGATIVE = frozenset("""
fall falls fell falling drop drops dropped plunge plunges plunged crash crashes crashed slump slumps slumped sink
sinks sank tumble tumbles tumbled decline declines declined loss losses lose loses lost miss misses missed weak
weaker weakness low lows cut cuts reject rejects rejected rejection ban bans banned fail fails failed failure
collapse collapses collapsed crisis recession inflation fear fears worry worries warn warns warned warning threat
threats attack attacks attacked war wars killed kill deaths dead delay delays delayed cancel cancels cancelled
canceled lawsuit sue sued probe investigation scandal resign resigns resigned fired downgrade downgraded bearish
default defaults strike strikes shutdown sanctions tariff tariffs layoffs uncertainty risk risks negative hack
hacked breach outage block blocks blocked denied deny denies halt halts halted
turun melemah anjlok merosot jatuh rugi defisit negatif tolak ditolak kalah gagal krisis resesi inflasi ancaman
serangan perang tewas tunda ditunda batal dibatalkan gugatan mundur dipecat larangan sanksi tarif phk
""".split())

NEGATORS = frozenset("not no never without cannot can't won't isn't aren't wasn't don't doesn't didn't "
                     "tidak bukan belum tanpa tak".split())

_TOKEN = re.compile(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9'.-]*")


def tokens(text: str | None) -> list[str]:
    return [t.strip(".'-").lower() for t in _TOKEN.findall(text or "") if t.strip(".'-")]


def content_tokens(text: str | None) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 1 and not t.isdigit()]


def tone(text: str | None) -> float:
    """Lexicon tone in [-1, 1]. Zero means no scored words, not "neutral news"."""
    words = tokens(text)
    score = hits = 0
    flip_until = -1
    for i, word in enumerate(words):
        if word in NEGATORS:
            flip_until = i + 3
            continue
        polarity = 1 if word in POSITIVE else -1 if word in NEGATIVE else 0
        if not polarity:
            continue
        if i <= flip_until:
            polarity = -polarity
        score += polarity
        hits += 1
    if not hits:
        return 0.0
    return round(max(-1.0, min(1.0, score / (hits + 1) * 1.5)), 3)


def tone_label(value: float | None) -> str:
    if value is None:
        return "tidak diketahui"
    if value >= 0.25:
        return "positif"
    if value <= -0.25:
        return "negatif"
    return "netral/campuran"


def _parse_time(value: Any) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _text(item: dict) -> str:
    return f"{item.get('title') or ''}. {(item.get('summary') or '')[:200]}"


def _vectorizer():
    from sklearn.feature_extraction.text import TfidfVectorizer

    return TfidfVectorizer(
        tokenizer=content_tokens, token_pattern=None, lowercase=False,
        ngram_range=(1, 2), sublinear_tf=True, min_df=1, norm="l2",
    )


def keywords(question: str | None, limit: int = 7) -> str:
    """Search words for a market question: entities and numbers first."""
    text = re.sub(r"\?|\(.*?\)", " ", question or "")
    raw = re.findall(r"[A-Za-z0-9$%.'-]+", text)
    drop = STOPWORDS | {"will", "happen", "market", "resolve", "yes", "before", "end", "by"}
    picked: list[str] = []
    for word in raw:
        clean = word.strip(".'-")
        low = clean.lower()
        if not clean or low in drop or len(clean) < 2:
            continue
        if re.fullmatch(r"(19|20)\d\d", clean):
            continue
        if clean not in picked:
            picked.append(clean)
    entities = [w for w in picked if w[0].isupper() or any(ch.isdigit() for ch in w)]
    rest = [w for w in picked if w not in entities]
    return " ".join((entities + rest)[:limit])


def rank(query_text: str, items: list[dict], top: int = 12, min_score: float = 0.08) -> list[dict]:
    """Headlines ordered by TF-IDF similarity to `query_text`."""
    items = [i for i in items if i.get("title")]
    if not items or not content_tokens(query_text):
        return []
    vectorizer = _vectorizer()
    try:
        matrix = vectorizer.fit_transform([query_text] + [_text(i) for i in items])
    except ValueError:   # empty vocabulary after stopwords
        return []
    scores = (matrix[1:] @ matrix[0].T).toarray().ravel()
    ranked = sorted(zip(scores, items), key=lambda kv: -kv[0])
    out = []
    for score, item in ranked[:top]:
        if score < min_score:
            break
        out.append({**item, "relevance": round(float(score), 3), "tone": tone(_text(item))})
    return out


def market_features(question: str, items: list[dict], now: float | None = None,
                    evidence_limit: int = 10) -> dict:
    """News features for one market at `now`, logged with every paper prediction."""
    now = now or datetime.now(timezone.utc).timestamp()
    relevant = rank(question, items, top=max(40, evidence_limit), min_score=0.12)
    ages = []
    for item in relevant:
        stamp = _parse_time(item.get("time"))
        item["age_h"] = round((now - stamp) / 3600, 1) if stamp else None
        if stamp:
            ages.append((now - stamp) / 3600)
    in_24 = [i for i in relevant if i.get("age_h") is not None and 0 <= i["age_h"] <= 24]
    in_72 = [i for i in relevant if i.get("age_h") is not None and 0 <= i["age_h"] <= 72]
    prev_48 = len(in_72) - len(in_24)
    weight = sum(i["relevance"] for i in in_72)
    mean_tone = (sum(i["tone"] * i["relevance"] for i in in_72) / weight) if weight else None
    return {
        "n_24h": len(in_24),
        "n_72h": len(in_72),
        "sources_72h": len({i.get("source") for i in in_72}),
        "max_relevance": relevant[0]["relevance"] if relevant else 0.0,
        "tone_72h": round(mean_tone, 3) if mean_tone is not None else None,
        "tone_label": tone_label(mean_tone),
        "newest_age_h": round(min(ages), 1) if ages else None,
        # Per-day rate in the last day against the two days before it.
        "acceleration": round(len(in_24) - prev_48 / 2, 2),
        "evidence": [{k: i.get(k) for k in ("title", "url", "source", "time", "relevance", "tone", "age_h")}
                     for i in relevant[:evidence_limit]],
    }


def stories(items: list[dict], threshold: float = 0.3, max_items: int = 600,
            min_size: int = 2) -> list[dict]:
    """Group headlines into events. Leader clustering on TF-IDF cosine similarity."""
    import numpy as np

    items = [i for i in items if i.get("title")][:max_items]
    if len(items) < 2:
        return []
    vectorizer = _vectorizer()
    try:
        matrix = vectorizer.fit_transform([_text(i) for i in items])
    except ValueError:
        return []
    sim = (matrix @ matrix.T).toarray()
    order = sorted(range(len(items)), key=lambda i: _parse_time(items[i].get("time")) or 0)

    # Running per-cluster sums of similarity rows make "mean similarity to every
    # member of every cluster" one vector division per headline instead of a
    # Python loop over clusters.
    clusters: list[list[int]] = []
    sums = np.zeros((len(items), len(items)))
    sizes = np.zeros(len(items))
    for i in order:
        k = len(clusters)
        if k:
            scores = sums[:k, i] / sizes[:k]
            best = int(np.argmax(scores))
            if scores[best] >= threshold:
                clusters[best].append(i)
                sums[best] += sim[i]
                sizes[best] += 1
                continue
        clusters.append([i])
        sums[k] = sim[i]
        sizes[k] = 1

    names = np.array(vectorizer.get_feature_names_out())
    out = []
    for members in clusters:
        if len(members) < min_size:
            continue
        sources = {items[i].get("source") for i in members}
        centrality = [float(np.mean(sim[i, members])) for i in members]
        rep = members[int(np.argmax(centrality))]
        weights = np.asarray(matrix[members].sum(axis=0)).ravel()
        terms = [t for t in names[np.argsort(-weights)[:8]] if " " not in t][:5]
        stamps = [s for s in (_parse_time(items[i].get("time")) for i in members) if s]
        tones = [tone(_text(items[i])) for i in members]
        mean_tone = sum(tones) / len(tones)
        first = min(stamps) if stamps else None
        last = max(stamps) if stamps else None
        out.append({
            "title": items[rep].get("title"),
            "url": items[rep].get("url"),
            "size": len(members),
            "sources": sorted(s for s in sources if s),
            "source_count": len(sources),
            "first_seen": datetime.fromtimestamp(first, timezone.utc).isoformat(timespec="seconds") if first else None,
            "last_seen": datetime.fromtimestamp(last, timezone.utc).isoformat(timespec="seconds") if last else None,
            "terms": terms,
            "tone": round(mean_tone, 3),
            "tone_label": tone_label(mean_tone),
            "headlines": [{k: items[i].get(k) for k in ("title", "url", "source", "time")}
                          for i in sorted(members, key=lambda i: -(_parse_time(items[i].get("time")) or 0))[:8]],
        })
    out.sort(key=lambda s: (-s["source_count"], -s["size"], s["last_seen"] or ""), reverse=False)
    return out


def digest(items: list[dict], hours: float = 24, limit: int = 15) -> dict:
    """Stories of the last `hours`, each with a one-paragraph extractive summary."""
    now = datetime.now(timezone.utc).timestamp()
    recent = [i for i in items
              if (_parse_time(i.get("time")) or 0) >= now - hours * 3600
              and (_parse_time(i.get("time")) or 0) <= now + 3600]
    found = stories(recent)[:limit]
    for story in found:
        span = ""
        if story["first_seen"] and story["last_seen"]:
            hours_open = (_parse_time(story["last_seen"]) - _parse_time(story["first_seen"])) / 3600
            span = f" dalam {hours_open:.0f} jam" if hours_open >= 1 else " dalam kurang dari sejam"
        story["summary"] = (
            f"{story['title']} — {story['size']} judul dari {story['source_count']} sumber{span}. "
            f"Kata kunci: {', '.join(story['terms']) or '—'}. Nada kabar: {story['tone_label']}."
        )
    return {
        "hours": hours,
        "headlines_considered": len(recent),
        "stories": found,
        "note": ("Ringkasan ekstraktif: setiap kalimat disusun dari judul yang benar-benar terbit, "
                 "tanpa kalimat karangan. Cerita diurutkan menurut jumlah sumber berbeda yang "
                 "memuatnya — satu peristiwa yang diliput banyak redaksi lebih mungkin nyata "
                 "daripada satu judul yang diulang."),
    }


def rising_terms(items: list[dict], hours: float = 24, baseline_hours: float = 72,
                 limit: int = 20) -> dict:
    """Terms whose share of headlines grew most in the last `hours`."""
    now = datetime.now(timezone.utc).timestamp()
    recent, previous = [], []
    for item in items:
        stamp = _parse_time(item.get("time"))
        if not stamp or stamp > now + 3600:
            continue
        if stamp >= now - hours * 3600:
            recent.append(item)
        elif stamp >= now - (hours + baseline_hours) * 3600:
            previous.append(item)

    def doc_terms(item: dict) -> set[str]:
        words = content_tokens(item.get("title"))
        grams = {f"{a} {b}" for a, b in zip(words, words[1:])}
        return set(words) | grams

    recent_df: Counter = Counter()
    sources: dict[str, set] = defaultdict(set)
    examples: dict[str, list] = defaultdict(list)
    for item in recent:
        for term in doc_terms(item):
            recent_df[term] += 1
            sources[term].add(item.get("source"))
            if len(examples[term]) < 3:
                examples[term].append({k: item.get(k) for k in ("title", "url", "source", "time")})
    previous_df: Counter = Counter()
    for item in previous:
        previous_df.update(doc_terms(item))

    n_recent, n_prev = max(1, len(recent)), max(1, len(previous))
    rows = []
    for term, count in recent_df.items():
        if count < 3 or len(sources[term]) < 2:
            continue
        share_now = count / n_recent
        share_before = (previous_df[term] + 0.5) / (n_prev + 1)
        growth = share_now / share_before
        rows.append({
            "term": term, "headlines": count, "sources": len(sources[term]),
            "previous": previous_df[term], "share_pct": round(share_now * 100, 2),
            "growth": round(growth, 2),
            "score": round(math.log(growth) * math.sqrt(count), 3),
            "examples": examples[term],
        })
    rows.sort(key=lambda r: -r["score"])
    # A bigram and its own words usually rise together; keep the more specific one.
    kept: list[dict] = []
    for row in rows:
        if any(row["term"] in other["term"].split() for other in kept if " " in other["term"]):
            continue
        kept.append(row)
        if len(kept) >= limit:
            break
    return {
        "hours": hours, "baseline_hours": baseline_hours,
        "recent_headlines": len(recent), "baseline_headlines": len(previous),
        "terms": kept,
        "note": ("Pertumbuhan = porsi judul yang memuat kata itu sekarang dibanding "
                 f"{baseline_hours:.0f} jam sebelumnya. Minimal 3 judul dari 2 sumber berbeda. "
                 "Ini percepatan liputan, bukan volume pencarian publik."),
    }


def dedupe(items: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def market_news(question: str, corpus_items: list[dict] | None = None,
                search: bool = True, days: float = 7, evidence_limit: int = 10) -> dict:
    """Search + corpus → relevance-ranked evidence and features for one market."""
    from src import news
    from src.news.sources import BY_CODE

    query = keywords(question)
    fetched: list[dict] = []
    failed = None
    if search and query:
        try:
            fetched, _ = news.fetch_feed(BY_CODE["google"], query)
        except Exception as exc:  # noqa: BLE001 — local corpus still answers
            failed = getattr(exc, "message", None) or str(exc)[:140]

    now = datetime.now(timezone.utc).timestamp()
    pool = corpus_items if corpus_items is not None else news.corpus()
    wanted = set(content_tokens(question))
    local = [i for i in pool
             if (_parse_time(i.get("time")) or 0) >= now - days * 86400
             and wanted & set(content_tokens(i.get("title")))]
    items = dedupe(fetched + local)
    feats = market_features(question, items, now, evidence_limit)
    return {
        "query": query,
        "searched": bool(fetched),
        "search_failed": failed,
        "candidates": len(items),
        **feats,
        "note": ("Relevansi = kemiripan kata (TF-IDF), bukan pemahaman makna. Nada = hitungan kata "
                 "positif/negatif, bukan arah YES/NO — pertanyaan berbentuk negatif membalik artinya."),
    }
