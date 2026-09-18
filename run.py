"""Bloomberg Hub — command line.

    python run.py health                     probe every source from this connection
    python run.py sources                    print the risk catalog
    python run.py polygon --exchange         is the CTF exchange still filling orders?
    python run.py polygon --wallet 0x…       balance, type, verdict
    python run.py polygon --settlement       on-chain activity while the API is dark
    python run.py edge --prob 62 --price 55  model vs market: EV, Kelly, stake
    python run.py devig -o "-150,+130"       strip the bookmaker margin
    python run.py poisson --home 1.8 --away 1.2
    python run.py esports --schedule dota2
    python run.py tech --watch
    python run.py cache --clear

    python run.py ml --train --markets 600    latih model deadline dari pasar yang sudah selesai
    python run.py ml --status                 ringkasan model dan vonisnya
    python run.py deadlines --days 3          pasar bertenggat dekat + prediksi + EV
    python run.py paper --scan                settle, prediksi, buka posisi virtual
    python run.py paper --settle              cocokkan posisi dengan hasil sebenarnya
    python run.py paper --stats               win rate, ROI, Brier model vs pasar
    python run.py news --catalog              daftar 157 feed teruji
    python run.py news --digest --hours 24    cerita yang diliput banyak sumber
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The Windows console defaults to cp1252, which mangles the em-dashes and
# middots this CLI prints. Reconfiguring is cheaper than avoiding the glyphs.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

import config  # noqa: E402

console = Console()

STATUS_STYLE = {
    "live": "bold green", "ready": "bold green",
    "rate_limited": "yellow", "upstream_down": "yellow",
    "missing_dep": "yellow", "no_credential": "yellow", "not_cloned": "yellow",
    "blocked": "bold red",
}


def show_error(exc: BaseException) -> None:
    from src.core.errors import HubError

    if isinstance(exc, HubError):
        console.print(f"[bold red]{exc.code}[/] {exc.message}")
        if exc.hint:
            console.print(f"[dim]→ {exc.hint}[/]")
    else:
        console.print(f"[bold red]{type(exc).__name__}[/] {exc}")


def frame(data, limit: int = 25) -> None:
    """Print any tabular thing as a rich table."""
    from src.core.frames import to_records

    records, columns, truncated = to_records(data, limit=limit)
    if not records:
        console.print("[dim]Tidak ada baris.[/]")
        return
    table = Table(show_lines=False, header_style="bold")
    for column in columns[:12]:
        table.add_column(str(column), overflow="fold")
    for row in records:
        table.add_row(*[("" if row.get(c) is None else str(row.get(c))) for c in columns[:12]])
    console.print(table)
    if truncated:
        console.print(f"[dim]Dipotong di {limit} baris.[/]")


# --------------------------------------------------------------- commands


def cmd_health(args) -> None:
    from src.core import health

    console.print("[dim]Mengecek setiap sumber dari koneksi ini…[/]")
    report = health.check_all(deep=True, force=True)
    summary = report["summary"]

    table = Table(title=f"Kondisi sumber data — {summary['live']} siap · "
                        f"{summary['degraded']} perlu disiapkan · {summary['blocked']} terblokir")
    for column in ("Status", "Sumber", "Kategori", "Keterangan"):
        table.add_column(column, overflow="fold")
    for row in report["sources"]:
        table.add_row(
            f"[{STATUS_STYLE.get(row['status'], 'white')}]{row['label_status']}[/]",
            row["label"] + ("  [red]$[/]" if row["money_movement"] else ""),
            row.get("category_label", row["category"]),
            row["detail"] or "",
        )
    console.print(table)
    console.print(f"[dim]Selesai dalam {report['elapsed_ms'] / 1000:.1f} detik.[/]")


def cmd_sources(args) -> None:
    from src.core import registry

    catalog = registry.catalog()
    table = Table(title=f"Daftar sumber data — {catalog['counts']['money_movement']} dari "
                        f"{catalog['counts']['total']} bisa memindahkan dana")
    for column in ("Sumber", "Sifat", "Asal", "Catatan"):
        table.add_column(column, overflow="fold")
    for source in catalog["sources"]:
        sifat = ("[bold red]Bisa transaksi[/]" if source["money_movement"]
                 else "[green]Hitungan lokal[/]" if source["mode"] == "compute"
                 else "Baca saja")
        table.add_row(source["label"], sifat, source["upstream"],
                      source["notes"] or source["verdict"])
    console.print(table)


def cmd_polygon(args) -> None:
    from src.polymarket.onchain import CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, PolygonReader

    reader = PolygonReader(config.POLYGON_RPC)

    if args.wallet:
        result = reader.scan_wallet(args.wallet, deep=args.deep)
        balance = result["balance"]
        table = Table(title=f"Wallet {balance['address']}")
        table.add_column("Field"); table.add_column("Value", justify="right")
        for key in ("POL", "USDC.e", "USDC", "total_usd"):
            table.add_row(key, str(balance[key]))
        table.add_row("Jenis", result["contract"]["type"])
        table.add_row("Bytecode", f"{result['contract']['bytecode_bytes']} byte")
        table.add_row("Nonce", str(result["contract"]["nonce"]))
        table.add_row("Blok", str(result["block"]))
        console.print(table)
        console.print(Panel(result["verdict"], border_style="red" if not balance["funded"] else "green"))
        if args.deep:
            console.print("\n[bold]Arus USDC.e[/]")
            frame(result["usdc"]["flows"], 15)
            console.print("\n[bold]Perubahan posisi[/]")
            frame(result["positions"]["changes"], 15)
        return

    if args.settlement:
        result = reader.settlement_activity(args.blocks)
        table = Table(title=f"Penyelesaian on-chain — {result['blocks_scanned']} blok")
        table.add_column("Metrik"); table.add_column("Jumlah", justify="right")
        for key, value in result["summary"].items():
            table.add_row(key, f"{value:,}" if isinstance(value, int) else str(value))
        console.print(table)
        console.print(f"[dim]{result['caveat']}[/]")
        return

    if args.exchange:
        for address in (CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE):
            result = reader.check_exchange_activity(args.blocks, address)
            style = "green" if result["order_fills"] else "red"
            console.print(Panel(
                f"[bold]{result['label']}[/]  {result['exchange']}\n"
                f"{result['total_logs']} log · [bold]{result['order_fills']} OrderFilled[/]\n"
                f"event: {result['events'] or '(tidak ada)'}\n\n{result['interpretation']}",
                border_style=style, title=result["status"],
            ))
        return

    console.print(f"Blok terakhir: [bold]{reader.get_block_number():,}[/]  ({reader.rpc_url})")


def cmd_edge(args) -> None:
    from src.sports.betting import market_edge

    result = market_edge(
        args.prob / 100 if args.prob > 1 else args.prob,
        args.price / 100 if args.price > 1 else args.price,
        bankroll=args.bankroll, kelly_multiplier=args.kelly,
    )
    verdicts = {
        "BET": ("green", "Layak diambil"),
        "MARGINAL": ("yellow", "Tipis — pertimbangkan lagi"),
        "NO BET": ("red", "Sebaiknya dilewati"),
    }
    colour, kesimpulan = verdicts[result["verdict"]]
    table = Table(title="Perkiraan Anda dibanding harga pasar")
    table.add_column("Keterangan"); table.add_column("Nilai", justify="right")
    for label, key in (
        ("Perkiraan Anda", "model_prob"), ("Harga pasar", "market_price"),
        ("Selisih (poin)", "edge_pp"), ("Untung rata-rata per $100", "ev_per_100"),
        ("Porsi maksimal menurut Kelly", "kelly_full"), ("Porsi yang dipakai", "kelly_used"),
        ("Saran taruhan", "stake"), ("Titik impas", "breakeven_prob"),
    ):
        table.add_row(label, str(result[key]))
    console.print(table)
    note = result["note"]
    console.print(Panel(kesimpulan + (chr(10) + note if note else ""), border_style=colour))


def cmd_devig(args) -> None:
    from src.sports.betting import devig, parse_odds

    odds = [parse_odds(o.strip()) for o in args.odds.split(",")]
    result = devig(odds, method=args.method)
    table = Table(title=f"De-vig ({result['method']}) — overround {result['overround_pct']}")
    for column in ("Outcome", "Probabilitas", "Fair desimal", "Multiplicative", "Power", "Shin"):
        table.add_column(column, justify="right")
    for i, prob in enumerate(result["probabilities"]):
        table.add_row(
            f"#{i + 1}", f"{prob:.2%}", str(result["fair_decimal_odds"][i]),
            f"{result['all_methods']['multiplicative'][i]:.2%}",
            f"{result['all_methods']['power'][i]:.2%}",
            f"{result['all_methods']['shin'][i]:.2%}",
        )
    console.print(table)


def cmd_poisson(args) -> None:
    from src.sports.betting import match_probabilities

    result = match_probabilities(args.home, args.away, total_line=args.line)
    for title, rows in (("1X2", result["one_x_two"]), ("Total", result["totals"]),
                        ("BTTS", result["btts"])):
        table = Table(title=title)
        table.add_column("Outcome"); table.add_column("Peluang", justify="right")
        table.add_column("Fair desimal", justify="right")
        for row in rows:
            table.add_row(row["outcome"], row["percent"], str(row["fair_decimal"]))
        console.print(table)


def cmd_esports(args) -> None:
    from src.sports.esports import Dota2, EsportsSchedules

    if args.schedule:
        frame(EsportsSchedules().upcoming(args.schedule, args.limit), args.limit)
    elif args.teams:
        frame(Dota2().team_strength(args.limit), args.limit)
    else:
        frame(Dota2().hero_win_rates(args.limit), args.limit)


def cmd_tech(args) -> None:
    from src.tech.models import WATCHED_AUTHORS, ModelTracker

    tracker = ModelTracker()
    if args.watch:
        frame(tracker.watch_authors(WATCHED_AUTHORS, args.hours, 8), args.limit)
    elif args.search:
        frame(tracker.search_models(args.search, args.limit), args.limit)
    else:
        frame(tracker.new_models(args.hours, 200), args.limit)


def cmd_politics(args) -> None:
    from src.politics import ElectIndexForecast, GDELT

    if args.electindex:
        frame(ElectIndexForecast().list_files(), 50)
    else:
        frame(GDELT().articles(args.query, args.limit, args.timespan), args.limit)


def cmd_cache(args) -> None:
    from src.core import cache

    if args.clear:
        console.print(f"[green]{cache.clear(args.namespace)} entri dihapus.[/]")
    stats = cache.stats()
    table = Table(title="Cache")
    table.add_column("Metrik"); table.add_column("Nilai", justify="right")
    table.add_row("Entri memori", str(stats["memory_entries"]))
    table.add_row("Entri disk", str(stats["disk_entries"]))
    table.add_row("Ukuran disk", f"{stats['disk_bytes'] / 1024:.1f} KB")
    table.add_row("Lokasi", stats["dir"])
    console.print(table)



# ------------------------------------------------------- machine learning


def _progress():
    import time

    last = {"t": 0.0}

    def report(stage: str, done: int, total: int, note: str = "") -> None:
        if time.monotonic() - last["t"] < 5 and done != total:
            return
        last["t"] = time.monotonic()
        amount = f" {done}/{total}" if total else ""
        console.print(f"[dim]{stage}{amount} {note}[/]")

    return report


def cmd_ml(args) -> None:
    from src.ml import model

    if args.train:
        sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
        report = model.train_from_sources(sources=sources, max_markets=args.markets, progress=_progress(),
                                          offline=args.offline)
    else:
        report = model.report()
        if not report:
            console.print("[yellow]Model belum dilatih.[/] Jalankan: python run.py ml --train")
            return

    c = report["metrics"]["contested"]
    b = report["bootstrap"]
    table = Table(title=f"Model {report['model_id']}")
    table.add_column("Metrik")
    table.add_column("Nilai", justify="right")
    d = report["data"]
    table.add_row("Data", f"{d['rows']} snapshot · {d['markets']} pasar · {d['by_source']}")
    table.add_row("Diuji", f"{d['tested_markets']} pasar · {d['contested_rows']} snapshot 5–95%")
    table.add_row("Brier model / pasar", f"{c['brier_model']} / {c['brier_market']}")
    table.add_row("Selisih Brier (90%)", f"{b['mean']:+.5f} [{b['low']:+.5f}, {b['high']:+.5f}]")
    table.add_row("Arah benar", f"{(c['direction_accuracy'] or 0) * 100:.1f}%")
    console.print(table)
    console.print(Panel(report["verdict"], border_style="green" if report["beats_market"] else "yellow"))

    effect = Table(title="Apakah harga bergerak menuju hasilnya saat tenggat mepet?")
    for column in ("Jenis", "Sisa waktu", "Snapshot", "YES: harga → terjadi", "Bacaan YES",
                   "Favorit: harga → menang", "Bacaan favorit"):
        effect.add_column(column, overflow="fold")
    for row in report["deadline_effect"]:
        yes = (f"{row['avg_yes_price'] * 100:.1f}% → {row['yes_rate'] * 100:.1f}% "
               f"({row['yes_gap_pp']:+.1f} ± {row['yes_se_pp']:.1f})") if "avg_yes_price" in row else "—"
        effect.add_row(row.get("kind", "semua"), row["bucket"], str(row["n"]), yes, row.get("yes_reading", ""),
                       f"{row['avg_favorite_price'] * 100:.1f}% → {row['favorite_win_rate'] * 100:.1f}% "
                       f"({row['gap_pp']:+.1f} ± {row['se_pp']:.1f})", row["reading"])
    console.print(effect)


def cmd_deadlines(args) -> None:
    from src.ml import deadlines

    sources = ("polymarket", "limitless", "manifold") if args.source == "all" else (args.source,)
    data = deadlines.collect(days=args.days, sources=sources, limit=args.limit, query=args.query)
    for failure in data["failed"]:
        console.print(f"[yellow]{failure['source']}:[/] {failure['reason']}")
    table = Table(title=f"{data['count']} pasar dengan tenggat ≤ {args.days:g} hari")
    for column in ("Sisa", "Pasar", "Sumber", "YES", "Model", "Arah", "EV terbaik"):
        table.add_column(column, overflow="fold")
    for m in data["markets"][: args.limit]:
        model = m["model"]
        best = m["ev"].get("best")
        table.add_row(
            m["time_left"], m["question"][:90], m["source"], f"{m['p_yes'] * 100:.1f}%",
            f"{model['prob_yes'] * 100:.1f}%" if model.get("available") else "—",
            model.get("direction", "—") if model.get("available") else "—",
            f"{best['outcome']} {best['ev_model_pct']:+.1f}%" if best else "—",
        )
    console.print(table)
    console.print(f"[dim]{data['note']}[/]")


def cmd_paper(args) -> None:
    from src.ml import paper

    if args.scan:
        result = paper.scan(progress=_progress())
        if not result.get("ok"):
            console.print(f"[yellow]{result.get('reason')}[/]")
        else:
            console.print(f"Dipindai {result['scanned']} pasar · {result['predictions_logged']} prediksi baru · "
                          f"[bold]{result['opened_count']} posisi dibuka[/] · "
                          f"{result['settled']['settled_positions']} posisi diselesaikan")
            for failure in result["failed"]:
                console.print(f"[yellow]{failure['source']}:[/] {failure['reason']}")
            if result.get("concentration_note"):
                console.print(f"[yellow]Perhatian:[/] {result['concentration_note']}")
    elif args.settle:
        result = paper.settle(force=True)
        console.print(f"Diperiksa {result['checked_markets']} pasar · {result['settled_positions']} posisi dan "
                      f"{result['settled_predictions']} prediksi selesai")

    stats = paper.stats()
    overall = stats["overall"]
    table = Table(title="Paper test — uang virtual")
    for column in ("Kelompok", "Posisi", "Win rate", "Break-even", "Selisih", "ROI"):
        table.add_column(column, justify="right")

    def add(name: str, row: dict) -> None:
        if not row["n"]:
            table.add_row(name, "0", "—", "—", "—", "—")
            return
        table.add_row(name, str(row["n"]), f"{row['win_rate'] * 100:.1f}%", f"{row['avg_entry'] * 100:.1f}%",
                      f"{row['edge_pp']:+.1f} pp", f"{row['roi_pct']:+.1f}%")

    add("Semua", overall)
    for row in stats["by_bucket"]:
        add(row["group"], row)
    console.print(table)
    console.print(Panel(stats["verdict"]))
    console.print(f"[dim]Buku besar: {paper.POSITIONS} · ringkasan: {paper.SUMMARY_TXT}[/]")


def cmd_news(args) -> None:
    from src import news
    from src.news import sources

    if args.digest:
        from src.ml import newsml

        items = news.corpus()
        if len(items) < 40:
            items += news.headlines(limit=150)["news"]
        result = newsml.digest(items, args.hours)
        for story in result["stories"][:15]:
            console.print(f"[bold]{story['title']}[/]\n  [dim]{story['summary']}[/]")
        return

    catalog = sources.catalog()
    table = Table(title=f"{catalog['total']} sumber berita — diuji {catalog['probed_at']}")
    for column in ("Sumber", "Kategori", "Jenis", "Bahasa", "Gunanya"):
        table.add_column(column, overflow="fold")
    for feed in catalog["feeds"]:
        table.add_row(feed["name"], feed["category"], feed["kind"], feed["lang"], feed["good_for"])
    console.print(table)


# --------------------------------------------------------------- kesiapan

# Daftar hal yang bisa dilengkapi, diurutkan dari yang paling banyak membuka
# fitur. `unlocks` berisi endpoint yang benar-benar ada — bukan janji.
LANGKAH_SETUP = [
    {
        "id": "ml",
        "nama": "Latih model deadline (machine learning)",
        "file": "data/ml/models/deadline.joblib",
        "buka": "Halaman Deadlines dan Paper Test: peluang model, arah, EV model, dan paper test",
        "endpoint": 6,
        "waktu": "5–15 menit unduh riwayat harga",
        "biaya": "gratis (API publik Manifold)",
        "perintah": "python run.py ml --train --markets 600",
    },
    {
        "id": "anthropic",
        "nama": "Anthropic API key (opsional)",
        "env": ["ANTHROPIC_API_KEY"],
        "buka": "Tombol 'Minta pendapat Claude' — analisis berita per pasar",
        "endpoint": 1,
        "waktu": "3 menit",
        "biaya": "bayar per pemakaian, biasanya beberapa sen per analisis",
        "opsional": True,
        "tautan": "https://platform.claude.com (menu API keys)",
    },
    {
        "id": "omdb",
        "nama": "OMDb API key",
        "env": ["OMDB_API_KEY"],
        "buka": "Halaman Culture: kartu Films (cari film, detail box office)",
        "endpoint": 2,
        "waktu": "3 menit",
        "biaya": "gratis, 1.000 permintaan per hari",
        "tautan": "https://www.omdbapi.com/apikey.aspx",
    },
    {
        "id": "tmdb",
        "nama": "TMDb API key  [BERBAYAR]",
        "env": ["TMDB_API_KEY"],
        "buka": "Tidak membuka apa pun lagi — sisi film sudah pindah ke OMDb, sisi serial ke TVmaze",
        "endpoint": 0,
        "waktu": "—",
        "biaya": "USD 149 per bulan",
        "opsional": True,
        "berbayar": True,
        "tautan": "https://www.themoviedb.org/settings/api",
    },
    {
        "id": "spotify",
        "nama": "Spotify client ID + secret  [BERBAYAR]",
        "env": ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"],
        "buka": "Tidak membuka apa pun lagi — musik sudah pindah ke Deezer, yang tanpa kunci",
        "endpoint": 0,
        "waktu": "—",
        "biaya": "perlu langganan Spotify Premium",
        "opsional": True,
        "berbayar": True,
        "tautan": "https://developer.spotify.com/dashboard",
    },
    {
        "id": "electindex",
        "nama": "Data ElectIndex 2026",
        "folder": "electindex",
        "buka": "Halaman Politics: survei nasional, lembaga bermasalah, isi dataset",
        "endpoint": 4,
        "waktu": "10 menit unduh, 562 MB",
        "biaya": "gratis",
        "perintah": "git clone --depth 1 https://github.com/ElectIndex/26_us_forecast_data.git "
                    "data/repos/electindex",
    },
    {
        "id": "hf",
        "nama": "HuggingFace token",
        "env": ["HF_TOKEN"],
        "buka": "Halaman Tech sudah jalan tanpa ini — token hanya menaikkan batas permintaan",
        "endpoint": 0,
        "waktu": "2 menit",
        "biaya": "gratis",
        "opsional": True,
        "tautan": "https://huggingface.co/settings/tokens",
    },
]

# Hal yang sering dikira perlu, padahal belum tersambung ke halaman mana pun.
BELUM_TERPAKAI = [
    ("TMDb dan Spotify", "Keduanya sudah tidak gratis sejak 2026: TMDb meminta langganan "
                         "pengembang USD 149 per bulan, Spotify mewajibkan Spotify Premium. "
                         "Jangan bayar. Halaman Culture sudah dipindahkan ke Deezer (musik) "
                         "dan TVmaze (serial) yang tidak perlu kunci sama sekali, plus OMDb "
                         "untuk film yang kuncinya gratis."),
    ("Riot API key", "Kelas LoL ada di src/sports/esports.py, tapi belum ada endpoint "
                     "maupun halaman yang memanggilnya. Mengambil kuncinya sekarang tidak "
                     "membuka apa pun."),
    ("Data OpenElections", "Kelas pembacanya ada, tapi belum ada endpoint. Sama seperti di atas."),
    ("Repo backtesting", "Tidak perlu di-clone. Halaman Backtest memakai simulator lokal "
                         "di src/backtest yang sudah lengkap."),
    ("Kunci Polymarket", "Alamatnya diblokir dari koneksi ini. Kunci selengkap apa pun "
                         "tidak akan membuat pesanan sampai."),
]


def _status_langkah(langkah: dict) -> tuple[bool, str]:
    """Kembalikan (sudah_siap, keterangan)."""
    if "env" in langkah:
        kurang = [k for k in langkah["env"] if not getattr(config, k, "")]
        if kurang:
            return False, "belum diisi: " + ", ".join(kurang)
        return True, "sudah diisi"

    if "file" in langkah:
        path = Path(config.BASE_DIR) / langkah["file"]
        return (True, "sudah ada") if path.exists() else (False, f"belum ada {langkah['file']}")

    folder = Path(config.REPOS_DIR) / langkah["folder"]
    if not folder.exists():
        return False, f"belum diunduh ke {folder}"
    jumlah = sum(1 for _ in folder.iterdir())
    return True, f"sudah ada, {jumlah} berkas"


def cmd_setup(args) -> None:
    """Periksa apa yang masih perlu dilengkapi, dan tunjukkan langkahnya."""
    import shutil

    if args.buat_env:
        contoh = Path(config.BASE_DIR) / ".env.example"
        tujuan = Path(config.BASE_DIR) / ".env"
        if tujuan.exists():
            console.print(f"[yellow]{tujuan} sudah ada — tidak ditimpa.[/]")
        elif not contoh.exists():
            console.print("[red].env.example tidak ditemukan.[/]")
        else:
            shutil.copyfile(contoh, tujuan)
            console.print(f"[green]{tujuan} dibuat dari .env.example.[/] "
                          "Buka berkas itu dan isi kuncinya.")
        console.print()

    table = Table(title="Kesiapan Bloomberg Hub")
    for kolom in ("Status", "Yang dilengkapi", "Membuka", "Waktu"):
        table.add_column(kolom, overflow="fold")

    belum = []
    for langkah in LANGKAH_SETUP:
        siap, keterangan = _status_langkah(langkah)
        if siap:
            tanda = "[bold green]SIAP[/]"
        elif langkah.get("berbayar"):
            tanda = "[red]BERBAYAR[/]"
        elif langkah.get("opsional"):
            tanda = "[dim]opsional[/]"
        else:
            tanda = "[yellow]PERLU[/]"
            belum.append(langkah)
        nama = langkah["nama"]
        if not siap:
            nama += f"\n[dim]{keterangan}[/]"
        table.add_row(tanda, nama, langkah["buka"], langkah["waktu"])
    console.print(table)

    if not (Path(config.BASE_DIR) / ".env").exists():
        console.print()
        console.print(Panel(
            "Berkas [bold].env[/] belum ada. Buat dulu dengan:\n\n"
            "    [bold]python run.py setup --buat-env[/]\n\n"
            "Lalu isi kuncinya di berkas itu.",
            border_style="yellow", title="Langkah pertama",
        ))

    if belum:
        console.print()
        console.print("[bold]Langkah berikutnya, urut dari yang paling berguna:[/]")
        for i, langkah in enumerate(belum, 1):
            console.print(f"\n  [bold]{i}. {langkah['nama']}[/] — {langkah['waktu']}, {langkah['biaya']}")
            console.print(f"     Membuka {langkah['endpoint']} endpoint: {langkah['buka']}")
            if "tautan" in langkah:
                console.print(f"     Buka: [blue]{langkah['tautan']}[/]")
                console.print(f"     Lalu isi {', '.join(langkah['env'])} di berkas .env")
            if "perintah" in langkah:
                console.print(f"     Jalankan: [bold]{langkah['perintah']}[/]")
        console.print("\n[dim]Langkah rincinya ada di docs/SETUP.md[/]")
    else:
        console.print("\n[bold green]Semua yang berguna sudah lengkap.[/]")

    console.print()
    table2 = Table(title="Tidak perlu diambil sekarang")
    table2.add_column("Hal"); table2.add_column("Alasan", overflow="fold")
    for nama, alasan in BELUM_TERPAKAI:
        table2.add_row(nama, alasan)
    console.print(table2)

# ------------------------------------------------------------------ parse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bloomberg Hub — intelijen prediction market",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health", help="Probe semua sumber dari koneksi ini")
    sub.add_parser("sources", help="Cetak katalog risiko")

    p = sub.add_parser("polygon", help="Baca Polygon on-chain")
    p.add_argument("--wallet", help="Alamat untuk dipindai")
    p.add_argument("--deep", action="store_true", help="Sertakan riwayat aktivitas")
    p.add_argument("--exchange", action="store_true", help="Cek kedua kontrak exchange")
    p.add_argument("--settlement", action="store_true", help="Aktivitas lapisan token")
    p.add_argument("--blocks", type=int, default=200)

    p = sub.add_parser("edge", help="Model vs harga pasar")
    p.add_argument("--prob", type=float, required=True, help="Probabilitas model (62 atau 0.62)")
    p.add_argument("--price", type=float, required=True, help="Harga pasar (55 atau 0.55)")
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--kelly", type=float, default=0.5)

    p = sub.add_parser("devig", help="Buang margin bandar")
    p.add_argument("-o", "--odds", required=True, help='Contoh: "-150,+130"')
    p.add_argument("--method", default="power", choices=["power", "shin", "multiplicative"])

    p = sub.add_parser("poisson", help="Model pertandingan dari xG")
    p.add_argument("--home", type=float, required=True)
    p.add_argument("--away", type=float, required=True)
    p.add_argument("--line", type=float, default=2.5)

    p = sub.add_parser("esports", help="Data esports")
    p.add_argument("--schedule", help="dota2 | counterstrike | leagueoflegends | valorant")
    p.add_argument("--teams", action="store_true")
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("tech", help="Rilis model")
    p.add_argument("--watch", action="store_true", help="Hanya lab yang dipantau")
    p.add_argument("--search", help="Cari model")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("politics", help="GDELT / ElectIndex")
    p.add_argument("--query", default="midterm election")
    p.add_argument("--timespan", default="7d")
    p.add_argument("--electindex", action="store_true")
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("setup", help="Periksa apa yang masih perlu dilengkapi")
    p.add_argument("--buat-env", action="store_true", dest="buat_env",
                   help="Salin .env.example jadi .env kalau belum ada")

    p = sub.add_parser("cache", help="Statistik / bersihkan cache")
    p.add_argument("--clear", action="store_true")
    p.add_argument("--namespace")

    p = sub.add_parser("ml", help="Model machine learning untuk pasar bertenggat")
    p.add_argument("--train", action="store_true", help="Ambil riwayat harga lalu latih ulang")
    p.add_argument("--status", action="store_true", help="Tampilkan laporan model terakhir")
    p.add_argument("--markets", type=int, default=600, help="Jumlah pasar selesai yang diambil")
    p.add_argument("--sources", default="manifold,polymarket")
    p.add_argument("--offline", action="store_true",
                   help="Tanpa jaringan: latih ulang dari riwayat yang sudah tersimpan di data/ml/raw")

    p = sub.add_parser("deadlines", help="Pasar dengan tenggat terdekat")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--source", default="all", choices=["all", "polymarket", "limitless", "manifold"])
    p.add_argument("--query")
    p.add_argument("--limit", type=int, default=40)

    p = sub.add_parser("paper", help="Paper test: scan, settle, statistik")
    p.add_argument("--scan", action="store_true")
    p.add_argument("--settle", action="store_true")
    p.add_argument("--stats", action="store_true")

    p = sub.add_parser("news", help="Katalog sumber berita dan ringkasan cerita")
    p.add_argument("--catalog", action="store_true")
    p.add_argument("--digest", action="store_true")
    p.add_argument("--hours", type=float, default=24)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    handlers = {
        "health": cmd_health, "sources": cmd_sources, "polygon": cmd_polygon,
        "edge": cmd_edge, "devig": cmd_devig, "poisson": cmd_poisson,
        "esports": cmd_esports, "tech": cmd_tech, "politics": cmd_politics,
        "cache": cmd_cache, "setup": cmd_setup,
        "ml": cmd_ml, "deadlines": cmd_deadlines, "paper": cmd_paper, "news": cmd_news,
    }
    try:
        handlers[args.command](args)
    except KeyboardInterrupt:
        console.print("[dim]Dibatalkan.[/]")
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        show_error(exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
