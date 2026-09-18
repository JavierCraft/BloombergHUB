"""Kabar terkini dari banyak sumber sekaligus.

Daftar sumbernya ada di `src/news/sources.py` — 157 feed yang lolos probe dari
koneksi ini pada 17 September 2026, dikelompokkan per kategori, plus Hacker News
lewat Algolia. Yang tidak lolos dicatat di sana beserta alasannya.

Tiga hal yang berubah dibanding versi tujuh-sumber:

  * **Setiap feed punya simpanan sendiri.** Panel berita menanyakan kabar tiap
    15 detik; kalau setiap tanya berarti puluhan permintaan HTTP, sumbernya akan
    membatasi kita. Sekarang feed hanya diambil ulang setelah TTL-nya lewat
    (3 menit untuk Google News, 30–60 menit untuk rilis resmi dan blog).
  * **Ada tenggat.** Permintaan menunggu paling lama `budget_s` detik. Feed yang
    belum menjawab tidak menahan yang lain — ia tetap selesai di belakang,
    mengisi simpanan, dan ikut tampil pada pembaruan berikutnya. Namanya
    dilaporkan sebagai "belum menjawab", bukan disembunyikan.
  * **Halaman sektor meminta kategori, bukan kata.** Dulu panel Politics
    menyaring judul yang memuat kata "politics" — hampir tidak ada judul yang
    begitu. Sekarang ia mengambil feed berkategori politik.

Setiap feed yang benar-benar diambil dari jaringan juga ditulis ke korpus
`data/news/corpus-<kategori>.jsonl`. Korpus itu bahan belajar untuk
`src/ml/newsml.py`: analisis topik, kata yang sedang naik, dan fitur berita per
pasar di paper test.

Pengurai RSS/Atom memakai pustaka bawaan Python — tidak ada dependensi baru.
"""
from __future__ import annotations

import concurrent.futures
import html as html_mod
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from src.core import cache, textstore
from src.news import sources as catalog_mod
from src.news.sources import BY_CODE, FEEDS_CATALOG, HACKER_NEWS, Feed

USER_AGENT = "Mozilla/5.0 (compatible; BloombergHub/1.0; riset pasar prediksi)"
TIMEOUT = 15
MAX_WORKERS = 8           # measured on this machine: more parallel HTTPS makes things worse
DEFAULT_BUDGET_S = 8.0

HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_FRONT = "https://hn.algolia.com/api/v1/search"

NS_ATOM = "{http://www.w3.org/2005/Atom}"
NS_RSS1 = "{http://purl.org/rss/1.0/}"
NS_DC = "{http://purl.org/dc/elements/1.1/}"

# Backwards-compatible view of the catalog in the old dict shape.
FEEDS: dict[str, dict[str, Any]] = {
    f.code: {"name": f.name, "url": f.search_url, "home": f.url,
             "searchable": f.searchable, "category": f.category}
    for f in FEEDS_CATALOG
}


def _clean(text: str | None) -> str:
    """Buang tag HTML dan rapikan spasi — judul RSS sering menyisipkan markup."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _when(raw: str | None) -> str | None:
    """RFC 822 (RSS) atau ISO 8601 (Atom, Dublin Core) → ISO UTC."""
    if not raw:
        return None
    raw = raw.strip()
    stamp = None
    try:
        stamp = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def _fetch(url: str) -> bytes:
    import requests

    from src.core.errors import NetworkBlocked, RateLimited, UpstreamError

    host = url.split("/")[2] if "//" in url else url
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.exceptions.Timeout:
        raise NetworkBlocked(host, "tidak menjawab tepat waktu") from None
    except requests.exceptions.SSLError:
        # Halaman pemblokir ISP menyodorkan sertifikat yang tidak cocok.
        raise NetworkBlocked(host, "sertifikat tidak cocok — ciri halaman pemblokir") from None
    except requests.exceptions.ConnectionError:
        raise NetworkBlocked(host, "tidak bisa terhubung") from None

    if resp.status_code == 429:
        raise RateLimited(host, 60)
    if resp.status_code >= 400:
        raise UpstreamError(host, "", resp.status_code)
    return resp.content


def _atom_link(entry: ET.Element) -> str:
    fallback = ""
    for link in entry.findall(f"{NS_ATOM}link"):
        href = link.get("href", "")
        if link.get("rel", "alternate") == "alternate" and href:
            return href
        fallback = fallback or href
    return fallback


def _parse_rss(raw: bytes, source: str, category: str) -> list[dict]:
    """RSS 2.0, RSS 1.0 (RDF) dan Atom jadi baris seragam. Bentuk rusak
    menghasilkan lebih sedikit baris, bukan baris yang salah."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    rows: list[dict] = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        origin = item.find("source")
        rows.append({
            "title": title,
            "url": link,
            "source": _clean(origin.text) if origin is not None and origin.text else source,
            "category": category,
            "time": _when(item.findtext("pubDate") or item.findtext(f"{NS_DC}date")),
            "summary": _clean(item.findtext("description"))[:280],
        })

    if not rows:  # RSS 1.0 / RDF
        for item in root.iter(f"{NS_RSS1}item"):
            title = _clean(item.findtext(f"{NS_RSS1}title"))
            link = (item.findtext(f"{NS_RSS1}link") or "").strip()
            if not title or not link:
                continue
            rows.append({
                "title": title, "url": link, "source": source, "category": category,
                "time": _when(item.findtext(f"{NS_DC}date")),
                "summary": _clean(item.findtext(f"{NS_RSS1}description"))[:280],
            })

    if not rows:  # Atom
        for item in root.iter(f"{NS_ATOM}entry"):
            title = _clean(item.findtext(f"{NS_ATOM}title"))
            link = _atom_link(item)
            if not title or not link:
                continue
            rows.append({
                "title": title, "url": link, "source": source, "category": category,
                "time": _when(item.findtext(f"{NS_ATOM}published") or item.findtext(f"{NS_ATOM}updated")),
                "summary": _clean(item.findtext(f"{NS_ATOM}summary")
                                  or item.findtext(f"{NS_ATOM}content"))[:280],
            })
    return rows


def _decorate(rows: list[dict], feed: Feed) -> list[dict]:
    for row in rows:
        row.setdefault("feed", feed.code)
        row["feed_name"] = feed.name
        row["lang"] = feed.lang
        row["kind"] = feed.kind
    return rows


def fetch_feed(feed: Feed, query: str | None = None) -> tuple[list[dict], bool]:
    """One feed, through its own TTL cache. Returns `(rows, came_from_network)`."""
    if query and feed.searchable:
        url = feed.search_url.format(q=urllib.parse.quote(query))
        ttl = min(feed.ttl, 180)
    else:
        url = feed.url
        ttl = feed.ttl

    def pull() -> list[dict]:
        rows = _parse_rss(_fetch(url), feed.name, feed.category)
        return _decorate(rows[: feed.max_items], feed)

    rows, cached, _age = cache.cached_call("feed", url, ttl, pull)
    return rows, not cached


def _hacker_news(query: str | None, limit: int) -> list[dict]:
    import requests

    params: dict[str, Any] = {"hitsPerPage": min(50, limit), "tags": "story"}
    url = HN_FRONT
    if query:
        params["query"] = query
        url = HN_SEARCH

    resp = requests.get(url, params=params, timeout=TIMEOUT,
                        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    rows = []
    for hit in resp.json().get("hits", []):
        title = _clean(hit.get("title") or hit.get("story_title"))
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title:
            continue
        rows.append({
            "title": title,
            "url": link,
            "source": "Hacker News",
            "category": "Tech",
            "feed": "hn", "feed_name": "Hacker News", "lang": "en", "kind": "komunitas",
            "time": hit.get("created_at"),
            "summary": f"{hit.get('points') or 0} points · {hit.get('num_comments') or 0} comments",
        })
    return rows


def _newest_first(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: r.get("time") or "", reverse=True)


def _dedupe(rows: list[dict]) -> list[dict]:
    """Satu peristiwa sering muncul di beberapa sumber. Yang dibandingkan adalah
    judul yang sudah disederhanakan, bukan URL-nya, karena URL-nya selalu beda."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = re.sub(r"[^a-z0-9 ]", "", row["title"].lower())
        key = " ".join(key.split()[:8])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _not_future(rows: list[dict], slack_hours: float = 2.0) -> list[dict]:
    """A few feeds stamp items hours into the future (time-zone bugs upstream).
    They would pin themselves to the top of every newest-first list."""
    limit = datetime.now(timezone.utc).timestamp() + slack_hours * 3600
    out = []
    for row in rows:
        try:
            if row.get("time") and datetime.fromisoformat(row["time"].replace("Z", "+00:00")).timestamp() > limit:
                row = {**row, "time": None}
        except ValueError:
            row = {**row, "time": None}
        out.append(row)
    return out


def select_feeds(sources: list[str] | None = None, categories: list[str] | None = None,
                 sector: str | None = None) -> list[Feed]:
    if sources:
        return [BY_CODE[c] for c in sources if c in BY_CODE]
    if sector:
        return catalog_mod.for_sector(sector) or catalog_mod.by_categories(None)
    return catalog_mod.by_categories(categories)


def _topic_name(query: str | None, sector: str | None, categories: list[str] | None) -> str:
    if query:
        return f"news-{re.sub(r'[^a-z0-9]+', '-', query.lower())[:30]}"
    if sector:
        return f"sector-{re.sub(r'[^a-z0-9]+', '-', sector.lower())[:30]}"
    if categories:
        return f"category-{re.sub(r'[^a-z0-9]+', '-', '-'.join(categories).lower())[:30]}"
    return "top-news"


def headlines(query: str | None = None, limit: int = 60,
              sources: list[str] | None = None, categories: list[str] | None = None,
              sector: str | None = None, budget_s: float = DEFAULT_BUDGET_S) -> dict:
    """Ambil kabar dari feed yang dipilih sekaligus.

    Kalau `query` diisi, feed yang bisa dicari akan mencarinya; feed lain tetap
    diambil berandanya lalu disaring di sini.
    """
    feeds = select_feeds(sources, categories, sector)
    wanted_categories = {f.category for f in feeds}
    if query and not any(f.searchable for f in feeds):
        # A keyword search always gets at least one engine that can search.
        feeds = [BY_CODE["google"], *feeds]
        if "Indonesia" in wanted_categories:
            feeds.insert(1, BY_CODE["google-id"])

    rows: list[dict] = []
    failed: list[dict] = []
    refreshed: list[dict] = []
    pending: list[str] = []

    def run(feed: Feed):
        batch, fresh = fetch_feed(feed, query)
        return feed, batch, fresh

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futures = {pool.submit(run, feed): feed for feed in feeds}
    include_hn = not sources and (not wanted_categories or wanted_categories & {"Tech", "AI"}
                                  or (not sector and not categories))
    hn_future = pool.submit(_hacker_news, query, 30) if include_hn else None
    done, not_done = concurrent.futures.wait(
        list(futures) + ([hn_future] if hn_future else []), timeout=budget_s)
    # Slow feeds keep running in the background and land in the cache for the
    # next tick. Waiting for them here is what used to freeze the panel.
    pool.shutdown(wait=False, cancel_futures=False)

    for future, feed in futures.items():
        if future not in done:
            pending.append(feed.name)
            continue
        try:
            _, batch, fresh = future.result()
        except Exception as exc:  # noqa: BLE001 — one feed never takes the rest down
            failed.append({"source": feed.name,
                           "reason": (getattr(exc, "message", None) or str(exc))[:140]})
            continue
        rows.extend(batch)
        if fresh and not query:
            refreshed.extend(batch)

    if hn_future is not None:
        if hn_future in done:
            try:
                rows.extend(hn_future.result())
            except Exception as exc:  # noqa: BLE001
                failed.append({"source": "Hacker News", "reason": str(exc)[:140]})
        else:
            pending.append("Hacker News")

    if query:
        words = [w for w in query.lower().split() if len(w) > 2]
        if words:
            rows = [
                r for r in rows
                if r.get("feed") in {"google", "google-id", "hn"}
                or any(w in (r["title"] + " " + r.get("summary", "")).lower() for w in words)
            ]

    rows = _dedupe(_newest_first(_not_future(rows)))[:limit]

    topic = _topic_name(query, sector, categories)
    stored = 0
    try:
        stored = textstore.append_records(topic, rows)
        textstore.write_snapshot(topic, rows, ["title", "source", "time", "url"])
        store_corpus(refreshed)
    except Exception:
        pass  # simpanan gagal tidak boleh menggagalkan tampilan

    return {
        "query": query,
        "sector": sector,
        "categories": sorted(wanted_categories),
        "feeds_asked": len(feeds) + (1 if include_hn else 0),
        "count": len(rows),
        "stored_new": stored,
        "file": topic,
        "failed_sources": failed,
        "pending_sources": pending,
        "fetched_at": textstore.now_iso(),
        "news": rows,
    }


def store_corpus(rows: list[dict]) -> int:
    """Append freshly fetched rows to the per-category learning corpus."""
    by_category: dict[str, list[dict]] = {}
    for row in rows:
        by_category.setdefault(row.get("category") or "General", []).append(row)
    added = 0
    for category, batch in by_category.items():
        added += textstore.append_records(f"corpus-{category.lower()}", batch)
    return added


def corpus(categories: list[str] | None = None, limit_per_category: int = 4000) -> list[dict]:
    """Everything stored for learning, newest first. Local disk only."""
    names = [c.lower() for c in (categories or catalog_mod.CATEGORIES)] + ["tech", "general"]
    rows: list[dict] = []
    seen: set[str] = set()
    for name in dict.fromkeys(names):
        for row in textstore.read_records(f"corpus-{name}", limit_per_category):
            key = row.get("url") or row.get("title")
            if key and key not in seen:
                seen.add(key)
                rows.append(row)
    return _newest_first(rows)


def stored(topic: str = "top-news", limit: int = 200) -> list[dict]:
    return textstore.read_records(topic, limit)


def search_stored(term: str, limit: int = 60) -> list[dict]:
    return textstore.search_records(term, limit)


def source_list() -> list[dict]:
    rows = [
        {"code": f.code, "name": f.name, "category": f.category, "searchable": f.searchable,
         "lang": f.lang, "kind": f.kind, "core": f.core}
        for f in FEEDS_CATALOG
    ]
    rows.append({"code": HACKER_NEWS["code"], "name": HACKER_NEWS["name"],
                 "category": HACKER_NEWS["category"], "searchable": True,
                 "lang": "en", "kind": "komunitas", "core": True})
    return rows
