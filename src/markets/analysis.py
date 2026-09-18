"""Derived reading of a Polymarket market: how one-sided it is, whether that
lean is fresh or settled, what acting on it costs, and what would have to be
true for the trade to be worth taking.

Everything here is arithmetic over numbers Gamma already publishes — price,
price change over 1h/24h/1w, spread, liquidity, 24h volume, resolution date.
Nothing is fetched that the caller did not already have.

The one thing this module refuses to do is say *why* a price moved. That needs a
causal model this project does not have, and a keyword match between a headline
and a market question is not one. So the vocabulary is kept honest:

  * **lean** — which side the money is on, and by how much. Measured.
  * **character** — whether that lean is long-standing or was repriced in the
    last hour, and whether it sits on a deep book or a thin one. Measured.
  * **cost** — spread and depth, i.e. what you give up entering. Measured.
  * **breakeven** — the belief you would need for the trade to be positive EV.
    Arithmetic, not a forecast.
  * **candidate causes** — supplied by the news panel, and labelled as
    candidates. Never asserted here.

A market that is 87% with no movement in a week on a deep book, and a market
that is 87% after a 20pp jump in one hour on a thin one, are the same price and
completely different situations. Telling them apart is what this file is for.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Price bands for describing how one-sided a market is. Chosen so the labels
# match how these markets actually trade: below 55% is noise, above 90% the
# remaining upside is too thin to be interesting on its own.
BANDS = (
    (0.90, "sangat dominan"),
    (0.70, "dominan"),
    (0.55, "condong"),
    (0.0, "seimbang"),
)

# A spread wider than this eats most of the edge a retail-sized view can have.
WIDE_SPREAD_PP = 3.0
# Below this, one order moves the price, and the quoted probability is soft.
THIN_LIQUIDITY_USD = 5_000
# A move this big in an hour is a repricing, not drift.
FRESH_MOVE_PP = 5.0


def _f(value: Any) -> float | None:
    """Gamma mixes strings, numbers and nulls in the same field."""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _pp(value: Any) -> float | None:
    """Gamma reports price changes as a probability delta (0.05 = 5 points)."""
    raw = _f(value)
    return round(raw * 100, 2) if raw is not None else None


def _days_until(iso: Any) -> float | None:
    if not iso:
        return None
    try:
        end = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return round((end - datetime.now(timezone.utc)).total_seconds() / 86400, 2)


def _band(price: float) -> str:
    for floor, label in BANDS:
        if price >= floor:
            return label
    return "seimbang"


def lean(market: dict) -> dict:
    """Which side the price is on, and how decisively."""
    outcomes = [o for o in (market.get("outcomes") or [])
                if isinstance(_f(o.get("price")), float)]
    if not outcomes:
        return {"known": False,
                "reason": "Harga outcome tidak tersedia, jadi arah pasar tidak dapat dibaca."}

    ranked = sorted(outcomes, key=lambda o: -_f(o["price"]))
    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    price = _f(top["price"])
    margin = round((price - _f(runner["price"])) * 100, 2) if runner else None

    tied = margin is not None and abs(margin) < 0.5
    return {
        "known": True,
        "outcome": None if tied else top["outcome"],
        "price": round(price, 4),
        "pct": f"{price * 100:.1f}%",
        "margin_pp": margin,
        "runner_up": runner["outcome"] if runner else None,
        "band": "seimbang" if tied else _band(price),
        "outcomes_count": len(ranked),
        "implied_odds": (round(1 / price, 2) if price > 0 else None),
    }


def character(market: dict) -> dict:
    """Is the lean settled or fresh, and does it sit on a real book?

    This is the part that separates two markets showing the same number.
    """
    h1, h24, w1, mo1 = (_pp(market.get(k)) for k in
                        ("change1h", "change24h", "change1w", "change1mo"))
    spread_pp = _pp(market.get("spread"))
    liquidity = _f(market.get("liquidity"))
    volume24h = _f(market.get("volume24h"))

    moves = [m for m in (h1, h24, w1) if m is not None]
    fresh = h1 is not None and abs(h1) >= FRESH_MOVE_PP
    quiet = bool(moves) and all(abs(m) < 1.0 for m in moves)

    if fresh:
        stability = "repricing baru"
        stability_why = f"Harga bergerak {h1:+.2f} poin dalam satu jam terakhir."
    elif quiet:
        stability = "mapan"
        stability_why = "Tidak ada pergerakan berarti pada 1 jam, 24 jam, maupun sepekan."
    elif h24 is not None and abs(h24) >= FRESH_MOVE_PP:
        stability = "bergerak"
        stability_why = f"Harga bergerak {h24:+.2f} poin dalam 24 jam."
    elif moves:
        stability = "tenang"
        stability_why = "Pergerakan ada tetapi kecil pada semua rentang yang tersedia."
    else:
        stability = "tidak diketahui"
        stability_why = "Data perubahan harga tidak dikirim sumber untuk pasar ini."

    thin = liquidity is not None and liquidity < THIN_LIQUIDITY_USD
    wide = spread_pp is not None and spread_pp > WIDE_SPREAD_PP

    if liquidity is None:
        depth, depth_why = "tidak diketahui", "Likuiditas tidak dikirim sumber."
    elif thin:
        depth = "tipis"
        depth_why = (f"Likuiditas ${liquidity:,.0f} — satu pesanan besar bisa "
                     f"menggeser harganya, jadi persentase di atas belum kokoh.")
    else:
        depth = "tebal"
        depth_why = f"Likuiditas ${liquidity:,.0f} menopang harga yang ditampilkan."

    return {
        "change_1h_pp": h1, "change_24h_pp": h24,
        "change_1w_pp": w1, "change_1mo_pp": mo1,
        "spread_pp": spread_pp,
        "liquidity": liquidity,
        "volume24h": volume24h,
        "stability": stability, "stability_why": stability_why,
        "depth": depth, "depth_why": depth_why,
        "thin": thin, "wide_spread": wide, "fresh_move": fresh,
    }


def economics(market: dict, side: dict) -> dict:
    """What buying the leading side costs, and the belief it would require.

    No forecast is produced. `breakeven_pct` is the probability at which the
    trade is exactly break-even — your own estimate has to beat it, and this
    module has no opinion on what your estimate should be.
    """
    price = side.get("price")
    if not price or price <= 0 or price >= 1:
        return {"known": False,
                "reason": "Harga di luar rentang yang bisa dihitung (0–1)."}

    ask = _f(market.get("best_ask")) or price
    spread_pp = _pp(market.get("spread")) or 0.0
    entry = min(0.999, max(ask, price))

    payout = round((1 - entry) / entry, 4)          # profit per 1 unit staked
    days = _days_until(market.get("end_date"))
    # Annualising a binary that settles in four days produces numbers like
    # "1.792% per year" — arithmetically right, and useless as a comparison,
    # because you cannot repeat the trade 90 times. Only quote it when the
    # holding period is long enough for the rate to mean anything.
    annualised = None
    if days and days >= 7:
        annualised = round(payout * (365 / days) * 100, 1)

    return {
        "known": True,
        "entry_price": round(entry, 4),
        "entry_pct": f"{entry * 100:.1f}%",
        "breakeven_pct": f"{entry * 100:.1f}%",
        "profit_per_unit": payout,
        "profit_pct_if_right": round(payout * 100, 1),
        "loss_pct_if_wrong": 100.0,
        "round_trip_cost_pp": round(spread_pp, 2),
        "days_to_resolution": days,
        "annualised_pct_if_right": annualised,
        "note": (
            "Untung dihitung jika outcome ini menang dan dipegang sampai "
            "penyelesaian; kalah berarti kehilangan seluruh taruhan. Biaya "
            "bolak-balik memakai spread yang dikirim sumber, belum termasuk "
            "slippage untuk ukuran besar atau biaya penarikan."
        ),
    }


def strategy(side: dict, traits: dict, econ: dict) -> dict:
    """A conditional plan built from the measured numbers above.

    Never "buy this". The output is a stance plus the conditions that would have
    to hold, because the one input that decides a bet — your own probability
    estimate — is the one input this program does not have.
    """
    points: list[str] = []
    stance = "periksa"

    if not side.get("known") or not econ.get("known"):
        return {"stance": "tunggu", "headline": "Data harga belum cukup untuk menyusun rencana.",
                "points": ["Tanpa harga outcome yang sah, tidak ada yang bisa dihitung."],
                "next_step": "Muat ulang atau buka pasarnya langsung."}

    price = side["price"]
    entry = econ["entry_price"]
    days = econ.get("days_to_resolution")

    if traits["fresh_move"]:
        stance = "tunggu"
        points.append(
            f"Harga baru saja dinilai ulang {traits['change_1h_pp']:+.2f} poin dalam sejam. "
            "Masuk di tengah repricing berarti membeli dari orang yang bergerak lebih dulu; "
            "tunggu buku tenang atau sampai Anda tahu kabar apa yang mendahuluinya."
        )

    if traits["thin"]:
        points.append(
            "Bukunya tipis, jadi persentase di layar lebih lemah daripada kelihatannya dan "
            "ukuran posisi harus kecil. Pakai limit order; market order akan mengisi diri sendiri."
        )
        if stance == "periksa":
            stance = "hati-hati"

    if traits["wide_spread"]:
        points.append(
            f"Spread {traits['spread_pp']:.2f} poin sudah memakan sebagian besar keunggulan "
            "yang realistis. Perkiraan Anda harus melampaui harga beli, bukan harga tengah."
        )
        if stance == "periksa":
            stance = "hati-hati"

    if price >= 0.90:
        upside = econ["profit_pct_if_right"]
        line = (f"Sisi ini sudah dihargai {side['pct']}. Menang hanya menambah {upside:.1f}%, "
                "sementara salah menghapus seluruh taruhan")
        if days and days > 0:
            line += f", dan modalnya terkunci {days:.0f} hari"
        points.append(line + ". Membeli favorit semata-mata karena ia favorit bukan alasan.")
        if stance == "periksa":
            stance = "hati-hati"

    if price <= 0.15:
        points.append(
            f"Sisi ini dihargai {side['pct']} — pasar menganggapnya tidak mungkin. Longshot "
            "hanya masuk akal kalau Anda punya alasan spesifik yang belum ada di harga, "
            "bukan karena hadiahnya besar."
        )

    if traits["stability"] == "mapan" and not traits["thin"]:
        points.append(
            "Harga mapan di buku yang tebal: ini konsensus yang sudah matang, bukan reaksi "
            "sesaat. Melawannya butuh informasi yang benar-benar belum masuk harga."
        )

    if days is not None and 0 < days <= 2:
        points.append(
            f"Tinggal {days:.1f} hari ke penyelesaian. Harga akan makin dikuasai kabar akhir, "
            "dan kesempatan keluar menyempit."
        )
    elif days is not None and days < 0:
        stance = "hindari"
        points.append("Tanggal penyelesaian yang dikirim sumber sudah lewat; periksa status pasar.")

    if not points:
        points.append(
            "Tidak ada tanda bahaya struktural: spread wajar, buku memadai, harga tidak ekstrem. "
            "Yang menentukan tinggal apakah perkiraan Anda berbeda dari harga."
        )

    headline = {
        "periksa": "Layak diperiksa — tidak ada hambatan struktural.",
        "hati-hati": "Bisa dilihat, tapi strukturnya menuntut kehati-hatian.",
        "tunggu": "Tunggu dulu — kondisinya sedang tidak menguntungkan pemasuk baru.",
        "hindari": "Jangan dulu.",
    }[stance]

    return {
        "stance": stance,
        "headline": headline,
        "points": points,
        "breakeven": (
            f"Masuk di {econ['entry_pct']} baru impas kalau peluang sebenarnya juga "
            f"{econ['breakeven_pct']}. Di bawah itu Anda rugi rata-rata, seberapa pun "
            "yakinnya terasa."
        ),
        "next_step": (
            "Susun perkiraan peluang Anda sendiri lebih dulu, lalu masukkan ke halaman Edge "
            "untuk EV dan ukuran posisi. Kalau Anda tidak punya perkiraan yang berdiri "
            "sendiri, tidak ada taruhan di sini — hanya menebak harga orang lain."
        ),
    }


def analyse(market: dict, model: dict | None = None, cost_pp: float = 0.0) -> dict:
    """Full derived reading for one market.

    `model` is an optional `src.ml.model.predict_market` result; with it, the EV
    block carries the model's probability next to the market's own. `cost_pp` is
    the entry cost assumed when the source publishes no bid/ask (Manifold).
    """
    from src.markets.ev import expected_value

    side = lean(market)
    traits = character(market)
    econ = economics(market, side) if side.get("known") else {"known": False}
    plan = strategy(side, traits, econ)
    ev = expected_value(market, model, cost_pp=cost_pp)

    if side.get("known"):
        if side["outcome"] is None:
            reading = ("Dua harga teratas praktis seimbang — pasar belum memilih sisi.")
        else:
            reading = (
                f"{side['outcome']} dihargai {side['pct']}"
                + (f", unggul {side['margin_pp']:.1f} poin dari {side['runner_up']}"
                   if side.get("margin_pp") is not None and side.get("runner_up") else "")
                + f". Itu {side['band']}, dan sifatnya {traits['stability']} "
                + f"di buku yang {traits['depth']}."
            )
    else:
        reading = side.get("reason", "Arah pasar tidak dapat dibaca.")

    return {
        "lean": side,
        "character": traits,
        "economics": econ,
        "ev": ev,
        "strategy": plan,
        "reading": reading,
        "why_note": (
            "Ini menjelaskan seberapa kuat dan seberapa baru kecondongannya, bukan "
            "penyebabnya. Harga adalah konsensus transaksi — bukan jumlah orang, bukan "
            "kebenaran. Berita di panel sebelah adalah kandidat penyebab yang masih harus "
            "Anda periksa sendiri; kecocokan kata kunci bukan bukti sebab-akibat."
        ),
    }


def sector_brief(markets: list[dict], sector: str = "") -> dict:
    """One paragraph of structure for a whole sector: where the money is, what
    moved, and what is still genuinely contested."""
    rows = []
    for market in markets or []:
        side = lean(market)
        if not side.get("known"):
            continue
        rows.append({
            "question": market.get("question"),
            "url": market.get("url"),
            "id": market.get("id"),
            "outcome": side["outcome"], "price": side["price"], "pct": side["pct"],
            "band": side["band"],
            "volume24h": _f(market.get("volume24h")) or 0.0,
            "liquidity": _f(market.get("liquidity")) or 0.0,
            "change_24h_pp": _pp(market.get("change24h")),
            "days": _days_until(market.get("end_date")),
        })

    if not rows:
        return {"known": False, "sector": sector, "markets": 0,
                "note": "Belum ada pasar dengan harga yang bisa dibaca di kategori ini."}

    total_volume = sum(r["volume24h"] for r in rows)
    movers = [r for r in rows if r["change_24h_pp"] is not None]
    biggest = max(movers, key=lambda r: abs(r["change_24h_pp"])) if movers else None
    contested = min(rows, key=lambda r: abs(r["price"] - 0.5))
    settled = max(rows, key=lambda r: r["price"])
    busiest = max(rows, key=lambda r: r["volume24h"])
    soon = [r for r in rows if r["days"] is not None and r["days"] > 0]
    soonest = min(soon, key=lambda r: r["days"]) if soon else None

    concentration = (round(busiest["volume24h"] / total_volume * 100, 1)
                     if total_volume > 0 else None)

    # EV across the sector — only present when the caller attached analyses.
    ev_rows, entry_costs, modelled = [], [], 0
    for market in markets or []:
        ev = (market.get("analysis") or {}).get("ev") or {}
        if not ev.get("known"):
            continue
        costs = [r["ev_consensus_pct"] for r in ev["rows"] if r.get("ev_consensus_pct") is not None]
        if costs:
            entry_costs.append(max(costs))
        if (ev.get("model") or {}).get("available"):
            modelled += 1
        best = ev.get("best")
        if best:
            ev_rows.append({
                "question": market.get("question"), "url": market.get("url"), "id": market.get("id"),
                "outcome": best["outcome"], "pct": f"{best['ask'] * 100:.1f}%",
                "ev_pct": best["ev_model_pct"],
                "beats_market": bool((ev.get("model") or {}).get("beats_market")),
            })
    best_ev = max(ev_rows, key=lambda r: r["ev_pct"]) if ev_rows else None

    return {
        "known": True,
        "sector": sector,
        "markets": len(rows),
        "total_volume24h": round(total_volume, 2),
        "concentration_pct": concentration,
        "busiest": busiest,
        "biggest_mover": biggest,
        "most_contested": contested,
        "most_settled": settled,
        "resolving_soonest": soonest,
        "best_ev": best_ev,
        "positive_ev_markets": len(ev_rows),
        "modelled_markets": modelled,
        "avg_entry_cost_pct": round(sum(entry_costs) / len(entry_costs), 2) if entry_costs else None,
        "note": (
            "Dihitung dari pasar yang diambil pada permintaan ini saja — kategori diurutkan "
            "menurut volume 24 jam, jadi ini potongan teratas, bukan seluruh kategori. "
            "Volume 24 jam mengukur perputaran, bukan jumlah orang."
        ),
    }
