"""Orderbook helpers for Polymarket."""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import httpx
from loguru import logger

ORDERBOOK_CACHE: Dict[str, tuple[dict, float]] = {}
ORDERBOOK_TTL_SEC = 2.0


async def fetch_order_book(token_id: str) -> Optional[dict]:
    if not token_id:
        return None

    now = time.time()
    cached = ORDERBOOK_CACHE.get(token_id)
    if cached and (now - cached[1]) < ORDERBOOK_TTL_SEC:
        return cached[0]

    url = "https://clob.polymarket.com/book"
    param_candidates = (
        {"asset_id": token_id},
        {"market": token_id},
        {"tokens": token_id},
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for params in param_candidates:
                try:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and "asks" in data and "bids" in data:
                            ORDERBOOK_CACHE[token_id] = (data, now)
                            return data
                except Exception:
                    continue
    except Exception as exc:
        logger.debug("fetch_order_book error for token %s: %s", token_id, exc)
    return None


def summarize_liquidity_to_price(book: dict, max_price: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        asks = book.get("asks", []) if isinstance(book, dict) else []
        total_shares = 0.0
        total_usd = 0.0
        for level in asks:
            try:
                price = float(level.get("price"))
                size = float(level.get("size"))
            except Exception:
                continue
            if price <= max_price and price > 0 and size > 0:
                total_shares += size
                total_usd += price * size
        if total_shares > 0 and total_usd > 0:
            return total_shares, total_usd, total_usd / total_shares
    except Exception as exc:
        logger.debug("summarize_liquidity_to_price error: %s", exc)
    return None, None, None


def estimate_fill_on_bids(book: dict, min_price: float, target_usd: float) -> Tuple[float, float, Optional[float]]:
    try:
        bids = book.get("bids", []) if isinstance(book, dict) else []
        levels = []
        for level in bids:
            try:
                price = float(level.get("price"))
                size = float(level.get("size"))
            except Exception:
                continue
            if price >= min_price and price > 0 and size > 0:
                levels.append((price, size))
        if not levels:
            return 0.0, 0.0, None
        levels.sort(key=lambda x: x[0], reverse=True)

        filled_usd = 0.0
        filled_shares = 0.0
        for price, size in levels:
            if filled_usd >= target_usd:
                break
            level_usd = price * size
            need_usd = target_usd - filled_usd
            if level_usd <= need_usd + 1e-9:
                filled_usd += level_usd
                filled_shares += size
            else:
                add_shares = need_usd / price
                filled_usd += need_usd
                filled_shares += add_shares
                break

        if filled_shares > 0:
            return filled_usd, filled_shares, filled_usd / filled_shares
        return 0.0, 0.0, None
    except Exception as exc:
        logger.debug("estimate_fill_on_bids error: %s", exc)
        return 0.0, 0.0, None


def get_best_bid_price(book: dict) -> Optional[float]:
    try:
        bids = book.get("bids", []) if isinstance(book, dict) else []
        best = 0.0
        for level in bids:
            try:
                price = float(level.get("price"))
                size = float(level.get("size"))
            except Exception:
                continue
            if size > 0 and price > best:
                best = price
        return best if best > 0 else None
    except Exception:
        return None
