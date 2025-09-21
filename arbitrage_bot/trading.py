"""Trading helpers and cooldown logic."""
from __future__ import annotations

import asyncio
import csv
import json
import time
from typing import Dict, Optional

from loguru import logger
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from . import config
from .logging_utils import ensure_paper_trades_log_headers
from .orderbook import estimate_fill_on_bids, fetch_order_book, get_best_bid_price
from .state import BotState


def get_clob_client(state: BotState) -> Optional[ClobClient]:
    if state.clob_client is not None:
        return state.clob_client

    if not config.settings.private_key:
        logger.error("POLY_PRIVATE_KEY/PRIVATE_KEY is not set. Cannot create Polymarket client.")
        return None

    host = "https://clob.polymarket.com"
    try:
        signature_type = config.settings.signature_type.strip() if config.settings.signature_type else None
        if signature_type in {"1", "2"}:
            if not config.settings.proxy_address:
                logger.error("POLY_SIGNATURE_TYPE set but POLY_PROXY_ADDRESS missing. Cannot init ClobClient.")
                return None
            logger.info("Initializing ClobClient (signature_type=%s, proxy mode)...", signature_type)
            client = ClobClient(
                host,
                key=config.settings.private_key,
                chain_id=137,
                signature_type=int(signature_type),
                funder=config.settings.proxy_address,
            )
        else:
            if config.settings.proxy_address:
                logger.warning(
                    "POLY_PROXY_ADDRESS provided without POLY_SIGNATURE_TYPE; proceeding in direct EOA mode (proxy ignored)."
                )
            logger.info("Initializing ClobClient (EOA mode)...")
            client = ClobClient(host, key=config.settings.private_key, chain_id=137)

        client.set_api_creds(client.create_or_derive_api_creds())
        state.clob_client = client
        logger.success("Polymarket client initialized.")
        return client
    except Exception as exc:
        logger.error("Failed to initialize ClobClient: %s", exc)
        return None


async def save_trade_log(state: BotState, trade_details: dict, pre_trade_history: list[dict]) -> None:
    trade_time = trade_details["timestamp_utc"]
    pinnacle_match_id = trade_details["pinnacle_match_id"]
    logger.info("TRADE %s: waiting 120s to collect post-trade data...", pinnacle_match_id)

    await asyncio.sleep(120)

    combined_history = list(state.pinnacle_history) + list(state.polymarket_history)
    post_trade_history = [item for item in combined_history if trade_time < item["timestamp"] <= trade_time + 120]

    full_log = {
        "trade_details": trade_details,
        "pre_trade_window_60s": pre_trade_history,
        "post_trade_window_120s": post_trade_history,
    }

    log_filename = f"trade_{pinnacle_match_id}_{int(trade_time)}.json"
    filepath = config.LOGS_DIR / log_filename
    try:
        with filepath.open("w") as handle:
            json.dump(full_log, handle, indent=4)
        logger.success("Saved detailed trade log to %s", filepath)
    except Exception as exc:
        logger.error("Failed to save trade log: %s", exc)


def _append_trade_cooldown(state: BotState, cooldown_key: str, price: float) -> None:
    state.recent_trades[cooldown_key].append({"timestamp": time.time(), "price": price})


def check_trade_cooldown(state: BotState, cooldown_key: str, current_price: float, cooldown_seconds: int = 120) -> bool:
    trades = state.recent_trades[cooldown_key]
    now = time.time()
    state.recent_trades[cooldown_key] = [trade for trade in trades if now - trade["timestamp"] < cooldown_seconds]

    for trade in state.recent_trades[cooldown_key]:
        if current_price >= trade["price"]:
            logger.warning(
                "Cooldown active for %s: last trade price %.4f >= current price %.4f.",
                cooldown_key,
                trade["price"],
                current_price,
            )
            return False
    return True


async def place_polymarket_trade(state: BotState, trade_details: dict) -> bool:
    logger.success("--- Attempting trade on Polymarket ---")
    logger.info("Trade details: %s", json.dumps(trade_details, default=str))

    client = get_clob_client(state)
    if not client:
        return False

    is_successful_for_cooldown = False

    try:
        price = float(trade_details["polymarket_price"])
        if not (0 < price < 1):
            raise ValueError(f"Invalid Polymarket price: {price}")
        size_shares = trade_details.get("size_shares")
        if size_shares is None:
            size_shares = float(trade_details.get("bet_amount_usd", 0.0)) / price
        order_args = OrderArgs(
            price=price,
            size=size_shares,
            side=BUY,
            token_id=trade_details["polymarket_token_id"],
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        logger.success("Polymarket order posted successfully: %s", resp)
        trade_details["trade_status"] = "SUCCESS"
        trade_details["api_response"] = resp
        is_successful_for_cooldown = True
    except Exception as exc:
        logger.error("Error placing Polymarket trade: %s", exc)
        trade_details["trade_status"] = "FAILURE"
        trade_details["trade_error"] = str(exc)
        if "lower than the minimum" in str(exc).lower():
            logger.warning(
                "Trade rejected due to minimum size. Marking as SKIPPED_MIN_SIZE for cooldown/log purposes."
            )
            trade_details["trade_status"] = "SKIPPED_MIN_SIZE"
            is_successful_for_cooldown = True

    if is_successful_for_cooldown:
        cooldown_key = trade_details.get("polymarket_token_id") or f"{trade_details['polymarket_market_id']}:{trade_details.get('outcome_name','')}"
        _append_trade_cooldown(state, cooldown_key, trade_details["polymarket_price"])

        pre_trade_history = [
            item
            for item in list(state.pinnacle_history) + list(state.polymarket_history)
            if trade_details["timestamp_utc"] - 60 <= item["timestamp"] <= trade_details["timestamp_utc"]
        ]
        task = asyncio.create_task(save_trade_log(state, trade_details, pre_trade_history))
        state.background_tasks.add(task)
        task.add_done_callback(state.background_tasks.discard)
        return True
    return False


async def paper_sell_strategy(state: BotState) -> None:
    ensure_paper_trades_log_headers()
    while True:
        try:
            if not getattr(state, "paper_positions", None):
                await asyncio.sleep(2)
                continue
            positions: Dict[str, dict] = dict(state.paper_positions)  # type: ignore[attr-defined]
            for token_id, pos in positions.items():
                book = await fetch_order_book(token_id)
                if not book:
                    continue
                entry_price = pos.get("entry_price")
                if entry_price is None:
                    continue
                target_usd = pos.get("target_usd", config.settings.bet_amount_usd)
                tp_price = min(0.999, entry_price + config.settings.take_profit_abs)
                best_bid = get_best_bid_price(book)
                if best_bid is None or best_bid < tp_price:
                    continue

                filled_usd, filled_shares, wavg_exit = estimate_fill_on_bids(book, tp_price, target_usd)
                if filled_usd <= 0 or filled_shares <= 0 or not wavg_exit:
                    continue

                entry_usd = entry_price * filled_shares
                pnl_usd = filled_usd - entry_usd

                ensure_paper_trades_log_headers()
                with config.PAPER_TRADES_LOG_FILE.open("a", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(
                        [
                            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(pos.get("entry_ts", time.time()))),
                            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                            pos.get("mkey", ""),
                            pos.get("okey", ""),
                            pos.get("pm_market_id", ""),
                            token_id,
                            f"{entry_price:.4f}",
                            f"{wavg_exit:.4f}",
                            f"{filled_shares:.6f}",
                            f"{pnl_usd:.2f}",
                            "TP",
                            "paper",
                        ]
                    )
                logger.success(
                    "[PAPER SELL] Closed %s at %.4f (+%.2f USD).",
                    token_id,
                    wavg_exit,
                    pnl_usd,
                )
                del state.paper_positions[token_id]  # type: ignore[attr-defined]
        except Exception as exc:
            logger.debug("paper_sell_strategy loop error: %s", exc)
        finally:
            await asyncio.sleep(2)


def register_paper_position(state: BotState, token_id: str, position: dict) -> None:
    if not hasattr(state, "paper_positions"):
        state.paper_positions = {}
    if token_id not in state.paper_positions:
        state.paper_positions[token_id] = position
