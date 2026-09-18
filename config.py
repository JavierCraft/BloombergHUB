"""Configuration. Reads .env; never prints or logs a secret."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
# Semua yang ditulis aplikasi ada di bawah satu folder. Hosting tanpa-server
# (Vercel) memberi sistem berkas hanya-baca kecuali /tmp, jadi foldernya harus
# bisa dipindah — dan pembuatannya tidak boleh menggagalkan impor.
DATA_DIR = Path(os.getenv("BH_DATA_DIR") or (BASE_DIR / "data"))
REPOS_DIR = DATA_DIR / "repos"
CACHE_DIR = DATA_DIR / "cache"


def _ensure(folder: Path) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Sistem berkas hanya-baca: cache tetap bekerja di memori dan setiap
        # penulis ke disk sudah menangani kegagalannya sendiri.
        pass


for _folder in (DATA_DIR, REPOS_DIR, CACHE_DIR):
    _ensure(_folder)


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


# --- The money gate --------------------------------------------------------
# Off by default, and deliberately awkward to switch on. The audit's rule:
# exactly one package holds keys, and it does nothing unless you say so.
ENABLE_TRADING = _flag("BH_ENABLE_TRADING", False)

# --- Polymarket (blocked from this ISP — reachable only via VPS/VPN) --------
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
POLY_SECRET = os.getenv("POLY_SECRET", "")
POLY_PASSPHRASE = os.getenv("POLY_PASSPHRASE", "")
POLY_WALLET_ADDRESS = os.getenv("POLY_WALLET_ADDRESS", "")
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_HOST = os.getenv("POLY_HOST", "https://clob.polymarket.com")

# --- Polygon RPC (works from here) -----------------------------------------
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon.publicnode.com")

# --- Culture ---------------------------------------------------------------
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID", "")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET", "")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
# OMDb menggantikan sisi film TMDb: kuncinya gratis, 1.000 permintaan/hari.
OMDB_API_KEY = os.getenv("OMDB_API_KEY", "")

# --- Tech ------------------------------------------------------------------
HF_TOKEN = os.getenv("HF_TOKEN", "")

# --- Esports ---------------------------------------------------------------
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")

# --- StatsBomb (paid competitions only; open data needs nothing) -----------
SB_USERNAME = os.getenv("SB_USERNAME", "")
SB_PASSWORD = os.getenv("SB_PASSWORD", "")

# --- Web app ---------------------------------------------------------------
HOST = os.getenv("BH_HOST", "127.0.0.1")
PORT = int(os.getenv("BH_PORT", "5000"))
DEBUG = _flag("BH_DEBUG", False)
SECRET_KEY = os.getenv("BH_SECRET_KEY", "bloomberg-hub-local")

# Default cache TTL in seconds; each source can override it in the registry.
CACHE_TTL = int(os.getenv("BH_CACHE_TTL", "900"))

# --- Machine learning & paper test -----------------------------------------
ML_DIR = DATA_DIR / "ml"
_ensure(ML_DIR)


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# Virtual money only. Nothing in the paper test can place a real order.
PAPER_STAKE = _float("BH_PAPER_STAKE", 10.0)
# Minimum model EV (as a fraction of stake) before a paper position is opened.
PAPER_MIN_EV = _float("BH_PAPER_MIN_EV", 0.05)
# Only markets whose deadline falls inside this many days are scanned.
PAPER_MAX_DAYS = _float("BH_PAPER_MAX_DAYS", 14.0)
# Minutes between automatic settle + scan while the web app runs; 0 = off. On by
# default: a paper test is only honest if predictions are logged before outcomes
# are known, on a rhythm, including on days nobody opens the page.
PAPER_AUTOSCAN_MIN = int(_float("BH_PAPER_AUTOSCAN_MIN", 60))

# --- LLM (optional) ---------------------------------------------------------
# News analysis with Claude. Everything else works without it.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("BH_LLM_MODEL", "claude-opus-5")
# low | medium | high | xhigh | max — empty means the API default (high).
LLM_EFFORT = os.getenv("BH_LLM_EFFORT", "").strip().lower()


def secrets_status() -> dict:
    """Which credentials are set — booleans only, never the values."""
    return {
        "POLY_API_KEY": bool(POLY_API_KEY),
        "POLY_PRIVATE_KEY": bool(POLY_PRIVATE_KEY),
        "SPOTIPY_CLIENT_ID": bool(SPOTIPY_CLIENT_ID),
        "SPOTIPY_CLIENT_SECRET": bool(SPOTIPY_CLIENT_SECRET),
        "TMDB_API_KEY": bool(TMDB_API_KEY),
        "OMDB_API_KEY": bool(OMDB_API_KEY),
        "HF_TOKEN": bool(HF_TOKEN),
        "RIOT_API_KEY": bool(RIOT_API_KEY),
        "ANTHROPIC_API_KEY": bool(ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_AUTH_TOKEN")),
        "trading_enabled": ENABLE_TRADING,
    }
