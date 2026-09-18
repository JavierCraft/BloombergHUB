"""JSON API. One envelope, one error path, one cache policy for every endpoint.

Every route goes through `serve()`, which:

  1. asks the registry whether this source is allowed on a read-only path,
  2. fails fast with an actionable error if the package, key or network is
     missing — instead of hanging for 30 seconds on a blocked host,
  3. serves from the TTL cache when it can, and from a *stale* entry when the
     upstream is down, labelling the age either way,
  4. normalises pandas / polars / lists into JSON-safe records,
  5. returns `{ok, data, meta, error}` — always the same shape.

The UI can therefore render any endpoint with the same code path, and can tell
"we are down" apart from "you need a key" apart from "your ISP blocks this".
"""
from __future__ import annotations

import re
from typing import Any, Callable

from flask import Blueprint, jsonify, request

import config
from src.core import cache, frames, health, registry
from src.core.errors import BadRequest, HubError, Meta, TradingDisabled, fail, ok

bp = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# The single serving path
# ---------------------------------------------------------------------------


def serve(
    source_id: str,
    key: Any,
    producer: Callable[[], Any],
    limit: int | None = 500,
    columns: list[str] | None = None,
    ttl: int | None = None,
    tabular: bool = True,
    note: str = "",
    needs_module: bool = True,
):
    """Run `producer`, wrap the result, and never leak an unexplained traceback."""
    try:
        src = health.guard(source_id, needs_module) if source_id in registry.BY_ID else None
        effective_ttl = ttl if ttl is not None else (src.ttl if src else config.CACHE_TTL)

        with_timer = Meta(source=source_id, notes=[note] if note else [])

        def run() -> Any:
            raw = producer()
            if not tabular:
                return {"value": frames.json_safe(raw)}
            records, cols, truncated = frames.to_records(raw, limit=limit, columns=columns)
            return {"records": records, "columns": cols, "truncated": truncated}

        import time

        started = time.perf_counter()
        if effective_ttl and effective_ttl > 0:
            payload, cached, age = cache.cached_call(source_id, key, effective_ttl, run)
        else:
            payload, cached, age = run(), False, 0.0
        elapsed = int((time.perf_counter() - started) * 1000)

        if tabular:
            data = payload["records"]
            with_timer.columns = payload["columns"]
            with_timer.rows = len(data)
            with_timer.truncated = payload["truncated"]
            if payload["truncated"]:
                with_timer.notes.append(f"Dipotong di {limit} baris.")
        else:
            data = payload["value"]

        with_timer.cached = cached
        with_timer.cache_age_s = age if cached else None
        with_timer.elapsed_ms = elapsed
        if cached and age > (effective_ttl or 0):
            with_timer.notes.append(
                f"Sumber sedang bermasalah — menampilkan data lama ({int(age)}s)."
            )
        return jsonify(ok(data, with_timer))

    except Exception as exc:
        body, status = fail(exc, source_id, debug=config.DEBUG)
        return jsonify(body), status


def body() -> dict:
    return request.get_json(silent=True) or {}


def num(source: dict, name: str, default: float | None = None, required: bool = False) -> float:
    raw = source.get(name, default)
    if raw is None or raw == "":
        if required:
            raise BadRequest(f"Parameter '{name}' wajib diisi.")
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise BadRequest(f"Parameter '{name}' harus angka, dapat '{raw}'.") from None


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


@bp.get("/health")
def api_health():
    force = request.args.get("refresh") == "1"
    try:
        return jsonify(ok(health.check_all(deep=True, force=force)))
    except Exception as exc:
        body_, status = fail(exc, "health", debug=config.DEBUG)
        return jsonify(body_), status


@bp.get("/registry")
def api_registry():
    payload = registry.catalog()
    payload["secrets"] = config.secrets_status()
    return jsonify(ok(payload))


@bp.get("/cache")
def api_cache_stats():
    return jsonify(ok(cache.stats()))


@bp.get("/version")
def api_version():
    """PID, start time and code version of this server — read by `app.py` before starting another."""
    from src.web import version

    return jsonify(ok(version.snapshot()))


@bp.post("/cache/clear")
def api_cache_clear():
    namespace = body().get("namespace")
    return jsonify(ok({"removed": cache.clear(namespace), "namespace": namespace}))


# ---------------------------------------------------------------------------
# Polygon on-chain — the one Polymarket path still open
# ---------------------------------------------------------------------------


def _reader():
    from src.polymarket.onchain import PolygonReader

    return PolygonReader(config.POLYGON_RPC)


@bp.get("/polygon/block")
def api_block():
    return serve("polygon", "block", lambda: {"block": _reader().get_block_number()},
                 tabular=False, ttl=15)


@bp.post("/polygon/wallet")
def api_wallet():
    data = body()
    address = str(data.get("address", "")).strip()
    if not address.startswith("0x") or len(address) != 42:
        return fail(BadRequest("Alamat wallet harus 0x + 40 karakter hex."), "polygon")
    deep = bool(data.get("deep"))
    return serve("polygon", ("wallet", address.lower(), deep),
                 lambda: _reader().scan_wallet(address, deep=deep), tabular=False, ttl=60)


@bp.post("/polygon/exchange")
def api_exchange():
    data = body()
    blocks = int(num(data, "blocks", 200))
    address = data.get("address") or None
    from src.polymarket.onchain import CTF_EXCHANGE

    target = address or CTF_EXCHANGE
    return serve("polygon", ("exchange", target.lower(), blocks),
                 lambda: _reader().check_exchange_activity(blocks, target),
                 tabular=False, ttl=60)


@bp.get("/polygon/settlement")
def api_settlement():
    # ConditionalTokens mengeluarkan ~170 catatan per blok, jadi rentang lebar
    # membuat satu permintaan memakan puluhan detik. 60 blok sudah cukup untuk
    # menggambarkan keramaian, dan hasilnya disimpan lebih lama.
    blocks = max(10, min(500, int(request.args.get("blocks", 60))))
    return serve("polygon", ("settlement-v2", blocks),
                 lambda: _reader().settlement_activity(blocks), tabular=False, ttl=180)


@bp.post("/polygon/flows")
def api_flows():
    data = body()
    address = str(data.get("address", "")).strip()
    blocks = int(num(data, "blocks", 20000))
    return serve("polygon", ("flows", address.lower(), blocks),
                 lambda: _reader().usdc_flows(address, blocks), tabular=False, ttl=120)


@bp.post("/polygon/positions")
def api_positions():
    data = body()
    address = str(data.get("address", "")).strip()
    blocks = int(num(data, "blocks", 20000))
    return serve("polygon", ("positions", address.lower(), blocks),
                 lambda: _reader().position_changes(address, blocks), tabular=False, ttl=120)


# ---------------------------------------------------------------------------
# Betting maths — compute only, always available
# ---------------------------------------------------------------------------


@bp.post("/betting/devig")
def api_devig():
    from src.sports.betting import devig, parse_odds

    data = body()
    raw = data.get("odds")
    if isinstance(raw, str):
        raw = [x for x in raw.replace(";", ",").split(",") if x.strip()]
    if not raw or len(raw) < 2:
        return fail(BadRequest("Butuh minimal 2 odds, pisahkan dengan koma."), "betting")

    fmt = data.get("format", "auto")
    method = data.get("method", "power")
    return serve("betting", None,
                 lambda: devig([parse_odds(o, fmt) for o in raw], method=method),
                 tabular=False, ttl=0)


@bp.post("/betting/kelly")
def api_kelly():
    from src.sports.betting import kelly_from_prob, parse_odds

    data = body()
    odds = parse_odds(data.get("odds", 2.0), data.get("format", "auto"))
    prob = num(data, "prob", None)
    if prob is None:
        edge = num(data, "edge", 5.0) / 100.0
        prob = min(1.0, 1.0 / odds + edge)
    elif prob > 1:
        prob = prob / 100.0
    bankroll = num(data, "bankroll", 1000.0)

    def compute() -> dict:
        full = kelly_from_prob(prob, odds)
        return {
            "probability": round(prob, 5),
            "decimal_odds": round(odds, 4),
            "implied_prob": round(1 / odds, 5),
            "edge_pp": round((prob - 1 / odds) * 100, 3),
            "full_kelly": round(full, 5),
            "half_kelly": round(full / 2, 5),
            "quarter_kelly": round(full / 4, 5),
            "full_kelly_pct": f"{full:.2%}",
            "half_kelly_pct": f"{full / 2:.2%}",
            "stake_full": round(bankroll * full, 2),
            "stake_half": round(bankroll * full / 2, 2),
            "bankroll": bankroll,
            "note": "Half-Kelly adalah default yang waras — full Kelly menganggap "
                    "probabilitas Anda persis benar.",
        }

    return serve("betting", None, compute, tabular=False, ttl=0)


@bp.post("/betting/arb")
def api_arb():
    from src.sports.betting import detect_arbitrage, parse_odds

    data = body()
    raw = data.get("outcomes") or data.get("odds")
    if isinstance(raw, str):
        raw = [x for x in raw.replace(";", ",").split(",") if x.strip()]
    if not raw or len(raw) < 2:
        return fail(BadRequest("Butuh minimal 2 outcome."), "betting")
    fmt = data.get("format", "auto")
    return serve("betting", None,
                 lambda: detect_arbitrage(*[parse_odds(o, fmt) for o in raw]),
                 tabular=False, ttl=0)


@bp.post("/betting/poisson")
def api_poisson():
    from src.sports.betting import match_probabilities

    data = body()
    return serve("betting", None, lambda: match_probabilities(
        num(data, "home_xg", 1.5), num(data, "away_xg", 1.2),
        total_line=num(data, "total_line", 2.5),
    ), tabular=False, ttl=0)


@bp.post("/betting/edge")
def api_edge():
    from src.sports.betting import market_edge

    data = body()
    prob = num(data, "model_prob", required=True)
    price = num(data, "market_price", required=True)
    if prob > 1:
        prob /= 100.0
    if price > 1:
        price /= 100.0
    return serve("betting", None, lambda: market_edge(
        prob, price,
        bankroll=num(data, "bankroll", 1000.0),
        kelly_multiplier=num(data, "kelly_multiplier", 0.5),
        fee_bps=num(data, "fee_bps", 0.0),
    ), tabular=False, ttl=0)


@bp.post("/betting/calibration")
def api_calibration():
    from src.sports.betting import calibration_table

    data = body()
    predictions = data.get("predictions") or []
    outcomes = data.get("outcomes") or []
    if isinstance(predictions, str):
        predictions = [float(x) for x in predictions.replace(";", ",").split(",") if x.strip()]
    if isinstance(outcomes, str):
        outcomes = [float(x) for x in outcomes.replace(";", ",").split(",") if x.strip()]
    if len(predictions) != len(outcomes) or not predictions:
        return fail(
            BadRequest("Prediksi dan hasil harus sama panjang dan tidak kosong."), "betting")
    return serve("betting", None,
                 lambda: calibration_table(predictions, outcomes,
                                           bins=int(num(data, "bins", 10))),
                 tabular=False, ttl=0)


@bp.post("/backtest/simulate")
def api_simulate():
    from src.backtest import Simulator

    data = body()
    bets = data.get("bets") or []
    if not bets:
        return fail(BadRequest("Kirim daftar 'bets'."), "backtest")
    return serve("backtest", None, lambda: Simulator.run(
        bets,
        bankroll=num(data, "bankroll", 1000.0),
        kelly_multiplier=num(data, "kelly_multiplier", 0.5),
        max_stake_pct=num(data, "max_stake_pct", 0.05),
        flat_stake=data.get("flat_stake"),
    ), tabular=False, ttl=0)


# ---------------------------------------------------------------------------
# Sports
# ---------------------------------------------------------------------------


@bp.get("/sports/soccer/providers")
def api_soccer_providers():
    from src.sports.soccer import SoccerDataSource

    return jsonify(ok(SoccerDataSource.provider_status()))


@bp.get("/sports/soccer/leagues")
def api_soccer_leagues():
    """Daftar liga yang benar-benar diterima pustakanya, bukan daftar tetap."""
    from src.sports.soccer import DEFAULT_LEAGUE, SoccerDataSource

    return serve("soccer", "leagues", lambda: {
        "leagues": SoccerDataSource().liga_tersedia(),
        "default": DEFAULT_LEAGUE,
    }, tabular=False, ttl=86400)


@bp.get("/sports/soccer/understat")
def api_understat():
    from src.sports.soccer import DEFAULT_SEASON, SoccerDataSource

    league = request.args.get("league") or None
    season = request.args.get("season", DEFAULT_SEASON)
    return serve("soccer", ("understat", league, season),
                 lambda: SoccerDataSource().understat_schedule(league, [season]),
                 limit=400)


@bp.get("/sports/soccer/espn")
def api_espn():
    from src.sports.soccer import DEFAULT_SEASON, SoccerDataSource

    league = request.args.get("league") or None
    season = request.args.get("season", DEFAULT_SEASON)
    return serve("soccer", ("espn", league, season),
                 lambda: SoccerDataSource().espn_schedule(league, [season]),
                 limit=400)


@bp.get("/sports/soccer/fixtures")
def api_soccer_fixtures():
    """Pertandingan sepak bola yang sedang dihargai Polymarket, sebagai 1X2.

    Polymarket memecah satu pertandingan menjadi tiga pasar Yes/No terpisah
    (menang tuan rumah, seri, menang tamu). Ketiganya disusun ulang di sini
    menjadi satu baris 1X2, dan overround-nya dilaporkan apa adanya.
    """
    from src.markets import fixtures as fx

    limit = max(10, min(300, request.args.get("limit", 200, type=int)))
    return serve("polymarket_status", ("soccer-fixtures", limit), lambda: {
        "rows": fx.board("soccer", limit=limit),
        "note": ("Disusun dari tiga pasar Yes/No per pertandingan. Jumlah ketiga "
                 "harga jarang tepat 100% — selisihnya ditampilkan sebagai overround, "
                 "bukan dinormalkan diam-diam. Daftar ini mengikuti pertandingan yang "
                 "dihargai Polymarket, bukan seluruh jadwal liga."),
    }, tabular=False, ttl=60)


@bp.get("/sports/nba/leaders")
def api_nba_leaders():
    from src.sports.nba import NBAData

    stat = request.args.get("stat", "PTS")
    return serve("nba", ("leaders", stat), lambda: NBAData().league_leaders(stat),
                 limit=100,
                 columns=["RANK", "PLAYER", "TEAM", "GP", "MIN", "PTS", "REB", "AST",
                          "STL", "BLK", "FG_PCT", "FG3_PCT", "EFF"])


@bp.get("/sports/nba/standings")
def api_nba_standings():
    from src.sports.nba import NBAData

    return serve("nba", "standings", lambda: NBAData().standings(), limit=40,
                 columns=["Conference", "TeamCity", "TeamName", "WINS", "LOSSES",
                          "WinPCT", "ConferenceGamesBack", "L10", "strCurrentStreak"])


@bp.get("/sports/nba/recent")
def api_nba_recent():
    from src.sports.nba import NBAData

    limit = max(5, min(200, request.args.get("limit", 40, type=int)))
    return serve("nba", ("recent", limit),
                 lambda: NBAData().recent_games(limit=limit), limit=limit)


@bp.get("/sports/nba/today")
def api_nba_today():
    from src.sports.nba import NBAData

    return serve("nba", "today", lambda: NBAData().today_scoreboard(), limit=30, ttl=120)


@bp.get("/sports/nfl/upcoming")
def api_nfl_upcoming():
    from src.sports.nfl import NFLData

    season = request.args.get("season", type=int)
    return serve("nfl", ("upcoming", season), lambda: NFLData().upcoming(season, 60), limit=60)


@bp.get("/sports/nfl/results")
def api_nfl_results():
    from src.sports.nfl import NFLData

    season = request.args.get("season", type=int)
    return serve("nfl", ("results", season), lambda: NFLData().results(season, 60), limit=60)


@bp.get("/sports/nfl/injuries")
def api_nfl_injuries():
    from src.sports.nfl import NFLData

    season = request.args.get("season", type=int)
    return serve("nfl", ("injuries", season),
                 lambda: NFLData().injuries([season] if season else None), limit=300,
                 columns=["season", "week", "team", "full_name", "position",
                          "report_status", "practice_status", "date_modified"])


@bp.get("/sports/statsbomb/competitions")
def api_statsbomb():
    from src.sports.soccer import StatsBombSoccer

    return serve("statsbomb", "competitions", lambda: StatsBombSoccer().competitions(),
                 limit=300)


# ---------------------------------------------------------------------------
# Esports
# ---------------------------------------------------------------------------


@bp.get("/esports/dota/heroes")
def api_dota_heroes():
    from src.sports.esports import Dota2

    top = request.args.get("top", 40, type=int)
    return serve("dota2", ("heroes", top), lambda: Dota2().hero_win_rates(top), limit=top)


@bp.get("/esports/dota/teams")
def api_dota_teams():
    from src.sports.esports import Dota2

    top = request.args.get("top", 40, type=int)
    return serve("dota2", ("teams", top), lambda: Dota2().team_strength(top), limit=top)


@bp.get("/esports/dota/pro")
def api_dota_pro():
    from src.sports.esports import Dota2

    limit = request.args.get("limit", 40, type=int)
    return serve("dota2", ("pro", limit), lambda: Dota2().pro_matches(limit), limit=limit,
                 columns=["match_id", "league_name", "radiant_name", "dire_name",
                          "radiant_win", "duration", "start_time"])


@bp.get("/esports/schedule")
def api_esports_schedule():
    from src.sports.esports import EsportsSchedules

    game = request.args.get("game", "dota2")
    limit = request.args.get("limit", 25, type=int)
    return serve("liquipedia", ("schedule", game, limit),
                 lambda: EsportsSchedules().upcoming(game, limit), limit=limit)


@bp.get("/esports/board")
def api_esports_board():
    """Jadwal, odds Polymarket per pertandingan, dan indikasi form — satu tabel.

    Digabung di sisi peladen supaya tampilannya tidak menembak tiga endpoint per
    baris. Kegagalan salah satu sisi tidak menjatuhkan sisi lain: jadwal tanpa
    pasar tetap berguna, dan sebaliknya.
    """
    from src.markets import fixtures as fx
    from src.sports.esports import EsportsSchedules, LIQUIPEDIA_GAMES
    from src.sports.match_analysis import form_lean

    game = request.args.get("game", "dota2")
    if game not in LIQUIPEDIA_GAMES:
        return fail(BadRequest(f"Game harus salah satu dari {list(LIQUIPEDIA_GAMES)}."),
                    "liquipedia")
    limit = max(5, min(50, request.args.get("limit", 30, type=int)))

    def build() -> dict:
        failures = []

        def note(source: str, exc: Exception) -> None:
            failures.append({"source": source,
                             "reason": getattr(exc, "message", None) or str(exc)[:140]})

        try:
            listed = fx.board("esports", game)
        except Exception as exc:
            listed = []
            note("Polymarket", exc)

        try:
            rows = EsportsSchedules().upcoming(game, limit)
            schedule_source = "Liquipedia"
        except Exception as exc:
            # Liquipedia rate-limits hard, and a dead ticker should not take the
            # odds down with it. The priced fixtures already carry both team
            # names and a settlement time, so they stand in as the schedule —
            # labelled, so nobody mistakes it for the full tournament calendar.
            note("Liquipedia", exc)
            schedule_source = "Polymarket"
            rows = [{
                "starts_utc": market.get("start_date"),
                "team_a": market["teams"][0], "team_b": market["teams"][1],
                "format": None, "tournament": market.get("event_title"),
            } for market in listed[:limit] if len(market.get("teams") or []) >= 2]

        fx.attach(rows, listed)

        try:
            form_lean(game, rows)
        except Exception as exc:
            note("OpenDota", exc)
            for row in rows:
                row.setdefault("form", {"known": False, "reason": "Sumber form gagal dijawab."})

        return {
            "game": game, "game_name": LIQUIPEDIA_GAMES[game],
            "rows": rows,
            "listed_markets": len(listed),
            "schedule_source": schedule_source,
            "summary": fx.summary(rows),
            "failed": failures,
            "note": ("Jadwal dari Liquipedia, odds dari Polymarket, indikasi form dari "
                     "rating OpenDota. Ketiganya sumber terpisah yang dicocokkan lewat nama "
                     "tim — periksa tanggal dan turnamennya sebelum memakai odds."
                     if schedule_source == "Liquipedia" else
                     "Liquipedia tidak menjawab, jadi daftar ini berisi pertandingan yang "
                     "sedang dihargai Polymarket — bukan seluruh jadwal turnamen. Odds dari "
                     "Polymarket, indikasi form dari rating OpenDota."),
        }

    # Liquipedia's ticker moves in hours, not seconds, and its API rate-limits
    # hard enough that a 60s TTL turned normal tab-switching into 503s.
    return serve("liquipedia", ("board", game, limit), build, tabular=False, ttl=300)


@bp.get("/esports/analysis")
def api_esports_analysis():
    from src.sports.match_analysis import compare
    from src.sports.esports import LIQUIPEDIA_GAMES
    game = request.args.get("game", "dota2")
    a = (request.args.get("a") or "").strip()[:120]
    b = (request.args.get("b") or "").strip()[:120]
    if game not in LIQUIPEDIA_GAMES or not a or not b or a.casefold() == b.casefold():
        return fail(BadRequest("Pilih game dan dua tim berbeda."), "esports_analysis")
    return serve("esports_analysis", (game, a, b), lambda: compare(game, a, b),
                 tabular=False, ttl=300)


# ---------------------------------------------------------------------------
# Tech
# ---------------------------------------------------------------------------


@bp.get("/tech/new")
def api_tech_new():
    from src.tech import ModelTracker

    hours = request.args.get("hours", 24, type=int)
    return serve("huggingface", ("new", hours),
                 lambda: ModelTracker().new_models(hours, limit=200), limit=200)


@bp.get("/tech/trending")
def api_tech_trending():
    from src.tech import ModelTracker

    return serve("huggingface", "trending", lambda: ModelTracker().trending(40), limit=40)


@bp.get("/tech/watch")
def api_tech_watch():
    from src.tech.models import WATCHED_AUTHORS, ModelTracker

    hours = request.args.get("hours", 336, type=int)
    authors = request.args.get("authors")
    watched = [a.strip() for a in authors.split(",")] if authors else WATCHED_AUTHORS
    return serve("huggingface", ("watch", tuple(watched), hours),
                 lambda: ModelTracker().watch_authors(watched, hours, 8), limit=150)


@bp.get("/tech/search")
def api_tech_search():
    from src.tech import ModelTracker

    query = request.args.get("q", "")
    if not query:
        return fail(BadRequest("Parameter 'q' wajib."), "huggingface")
    return serve("huggingface", ("search", query),
                 lambda: ModelTracker().search_models(query, 40), limit=40)


@bp.get("/tech/datasets")
def api_tech_datasets():
    from src.tech import ModelTracker

    hours = request.args.get("hours", 48, type=int)
    return serve("huggingface", ("datasets", hours),
                 lambda: ModelTracker().new_datasets(hours, 60), limit=60)


@bp.get("/tech/llm/radar")
def api_tech_llm_radar():
    """Newest LLM announcements from lab blogs and AI trackers."""
    from src.tech.llm_radar import radar

    hours = max(24, min(2160, request.args.get("hours", 336, type=int)))
    return serve("news", ("llm-radar", hours), lambda: radar(hours), tabular=False, ttl=600)


@bp.get("/tech/llm/claude")
def api_tech_llm_claude():
    """Claude model list — live from the Models API with a key, documented otherwise."""
    from src.ml import llm

    def run():
        try:
            return llm.list_models()
        except Exception as exc:  # noqa: BLE001 — fall back to the documented list, say why
            return {"live": False, "models": llm.KNOWN_MODELS, "as_of": llm.KNOWN_MODELS_AS_OF,
                    "error": getattr(exc, "message", None) or str(exc)[:160],
                    "note": "API tidak bisa dibaca saat ini; menampilkan daftar terdokumentasi."}

    return serve("ml", "claude-models", run, tabular=False, ttl=3600)


# ---------------------------------------------------------------------------
# Culture
# ---------------------------------------------------------------------------


@bp.get("/culture/status")
def api_culture_status():
    """Sumber mana yang menyalakan halaman ini, dan mana yang ditinggalkan."""
    from src.culture import culture_status

    return serve("deezer", "status", culture_status, ttl=0)


# -- musik: Deezer, tanpa kunci ---------------------------------------------


@bp.get("/culture/music/chart")
def api_music_chart():
    from src.culture import DeezerMusic

    kind = request.args.get("type", "tracks")
    if kind not in ("tracks", "albums"):
        return fail(BadRequest("Parameter 'type' hanya menerima tracks atau albums."), "deezer")
    music = DeezerMusic()
    fn = music.top_tracks if kind == "tracks" else music.top_albums
    # Tangga album Deezer memang hanya berisi lima entri — `total` di responsnya
    # ikut mengatakan 5 berapa pun limit yang diminta. Dikatakan apa adanya
    # supaya tabel pendek tidak terbaca sebagai data yang gagal terambil.
    catatan = "Tangga album Deezer memang hanya memuat 5 entri." if kind == "albums" else ""
    return serve("deezer", ("chart", kind), lambda: fn(40), limit=40, note=catatan)


@bp.get("/culture/music/search")
def api_music_search():
    from src.culture import DeezerMusic

    query = request.args.get("q", "").strip()
    kind = request.args.get("type", "track")
    if not query:
        return fail(BadRequest("Parameter 'q' wajib."), "deezer")
    if kind not in ("track", "artist"):
        return fail(BadRequest("Parameter 'type' hanya menerima track atau artist."), "deezer")
    music = DeezerMusic()
    fn = music.search_track if kind == "track" else music.search_artist
    return serve("deezer", ("search", kind, query), lambda: fn(query, 30), limit=30)


# -- serial TV: TVmaze, tanpa kunci -----------------------------------------


@bp.get("/culture/tv/schedule")
def api_tv_schedule():
    from src.culture import TVmazeShows

    date = request.args.get("date", "").strip() or None
    return serve("tvmaze", ("schedule", date or "hari-ini"),
                 lambda: TVmazeShows().schedule(date, 60), limit=60, ttl=1800)


@bp.get("/culture/tv/search")
def api_tv_search():
    from src.culture import TVmazeShows

    query = request.args.get("q", "").strip()
    if not query:
        return fail(BadRequest("Parameter 'q' wajib."), "tvmaze")
    return serve("tvmaze", ("search", query), lambda: TVmazeShows().search(query, 30), limit=30)


# -- film: OMDb, kunci gratis -----------------------------------------------


@bp.get("/culture/movies/search")
def api_movies_search():
    from src.culture import OMDbMovies

    query = request.args.get("q", "").strip()
    kind = request.args.get("type", "movie")
    if not query:
        return fail(BadRequest("Parameter 'q' wajib."), "omdb")
    if kind not in ("movie", "series"):
        return fail(BadRequest("Parameter 'type' hanya menerima movie atau series."), "omdb")
    return serve("omdb", ("search", kind, query),
                 lambda: OMDbMovies().search(query, kind, 30), limit=30)


@bp.get("/culture/movies/detail")
def api_movies_detail():
    from src.culture import OMDbMovies

    imdb_id = request.args.get("id", "").strip()
    if not re.fullmatch(r"tt\d{6,10}", imdb_id):
        return fail(BadRequest("Parameter 'id' harus berupa id IMDb, misalnya tt3896198."), "omdb")
    return serve("omdb", ("detail", imdb_id),
                 lambda: OMDbMovies().detail(imdb_id), tabular=False)


# ---------------------------------------------------------------------------
# Politics
# ---------------------------------------------------------------------------


@bp.get("/politics/gdelt/articles")
def api_gdelt_articles():
    from src.politics import GDELT

    query = request.args.get("q", "midterm election")
    span = request.args.get("timespan", "7d")
    limit = request.args.get("limit", 50, type=int)
    return serve("gdelt", ("articles", query, span, limit),
                 lambda: GDELT().articles(query, limit, span), limit=limit)


@bp.get("/politics/gdelt/volume")
def api_gdelt_volume():
    from src.politics import GDELT

    query = request.args.get("q", "midterm election")
    span = request.args.get("timespan", "30d")
    return serve("gdelt", ("volume", query, span),
                 lambda: GDELT().volume_timeline(query, span), limit=400)


@bp.get("/politics/gdelt/tone")
def api_gdelt_tone():
    from src.politics import GDELT

    query = request.args.get("q", "midterm election")
    span = request.args.get("timespan", "30d")
    return serve("gdelt", ("tone", query, span),
                 lambda: GDELT().tone_timeline(query, span), limit=400)


@bp.get("/politics/electindex/files")
def api_electindex_files():
    from src.politics import ElectIndexForecast

    # Batasnya dinaikkan: kumpulan datanya memuat 2.619 berkas, dan daftar yang
    # terpotong di 100 baris memberi kesan isinya jauh lebih sedikit dari itu.
    return serve("electindex", "files", lambda: ElectIndexForecast().list_files(), limit=3000)


@bp.get("/politics/electindex/races")
def api_electindex_races():
    """506 balapan dengan keadaan terakhirnya, urut dari yang paling ketat."""
    from src.politics import ElectIndexForecast

    return serve("electindex", "races", lambda: ElectIndexForecast().races(), limit=600)


@bp.get("/politics/electindex/race")
def api_electindex_race():
    """Deret peluang harian satu balapan — bahan pembanding langsung untuk harga pasar."""
    from src.politics import ElectIndexForecast

    code = request.args.get("code", "").strip()
    if not code:
        return fail(BadRequest("Parameter 'code' wajib.",
                               hint="Contoh: PA-GOV. Daftar lengkapnya di "
                                    "/api/politics/electindex/races."), "electindex")
    return serve("electindex", ("race", code.upper()),
                 lambda: ElectIndexForecast().race(code), limit=400)


@bp.get("/politics/electindex/ballot")
def api_electindex_ballot():
    from src.politics import ElectIndexForecast

    drop = request.args.get("drop_banned", "1") != "0"
    return serve("electindex", ("ballot", drop),
                 lambda: ElectIndexForecast().generic_ballot(drop_banned=drop), limit=500)


@bp.get("/politics/electindex/banned")
def api_electindex_banned():
    from src.politics import ElectIndexForecast

    return serve("electindex", "banned", lambda: ElectIndexForecast().banned_pollsters(),
                 limit=200)


@bp.get("/politics/electindex/table")
def api_electindex_table():
    from src.politics import ElectIndexForecast

    key = request.args.get("key", "generic_ballot")
    return serve("electindex", ("table", key), lambda: ElectIndexForecast().table(key),
                 limit=500)


# ---------------------------------------------------------------------------
# Trading — the money gate. One package, one door, closed by default.
# ---------------------------------------------------------------------------


@bp.get("/trading/status")
def api_trading_status():
    money = [s.to_dict() for s in registry.money_sources()]
    return jsonify(ok({
        "enabled": config.ENABLE_TRADING,
        "policy": registry.DEFAULT_POLICY,
        "money_sources": money,
        "credentials_set": config.secrets_status(),
        "network": "Host CLOB Polymarket diblokir dari koneksi ini — bahkan dengan "
                   "gate terbuka, order tidak akan sampai.",
    }))


@bp.post("/trading/order")
def api_trading_order():
    """Deliberately refuses. Enabling this needs a config change AND a reachable host."""
    if not config.ENABLE_TRADING:
        return fail(TradingDisabled("memasang order"), "polymarket_clob")
    return fail(
        HubError(
            "Gate terbuka, tapi eksekusi order sengaja tidak diimplementasikan di UI web.",
            hint="Order harus dipasang dari proses terpisah yang memegang kunci "
                 "(src/polymarket/client.py), di mesin yang bisa menjangkau clob.polymarket.com.",
        ),
        "polymarket_clob",
    )


# ---------------------------------------------------------------------------
# Berita — banyak sumber sekaligus, disimpan ke berkas teks
# ---------------------------------------------------------------------------


def _news_selection() -> tuple[str | None, list[str] | None, list[str] | None]:
    """Validated `sector`, `category` and `feeds` query parameters."""
    from src.news.sources import BY_CODE, CATEGORIES, SECTOR_CATEGORIES

    sector = (request.args.get("sector") or "").strip().lower() or None
    if sector and sector not in SECTOR_CATEGORIES:
        raise BadRequest(f"Sektor '{sector}' tidak dikenal.",
                         hint="Pilihan: " + ", ".join(sorted(SECTOR_CATEGORIES)))
    categories = [c.strip() for c in (request.args.get("category") or "").split(",") if c.strip()] or None
    if categories:
        unknown = [c for c in categories if c not in CATEGORIES]
        if unknown:
            raise BadRequest(f"Kategori tidak dikenal: {', '.join(unknown)}.",
                             hint="Pilihan: " + ", ".join(CATEGORIES))
    feeds = [c.strip() for c in (request.args.get("feeds") or "").split(",") if c.strip()] or None
    if feeds:
        unknown = [c for c in feeds if c not in BY_CODE]
        if unknown:
            raise BadRequest(f"Kode feed tidak dikenal: {', '.join(unknown[:5])}.",
                             hint="Daftar lengkapnya di /api/news/catalog.")
    return sector, categories, feeds


@bp.get("/news")
def api_news():
    from src import news

    query = (request.args.get("q") or "").strip()[:120] or None
    limit = max(5, min(150, request.args.get("limit", 60, type=int)))
    try:
        sector, categories, feeds = _news_selection()
    except BadRequest as exc:
        return fail(exc, "news")
    # Berita berubah cepat; simpanan pendek saja supaya tetap terasa langsung.
    # Tiap feed juga punya simpanan sendiri di dalam news.headlines.
    return serve("news", ("headlines-v2", query, limit, sector, tuple(categories or ()), tuple(feeds or ())),
                 lambda: news.headlines(query, limit, sources=feeds, categories=categories, sector=sector),
                 tabular=False, ttl=30)


@bp.get("/news/sources")
def api_news_sources():
    from src import news

    return jsonify(ok(news.source_list()))


@bp.get("/news/catalog")
def api_news_catalog():
    """Every feed with its category, language, kind and what it is good for."""
    from src.news import sources

    return jsonify(ok(sources.catalog()))


def _corpus_or_fetch(categories: list[str] | None, sector: str | None) -> list[dict]:
    """Stored corpus; on a fresh install, one live fetch seeds it first."""
    from src import news

    items = news.corpus(categories=categories)
    if len(items) < 40:
        fetched = news.headlines(limit=150, categories=categories, sector=sector)["news"]
        seen = {i.get("url") for i in items}
        items = items + [i for i in fetched if i.get("url") not in seen]
    return items


@bp.get("/news/ml/digest")
def api_news_digest():
    """Stories of the last hours — the extractive 'news creation' half."""
    from src.ml import newsml

    hours = max(1.0, min(168.0, request.args.get("hours", 24, type=float)))
    try:
        sector, categories, _ = _news_selection()
    except BadRequest as exc:
        return fail(exc, "ml")
    if sector and not categories:
        from src.news.sources import SECTOR_CATEGORIES

        categories = list(SECTOR_CATEGORIES[sector])
    return serve("ml", ("digest", hours, sector, tuple(categories or ())),
                 lambda: newsml.digest(_corpus_or_fetch(categories, sector), hours),
                 tabular=False, ttl=300)


@bp.get("/news/ml/trends")
def api_news_trends():
    """Terms whose share of headlines is growing fastest."""
    from src.ml import newsml

    hours = max(1.0, min(72.0, request.args.get("hours", 24, type=float)))
    try:
        sector, categories, _ = _news_selection()
    except BadRequest as exc:
        return fail(exc, "ml")
    if sector and not categories:
        from src.news.sources import SECTOR_CATEGORIES

        categories = list(SECTOR_CATEGORIES[sector])
    return serve("ml", ("trends", hours, sector, tuple(categories or ())),
                 lambda: newsml.rising_terms(_corpus_or_fetch(categories, sector), hours),
                 tabular=False, ttl=300)


@bp.get("/news/ml/market")
def api_news_market():
    """Relevance-ranked evidence, tone and news features for one market question."""
    from src.ml import newsml

    question = (request.args.get("q") or "").strip()[:300]
    if len(question) < 4:
        return fail(BadRequest("Parameter 'q' (pertanyaan pasar) wajib diisi."), "ml")
    return serve("ml", ("market-news", question), lambda: newsml.market_news(question),
                 tabular=False, ttl=180)


@bp.post("/news/llm")
def api_news_llm():
    """Claude's structured reading of the headlines for one market (optional, paid)."""
    from src.ml import llm, newsml

    data = body()
    question = str(data.get("question") or "").strip()[:400]
    if len(question) < 4:
        return fail(BadRequest("Kirim 'question' — pertanyaan pasarnya."), "anthropic")

    def run() -> dict:
        found = newsml.market_news(question, evidence_limit=20)
        market = {
            "question": question,
            "rules": str(data.get("rules") or "")[:3000],
            "price_text": str(data.get("price") or "")[:40],
            "deadline": str(data.get("deadline") or "")[:40],
            "time_left": str(data.get("time_left") or "")[:40],
        }
        result = llm.analyse(market, found["evidence"])
        result["news_features"] = {k: found.get(k) for k in
                                   ("query", "n_24h", "n_72h", "sources_72h", "tone_label")}
        return result

    return serve("anthropic", ("llm", question, data.get("price")), run, tabular=False, ttl=0)


@bp.get("/news/llm/status")
def api_news_llm_status():
    from src.ml import llm

    return jsonify(ok(llm.status()))


@bp.get("/news/saved")
def api_news_saved():
    from src import news

    topic = request.args.get("topic", "berita-utama")
    limit = max(1, min(500, request.args.get("limit", 200, type=int)))
    return serve("news", ("saved", topic, limit),
                 lambda: news.stored(topic, limit), limit=limit, ttl=0)


@bp.get("/news/files")
def api_news_files():
    from src.core import textstore

    return jsonify(ok(textstore.stats()))


@bp.post("/news/clear")
def api_news_clear():
    from src.core import textstore

    topic = body().get("topic")
    return jsonify(ok({"removed": textstore.clear(topic), "topic": topic}))


# ---------------------------------------------------------------------------
# Pasar prediksi — harga, buku pesanan, dan aliran taruhan
# ---------------------------------------------------------------------------


@bp.get("/markets/search")
def api_markets_search():
    from src.markets import Manifold

    query = (request.args.get("q") or "").strip()
    limit = max(1, min(40, request.args.get("limit", 12, type=int)))
    if not query:
        return serve("manifold", ("top", limit),
                     lambda: Manifold().most_active(limit), limit=limit, ttl=30)
    return serve("manifold", ("search", query, limit),
                 lambda: Manifold().search(query, limit), limit=limit, ttl=30)


@bp.get("/markets/related")
def api_markets_related():
    from src.markets import related_markets

    query = (request.args.get("q") or "").strip()
    if not query:
        return fail(BadRequest("Parameter 'q' wajib diisi."), "manifold")
    return serve("manifold", ("related", query),
                 lambda: related_markets(query), tabular=False, ttl=30)


@bp.get("/markets/book/<market_id>")
def api_markets_book(market_id: str):
    from src.markets import Manifold

    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,40}", market_id):
        return fail(BadRequest("Nomor pasar tidak valid."), "manifold")
    return serve("manifold", ("overview", market_id),
                 lambda: Manifold().overview(market_id), tabular=False, ttl=30)


@bp.get("/markets/polymarket")
def api_markets_polymarket():
    """Coba Polymarket sungguhan, lalu laporkan apa adanya kalau gagal.

    Endpoint ini sengaja tetap ada: kalau Anda menjalankannya dari jaringan lain
    yang tidak memblokir, ia langsung bekerja tanpa perubahan kode.
    """
    from src.markets import Polymarket

    query = (request.args.get("q") or "").strip()[:180]
    sector = request.args.get("sector", "")
    if sector and sector not in {"technology", "culture", "politics", "soccer", "esports"}:
        return fail(BadRequest("Kategori Polymarket tidak dikenal."), "polymarket_status")
    def attempt() -> dict:
        # Polymarket first; when its hosts are blocked the panel is filled from
        # Limitless (real money) and Manifold (play money), labelled per market.
        # The derived reading — EV and the model included — rides along with
        # every market, so sector pages need no second round trip.
        from src.markets.sector import sector_markets

        result = sector_markets(sector, query, 12)
        if not result["reachable"]:
            result["error"] = result["reason"]
        return result

    return serve("polymarket_status", ("attempt", query, sector), attempt, tabular=False, ttl=30)


@bp.get("/markets/iev")
def api_markets_iev():
    """IEP / IEV from a live order book, plus depth up to a fair value."""
    from src.markets import Manifold, Polymarket
    from src.markets import orderbook

    source = request.args.get("source", "polymarket")
    fair = request.args.get("fair", type=float)
    if fair is not None and fair > 1:
        fair /= 100.0
    if fair is not None and not 0 < fair < 1:
        return fail(BadRequest("Nilai wajar harus di antara 0 dan 1 (atau 0–100%)."), "polymarket_status")
    if source == "polymarket":
        token = (request.args.get("token") or "").strip()
        if not re.fullmatch(r"\d{6,90}", token):
            return fail(BadRequest("Token CLOB Polymarket harus berupa deretan angka."), "polymarket_status")
        return serve("polymarket_status", ("iev", token, fair),
                     lambda: orderbook.from_polymarket(Polymarket().order_book(token), fair),
                     tabular=False, ttl=15)
    if source == "manifold":
        market_id = (request.args.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", market_id):
            return fail(BadRequest("Nomor pasar Manifold tidak valid."), "manifold")
        return serve("manifold", ("iev", market_id, fair),
                     lambda: orderbook.from_manifold(Manifold().order_book(market_id), fair),
                     tabular=False, ttl=15)
    if source == "limitless":
        from src.markets.limitless import Limitless

        slug = (request.args.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9-]{1,200}", slug):
            return fail(BadRequest("Slug pasar Limitless tidak valid."), "limitless")

        def build():
            book = Limitless().order_book(slug)
            result = orderbook.analyse_book(book["bids"], book["asks"],
                                            reference=book.get("last_trade_price"), fair=fair)
            return {**result, "unit": "lembar", "source": "Limitless (buku pesanan, USDC)"}

        return serve("limitless", ("iev", slug, fair), build, tabular=False, ttl=15)
    return fail(BadRequest("Parameter 'source' harus polymarket, limitless, atau manifold."), "polymarket_status")


@bp.get("/markets/polymarket/flow/<condition_id>")
def api_polymarket_flow(condition_id):
    from src.markets.flow import market_flow
    if not re.fullmatch(r"0x[a-fA-F0-9]{64}", condition_id):
        return fail(BadRequest("Condition ID harus 0x + 64 digit hex."), "polymarket_flow")
    return serve("polymarket_flow", condition_id.lower(), lambda: market_flow(condition_id),
                 tabular=False, ttl=30)


# ---------------------------------------------------------------------------
# Deadline radar, machine learning, paper test
# ---------------------------------------------------------------------------

_SOURCE_SETS = {"all": ("polymarket", "limitless", "manifold"), "polymarket": ("polymarket",),
                "limitless": ("limitless",), "manifold": ("manifold",)}
_MARKET_SOURCES = ("polymarket", "limitless", "manifold")


@bp.get("/deadlines")
def api_deadlines():
    """Markets whose deadline is close, with model probability, direction and EV."""
    from src.ml import deadlines

    days = max(0.05, min(30.0, request.args.get("days", 7, type=float)))
    source = request.args.get("source", "all")
    if source not in _SOURCE_SETS:
        return fail(BadRequest("Parameter 'source' harus all, polymarket, limitless, atau manifold."), "ml")
    query = (request.args.get("q") or "").strip()[:80] or None
    sports = request.args.get("sports", "1") != "0"
    limit = max(10, min(120, request.args.get("limit", 60, type=int)))
    sector = request.args.get("sector", "")
    if sector:
        # A sector page's own near-deadline markets: Limitless categories and
        # Manifold topics of that sector, Polymarket's tag when it answers.
        if sector not in deadlines.SECTORS:
            return fail(BadRequest("Sektor tidak dikenal.", hint="Pilihan: " + ", ".join(deadlines.SECTORS)), "ml")
        return serve("ml", ("sector-deadlines", sector, days, limit),
                     lambda: deadlines.sector_deadlines(sector, days, limit), tabular=False, ttl=180,
                     needs_module=False)
    return serve("ml", ("deadlines", days, source, query, sports, limit),
                 lambda: deadlines.collect(days, _SOURCE_SETS[source], limit, query, sports),
                 tabular=False, ttl=120, needs_module=False)


@bp.get("/ml/status")
def api_ml_status():
    from src.ml import jobs, llm, model, scheduler

    try:
        return jsonify(ok({
            "model": model.status(),
            "train_job": jobs.get("train"),
            "scan_job": jobs.get("scan"),
            "autoscan": scheduler.status(),
            "llm": llm.status(),
        }))
    except Exception as exc:  # noqa: BLE001
        body_, status = fail(exc, "ml", debug=config.DEBUG)
        return jsonify(body_), status


@bp.get("/ml/report")
def api_ml_report():
    from src.ml import model

    return serve("ml", "report", lambda: model.report() or {"trained": False}, tabular=False, ttl=0,
                 needs_module=False)


def _train_summary(report: dict) -> dict:
    return {
        "model_id": report.get("model_id"), "algorithm": report.get("algorithm"),
        "trained_at": report.get("trained_at"), "beats_market": report.get("beats_market"),
        "verdict": report.get("verdict"), "markets": (report.get("data") or {}).get("markets"),
        "rows": (report.get("data") or {}).get("rows"),
    }


@bp.post("/ml/train")
def api_ml_train():
    """Start (or attach to) a background training run. Poll /api/ml/status."""
    from src.ml import jobs, model

    data = body()
    try:
        max_markets = int(max(100, min(3000, num(data, "max_markets", 600))))
    except BadRequest as exc:
        return fail(exc, "ml")
    wanted = data.get("sources") or ["manifold", "polymarket"]
    if not isinstance(wanted, list) or not set(wanted) <= {"manifold", "polymarket"}:
        return fail(BadRequest("Sumber latih hanya manifold dan/atau polymarket."), "ml")
    try:
        registry.assert_readonly("ml")
        health.guard("ml")
    except HubError as exc:
        body_, status = fail(exc, "ml")
        return jsonify(body_), status
    job = jobs.start("train", model.train_from_sources, summarise=_train_summary,
                     sources=tuple(wanted), max_markets=max_markets)
    return jsonify(ok(job))


@bp.post("/ml/predict")
def api_ml_predict():
    """One market, freshly fetched, with model, direction and EV."""
    from src.ml import deadlines

    data = body()
    source = str(data.get("source") or "")
    market_id = str(data.get("market_id") or "").strip()
    if source not in _MARKET_SOURCES or not re.fullmatch(r"[A-Za-z0-9_x-]{1,200}", market_id):
        return fail(BadRequest("Kirim 'source' (polymarket/limitless/manifold) dan 'market_id' yang sah."), "ml")

    def run():
        try:
            return deadlines.one(source, market_id)
        except LookupError as exc:
            raise BadRequest(str(exc)) from None

    return serve("ml", ("predict", source, market_id), run, tabular=False, ttl=30)


@bp.get("/paper/summary")
def api_paper_summary():
    from src.ml import paper

    return serve("ml", "paper-stats", paper.stats, tabular=False, ttl=0)


@bp.get("/paper/ledger")
def api_paper_ledger():
    from src.ml import paper

    limit = max(10, min(2000, request.args.get("limit", 300, type=int)))
    return serve("ml", ("paper-ledger", limit), lambda: paper.ledger(limit), tabular=False, ttl=0)


@bp.post("/paper/scan")
def api_paper_scan():
    """Settle, predict and open paper positions — as a background job."""
    from src.ml import jobs, paper

    data = body()
    source = str(data.get("source") or "all")
    if source not in _SOURCE_SETS:
        return fail(BadRequest("Parameter 'source' harus all, polymarket, limitless, atau manifold."), "ml")
    try:
        days = num(data, "days", config.PAPER_MAX_DAYS)
        min_ev = num(data, "min_ev_pct", config.PAPER_MIN_EV * 100) / 100.0
        stake = num(data, "stake", config.PAPER_STAKE)
    except BadRequest as exc:
        return fail(exc, "ml")
    if not (0.05 <= days <= 30 and 0 <= min_ev <= 5 and 0 < stake <= 10_000):
        return fail(BadRequest("Rentang: days 0,05–30 · min_ev_pct 0–500 · stake 0–10.000."), "ml")
    job = jobs.start("scan", paper.scan, days=days, min_ev=min_ev, stake=stake,
                     sources=_SOURCE_SETS[source], with_news=bool(data.get("with_news", True)))
    return jsonify(ok(job))


@bp.post("/paper/settle")
def api_paper_settle():
    from src.ml import paper

    force = bool(body().get("force"))
    return serve("ml", None, lambda: paper.settle(force=force), tabular=False, ttl=0)


@bp.post("/paper/trade")
def api_paper_trade():
    """Open one paper position by hand. Virtual money only."""
    from src.ml import paper

    data = body()
    source = str(data.get("source") or "")
    market_id = str(data.get("market_id") or "").strip()
    side = str(data.get("side") or "").upper()
    try:
        stake = num(data, "stake", config.PAPER_STAKE)
    except BadRequest as exc:
        return fail(exc, "ml")
    if source not in _MARKET_SOURCES or not re.fullmatch(r"[A-Za-z0-9_x-]{1,200}", market_id):
        return fail(BadRequest("Kirim 'source' (polymarket/limitless/manifold) dan 'market_id' yang sah."), "ml")
    if side not in ("YES", "NO") or not 0 < stake <= 10_000:
        return fail(BadRequest("Sisi harus YES atau NO, dan stake di antara 0 dan 10.000."), "ml")

    def run():
        try:
            return paper.manual(source, market_id, side, stake, str(data.get("note") or ""))
        except (LookupError, ValueError) as exc:
            raise BadRequest(str(exc)) from None

    return serve("ml", None, run, tabular=False, ttl=0)


@bp.post("/paper/reset")
def api_paper_reset():
    from src.ml import paper

    if body().get("confirm") != "ARSIPKAN":
        return fail(BadRequest("Kirim confirm='ARSIPKAN' untuk memindahkan buku besar ke arsip.",
                               hint="Tidak ada yang dihapus: berkasnya dipindah ke data/ml/paper/arsip-*."), "ml")
    return serve("ml", None, paper.reset, tabular=False, ttl=0)


# ---------------------------------------------------------------------------
# Data publik (36 API) dan inventaris semua sumber
# ---------------------------------------------------------------------------


@bp.get("/datahub/catalog")
def api_datahub_catalog():
    from src import datahub
    from src.datahub.catalog import SECTOR_PAGES

    page = request.args.get("page")
    if page and page not in SECTOR_PAGES:
        body_, status = fail(BadRequest(f"Halaman '{page}' tidak dikenal.",
                                        hint="Pilihan: " + ", ".join(SECTOR_PAGES)), "datahub")
        return jsonify(body_), status
    payload = datahub.catalog()
    if page:
        payload["sources"] = [s for s in payload["sources"] if page in s["sectors"]]
    return jsonify(ok(payload))


@bp.get("/datahub/<code>")
def api_datahub(code: str):
    """Rows from one public data API, through its own TTL cache."""
    import time

    from src import datahub

    try:
        if not re.fullmatch(r"[a-z0-9-]{2,60}", code) or code not in datahub.BY_CODE:
            raise BadRequest(f"Sumber data '{code}' tidak dikenal.",
                             hint="Daftar kodenya ada di /api/datahub/catalog.")
        started = time.perf_counter()
        payload, cached, age = datahub.fetch(code)
    except Exception as exc:  # noqa: BLE001
        body_, status = fail(exc, code if code in datahub.BY_CODE else "datahub", debug=config.DEBUG)
        return jsonify(body_), status

    src = datahub.BY_CODE[code]
    rows = payload["rows"]
    notes = [f"Sebagian alamat gagal: {p['reason']}" for p in payload.get("partial") or []]
    meta = Meta(source=code, rows=len(rows), cached=cached, cache_age_s=age if cached else None,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                columns=list(rows[0].keys()) if rows else [], notes=notes)
    return jsonify(ok({**src.to_dict(), "rows": rows, "fetched_at": payload.get("fetched_at"),
                       "partial": payload.get("partial") or []}, meta))


@bp.get("/sources/all")
def api_sources_all():
    """Every source the app uses — integrations, news feeds, data APIs — with last known status."""
    from src import datahub
    from src.ml import jobs

    try:
        payload = datahub.inventory()
        payload["probe_job"] = jobs.get("probe")
        return jsonify(ok(payload))
    except Exception as exc:  # noqa: BLE001
        body_, status = fail(exc, "sources", debug=config.DEBUG)
        return jsonify(body_), status


@bp.get("/sources/probe")
def api_sources_probe_status():
    from src.ml import jobs

    return jsonify(ok(jobs.get("probe")))


@bp.post("/sources/probe")
def api_sources_probe():
    """Start (or attach to) a background check of every feed and data API."""
    from src import datahub
    from src.ml import jobs

    return jsonify(ok(jobs.start("probe", datahub.probe_all, summarise=datahub.probe_summary)))


# ---------------------------------------------------------------------------
# Pencarian mendalam — satu kotak untuk semuanya
# ---------------------------------------------------------------------------


@bp.get("/search")
def api_search():
    """Cari sekaligus di berita, simpanan berkas teks, pasar prediksi, dan katalog.

    Semua sumber dipanggil bersamaan dan yang gagal tidak menjatuhkan yang lain —
    hasilnya sebagian tetap lebih berguna daripada satu pesan galat.
    """
    import concurrent.futures

    from src import news
    from src.core import textstore
    from src.markets import Manifold

    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return fail(BadRequest("Kata kunci minimal dua huruf."), "search")

    def find_news():
        return news.headlines(query, 25)["news"]

    def find_stored():
        return textstore.search_records(query, 25)

    def find_markets():
        return Manifold().search(query, 10)

    def find_sources():
        term = query.lower()
        return [
            {
                "label": s.label, "category": s.category, "upstream": s.upstream,
                "notes": s.notes, "page": "/sources",
            }
            for s in registry.SOURCES
            if term in f"{s.label} {s.notes} {s.upstream} {s.category}".lower()
        ]

    bagian = {
        "news": find_news,
        "stored": find_stored,
        "markets": find_markets,
        "sources": find_sources,
    }
    hasil: dict[str, Any] = {}
    gagal: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): nama for nama, fn in bagian.items()}
        try:
            selesai = list(concurrent.futures.as_completed(futures, timeout=40))
        except concurrent.futures.TimeoutError:
            selesai = [f for f in futures if f.done()]
        for future in selesai:
            nama = futures[future]
            try:
                hasil[nama] = future.result()
            except Exception as exc:
                hasil[nama] = []
                gagal.append({
                    "section": nama,
                    "reason": getattr(exc, "message", None) or str(exc)[:140],
                })
    for nama in bagian:
        if nama not in hasil:
            hasil[nama] = []
            gagal.append({"section": nama, "reason": "terlalu lama menjawab"})

    return jsonify(ok({
        "query": query,
        "counts": {k: len(v) for k, v in hasil.items()},
        "total": sum(len(v) for v in hasil.values()),
        "failed": gagal,
        **hasil,
    }))
