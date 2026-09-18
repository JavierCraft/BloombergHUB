"""Polymarket on-chain reader via Polygon RPC.

The audit's central practical finding: every Polymarket HTTP endpoint is blocked
from this connection, but Polygon RPC is open — and Polymarket settles on
Polygon. So positions, balances and market activity stay readable even with the
API dark. This module is that path.

Verified against Polygon at block 93.282.644 (6 September 2026), not assumed:

  * CTF Exchange 0x4bFb41d5…B8982E emits exactly one event in recent blocks:
    `TokenRegistered(uint256,uint256,bytes32)` — 4 topics, 0 data words. The
    audit inferred "not a fill" from the shape; the topic hash confirms the name.
  * `OrderFilled` count over 5.000 blocks on BOTH known exchange addresses
    (CTF Exchange and NegRisk CTF Exchange): **zero**.
  * Meanwhile ConditionalTokens 0x4D97…6045 emitted 35.377 logs per 200 blocks
    and the NegRisk adapter 1.433. That is where settlement is observable.

So the honest statement is not "matching moved to v2" — it is that neither known
exchange address emits fills, and activity must be read at the token layer.

What this module CANNOT do, and will not pretend to: map an ERC-1155 token id to
a market title or category. That mapping lives in Gamma, which is blocked. Token
ids are reported raw.

Three bugs fixed from the previous version, all silent failures:
  * hexbytes 2.0 dropped the `0x` prefix from `.hex()`, so every topic comparison
    was false. Normalised through `_hex()`.
  * fills were detected with `"OrderFilled" in str(topic0)` — topic0 is a keccak
    hash and never contains that text, so the check could not ever fire.
  * NEG_RISK_CTF_EXCHANGE was set to the same address as CTF_EXCHANGE_V2.
"""
from __future__ import annotations

import time
from typing import Any

from web3 import Web3

# --- Polymarket / Gnosis contracts on Polygon mainnet -----------------------
CTF_EXCHANGE = "0x4bFb41d5B3570DEfd03c39a9A4D8de6Bd8B8982e"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Kept under the old name so existing callers do not break.
CTF_EXCHANGE_V1 = CTF_EXCHANGE

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

# --- Event topics, all computed and cross-checked against live logs ---------
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_ORDER_FILLED = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
TOPIC_ORDERS_MATCHED = "0x63bf4d16b7fa898ef4c4b2b6d90fd201e9c56313b65638af6088d149d2ce956c"
TOPIC_TOKEN_REGISTERED = "0xbc9a2432e8aeb48327246cddd6e872ef452812b4243c04e6bfb786a2cd8faf0d"
TOPIC_TRANSFER_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
TOPIC_TRANSFER_BATCH = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
TOPIC_POSITION_SPLIT = "0x2e6bb91f8cbcda0c93623c54d0403a43514fabc40084ec96b6d5379a74786298"
TOPIC_PAYOUT_REDEMPTION = "0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224"
TOPIC_CT_PAYOUT_REDEMPTION = "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
TOPIC_POSITIONS_MERGE = "0x6f13ca62553fcc2bcd2372180a43949c1e4cebba603901ede2f4e14f36b282ca"
TOPIC_CONDITION_RESOLUTION = "0xb44d84d3289691f71497564b85d4233648d9dbae8cbdbb4329f301c3a0185894"
TOPIC_CONDITION_PREPARATION = "0xab3760c3bd2bb38b5bcf54dc79802ed67338b4cf29f3054ded67ed24661e4177"
TOPIC_APPROVAL_FOR_ALL = "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"

EVENT_NAMES = {
    TOPIC_TRANSFER: "Transfer",
    TOPIC_ORDER_FILLED: "OrderFilled",
    TOPIC_ORDERS_MATCHED: "OrdersMatched",
    TOPIC_TOKEN_REGISTERED: "TokenRegistered",
    TOPIC_TRANSFER_SINGLE: "TransferSingle",
    TOPIC_TRANSFER_BATCH: "TransferBatch",
    TOPIC_POSITION_SPLIT: "PositionSplit",
    TOPIC_POSITIONS_MERGE: "PositionsMerge",
    TOPIC_PAYOUT_REDEMPTION: "PayoutRedemption",
    TOPIC_CT_PAYOUT_REDEMPTION: "PayoutRedemption",
    TOPIC_CONDITION_RESOLUTION: "ConditionResolution",
    TOPIC_CONDITION_PREPARATION: "ConditionPreparation",
    TOPIC_APPROVAL_FOR_ALL: "ApprovalForAll",
}

# `OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)`
# indexes orderHash/maker/taker, leaving five words in `data`. The audit used
# this arity as the test for "is this really a fill"; keep it as the guard.
ORDER_FILLED_DATA_WORDS = 5

# How many decoded events per kind reach the screen, and how many markets get
# named per kind. The aggregates above them cover the whole window regardless.
DETAILS_PER_EVENT = 20
TOP_MARKETS = 8

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
]

RPC_FALLBACKS = (
    "https://polygon.publicnode.com",
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
)


def _hex(value: Any) -> str:
    """Normalise HexBytes / bytes / str into a lowercase `0x…` string.

    hexbytes 2.0 changed `.hex()` to match `bytes.hex()` — no `0x` prefix. Every
    topic comparison written against the old behaviour silently became False.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    text = str(value)
    if hasattr(value, "hex") and not isinstance(value, str):
        try:
            text = value.hex()
        except (TypeError, ValueError):
            pass
    text = text.lower()
    return text if text.startswith("0x") else "0x" + text


def _data_words(log: Any) -> int:
    data = log.get("data") if isinstance(log, dict) else getattr(log, "data", None)
    if data is None:
        return 0
    if isinstance(data, (bytes, bytearray)):
        return len(data) // 32
    text = str(data)
    if text.startswith("0x"):
        text = text[2:]
    return len(text) // 64


def _addr_from_topic(topic: Any) -> str:
    """An indexed address is left-padded to 32 bytes; take the last 20."""
    raw = _hex(topic)
    return Web3.to_checksum_address("0x" + raw[-40:]) if len(raw) >= 42 else ""


def _inject_poa(w3: Web3) -> None:
    """Polygon is proof-of-authority: its `extraData` is 105 bytes, not 32.

    Without this middleware every `get_block` raises `ExtraDataLengthError`, and
    because the settlement scan wrapped those calls in a bare `except`, the
    failure surfaced as `time_utc: null` on every single event rather than as an
    error. Timestamps were therefore never available on the On-Chain page.

    Injected at layer 0 so it runs before the result is validated. web3 renamed
    this class in v7 (`geth_poa_middleware` -> `ExtraDataToPOAMiddleware`); both
    names are tried so the reader keeps working either side of that rename.
    """
    try:
        from web3.middleware import ExtraDataToPOAMiddleware as poa
    except ImportError:
        try:
            from web3.middleware import geth_poa_middleware as poa
        except ImportError:
            return
    try:
        w3.middleware_onion.inject(poa, layer=0)
    except ValueError:
        pass  # already injected on this instance


class PolygonReader:
    """Read Polymarket-related state from Polygon, with no Polymarket API."""

    def __init__(self, rpc_url: str | None = None, timeout: int = 20):
        urls = [rpc_url] if rpc_url else list(RPC_FALLBACKS)
        last_error: Exception | None = None

        for url in urls:
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
                _inject_poa(w3)
                block = w3.eth.block_number
                if block:
                    self.w3 = w3
                    self.rpc_url = url
                    return
            except Exception as exc:  # try the next endpoint
                last_error = exc

        raise ConnectionError(
            f"Tidak bisa terhubung ke Polygon RPC ({', '.join(u for u in urls if u)}): {last_error}"
        )

    # -- basics -------------------------------------------------------------

    def get_block_number(self) -> int:
        return int(self.w3.eth.block_number)

    def get_balance(self, address: str) -> dict:
        addr = Web3.to_checksum_address(address)
        pol = float(Web3.from_wei(self.w3.eth.get_balance(addr), "ether"))

        def erc20(token: str) -> float:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token), abi=ERC20_ABI
            )
            return contract.functions.balanceOf(addr).call() / 1e6

        usdc_e = erc20(USDC_E)
        usdc_native = erc20(USDC_NATIVE)

        return {
            "address": addr,
            "POL": round(pol, 6),
            "USDC.e": round(usdc_e, 2),
            "USDC": round(usdc_native, 2),
            "total_usd": round(usdc_e + usdc_native, 2),
            "funded": bool(usdc_e or usdc_native or pol),
        }

    def get_contract_type(self, address: str) -> dict:
        """EOA or contract, plus the Safe-proxy signature Polymarket wallets use."""
        addr = Web3.to_checksum_address(address)
        code = self.w3.eth.get_code(addr)
        size = len(code)
        nonce = self.w3.eth.get_transaction_count(addr)

        if size == 0:
            kind, note = "EOA", "Alamat biasa, bukan kontrak."
        elif size == 125:
            kind, note = (
                "CONTRACT (Safe proxy)",
                "Bytecode 125 byte — pola proxy Safe yang dipakai wallet Polymarket.",
            )
        else:
            kind, note = "CONTRACT", f"Bytecode {size} byte."

        return {"address": addr, "type": kind, "bytecode_bytes": size,
                "nonce": nonce, "note": note}

    # -- exchange activity --------------------------------------------------

    def _logs(self, address: str, blocks: int, topics: list | None = None) -> tuple[list, int, int]:
        to_block = self.get_block_number()
        from_block = max(0, to_block - blocks + 1)
        query = {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": Web3.to_checksum_address(address),
        }
        if topics:
            query["topics"] = topics
        return list(self.w3.eth.get_logs(query)), from_block, to_block

    def check_exchange_activity(self, blocks: int = 200, address: str = CTF_EXCHANGE) -> dict:
        """Is this exchange contract actually filling orders?

        The test is not "are there logs" — it is "are there logs whose topic is
        OrderFilled and whose data carries five words". Token registrations look
        busy and move no money.
        """
        started = time.perf_counter()
        logs, from_block, to_block = self._logs(address, blocks)

        by_event: dict[str, int] = {}
        fills = 0
        samples: list[dict] = []

        for log in logs:
            topics = log.get("topics", [])
            topic0 = _hex(topics[0]) if topics else ""
            words = _data_words(log)
            name = EVENT_NAMES.get(topic0, f"unknown({topic0[:12]}…)")
            by_event[name] = by_event.get(name, 0) + 1

            if topic0 == TOPIC_ORDER_FILLED and words >= ORDER_FILLED_DATA_WORDS:
                fills += 1

            if len(samples) < 3:
                samples.append({
                    "block": log["blockNumber"],
                    "tx": _hex(log["transactionHash"]),
                    "event": name,
                    "topics": len(topics),
                    "data_words": words,
                })

        active = fills > 0
        return {
            "exchange": Web3.to_checksum_address(address),
            "label": {
                CTF_EXCHANGE.lower(): "CTF Exchange",
                NEG_RISK_CTF_EXCHANGE.lower(): "NegRisk CTF Exchange",
            }.get(address.lower(), "Kontrak"),
            "blocks_scanned": blocks,
            "from_block": from_block,
            "to_block": to_block,
            "total_logs": len(logs),
            "order_fills": fills,
            "events": by_event,
            "samples": samples,
            "status": "ACTIVE" if active else "NO FILLS",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "interpretation": (
                f"{fills} OrderFilled terdeteksi — kontrak ini masih memproses trade."
                if active else
                "Nol OrderFilled. Log yang ada bukan fill (butuh 5 data word). "
                "Kode yang meng-hardcode alamat ini tidak akan melihat satu trade pun."
            ),
        }

    # Old name, old default, same answer.
    def check_exchange_v1_activity(self, last_n_blocks: int = 100) -> dict:
        return self.check_exchange_activity(last_n_blocks, CTF_EXCHANGE)

    def settlement_activity(self, blocks: int = 60, include_transfers: bool = False) -> dict:
        """Live market activity read at the token layer, not the exchange layer.

        This is the workaround for the API block: splits, merges, redemptions and
        resolutions on ConditionalTokens are the on-chain shadow of Polymarket
        activity. It gives volume and direction; it cannot give market titles,
        because that mapping is only in Gamma.

        Counting is done with one filtered `get_logs` per event topic, run
        concurrently. Fetching the contract's logs unfiltered pulls roughly 170
        records per block — about 10.000 for a 60-block window — and took 16
        seconds. Filtering by topic and skipping the ERC-1155 transfer firehose
        by default brings the same summary down to a couple of seconds.
        """
        import concurrent.futures

        started = time.perf_counter()
        to_block = self.get_block_number()
        from_block = max(0, to_block - blocks + 1)

        wanted = [
            ("position_splits", CONDITIONAL_TOKENS, TOPIC_POSITION_SPLIT),
            ("position_merges", CONDITIONAL_TOKENS, TOPIC_POSITIONS_MERGE),
            ("redemptions", CONDITIONAL_TOKENS, TOPIC_CT_PAYOUT_REDEMPTION),
            ("markets_resolved", CONDITIONAL_TOKENS, TOPIC_CONDITION_RESOLUTION),
            ("markets_created", CONDITIONAL_TOKENS, TOPIC_CONDITION_PREPARATION),
            ("neg_risk_redemptions", NEG_RISK_ADAPTER, TOPIC_PAYOUT_REDEMPTION),
        ]
        if include_transfers:
            wanted.append(("erc1155_transfers", CONDITIONAL_TOKENS, TOPIC_TRANSFER_SINGLE))

        event_logs: dict[str, list] = {}

        def count(address: str, topic: str) -> int | None:
            try:
                logs = self.w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": Web3.to_checksum_address(address),
                    "topics": [topic],
                })
                event_logs[topic] = list(logs)
                return len(event_logs[topic])
            except Exception:
                return None

        counts: dict[str, int | None] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            futures = {pool.submit(count, addr, topic): name for name, addr, topic in wanted}
            for future in concurrent.futures.as_completed(futures):
                counts[futures[future]] = future.result()

        failed = [k for k, v in counts.items() if v is None]

        from datetime import datetime, timezone
        from decimal import Decimal

        from src.polymarket.events import decode_event

        # Decode *every* log, not just the twenty that get displayed. The counts
        # were always the full window; the money was not. Aggregating over a
        # 20-row sample and calling it "total USDC" would be a wrong number
        # wearing the costume of a precise one.
        decoded: dict[str, list[dict]] = {}
        for name, _addr, topic in wanted:
            rows = []
            for log in sorted(event_logs.get(topic, []),
                              key=lambda x: (x["blockNumber"], x["logIndex"]), reverse=True):
                try:
                    rows.append(decode_event(log, name))
                except Exception:
                    continue  # one unparsable log must not lose the other 2.000
            decoded[name] = rows

        details = {name: rows[:DETAILS_PER_EVENT] for name, rows in decoded.items()}

        # Timestamps: only for blocks actually shown, plus the window edges.
        # Polygon blocks are ~2s apart, so interpolating the rest would look
        # accurate and be fabricated; every one of these is read from the chain.
        blocks_needed = {row["block"] for rows in details.values() for row in rows}
        blocks_needed.update({from_block, to_block})

        def timestamp(block: int) -> tuple[int, str | None]:
            try:
                stamp = self.w3.eth.get_block(block)["timestamp"]
                return block, datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            except Exception:
                return block, None

        timestamps: dict[int, str | None] = {}
        if blocks_needed:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                timestamps.update(pool.map(timestamp, sorted(blocks_needed)))

        for rows in details.values():
            for row in rows:
                row["time_utc"] = timestamps.get(row["block"])

        window_seconds = None
        if timestamps.get(from_block) and timestamps.get(to_block):
            window_seconds = int(
                (datetime.fromisoformat(timestamps[to_block])
                 - datetime.fromisoformat(timestamps[from_block])).total_seconds()
            )

        def money(row: dict) -> Decimal | None:
            """USDC value of one event, or None when the collateral is unknown.

            `amount` is only set by the decoder when the collateral is a USDC it
            recognises. Anything else keeps raw integer units, which cannot be
            added to dollars, so those are counted separately, not coerced.
            """
            try:
                return Decimal(row["amount"]) if row.get("amount") is not None else None
            except Exception:
                return None

        def stats_for(name: str) -> dict:
            rows = decoded.get(name) or []
            amounts = sorted(v for v in (money(r) for r in rows) if v is not None)
            wallets = {r.get("wallet") for r in rows if r.get("wallet")}
            conditions = {r.get("condition_id") for r in rows if r.get("condition_id")}
            blocks_seen = [r["block"] for r in rows]
            total = sum(amounts) if amounts else None

            per_condition: dict[str, dict] = {}
            for row in rows:
                cid = row.get("condition_id")
                if not cid:
                    continue
                slot = per_condition.setdefault(cid, {"condition_id": cid, "events": 0,
                                                      "usdc": Decimal(0), "priced": 0})
                slot["events"] += 1
                value = money(row)
                if value is not None:
                    slot["usdc"] += value
                    slot["priced"] += 1
            top = sorted(per_condition.values(),
                         key=lambda r: (r["usdc"], r["events"]), reverse=True)[:TOP_MARKETS]

            return {
                "count": counts.get(name),
                "decoded": len(rows),
                "unique_wallets": len(wallets) or None,
                "unique_conditions": len(conditions) or None,
                "with_usdc_amount": len(amounts),
                "usdc_total": str(total) if total is not None else None,
                "usdc_largest": str(amounts[-1]) if amounts else None,
                "usdc_smallest": str(amounts[0]) if amounts else None,
                "usdc_median": str(amounts[len(amounts) // 2]) if amounts else None,
                "usdc_mean": (str((total / len(amounts)).quantize(Decimal("0.000001")))
                              if amounts else None),
                "first_block": min(blocks_seen) if blocks_seen else None,
                "last_block": max(blocks_seen) if blocks_seen else None,
                "per_minute": (round(len(rows) / (window_seconds / 60), 2)
                               if window_seconds and window_seconds > 0 and rows else None),
                "top_conditions": [
                    {"condition_id": r["condition_id"], "events": r["events"],
                     "usdc": str(r["usdc"]) if r["priced"] else None}
                    for r in top
                ],
            }

        stats = {name: stats_for(name) for name, _addr, _topic in wanted}

        # Resolve the condition ids on screen to the markets they settle. This is
        # what turns "0x2bfe...b906c7 - 5 USDC" into "Counter-Strike: B8 vs
        # Nuclear TigeRES - B8 60,5%". Only displayed ids are looked up.
        shown = [row.get("condition_id") for rows in details.values() for row in rows]
        shown += [t["condition_id"] for block in stats.values()
                  for t in block["top_conditions"]]
        try:
            from src.markets.resolve import markets_by_condition

            lookup = markets_by_condition(shown)
        except Exception as exc:
            lookup = {"markets": {}, "asked": 0, "resolved": 0,
                      "failed": getattr(exc, "message", None) or str(exc)[:140],
                      "price_as_of": None, "note": ""}

        by_condition = lookup.get("markets", {})

        def attach(cid: str | None) -> dict | None:
            return by_condition.get(str(cid).lower()) if cid else None

        for rows in details.values():
            for row in rows:
                row["market"] = attach(row.get("condition_id"))
        for block in stats.values():
            for top in block["top_conditions"]:
                top["market"] = attach(top["condition_id"])

        summary = {
            "position_splits": counts.get("position_splits"),
            "position_merges": counts.get("position_merges"),
            "redemptions": counts.get("redemptions"),
            "neg_risk_redemptions": counts.get("neg_risk_redemptions"),
            "markets_resolved": counts.get("markets_resolved"),
            "markets_created": counts.get("markets_created"),
            "blocks": blocks,
        }
        if include_transfers:
            summary["erc1155_transfers"] = counts.get("erc1155_transfers") or 0

        return {
            "blocks_scanned": blocks,
            "from_block": from_block,
            "to_block": to_block,
            "window": {
                "from_block": from_block,
                "to_block": to_block,
                "from_time_utc": timestamps.get(from_block),
                "to_time_utc": timestamps.get(to_block),
                "seconds": window_seconds,
            },
            "summary": summary,
            "stats": stats,
            "details": details,
            "details_limit_per_event": DETAILS_PER_EVENT,
            "market_lookup": {k: v for k, v in lookup.items() if k != "markets"},
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "incomplete": failed,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "caveat": (
                "ConditionalTokens juga dipakai layanan lain, jadi angka ini adalah "
                "batas atas aktivitas Polymarket, bukan angka pasti. Jumlah USDC "
                "dihitung dari seluruh event pada rentang blok ini, bukan dari 20 "
                "baris yang ditampilkan. Judul dan harga pasar berasal dari Gamma "
                "saat pemuatan, bukan harga eksekusi event - event penyelesaian "
                "memang tidak memuat harga."
            ),
        }

    # -- wallet -------------------------------------------------------------

    def position_changes(self, address: str, blocks: int = 20_000, limit: int = 50) -> dict:
        """ERC-1155 movements for one wallet — its position changes, on-chain.

        Answers "did this wallet trade, when, and in what size" without any
        Polymarket API. It does not answer "in which market", by design: that
        needs Gamma metadata, which is blocked, and guessing would be worse than
        leaving it blank.
        """
        addr = Web3.to_checksum_address(address)
        padded = "0x" + addr[2:].lower().rjust(64, "0")
        to_block = self.get_block_number()
        from_block = max(0, to_block - blocks)
        rows: list[dict] = []

        for direction, topics in (
            ("out", [TOPIC_TRANSFER_SINGLE, None, padded, None]),
            ("in", [TOPIC_TRANSFER_SINGLE, None, None, padded]),
        ):
            try:
                logs = self.w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": Web3.to_checksum_address(CONDITIONAL_TOKENS),
                    "topics": topics,
                })
            except Exception:
                continue

            for log in logs[-limit:]:
                data = log.get("data")
                raw = data.hex() if isinstance(data, (bytes, bytearray)) else str(data).replace("0x", "")
                token_id = int(raw[0:64], 16) if len(raw) >= 64 else None
                value = int(raw[64:128], 16) if len(raw) >= 128 else None
                rows.append({
                    "direction": direction,
                    "block": log["blockNumber"],
                    "tx": _hex(log["transactionHash"]),
                    "token_id": str(token_id) if token_id is not None else None,
                    "shares": round(value / 1e6, 4) if value is not None else None,
                    "counterparty": _addr_from_topic(log["topics"][2] if direction == "in"
                                                     else log["topics"][3]),
                })

        rows.sort(key=lambda r: r["block"], reverse=True)
        return {
            "address": addr,
            "from_block": from_block,
            "to_block": to_block,
            "blocks_scanned": blocks,
            "count": len(rows),
            "changes": rows[:limit],
            "note": "Token id tidak dipetakan ke judul market — metadata Gamma diblokir.",
        }

    def usdc_flows(self, address: str, blocks: int = 20_000, limit: int = 50) -> dict:
        """USDC.e in and out of a wallet — the money side of the same story."""
        addr = Web3.to_checksum_address(address)
        padded = "0x" + addr[2:].lower().rjust(64, "0")
        to_block = self.get_block_number()
        from_block = max(0, to_block - blocks)
        rows: list[dict] = []

        for direction, topics in (
            ("out", [TOPIC_TRANSFER, padded, None]),
            ("in", [TOPIC_TRANSFER, None, padded]),
        ):
            try:
                logs = self.w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": Web3.to_checksum_address(USDC_E),
                    "topics": topics,
                })
            except Exception:
                continue

            for log in logs[-limit:]:
                data = log.get("data")
                raw = data.hex() if isinstance(data, (bytes, bytearray)) else str(data).replace("0x", "")
                amount = int(raw[:64], 16) / 1e6 if len(raw) >= 64 else None
                rows.append({
                    "direction": direction,
                    "block": log["blockNumber"],
                    "tx": _hex(log["transactionHash"]),
                    "usdc": round(amount, 2) if amount is not None else None,
                    "counterparty": _addr_from_topic(
                        log["topics"][2] if direction == "out" else log["topics"][1]
                    ),
                })

        rows.sort(key=lambda r: r["block"], reverse=True)
        total_in = sum(r["usdc"] or 0 for r in rows if r["direction"] == "in")
        total_out = sum(r["usdc"] or 0 for r in rows if r["direction"] == "out")
        return {
            "address": addr,
            "blocks_scanned": blocks,
            "count": len(rows),
            "total_in": round(total_in, 2),
            "total_out": round(total_out, 2),
            "net": round(total_in - total_out, 2),
            "flows": rows[:limit],
        }

    def scan_wallet(self, address: str, deep: bool = False) -> dict:
        """Balance + contract type + block, with optional activity history."""
        result = {
            "balance": self.get_balance(address),
            "contract": self.get_contract_type(address),
            "block": self.get_block_number(),
            "rpc": self.rpc_url,
        }
        # Kept for callers that read result["contract_type"] as a string.
        result["contract_type"] = result["contract"]["type"]

        if deep:
            result["positions"] = self.position_changes(address)
            result["usdc"] = self.usdc_flows(address)

        balance = result["balance"]
        if not balance["funded"]:
            result["verdict"] = (
                "Kosong di ketiga aset. Kalau wallet ini pernah aktif, operatornya "
                "sudah menarik semuanya — bukan sedang jeda."
            )
        else:
            result["verdict"] = f"Aktif — saldo ${balance['total_usd']:,.2f}."
        return result
