"""Rute halaman. Template menerima navigasi dan katalog; datanya lewat API."""
from __future__ import annotations

from flask import Blueprint, render_template

import config
from src.core import registry

bp = Blueprint("pages", __name__)

# `icon` adalah atribut `d` sebuah <path>. Beberapa sub-path digabung dalam satu
# string supaya cukup satu elemen per ikon.
NAV = [
    {
        "id": "home", "path": "/", "label": "Overview",
        "hint": "Kondisi semua sumber data",
        "icon": "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
    },
    {
        "id": "edge", "path": "/edge", "label": "Edge",
        "hint": "Bandingkan perkiraan Anda dengan harga pasar",
        "icon": "M12 22c5.5 0 10-4.5 10-10S17.5 2 12 2 2 6.5 2 12s4.5 10 10 10z"
                "M12 18a6 6 0 100-12 6 6 0 000 12zM12 14a2 2 0 100-4 2 2 0 000 4z",
    },
    {
        "id": "news", "path": "/news", "label": "News & Markets",
        "hint": "Kabar terkini dan buku pesanan pasar prediksi",
        "icon": "M4 22h16a2 2 0 002-2V4a2 2 0 00-2-2H8a2 2 0 00-2 2v16a2 2 0 01-2 2zm0 0a2 2 0 01-2-2v-9h4"
                "M18 14h-8M15 18h-5M10 6h8v4h-8z",
    },
    {
        "id": "deadlines", "path": "/deadlines", "label": "Deadlines",
        "hint": "Pasar dengan tenggat terdekat — prediksi ML, arah, EV, dan IEV",
        "icon": "M12 22c5.5 0 10-4.5 10-10S17.5 2 12 2 2 6.5 2 12s4.5 10 10 10zM12 6v6l4 2M5 3L2 6M22 6l-3-3",
    },
    {
        "id": "paper", "path": "/paper", "label": "Paper Test",
        "hint": "Uji model dengan uang virtual — win rate, akurasi, dan kalibrasi",
        "icon": "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11",
    },
    {
        "id": "polygon", "path": "/on-chain", "label": "On-Chain",
        "hint": "Saldo dompet dan aktivitas pasar di Polygon",
        "icon": "M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"
                "M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71",
    },
    {
        "id": "sports", "path": "/sports", "label": "Sports",
        "hint": "Sepak bola, NBA, dan NFL",
        "icon": "M22 12h-4l-3 9L9 3l-3 9H2",
    },
    {
        "id": "esports", "path": "/esports", "label": "Esports",
        "hint": "Jadwal turnamen dan kekuatan tim",
        "icon": "M6 11h4M8 9v4M15 12h.01M18 10h.01M17 5H7a5 5 0 00-5 5v4a5 5 0 005 5h10a5 5 0 005-5v-4a5 5 0 00-5-5z",
    },
    {
        "id": "politics", "path": "/politics", "label": "Politics",
        "hint": "Pemberitaan global dan data pemilu",
        "icon": "M3 21h18M4 21V10l8-6 8 6v11M9 21v-6h6v6",
    },
    {
        "id": "tech", "path": "/tech", "label": "Tech",
        "hint": "Rilis model AI terbaru",
        "icon": "M4 4h16v16H4zM9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3",
    },
    {
        "id": "culture", "path": "/culture", "label": "Culture",
        "hint": "Musik, serial, dan film",
        "icon": "M2 4h20v16H2zM7 4v16M17 4v16M2 9h5M2 15h5M17 9h5M17 15h5",
    },
    {
        "id": "backtest", "path": "/backtest", "label": "Backtest",
        "hint": "Ukur ketepatan perkiraan Anda",
        "icon": "M3 3v18h18M7 14l4-4 3 3 5-6",
    },
    {
        "id": "sources", "path": "/sources", "label": "Sources",
        "hint": "Daftar lengkap sumber dan tingkat risikonya",
        "icon": "M12 8c4.97 0 9-1.34 9-3s-4.03-3-9-3-9 1.34-9 3 4.03 3 9 3z"
                "M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3",
    },
]


def _render(template: str, page: str, **kwargs):
    return render_template(
        template, page=page, nav=NAV,
        trading_enabled=config.ENABLE_TRADING, **kwargs,
    )


@bp.get("/")
def index():
    from src import datahub

    return _render("index.html", "home", counts=datahub.counts())


@bp.get("/edge")
def edge():
    return _render("edge.html", "edge")


@bp.get("/news")
def news():
    return _render("news.html", "news")


@bp.get("/deadlines")
def deadlines():
    return _render("deadlines.html", "deadlines")


@bp.get("/paper")
def paper():
    return _render("paper.html", "paper")


@bp.get("/on-chain")
def polygon():
    return _render("polygon.html", "polygon")


@bp.get("/sports")
def sports():
    return _render("sports.html", "sports")


@bp.get("/esports")
def esports():
    return _render("esports.html", "esports")


@bp.get("/politics")
def politics():
    return _render("politics.html", "politics")


@bp.get("/tech")
def tech():
    return _render("tech.html", "tech")


@bp.get("/culture")
def culture():
    return _render("culture.html", "culture")


@bp.get("/backtest")
def backtest():
    return _render("backtest.html", "backtest")


@bp.get("/sources")
def sources():
    from src import datahub

    return _render(
        "sources.html", "sources",
        catalog=registry.catalog(), categories=registry.CATEGORIES, counts=datahub.counts(),
    )
