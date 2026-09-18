"""One function per API: raw JSON payloads → readable rows.

Each parser receives the list of payloads for its source's URLs, in order, and
returns `list[dict]` with short English column names (UI convention) and values
ready to display. A malformed payload yields fewer rows, never a crash — the
caller reports an empty result honestly.
"""
from __future__ import annotations

import inspect
import math
from datetime import datetime, timezone
from typing import Any, Callable


def _f(value: Any, digits: int | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return round(out, digits) if digits is not None else out


def _ms_iso(ms: Any) -> str | None:
    value = _f(ms)
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="minutes") if value else None


ASSETS = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP", "dogecoin": "DOGE",
          "binancecoin": "BNB"}


def coingecko_prices(payloads: list) -> list[dict]:
    data = payloads[0] or {}
    rows = []
    for key, symbol in ASSETS.items():
        item = data.get(key) or {}
        if not item:
            continue
        rows.append({"asset": symbol, "usd": _f(item.get("usd"), 4), "idr": _f(item.get("idr"), 0),
                     "change_24h_pct": _f(item.get("usd_24h_change"), 2),
                     "market_cap_usd": _f(item.get("usd_market_cap"), 0)})
    return rows


def coingecko_trending(payloads: list) -> list[dict]:
    rows = []
    for i, coin in enumerate((payloads[0] or {}).get("coins") or [], 1):
        item = coin.get("item") or {}
        change = ((item.get("data") or {}).get("price_change_percentage_24h") or {}).get("usd")
        rows.append({"rank": i, "coin": item.get("name"), "symbol": item.get("symbol"),
                     "market_cap_rank": item.get("market_cap_rank"),
                     "price_usd": _f((item.get("data") or {}).get("price"), 6),
                     "change_24h_pct": _f(change, 2),
                     "url": f"https://www.coingecko.com/en/coins/{item.get('slug') or item.get('id')}"})
    return rows


def fear_greed(payloads: list) -> list[dict]:
    rows = []
    for item in (payloads[0] or {}).get("data") or []:
        stamp = _f(item.get("timestamp"))
        rows.append({"date": datetime.fromtimestamp(stamp, timezone.utc).date().isoformat() if stamp else None,
                     "value": int(_f(item.get("value")) or 0), "reading": item.get("value_classification")})
    return rows


def defillama_chains(payloads: list) -> list[dict]:
    chains = [c for c in (payloads[0] or []) if isinstance(c, dict) and _f(c.get("tvl"))]
    chains.sort(key=lambda c: -_f(c["tvl"]))
    return [{"rank": i, "chain": c.get("name"), "tvl_usd": _f(c.get("tvl"), 0), "token": c.get("tokenSymbol")}
            for i, c in enumerate(chains[:20], 1)]


def frankfurter(payloads: list) -> list[dict]:
    data = payloads[0] or {}
    return [{"pair": f"USD/{code}", "rate": _f(rate, 4), "date": data.get("date"), "source": "ECB"}
            for code, rate in sorted((data.get("rates") or {}).items())]


def open_er_api(payloads: list) -> list[dict]:
    data = payloads[0] or {}
    wanted = ("IDR", "EUR", "JPY", "GBP", "CNY", "SGD", "AUD", "MYR", "KRW", "INR")
    rates = data.get("rates") or {}
    return [{"pair": f"USD/{code}", "rate": _f(rates.get(code), 4), "updated": data.get("time_last_update_utc")}
            for code in wanted if code in rates]


METALS = {"XAU": "Emas (troy oz)", "XAG": "Perak (troy oz)", "HG": "Tembaga (lb)"}


def gold_api(payloads: list) -> list[dict]:
    rows = []
    for data in payloads:
        if isinstance(data, dict) and _f(data.get("price")) is not None:
            rows.append({"metal": METALS.get(data.get("symbol"), data.get("name")), "symbol": data.get("symbol"),
                         "price_usd": _f(data.get("price"), 2), "updated": data.get("updatedAt")})
    return rows


def treasury_debt(payloads: list) -> list[dict]:
    rows = []
    for item in (payloads[0] or {}).get("data") or []:
        total = _f(item.get("tot_pub_debt_out_amt"))
        rows.append({"date": item.get("record_date"),
                     "total_debt_trillion_usd": round(total / 1e12, 4) if total else None,
                     "held_by_public_trillion": round((_f(item.get("debt_held_public_amt")) or 0) / 1e12, 4),
                     "intragovernmental_trillion": round((_f(item.get("intragov_hold_amt")) or 0) / 1e12, 4)})
    return rows


def treasury_rates(payloads: list) -> list[dict]:
    items = (payloads[0] or {}).get("data") or []
    latest = items[0].get("record_date") if items else None
    return [{"date": item.get("record_date"), "type": item.get("security_type_desc"),
             "security": item.get("security_desc"), "avg_rate_pct": _f(item.get("avg_interest_rate_amt"), 3)}
            for item in items if item.get("record_date") == latest]


def bls_cpi(payloads: list) -> list[dict]:
    series = (((payloads[0] or {}).get("Results") or {}).get("series") or [{}])[0].get("data") or []
    by_key = {(row.get("year"), row.get("period")): _f(row.get("value")) for row in series}
    rows = []
    for row in series[:13]:
        value = _f(row.get("value"))
        prior = by_key.get((str(int(row.get("year", 0)) - 1), row.get("period")))
        rows.append({"period": f"{row.get('periodName')} {row.get('year')}", "cpi_u": value,
                     "yoy_pct": round((value / prior - 1) * 100, 2) if value and prior else None,
                     "preliminary": "ya" if any(f.get("code") == "P" for f in row.get("footnotes") or [] if f) else ""})
    return rows


WB_NAMES = {"NY.GDP.MKTP.CD": "PDB (USD)", "FP.CPI.TOTL.ZG": "Inflasi (% per tahun)",
            "SL.UEM.TOTL.ZS": "Pengangguran (% angkatan kerja)"}


def worldbank(payloads: list) -> list[dict]:
    rows = []
    for data in payloads:
        if not isinstance(data, list) or len(data) < 2:
            continue
        for item in data[1] or []:
            code = (item.get("indicator") or {}).get("id")
            value = _f(item.get("value"))
            if value is None:
                continue
            rows.append({"indicator": WB_NAMES.get(code, (item.get("indicator") or {}).get("value")),
                         "year": item.get("date"),
                         "value": round(value / 1e9, 2) if code == "NY.GDP.MKTP.CD" else round(value, 2),
                         "unit": "miliar USD" if code == "NY.GDP.MKTP.CD" else "%"})
    return rows


def federal_register(payloads: list) -> list[dict]:
    return [{"published": item.get("publication_date"), "type": item.get("subtype") or item.get("type"),
             "title": item.get("title"), "document": item.get("document_number"), "url": item.get("html_url")}
            for item in (payloads[0] or {}).get("results") or []]


def wikipedia_mostread(payloads: list) -> list[dict]:
    articles = ((payloads[0] or {}).get("mostread") or {}).get("articles") or []
    rows = []
    for i, item in enumerate(articles[:30], 1):
        if (item.get("titles") or {}).get("normalized", "").startswith(("Special:", "Main Page")):
            continue
        rows.append({"rank": i, "article": (item.get("titles") or {}).get("normalized") or item.get("title"),
                     "views": item.get("views"), "about": (item.get("description") or "")[:120],
                     "url": ((item.get("content_urls") or {}).get("desktop") or {}).get("page")})
    return rows


def mastodon_links(payloads: list) -> list[dict]:
    rows = []
    for item in payloads[0] or []:
        history = (item.get("history") or [{}])[0]
        rows.append({"title": item.get("title"), "provider": item.get("provider_name"),
                     "shares_today": int(_f(history.get("uses")) or 0),
                     "accounts_today": int(_f(history.get("accounts")) or 0), "url": item.get("url")})
    return rows


def mastodon_tags(payloads: list) -> list[dict]:
    rows = []
    for item in payloads[0] or []:
        history = (item.get("history") or [{}])[0]
        rows.append({"tag": f"#{item.get('name')}", "uses_today": int(_f(history.get("uses")) or 0),
                     "accounts_today": int(_f(history.get("accounts")) or 0), "url": item.get("url")})
    return rows


def openrouter_models(payloads: list) -> list[dict]:
    models = [m for m in (payloads[0] or {}).get("data") or [] if isinstance(m, dict)]
    models.sort(key=lambda m: -(_f(m.get("created")) or 0))
    rows = []
    for m in models[:40]:
        pricing = m.get("pricing") or {}
        prompt, completion = _f(pricing.get("prompt")), _f(pricing.get("completion"))
        created = _f(m.get("created"))
        rows.append({"released": datetime.fromtimestamp(created, timezone.utc).date().isoformat() if created else None,
                     "model": m.get("name"), "id": m.get("id"), "context_tokens": m.get("context_length"),
                     "input_usd_per_mtok": round(prompt * 1e6, 3) if prompt is not None else None,
                     "output_usd_per_mtok": round(completion * 1e6, 3) if completion is not None else None,
                     "url": f"https://openrouter.ai/{m.get('id')}"})
    return rows


def hf_papers(payloads: list) -> list[dict]:
    rows = []
    for item in payloads[0] or []:
        paper = item.get("paper") or {}
        rows.append({"published": (item.get("publishedAt") or "")[:10], "title": paper.get("title") or item.get("title"),
                     "upvotes": paper.get("upvotes"), "url": f"https://huggingface.co/papers/{paper.get('id')}"})
    rows.sort(key=lambda r: -(r["upvotes"] or 0))
    return rows


def github_releases(payloads: list) -> list[dict]:
    rows = []
    for data in payloads:
        for item in (data or [])[:1] if isinstance(data, list) else []:
            repo = "/".join((item.get("html_url") or "").split("/")[3:5])
            rows.append({"repo": repo, "tag": item.get("tag_name"), "published": (item.get("published_at") or "")[:10],
                         "name": item.get("name"), "url": item.get("html_url")})
    return rows


def pypi_versions(payloads: list) -> list[dict]:
    rows = []
    for data in payloads:
        info = (data or {}).get("info") or {} if isinstance(data, dict) else {}
        version = info.get("version")
        if not info.get("name") or not version:
            continue        # a package PyPI did not answer for is left out, not shown as a blank row
        files = ((data or {}).get("releases") or {}).get(version) or []
        rows.append({"package": info.get("name"), "version": version,
                     "released": (files[0].get("upload_time_iso_8601") or "")[:10] if files else None,
                     "url": info.get("package_url")})
    return rows


def lobsters(payloads: list) -> list[dict]:
    return [{"title": item.get("title"), "score": item.get("score"), "comments": item.get("comment_count"),
             "tags": ", ".join(item.get("tags") or []), "url": item.get("url") or item.get("short_id_url")}
            for item in (payloads[0] or [])[:25]]


def devto(payloads: list) -> list[dict]:
    return [{"published": (item.get("published_at") or "")[:10], "title": item.get("title"),
             "reactions": item.get("public_reactions_count"), "comments": item.get("comments_count"),
             "tags": item.get("tag_list") if isinstance(item.get("tag_list"), str) else ", ".join(item.get("tag_list") or []),
             "url": item.get("url")} for item in payloads[0] or []]


def apple_chart(payloads: list) -> list[dict]:
    results = ((payloads[0] or {}).get("feed") or {}).get("results") or []
    return [{"rank": i, "title": item.get("name"), "artist": item.get("artistName"),
             "released": item.get("releaseDate"), "url": item.get("url")}
            for i, item in enumerate(results, 1)]


def _itunes_link(link) -> str | None:
    links = link if isinstance(link, list) else [link] if isinstance(link, dict) else []
    hrefs = [(l.get("attributes") or {}) for l in links if isinstance(l, dict)]
    page = next((a.get("href") for a in hrefs if a.get("rel") == "alternate" and a.get("type") == "text/html"), None)
    return page or next((a.get("href") for a in hrefs if a.get("href")), None)


def itunes_movies(payloads: list) -> list[dict]:
    entries = ((payloads[0] or {}).get("feed") or {}).get("entry") or []
    rows = []
    for i, item in enumerate(entries, 1):
        rows.append({"rank": i, "title": ((item.get("im:name") or {}).get("label")),
                     "genre": (((item.get("category") or {}).get("attributes") or {}).get("label")),
                     "released": (((item.get("im:releaseDate") or {}).get("attributes") or {}).get("label")),
                     "url": _itunes_link(item.get("link"))})
    return rows


def thesportsdb(payloads: list) -> list[dict]:
    return [{"date": item.get("dateEvent"), "time_utc": item.get("strTime"), "match": item.get("strEvent"),
             "round": item.get("intRound"), "venue": item.get("strVenue")}
            for item in (payloads[0] or {}).get("events") or []]


def openligadb(payloads: list) -> list[dict]:
    rows = []
    for match in payloads[0] or []:
        results = match.get("matchResults") or []
        final = next((r for r in results if r.get("resultTypeID") == 2), results[-1] if results else None)
        rows.append({"kickoff_utc": match.get("matchDateTimeUTC"),
                     "home": (match.get("team1") or {}).get("teamName"), "away": (match.get("team2") or {}).get("teamName"),
                     "score": f"{final.get('pointsTeam1')}–{final.get('pointsTeam2')}" if final else None,
                     "finished": "ya" if match.get("matchIsFinished") else "",
                     "matchday": (match.get("group") or {}).get("groupName")})
    return rows


def jolpica_f1(payloads: list) -> list[dict]:
    races = (((payloads[0] or {}).get("MRData") or {}).get("RaceTable") or {}).get("Races") or []
    rows = []
    for race in races:
        base = {"season": race.get("season"), "round": race.get("round"), "race": race.get("raceName"),
                "circuit": (race.get("Circuit") or {}).get("circuitName")}
        for session in ("FirstPractice", "SecondPractice", "ThirdPractice", "SprintQualifying", "Sprint", "Qualifying"):
            if race.get(session):
                rows.append({**base, "session": session, "date": race[session].get("date"),
                             "time_utc": race[session].get("time")})
        rows.append({**base, "session": "Race", "date": race.get("date"), "time_utc": race.get("time")})
    return rows


def nhl_schedule(payloads: list) -> list[dict]:
    rows = []
    for day in (payloads[0] or {}).get("gameWeek") or []:
        for game in day.get("games") or []:
            rows.append({"date": day.get("date"), "start_utc": game.get("startTimeUTC"),
                         "away": ((game.get("awayTeam") or {}).get("commonName") or {}).get("default")
                         or (game.get("awayTeam") or {}).get("abbrev"),
                         "home": ((game.get("homeTeam") or {}).get("commonName") or {}).get("default")
                         or (game.get("homeTeam") or {}).get("abbrev"),
                         "type": {1: "preseason", 2: "regular", 3: "playoff"}.get(game.get("gameType"), game.get("gameType")),
                         "state": game.get("gameState")})
    return rows


def mlb_schedule(payloads: list) -> list[dict]:
    rows = []
    for day in (payloads[0] or {}).get("dates") or []:
        for game in day.get("games") or []:
            teams = game.get("teams") or {}
            away, home = teams.get("away") or {}, teams.get("home") or {}
            rows.append({"start_utc": game.get("gameDate"), "away": (away.get("team") or {}).get("name"),
                         "home": (home.get("team") or {}).get("name"),
                         "score": f"{away.get('score')}–{home.get('score')}" if away.get("score") is not None else None,
                         "status": (game.get("status") or {}).get("detailedState")})
    return rows


STEAM_GAMES = {730: "Counter-Strike 2", 570: "Dota 2", 578080: "PUBG", 1172470: "Apex Legends", 252490: "Rust"}


def steam_players(payloads: list) -> list[dict]:
    rows = []
    for appid, data in zip(STEAM_GAMES, payloads):
        count = ((data or {}).get("response") or {}).get("player_count")
        if count is not None:
            rows.append({"game": STEAM_GAMES[appid], "players_now": int(count), "appid": appid,
                         "url": f"https://steamcharts.com/app/{appid}"})
    rows.sort(key=lambda r: -r["players_now"])
    return rows


def usgs_quakes(payloads: list) -> list[dict]:
    rows = []
    for feature in (payloads[0] or {}).get("features") or []:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
        rows.append({"time_utc": _ms_iso(props.get("time")), "magnitude": _f(props.get("mag"), 1),
                     "place": props.get("place"), "depth_km": _f(coords[2], 1) if len(coords) > 2 else None,
                     "tsunami": "ya" if props.get("tsunami") else "", "alert": props.get("alert"), "url": props.get("url")})
    rows.sort(key=lambda r: r["time_utc"] or "", reverse=True)
    return rows


def bmkg_gempa(payloads: list) -> list[dict]:
    rows = []
    seen = set()
    for data in payloads:
        info = (data or {}).get("Infogempa") or {}
        items = info.get("gempa")
        items = items if isinstance(items, list) else [items] if items else []
        for g in items:
            key = (g.get("DateTime"), g.get("Magnitude"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"time_wib": f"{g.get('Tanggal')} {g.get('Jam')}", "magnitude": _f(g.get("Magnitude"), 1),
                         "depth": g.get("Kedalaman"), "region": g.get("Wilayah"),
                         "tsunami_potential": g.get("Potensi"), "felt": g.get("Dirasakan")})
    return rows


def gdacs(payloads: list) -> list[dict]:
    rows = []
    for feature in (payloads[0] or {}).get("features") or []:
        props = feature.get("properties") or {}
        rows.append({"type": props.get("eventtype"), "name": props.get("name") or props.get("eventname"),
                     "country": props.get("country"), "alert": props.get("alertlevel"),
                     "from": (props.get("fromdate") or "")[:16], "to": (props.get("todate") or "")[:16],
                     "url": ((props.get("url") or {}).get("report") if isinstance(props.get("url"), dict) else None)})
    return rows


def eonet(payloads: list) -> list[dict]:
    rows = []
    for event in (payloads[0] or {}).get("events") or []:
        geometry = event.get("geometry") or [{}]
        rows.append({"title": event.get("title"),
                     "category": ", ".join(c.get("title", "") for c in event.get("categories") or []),
                     "latest": (geometry[-1].get("date") or "")[:16],
                     "url": ((event.get("sources") or [{}])[0]).get("url") or event.get("link")})
    return rows


CITIES = ("New York", "London", "Tokyo", "Jakarta", "Seoul")


def open_meteo(payloads: list) -> list[dict]:
    data = payloads[0]
    items = data if isinstance(data, list) else [data]
    rows = []
    for city, item in zip(CITIES, items):
        daily = (item or {}).get("daily") or {}
        for day, tmax, rain in zip(daily.get("time") or [], daily.get("temperature_2m_max") or [],
                                   daily.get("precipitation_sum") or []):
            rows.append({"city": city, "date": day, "max_temp_c": _f(tmax, 1),
                         "max_temp_f": round(_f(tmax) * 9 / 5 + 32, 1) if _f(tmax) is not None else None,
                         "rain_mm": _f(rain, 1)})
    return rows


def launches(payloads: list) -> list[dict]:
    rows = []
    for item in (payloads[0] or {}).get("results") or []:
        provider = (item.get("launch_service_provider") or {}).get("name")
        rows.append({"net_utc": (item.get("net") or "")[:16], "name": item.get("name"), "provider": provider,
                     "status": (item.get("status") or {}).get("abbrev"),
                     "pad": ((item.get("pad") or {}).get("location") or {}).get("name"),
                     "url": item.get("url")})
    return rows


PARSERS: dict[str, Callable[[list], list[dict]]] = {
    name: fn for name, fn in list(globals().items())
    if inspect.isfunction(fn) and fn.__module__ == __name__ and not name.startswith("_")
}
