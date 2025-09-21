"""Utility helpers for logging and CSV tracking."""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from . import config


def configure_logging(level: str) -> None:
    """Configure loguru to write to stderr with the desired level."""
    logger.remove()
    logger.add(sys.stderr, level=level.upper())


def ensure_opportunity_log_headers() -> None:
    path = config.OPPORTUNITY_LOG_FILE
    if not path.exists():
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "timestamp_utc",
                    "mkey",
                    "oKey",
                    "o_pin",
                    "p_yes",
                    "o_pm",
                    "ratio",
                    "edge_pct",
                    "liquidity",
                    "trigger_type",
                    "reason",
                    "pm_market_id",
                    "token_id",
                    "avail_shares_at_th",
                    "avail_usd_at_th",
                    "wavg_price_at_th",
                ]
            )


def ensure_paper_trades_log_headers() -> None:
    path = config.PAPER_TRADES_LOG_FILE
    if not path.exists():
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "timestamp_entry_utc",
                    "timestamp_exit_utc",
                    "mkey",
                    "oKey",
                    "pm_market_id",
                    "token_id",
                    "entry_price",
                    "exit_price",
                    "shares",
                    "pnl_usd",
                    "reason",
                    "mode",
                ]
            )


_last_opportunity_state: dict[tuple[str, str], dict[str, float]] = {}


def log_opportunity_change(
    *,
    mkey: str,
    okey: str,
    o_pin: float,
    p_yes: float,
    o_pm: float,
    ratio: float,
    edge_pct: Optional[float],
    liquidity: float,
    pm_market_id: str,
    token_id: str | None,
    trigger_type: str,
    reason: str,
    avail_shares_at_th: Optional[float] = None,
    avail_usd_at_th: Optional[float] = None,
    wavg_price_at_th: Optional[float] = None,
) -> None:
    """Log changes to the opportunity CSV with rate limiting on updates."""
    key = (mkey, okey)
    prev = _last_opportunity_state.get(key)
    should_log = prev is None
    change_reason = reason

    if prev is not None:
        prev_ratio = prev.get("ratio")
        prev_o_pin = prev.get("o_pin")
        prev_p_yes = prev.get("p_yes")

        if prev_ratio is None or abs(ratio - prev_ratio) >= 0.01:
            should_log = True
            change_reason = f"ratio_delta={ratio - (prev_ratio or 0):.4f}"
        elif (o_pin != prev_o_pin) or (p_yes != prev_p_yes):
            should_log = True
            change_reason = "price_change"

        if (
            trigger_type == "ARBITRAGE"
            and prev_ratio is not None
            and prev_ratio < config.ARB_RATIO <= ratio
        ):
            should_log = True
            change_reason = "cross_up_1.12"

    if not should_log:
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    with config.OPPORTUNITY_LOG_FILE.open("a", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                timestamp,
                mkey,
                okey,
                f"{o_pin:.4f}" if o_pin else "",
                f"{p_yes:.4f}" if p_yes else "",
                f"{o_pm:.4f}" if o_pm else "",
                f"{ratio:.4f}" if ratio else "",
                f"{edge_pct:.2f}" if edge_pct is not None else "",
                f"{liquidity:.2f}",
                trigger_type,
                change_reason,
                pm_market_id,
                token_id or "",
                f"{avail_shares_at_th:.4f}" if avail_shares_at_th is not None else "",
                f"{avail_usd_at_th:.2f}" if avail_usd_at_th is not None else "",
                f"{wavg_price_at_th:.4f}" if wavg_price_at_th is not None else "",
            ]
        )
    _last_opportunity_state[key] = {"ratio": ratio, "o_pin": o_pin, "p_yes": p_yes}


def snapshot_json(data: dict, target: Path) -> None:
    try:
        with target.open("w") as handle:
            json.dump(data, handle, indent=2)
    except Exception as exc:
        logger.debug("Failed to dump %s: %s", target, exc)
