"""Core arbitrage comparison logic."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, Iterable, List, Optional

from loguru import logger
from thefuzz import fuzz

from . import config
from .logging_utils import log_opportunity_change
from .matching import MatchCandidate, match_approver, normalize_title
from .orderbook import fetch_order_book, summarize_liquidity_to_price
from .state import BotState
from .trading import check_trade_cooldown, place_polymarket_trade, register_paper_position


def calculate_decimal_odds(price: Optional[float]) -> Optional[float]:
    if price is None:
        return None
    try:
        price_f = float(price)
        if 0 < price_f < 1:
            return 1 / price_f
    except (TypeError, ValueError):
        return None
    return None


def find_polymarket_moneyline_market(polymarket_event: dict) -> Optional[dict]:
    markets = polymarket_event.get("markets", [])
    title = polymarket_event.get("title")

    for market in markets:
        if market.get("sportsMarketType") == "moneyline":
            logger.trace("Found explicit moneyline market for '%s'", title)
            return market

    best_score = 0
    best_market = None
    for market in markets:
        question = market.get("question") or ""
        try:
            outcomes = json.loads(market.get("outcomes", "[]"))
        except Exception:
            outcomes = []
        if len(outcomes) not in (2, 3):
            continue
        score = fuzz.ratio(normalize_title(title), normalize_title(question))
        if score > best_score:
            best_score = score
            best_market = market
    if best_market and best_score > 95:
        logger.trace("Fuzzy-identified moneyline market for '%s' (score %s)", title, best_score)
        return best_market
    return None


def build_moneyline_from_binary_markets(polymarket_event: dict, home_name: str, away_name: str) -> Dict[str, dict]:
    results: Dict[str, dict] = {}
    home_l = (home_name or "").lower()
    away_l = (away_name or "").lower()

    for market in polymarket_event.get("markets", []):
        try:
            if not market.get("active") or market.get("closed"):
                continue
            if not market.get("enableOrderBook", True):
                continue

            ql = (market.get("question") or "").lower()
            gil = (market.get("groupItemTitle") or "").lower()

            key = None
            if "draw" in ql or "draw" in gil:
                key = "draw"
            elif home_l and (home_l in ql or home_l == gil) and ("win" in ql or home_l == gil):
                key = "home"
            elif away_l and (away_l in ql or away_l == gil) and ("win" in ql or away_l == gil):
                key = "away"
            if not key:
                continue

            try:
                prices = json.loads(market.get("outcomePrices", "[]"))
            except Exception:
                try:
                    prices = json.loads(market.get("prices", "[]"))
                except Exception:
                    prices = []
            try:
                tokens = json.loads(market.get("clobTokenIds", "[]"))
            except Exception:
                tokens = []
            if not prices:
                continue

            p_yes = float(prices[0])
            if not (0.001 <= p_yes <= 0.999):
                continue

            token_id = tokens[0] if tokens else None
            liquidity = float(market.get("liquidityNum", 0) or 0.0)

            results[key] = {
                "market": market,
                "p_yes": p_yes,
                "token_id": token_id,
                "liquidity": liquidity,
            }
        except Exception:
            continue

    return results


def create_test_pinnacle_event(polymarket_event: dict) -> Optional[dict]:
    try:
        moneyline_market = find_polymarket_moneyline_market(polymarket_event)
        if not moneyline_market:
            return None

        outcomes = json.loads(moneyline_market.get("outcomes", "[]"))
        prices = json.loads(moneyline_market.get("prices", "[]")) or json.loads(
            moneyline_market.get("outcomePrices", "[]") or "[]"
        )
        if len(outcomes) < 2 or len(prices) < 2:
            return None

        target_price = None
        target_outcome = None
        for name, price in zip(outcomes, prices):
            price_float = float(price)
            if 0.001 <= price_float <= 0.999:
                target_price = price_float
                target_outcome = name
                break
        if target_price is None:
            return None

        o_pm = 1 / target_price
        test_pinnacle_odd = max(1.01, o_pm / 1.15)
        home_name = outcomes[0]
        away_name = outcomes[1]
        win1 = 15.0
        win2 = 15.0
        if target_outcome == home_name:
            win1 = test_pinnacle_odd
        elif target_outcome == away_name:
            win2 = test_pinnacle_odd

        match_id = f"test_{polymarket_event.get('id', int(time.time()))}"
        return {
            "Pid": int(f"999{polymarket_event['id']}") if polymarket_event.get("id") else int(time.time()),
            "LeagueName": polymarket_event.get("series", [{}])[0].get("title", "Test League"),
            "homeName": home_name,
            "awayName": away_name,
            "MatchId": match_id,
            "match": f"{home_name} vs {away_name}",
            "isLive": True,
            "HomeScore": 0,
            "AwayScore": 0,
            "Periods": [
                {
                    "Win1x2": {
                        "Win1": {"value": win1},
                        "Win2": {"value": win2},
                        "WinNone": {"value": 15.0},
                    }
                }
            ],
        }
    except Exception as exc:
        logger.debug("Failed to create test Pinnacle event: %s", exc)
        return None


async def run_strategy(state: BotState) -> None:
    while True:
        current_pinnacle = dict(state.pinnacle_data)

        if config.settings.test_mode and state.polymarket_data:
            logger.warning("TEST_MODE active: injecting synthetic Pinnacle event for validation")
            for pm_event in state.polymarket_data.values():
                test_event = create_test_pinnacle_event(pm_event)
                if test_event:
                    current_pinnacle[test_event["MatchId"]] = test_event
                    break

        logger.info(
            "Strategy tick: %s Pinnacle events vs %s Polymarket events",
            len(current_pinnacle),
            len(state.polymarket_data),
        )

        for pin_event_id, pin_event in current_pinnacle.items():
            pin_title = pin_event.get("match")
            if not pin_title:
                continue

            pm_event, score = _find_and_confirm_match(pin_title, state.polymarket_data.values())
            if not pm_event:
                continue

            pin_odds_list = _extract_pinnacle_odds(pin_event)
            if not pin_odds_list:
                continue

            moneyline_market = find_polymarket_moneyline_market(pm_event)
            if moneyline_market:
                await _process_moneyline_market(state, pin_event_id, pin_title, pin_odds_list, pm_event, moneyline_market)
            else:
                await _process_binary_markets(state, pin_event_id, pin_title, pin_odds_list, pm_event)

        await asyncio.sleep(2)


def _find_and_confirm_match(pin_title: str, polymarket_events: Iterable[dict]) -> tuple[Optional[dict], int]:
    best_event = None
    best_score = 0
    for event in polymarket_events:
        score = fuzz.token_sort_ratio(normalize_title(pin_title), normalize_title(event.get("title")))
        if score > best_score:
            best_score = score
            best_event = event
    if not best_event or best_score < 70:
        return None, best_score

    candidate = MatchCandidate(
        pinnacle_title=pin_title,
        polymarket_title=best_event.get("title", ""),
        polymarket_id=best_event.get("id", ""),
        score=best_score,
    )
    if not match_approver.is_approved(candidate):
        return None, best_score
    logger.info("Match confirmed: '%s' ↔ '%s' (score %s)", pin_title, best_event.get("title"), best_score)
    return best_event, best_score


def _extract_pinnacle_odds(pin_event: dict) -> List[dict]:
    results: List[dict] = []
    periods = pin_event.get("Periods")
    if not periods or not isinstance(periods, list):
        return results
    first_period = periods[0] or {}
    win1x2 = first_period.get("Win1x2") if isinstance(first_period, dict) else None
    if not isinstance(win1x2, dict):
        return results

    home_name = pin_event.get("homeName")
    away_name = pin_event.get("awayName")

    if home_name and win1x2.get("Win1", {}).get("value"):
        results.append({"name": home_name, "price": win1x2["Win1"]["value"]})
    if away_name and win1x2.get("Win2", {}).get("value"):
        results.append({"name": away_name, "price": win1x2["Win2"]["value"]})
    if win1x2.get("WinNone", {}).get("value"):
        results.append({"name": "Draw", "price": win1x2["WinNone"]["value"]})
    return results


async def _process_moneyline_market(
    state: BotState,
    pin_event_id: str,
    pin_title: str,
    pin_odds_list: List[dict],
    pm_event: dict,
    market: dict,
) -> None:
    try:
        outcomes = json.loads(market.get("outcomes", "[]"))
        prices = json.loads(market.get("outcomePrices", "[]"))
        tokens = json.loads(market.get("clobTokenIds", "[]"))
    except Exception:
        return

    for idx, outcome_name in enumerate(outcomes):
        pin_outcome = next(
            (
                o
                for o in pin_odds_list
                if outcome_name and o.get("name", "").lower() in outcome_name.lower()
            ),
            None,
        )
        if not pin_outcome or idx >= len(prices):
            continue

        try:
            polymarket_price = float(prices[idx])
        except Exception:
            polymarket_price = None
        o_pm = calculate_decimal_odds(polymarket_price)
        await _evaluate_opportunity(
            state,
            pin_event_id,
            pin_title,
            pm_event,
            outcome_name,
            pin_outcome.get("price"),
            o_pm,
            polymarket_price,
            tokens[idx] if idx < len(tokens) else None,
            float(market.get("liquidityNum", 0) or 0.0),
            market.get("id"),
        )


async def _process_binary_markets(
    state: BotState,
    pin_event_id: str,
    pin_title: str,
    pin_odds_list: List[dict],
    pm_event: dict,
) -> None:
    home_name = pin_event.get("homeName")
    away_name = pin_event.get("awayName")
    if not home_name or not away_name:
        return

    assembled = build_moneyline_from_binary_markets(pm_event, home_name, away_name)
    mapping = [
        ("home", next((o for o in pin_odds_list if o.get("name") == home_name), None), home_name),
        ("draw", next((o for o in pin_odds_list if o.get("name") == "Draw"), None), "Draw"),
        ("away", next((o for o in pin_odds_list if o.get("name") == away_name), None), away_name),
    ]

    for key, pin_outcome, label in mapping:
        if key not in assembled or not pin_outcome:
            continue
        data = assembled[key]
        o_pm = calculate_decimal_odds(data.get("p_yes"))
        await _evaluate_opportunity(
            state,
            pin_event_id,
            pin_title,
            pm_event,
            label,
            pin_outcome.get("price"),
            o_pm,
            data.get("p_yes"),
            data.get("token_id"),
            data.get("liquidity", 0.0),
            data.get("market", {}).get("id"),
        )


async def _evaluate_opportunity(
    state: BotState,
    pin_event_id: str,
    pin_title: str,
    pm_event: dict,
    outcome_label: str,
    o_pin: Optional[float],
    o_pm: Optional[float],
    polymarket_price: Optional[float],
    token_id: Optional[str],
    liquidity: float,
    market_id: Optional[str],
) -> None:
    if not (o_pin and o_pm and polymarket_price is not None):
        return

    ratio = o_pm / o_pin if o_pin else None
    edge_pct = ((ratio - 1.0) * 100.0) if ratio else None

    threshold_price = 1.0 / (o_pin * config.ARB_RATIO)
    avail_shares_at_th = avail_usd_at_th = wavg_price_at_th = None
    if token_id:
        book = await fetch_order_book(token_id)
        if book:
            s, u, w = summarize_liquidity_to_price(book, threshold_price)
            avail_shares_at_th, avail_usd_at_th, wavg_price_at_th = s, u, w
        else:
            avail_shares_at_th = 0.0
            avail_usd_at_th = 0.0

    log_opportunity_change(
        mkey=pin_title or str(pin_event_id),
        okey=outcome_label,
        o_pin=o_pin or 0.0,
        p_yes=polymarket_price or 0.0,
        o_pm=o_pm or 0.0,
        ratio=ratio or 0.0,
        edge_pct=edge_pct,
        liquidity=liquidity,
        pm_market_id=market_id or "",
        token_id=token_id,
        trigger_type="INFO",
        reason="scan",
        avail_shares_at_th=avail_shares_at_th,
        avail_usd_at_th=avail_usd_at_th,
        wavg_price_at_th=wavg_price_at_th,
    )

    if not (ratio and ratio >= config.ARB_RATIO):
        return

    cooldown_key = token_id or f"{market_id}:{outcome_label}"
    if not check_trade_cooldown(state, cooldown_key, polymarket_price):
        return

    bet_amount = config.settings.bet_amount_usd
    if liquidity < bet_amount:
        logger.warning(
            "Skipping %s: liquidity %.2f insufficient for bet %.2f.",
            outcome_label,
            liquidity,
            bet_amount,
        )
        return

    if avail_usd_at_th is not None and avail_usd_at_th < bet_amount:
        logger.warning(
            "Skipping %s: depth at threshold %.2f < bet %.2f.",
            outcome_label,
            avail_usd_at_th,
            bet_amount,
        )
        return

    log_opportunity_change(
        mkey=pin_title or str(pin_event_id),
        okey=outcome_label,
        o_pin=o_pin or 0.0,
        p_yes=polymarket_price or 0.0,
        o_pm=o_pm or 0.0,
        ratio=ratio,
        edge_pct=edge_pct,
        liquidity=liquidity,
        pm_market_id=market_id or "",
        token_id=token_id,
        trigger_type="ARBITRAGE",
        reason="threshold",
        avail_shares_at_th=avail_shares_at_th,
        avail_usd_at_th=avail_usd_at_th,
        wavg_price_at_th=wavg_price_at_th,
    )

    trade_details = {
        "timestamp_utc": time.time(),
        "pinnacle_match_id": pin_event_id,
        "polymarket_event_id": pm_event.get("id"),
        "polymarket_market_id": market_id,
        "polymarket_token_id": token_id,
        "match_title": pin_title,
        "outcome_name": outcome_label,
        "pinnacle_odds": o_pin,
        "polymarket_price": polymarket_price,
        "calculated_polymarket_odds": o_pm,
        "liquidity_available": liquidity,
        "bet_amount_usd": bet_amount,
        "size_shares": (bet_amount / polymarket_price) if polymarket_price else None,
    }

    if config.settings.sell_mode in {"paper", "both"} and token_id:
        register_paper_position(
            state,
            token_id,
            {
                "entry_ts": time.time(),
                "mkey": pin_title or str(pin_event_id),
                "okey": outcome_label,
                "pm_market_id": market_id,
                "token_id": token_id,
                "entry_price": polymarket_price,
                "target_usd": bet_amount,
                "shares": bet_amount / polymarket_price if polymarket_price else None,
            },
        )
        if config.settings.sell_mode == "paper":
            return

    await place_polymarket_trade(state, trade_details)
