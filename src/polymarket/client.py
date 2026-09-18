"""The only module in this project that touches private keys.

That sentence is the design. `machina-sports/sports-skills` publishes 26 skills
of which exactly one can move money — separate package, marked `critical`,
`requires_explicit_confirmation: true`. The audit's reasoning for copying that:
a repo which mixes "read the price" and "place the order" in one module turns
every read bug into a possible unintended order.

So everything here is behind two locks:

  1. `BH_ENABLE_TRADING=1` must be set. Off by default.
  2. `src/core/registry.py` records this source as `money_movement: true`, and
     `assert_readonly()` refuses to serve it through any read-only path.

And one fact neither lock can fix: `clob.polymarket.com` is unreachable from
this connection. With both locks open, orders still will not arrive. Run this
from a machine that can reach the host.

Bug fixed from the previous version: it passed `POLYGON_RPC` as ClobClient's
first argument. That argument is the CLOB **host**, not an RPC endpoint, so the
client was pointed at a JSON-RPC node and could never have worked.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config  # noqa: E402


def _require_gate(action: str) -> None:
    from src.core.errors import TradingDisabled

    if not config.ENABLE_TRADING:
        raise TradingDisabled(action)


def _require_client():
    from src.core.errors import MissingDependency

    try:
        from py_clob_client.client import ClobClient  # noqa: F401
    except ImportError:
        raise MissingDependency("py-clob-client", "py_clob_client") from None


class PolymarketReader:
    """Read-only CLOB access. No key, no signing, no order placement.

    Still gated on nothing but reachability — reading prices cannot move money.
    It will simply fail with NETWORK_BLOCKED from this connection.
    """

    def __init__(self, host: str | None = None):
        _require_client()
        from py_clob_client.client import ClobClient

        self.host = host or config.POLY_HOST
        self.client = ClobClient(self.host)

    def markets(self, next_cursor: str = "") -> dict:
        return self.client.get_markets(next_cursor=next_cursor)

    def market(self, condition_id: str) -> dict:
        return self.client.get_market(condition_id)

    def orderbook(self, token_id: str) -> dict:
        return self.client.get_order_book(token_id)

    def last_price(self, token_id: str) -> dict:
        return self.client.get_last_trade_price(token_id)


class PolymarketClient:
    """Signing client. Constructing it already requires the gate to be open.

    Credentials are read from config and never logged, echoed, or returned.
    """

    def __init__(self, host: str | None = None):
        _require_gate("membuat klien penandatangan")
        _require_client()

        from src.core.errors import MissingCredential

        missing = [
            name for name, value in (
                ("POLY_API_KEY", config.POLY_API_KEY),
                ("POLY_SECRET", config.POLY_SECRET),
                ("POLY_PASSPHRASE", config.POLY_PASSPHRASE),
                ("POLY_PRIVATE_KEY", config.POLY_PRIVATE_KEY),
            ) if not value
        ]
        if missing:
            raise MissingCredential(*missing)

        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON

        # First argument is the CLOB host. Passing an RPC URL here — as the
        # previous version did — points the client at the wrong service entirely.
        self.client = ClobClient(
            host or config.POLY_HOST,
            key=config.POLY_PRIVATE_KEY,
            chain_id=POLYGON,
            funder=config.POLY_WALLET_ADDRESS or None,
        )
        self.client.set_api_creds(self.client.create_or_derive_api_creds())

    def place_limit_order(
        self, token_id: str, side: str, price: float, size: float, confirm: bool = False
    ) -> dict:
        """Place a GTC limit order.

        `confirm=True` is required per call. The gate says "this process may
        trade"; this argument says "this specific order is intended". The
        catalog's `requires_explicit_confirmation` is not decoration.
        """
        _require_gate("memasang limit order")
        if not confirm:
            from src.core.errors import TradingDisabled

            raise TradingDisabled("memasang order tanpa confirm=True")
        if side not in ("BUY", "SELL"):
            raise ValueError("side harus 'BUY' atau 'SELL'")
        if not 0 < price < 1:
            raise ValueError("Harga share Polymarket harus di antara 0 dan 1")

        from py_clob_client.clob_types import OrderArgs, OrderType

        signed = self.client.create_order(
            OrderArgs(price=price, size=size, side=side, token_id=token_id)
        )
        return self.client.post_order(signed, OrderType.GTC)

    def cancel_all(self, confirm: bool = False) -> dict:
        _require_gate("membatalkan semua order")
        if not confirm:
            from src.core.errors import TradingDisabled

            raise TradingDisabled("membatalkan order tanpa confirm=True")
        return self.client.cancel_all()

    def open_orders(self) -> list:
        """Reading your own orders needs the key, but moves no money."""
        return self.client.get_orders()


def status() -> dict:
    """What the UI shows on the Sources page. Booleans only, never key values."""
    return {
        "enabled": config.ENABLE_TRADING,
        "host": config.POLY_HOST,
        "credentials_set": config.secrets_status(),
        "reachable_from_here": False,
        "note": "Host CLOB Polymarket diblokir dari koneksi ini. Bahkan dengan gate "
                "terbuka dan kunci lengkap, order tidak akan sampai dari mesin ini.",
    }
