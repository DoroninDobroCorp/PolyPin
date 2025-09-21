import asyncio
import json
import os
import pathlib
import sys
import time
from collections import defaultdict, deque

import csv
import httpx
import websockets
from loguru import logger
from typing import Optional, Tuple, Dict
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from dotenv import load_dotenv
from thefuzz import fuzz

# --- Directory for Detailed Trade Logs ---
LOGS_DIR = pathlib.Path(__file__).parent / "trade_logs"

# Configure logger to show trace level messages
logger.remove()
logger.add(sys.stderr, level="TRACE")

# --- Opportunity change logging (CSV) ---
OPPORTUNITY_LOG_DIR = pathlib.Path(__file__).parent / "opportunity_logs"
OPPORTUNITY_LOG_FILE = OPPORTUNITY_LOG_DIR / "opportunities_changes.csv"
last_opportunity_state = {}

# --- Orderbook cache (to avoid hammering endpoint) ---
ORDERBOOK_CACHE = {}
ORDERBOOK_TTL_SEC = 2.0

def ensure_opportunity_logs_initialized():
    try:
        OPPORTUNITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not OPPORTUNITY_LOG_FILE.exists():
            with open(OPPORTUNITY_LOG_FILE, "w", newline="") as f:
                writer = csv.writer(f)
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
    except Exception as e:
        logger.error(f"Failed to init opportunity log: {e}")

def ensure_paper_trades_log_initialized():
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        paper_log = LOGS_DIR / "paper_trades.csv"
        if not paper_log.exists():
            with open(paper_log, "w", newline="") as f:
                writer = csv.writer(f)
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
    except Exception as e:
        logger.error(f"Failed to init paper trades log: {e}")

def log_opportunity_change(
    mkey: str,
    okey: str,
    *,
    o_pin: float,
    p_yes: float,
    o_pm: float,
    ratio: float,
    edge_pct: float,
    liquidity: float,
    pm_market_id: str,
    token_id: str,
    trigger_type: str,
    reason: str,
    avail_shares_at_th: Optional[float] = None,
    avail_usd_at_th: Optional[float] = None,
    wavg_price_at_th: Optional[float] = None,
):
    """Логирует изменения только при существенных сдвигах для наглядности.

    Условия логирования:
      - первая запись по (mkey, okey)
      - изменение ratio >= 0.01
      - изменение цен (o_pin или p_yes)
      - пересечение порога 1.12 вверх помечается явно
    """
    try:
        key = (mkey, okey)
        prev = last_opportunity_state.get(key)
        should_log = False
        change_reason = reason

        if prev is None:
            should_log = True
            change_reason = "new"
        else:
            prev_ratio = prev.get("ratio")
            prev_o_pin = prev.get("o_pin")
            prev_p_yes = prev.get("p_yes")

            if prev_ratio is None or abs(ratio - prev_ratio) >= 0.01:
                should_log = True
                change_reason = f"ratio_delta={ratio - (prev_ratio or 0):.4f}"
            elif (o_pin != prev_o_pin) or (p_yes != prev_p_yes):
                should_log = True
                change_reason = "price_change"

            # Явная пометка момента появления арбитража
            if trigger_type == "ARBITRAGE" and prev_ratio is not None and prev_ratio < 1.12 <= ratio:
                should_log = True
                change_reason = "cross_up_1.12"

        if should_log:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            with open(OPPORTUNITY_LOG_FILE, "a", newline="") as f:
                writer = csv.writer(f)
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
            last_opportunity_state[key] = {"ratio": ratio, "o_pin": o_pin, "p_yes": p_yes}
    except Exception as e:
        logger.error(f"Failed to write opportunity change log: {e}")

# --- Orderbook helpers ---
async def fetch_order_book(token_id: str) -> Optional[dict]:
    """Получает ордербук Polymarket для данного token_id.

    Пытается использовать несколько вариантов параметров запроса, так как публичные доки допускают разные названия.
    Результат кэшируется на ORDERBOOK_TTL_SEC, чтобы не перегружать API.
    """
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
                        # Ensure this looks like a book
                        if isinstance(data, dict) and "asks" in data and "bids" in data:
                            ORDERBOOK_CACHE[token_id] = (data, now)
                            return data
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"fetch_order_book error for token {token_id}: {e}")
    return None

def summarize_liquidity_to_price(book: dict, max_price: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Суммирует доступные объёмы на стороне ask до max_price включительно.

    Возвращает (shares, usd, wavg_price). Если нет доступных уровней — (None, None, None).
    """
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
    except Exception as e:
        logger.debug(f"summarize_liquidity_to_price error: {e}")
    return None, None, None

def estimate_fill_on_bids(book: dict, min_price: float, target_usd: float) -> Tuple[float, float, Optional[float]]:
    """Оценивает заполняемость по стороне bid при условии цены >= min_price.

    Возвращает (filled_usd, filled_shares, wavg_price). Если нет ликвидности — (0, 0, None).
    """
    try:
        bids = book.get("bids", []) if isinstance(book, dict) else []
        # Сортируем по цене по убыванию, чтобы сначала брать лучшие цены
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
            # доступный $ на этом уровне
            level_usd = price * size
            need_usd = target_usd - filled_usd
            if level_usd <= need_usd + 1e-9:
                # берём весь уровень
                filled_usd += level_usd
                filled_shares += size
            else:
                # частичное заполнение уровня
                add_shares = need_usd / price
                filled_usd += need_usd
                filled_shares += add_shares
                break

        if filled_shares > 0:
            return filled_usd, filled_shares, filled_usd / filled_shares
        return 0.0, 0.0, None
    except Exception as e:
        logger.debug(f"estimate_fill_on_bids error: {e}")
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

# --- Globals ---
pinnacle_data = {}
polymarket_data = {}
recent_trades = defaultdict(list)  # Cache for recent trades to avoid duplicates
TEST_MODE = False  # <<< SET TO True TO INJECT A FAKE ARBITRAGE OPPORTUNITY

# --- Historical Data for Logging ---
# Deques to store a rolling window of the last ~5-10 minutes of data
pinnacle_history = deque(maxlen=500)
polymarket_history = deque(maxlen=500)
background_tasks = set()


# --- Polymarket Client Configuration ---
# Load credentials from environment/.env to avoid hardcoding secrets
load_dotenv()

# PRIVATE KEY for signing (EOA or Magic/Wallet-derived)
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")

# Polymarket Proxy address (a.k.a. funder address shown on profile). Required for signature_type 1 or 2
POLY_PROXY_ADDRESS = os.getenv("POLY_PROXY_ADDRESS") or os.getenv("FUNDER_ADDRESS")

# Signature type per py_clob_client:
# 1 = Email/Magic, 2 = Browser wallet (Metamask/Coinbase), unset = direct EOA
SIGNATURE_TYPE = os.getenv("POLY_SIGNATURE_TYPE")

# --- Strategy/Trading Config ---
# Default bet amount $5 as per spec (configurable via env)
def _float_env(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except Exception:
        return float(default)

BET_AMOUNT_USD: float = _float_env("BET_AMOUNT_USD", "5")
TAKE_PROFIT_ABS: float = _float_env("TAKE_PROFIT_ABS", "0.01")  # +$0.01 per share
STOP_LOSS_ABS: float = _float_env("STOP_LOSS_ABS", "0.03")      # -$0.03 per share
SELL_MODE: str = (os.getenv("SELL_MODE", "paper") or "paper").lower()  # 'paper' or 'live'

# Paper positions tracker for evaluating SELL strategy
PAPER_POSITIONS: Dict[str, dict] = {}
PAPER_TRADES_LOG_FILE = LOGS_DIR / "paper_trades.csv"

clob_client: ClobClient = None


def get_clob_client():
    """Initializes and returns a singleton ClobClient instance (per docs).

    Modes:
      - signature_type=1 + funder=POLY_PROXY_ADDRESS (Email/Magic login)
      - signature_type=2 + funder=POLY_PROXY_ADDRESS (Browser wallet login)
      - direct EOA (no signature_type, no funder)
    """
    global clob_client
    if clob_client is not None:
        return clob_client

    if not PRIVATE_KEY:
        logger.error("Polymarket PRIVATE_KEY is not set. Set POLY_PRIVATE_KEY in env/.env")
        return None

    host = "https://clob.polymarket.com"

    try:
        if SIGNATURE_TYPE and SIGNATURE_TYPE.strip() in {"1", "2"}:
            if not POLY_PROXY_ADDRESS:
                logger.error(
                    "POLY_SIGNATURE_TYPE is set but POLY_PROXY_ADDRESS is missing. Cannot init ClobClient."
                )
                return None
            sig_type = int(SIGNATURE_TYPE.strip())
            logger.info(
                f"Initializing ClobClient (signature_type={sig_type}, proxy mode) for Polymarket..."
            )
            clob_client = ClobClient(
                host,
                key=PRIVATE_KEY,
                chain_id=137,
                signature_type=sig_type,
                funder=POLY_PROXY_ADDRESS,
            )
        else:
            # Direct EOA mode
            if POLY_PROXY_ADDRESS:
                logger.warning(
                    "POLY_PROXY_ADDRESS provided without POLY_SIGNATURE_TYPE; proceeding in EOA mode (funder ignored)."
                )
            logger.info("Initializing ClobClient (EOA mode) for Polymarket...")
            clob_client = ClobClient(host, key=PRIVATE_KEY, chain_id=137)

        clob_client.set_api_creds(clob_client.create_or_derive_api_creds())
        logger.success("ClobClient initialized successfully.")
        return clob_client
    except Exception as e:
        logger.error(f"Failed to initialize ClobClient: {e}")
        return None


# --- Detailed Logging ---
async def save_trade_log(trade_details: dict, pre_trade_history: list):
    """
    Waits 120 seconds after a trade, collects post-trade data, and saves a comprehensive
    log file covering the T-60s to T+120s window.
    """
    trade_time = trade_details["timestamp_utc"]
    pinnacle_match_id = trade_details["pinnacle_match_id"]
    logger.info(
        f"TRADE EXECUTED ({pinnacle_match_id}). Waiting 120s to capture post-trade market data for logging."
    )

    await asyncio.sleep(120)

    logger.info(f"LOGGING ({pinnacle_match_id}): Collecting post-trade data...")
    # Combine the history deques into a single list for filtering
    combined_history = list(pinnacle_history) + list(polymarket_history)

    # Filter for items that occurred in the 120-second window after the trade
    post_trade_history = [
        item
        for item in combined_history
        if trade_time < item["timestamp"] <= trade_time + 120
    ]

    full_log = {
        "trade_details": trade_details,
        "pre_trade_window_60s": pre_trade_history,
        "post_trade_window_120s": post_trade_history,
    }

    # Ensure the log directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Create a unique filename
    log_filename = f"trade_{pinnacle_match_id}_{int(trade_time)}.json"
    filepath = LOGS_DIR / log_filename

    try:
        with open(filepath, "w") as f:
            json.dump(full_log, f, indent=4)
        logger.success(f"Successfully saved detailed trade log to: {filepath}")
    except Exception as e:
        logger.error(f"Failed to save trade log file: {e}")


# --- Test Data Generation ---
def create_test_pinnacle_event(polymarket_event: dict):
    """
    Generates a realistic fake Pinnacle event based on a real Polymarket event
    to trigger a valid and testable arbitrage condition.
    It calculates a Pinnacle odd that is guaranteed to be slightly better than
    the Polymarket odds, ensuring the price is within valid trading limits.
    """
    logger.warning(
        "Creating a test Pinnacle event based on Polymarket event: {}",
        polymarket_event.get("title"),
    )
    try:
        # Find the moneyline market to use as a template
        moneyline_market = find_polymarket_moneyline_market(polymarket_event)
        if not moneyline_market:
            return None

        outcomes = json.loads(moneyline_market.get("outcomes", "[]"))
        prices = json.loads(moneyline_market.get("prices", "[]"))

        if len(outcomes) < 2 or len(prices) < 2:
            return None

        # Find a valid price from the polymarket event to build our test case on
        target_price = None
        target_outcome_name = None

        # We iterate through the outcomes to find one with a valid price
        for i, price in enumerate(prices):
            price_float = float(price)
            if 0.001 <= price_float <= 0.999:
                target_price = price_float
                target_outcome_name = outcomes[i]
                break

        if target_price is None:
            logger.warning(
                f"Could not find a Polymarket price in the valid range for test event '{polymarket_event.get('title')}'. Skipping test event creation."
            )
            return None

        # Calculate the Polymarket odds from the valid price
        o_pm = 1 / target_price

        # Calculate a Pinnacle odd that is guaranteed to trigger the arbitrage by a small margin.
        # The arbitrage condition is: o_pm >= o_pin * 1.12  =>  o_pin <= o_pm / 1.12
        # We set o_pin slightly lower to ensure the condition is met comfortably.
        test_pinnacle_odd = o_pm / 1.15

        # Ensure the generated odd is realistic (Pinnacle odds are usually > 1.0)
        if test_pinnacle_odd < 1.01:
            test_pinnacle_odd = 1.01  # Set a floor for realistic odds

        logger.info(
            f"Generating test event for '{target_outcome_name}'. PM Price: {target_price:.4f} (Odds: {o_pm:.2f}). Calculated Pinnacle Odd: {test_pinnacle_odd:.2f}"
        )

        # Create a fake Pinnacle event with the calculated odds.
        # We need to map the target_outcome to either homeName or awayName.
        home_name = outcomes[0]
        away_name = outcomes[1]

        win1_odd = 15.0  # default high odd
        win2_odd = 15.0  # default high odd

        if target_outcome_name == home_name:
            win1_odd = test_pinnacle_odd
        elif target_outcome_name == away_name:
            win2_odd = test_pinnacle_odd
        # Note: This logic assumes a 2-way market for simplicity. If the valid price
        # belongs to a third outcome (draw), this test might not behave as expected,
        # but it's sufficient for testing the main buy logic.

        test_event = {
            "Pid": int(f"999{polymarket_event['id']}"),  # Create a fake unique ID
            "LeagueName": polymarket_event.get("series", [{}])[0].get(
                "title", "Test League"
            ),
            "homeName": home_name,
            "awayName": away_name,
            "MatchId": f"test_{polymarket_event['id']}",
            "isLive": True,
            "HomeScore": 0,
            "AwayScore": 0,
            "Periods": [
                {
                    "Win1x2": {
                        "Win1": {"value": round(win1_odd, 4)},
                        "WinNone": {"value": 0},
                        "Win2": {"value": round(win2_odd, 4)},
                    }
                }
            ],
            "Source": "Pinnacle (Test)",
            "SportName": polymarket_event.get("tags", [{}])[0].get(
                "label", "Test Sport"
            ),
            "match": f"{home_name} vs {away_name}",
        }
        logger.warning(
            f"Created a test Pinnacle event based on Polymarket event: {polymarket_event.get('title')}"
        )
        return test_event
    except Exception as e:
        logger.error(f"Failed to create test event: {e}")
        return None


def find_polymarket_moneyline_market(polymarket_event: dict):
    """
    Finds the most likely moneyline market within a Polymarket event.
    First, it looks for an explicit 'moneyline' type. If none is found,
    it looks for a market where the question is a near-perfect match
    to the event title, which is a strong heuristic for the main market.
    """
    pm_markets = polymarket_event.get("markets", [])
    pm_title = polymarket_event.get("title")

    # 1. Primary Method: Look for the explicit 'moneyline' type.
    for market in pm_markets:
        if market.get("sportsMarketType") == "moneyline":
            logger.trace(f"Found explicit moneyline market for '{pm_title}'")
            return market

    # 2. Secondary Method: Fuzzy match question against the event title.
    # This is effective because spread/total markets have different questions.
    best_title_match_score = 0
    best_market_candidate = None
    for market in pm_markets:
        market_question = market.get("question")
        if not market_question:
            continue

        # We only consider markets with 2 or 3 outcomes as candidates
        num_outcomes = len(json.loads(market.get("outcomes", "[]")))
        if num_outcomes not in [2, 3]:
            continue

        score = fuzz.ratio(pm_title, market_question)
        if score > best_title_match_score:
            best_title_match_score = score
            best_market_candidate = market

    # Use a high threshold to be confident it's the main market
    if best_title_match_score > 95:
        logger.trace(
            f"Found moneyline market for '{pm_title}' by title match (Score: {best_title_match_score}%)"
        )
        return best_market_candidate

    logger.debug(
        f"No suitable moneyline market found for Polymarket event: '{pm_title}'"
    )
    return None


def build_moneyline_from_binary_markets(polymarket_event: dict, home_name: str, away_name: str):
    """
    Fallback builder: synthesize a moneyline from three separate binary markets
    (home win / draw / away win) commonly used on Polymarket.

    Returns a dict with optional keys 'home', 'draw', 'away', each containing:
    {
        'market': <market_dict>,
        'p_yes': <float price for YES>,
        'token_id': <str>,
        'liquidity': <float>,
        'order_min_size': <float or int or None>,
    }
    If none found, returns {}.
    """
    try:
        results = {}

        home_l = (home_name or "").lower()
        away_l = (away_name or "").lower()

        for m in polymarket_event.get("markets", []):
            try:
                if not m.get("active") or m.get("closed"):
                    continue
                if not m.get("enableOrderBook", True):
                    continue

                ql = (m.get("question") or "").lower()
                gil = (m.get("groupItemTitle") or "").lower()

                key = None
                # Draw detection
                if ("draw" in ql) or ("draw" in gil):
                    key = "draw"
                # Home win
                elif (home_l and (home_l in ql or home_l == gil)) and ("win" in ql or home_l == gil):
                    key = "home"
                # Away win
                elif (away_l and (away_l in ql or away_l == gil)) and ("win" in ql or away_l == gil):
                    key = "away"

                if not key:
                    continue

                # Parse prices and token ids
                prices = []
                tokens = []
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                except Exception:
                    try:
                        prices = json.loads(m.get("prices", "[]"))
                    except Exception:
                        prices = []

                try:
                    tokens = json.loads(m.get("clobTokenIds", "[]"))
                except Exception:
                    tokens = []

                if not prices or len(prices) < 1:
                    continue

                p_yes = float(prices[0])  # YES is index 0 for binary markets
                if not (0.001 <= p_yes <= 0.999):
                    continue

                token_id = tokens[0] if tokens else None
                liquidity = float(m.get("liquidityNum", 0) or 0)
                order_min_size = m.get("orderMinSize")

                results[key] = {
                    "market": m,
                    "p_yes": p_yes,
                    "token_id": token_id,
                    "liquidity": liquidity,
                    "order_min_size": order_min_size,
                }

            except Exception:
                # Skip malformed market silently
                continue

        return results
    except Exception as e:
        logger.error(f"Failed to build moneyline from binary markets: {e}")
        return {}


# --- TRADING LOGIC ---
# This section will contain functions related to executing trades.


async def place_polymarket_trade(trade_details: dict):
    """
    Attempts to place a trade on Polymarket. If the trade fails due to a minimum
    size error, it is treated as a successful attempt for logging and cooldown
    purposes, as per user request.
    """
    logger.success("--- ATTEMPTING TRADE ON POLYMARKET ---")

    # Log details as per specification.md
    logger.info("Trade Details:")
    logger.info(
        f"  Timestamp (UTC): {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(trade_details['timestamp_utc']))}"
    )
    logger.info("  Sources: Pinnacle / Polymarket")
    logger.info(
        f"  Match (Pinnacle ID): {trade_details['pinnacle_match_id']} - {trade_details['match_title']}"
    )
    logger.info(f"  Market (Polymarket ID): {trade_details['polymarket_market_id']}")
    logger.info(f"  Outcome: {trade_details['outcome_name']}")
    logger.info("  Bet Type: YES (buy)")
    logger.info(f"  Price (Polymarket): {trade_details['polymarket_price']:.4f}")
    logger.info(f"  Volume (Bet Amount): ${trade_details['bet_amount_usd']:.2f}")

    client = get_clob_client()
    if not client:
        logger.error("Cannot place trade: ClobClient is not available.")
        return False

    is_successful_for_cooldown = False

    try:
        # Prepare the order (size is in shares, not USD)
        price = float(trade_details["polymarket_price"]) if trade_details.get("polymarket_price") is not None else 0.0
        if price <= 0 or price >= 1:
            raise ValueError(f"Invalid polymarket price for order: {price}")
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

        # Post the order
        logger.info("Posting order to Polymarket CLOB...")
        resp = client.post_order(signed_order, OrderType.GTC)
        logger.success(f"Polymarket order posted successfully! Response: {resp}")

        is_successful_for_cooldown = True
        trade_details["trade_status"] = "SUCCESS"
        trade_details["api_response"] = resp

    except Exception as e:
        logger.error(f"An error occurred while placing Polymarket trade: {e}")
        trade_details["trade_status"] = "FAILURE"
        trade_details["trade_error"] = str(e)

        # Per user request: treat "minimum size" error as a "successful" attempt for logging/cooldown
        if "lower than the minimum" in str(e):
            logger.warning(
                "Trade failed due to minimum size limit. Treating as 'executed' for logging and cooldown purposes."
            )
            is_successful_for_cooldown = True
            trade_details[
                "trade_status"
            ] = "SKIPPED_MIN_SIZE"  # Overwrite status for clarity in logs

    if is_successful_for_cooldown:
        # Record the trade to prevent immediate re-buys
        market_id = trade_details["polymarket_market_id"]
        token_id = trade_details.get("polymarket_token_id")
        outcome_name = trade_details.get("outcome_name")
        price = trade_details["polymarket_price"]
        cooldown_key = token_id or f"{market_id}:{outcome_name}"
        recent_trades[cooldown_key].append({"timestamp": time.time(), "price": price})
        logger.info(f"Trade attempt recorded to local cache (cooldown key: {cooldown_key}).")

        # --- Trigger Detailed Logging ---
        trade_time = trade_details["timestamp_utc"]
        # Combine history deques into a single list for easier filtering
        combined_history = list(pinnacle_history) + list(polymarket_history)

        # Snapshot the history from the 60 seconds leading up to the trade
        pre_trade_history = [
            item
            for item in combined_history
            if trade_time - 60 <= item["timestamp"] <= trade_time
        ]

        # Start the background task to save the full log after 120s
        task = asyncio.create_task(save_trade_log(trade_details, pre_trade_history))
        background_tasks.add(task)
        task.add_done_callback(
            background_tasks.discard
        )  # Ensures the task is removed from the set when done

        return True  # Return true because it was "successful" for our purposes

    return False


def check_trade_cooldown(cooldown_key: str, current_price: float) -> bool:
    """
    Checks if a trade for this market outcome is on cooldown.
    As per spec: "if по данному исходу в последние 2 минуты уже была
    покупка по цене не хуже текущей, новую не открывать."

    Returns True if a trade should be placed, False if it's on cooldown.
    """
    cooldown_period_seconds = 120
    now = time.time()

    # Clean up old trades from the cache
    recent_trades[cooldown_key] = [
        trade
        for trade in recent_trades[cooldown_key]
        if now - trade["timestamp"] < cooldown_period_seconds
    ]

    # Check if any recent trade was made at a price better or equal to the current one
    for trade in recent_trades[cooldown_key]:
        if current_price >= trade["price"]:
            logger.warning(
                f"Skipping trade for key {cooldown_key}. "
                f"Recent trade at price {trade['price']} is not worse than current price {current_price}."
            )
            return False

    return True


# --- DATA FETCHING AND PROCESSING ---

# URL for Polymarket API found by the user
POLYMARKET_API_URL = "https://gamma-api.polymarket.com/events"


async def pinnacle_handler(websocket: websockets.ClientConnection):
    """
    Handles incoming data from the Pinnacle parser WebSocket.
    For websockets library >= 10.1, the `path` argument is removed from the handler's
    signature. It's accessible via `websocket.path` if needed.
    """
    logger.info("Pinnacle parser connected from path: {}", websocket.remote_address)
    try:
        async for message in websocket:
            # logger.trace("Raw message received from Pinnacle: {}", len(message))
            data = json.loads(message)
            # with open('pinnacle_data.json', 'w') as f:
            #     json.dump(message, f, indent=4)
            # logger.debug(f"Pinnacle: Received data for matchId {data.get('MatchId')}, home: {data.get('homeName')}, away: {data.get('awayName')}")
            if "MatchId" in data and data.get("homeName") and data.get("awayName"):
                # if 'MatchId' in data and data.get('SportName') == 'Soccer':
                # Construct the match title from home and away names, as it's not in the root object
                data["match"] = f"{data['homeName']} vs {data['awayName']}"
                pinnacle_data[data["MatchId"]] = data
                with open("pinnacle_data.json", "w") as f:
                    json.dump(pinnacle_data, f, indent=4)
            #  logger.debug(f"Pinnacle: Stored data for {data['match']}")

            # Add raw data to history for detailed logging
            pinnacle_history.append(
                {"timestamp": time.time(), "source": "Pinnacle", "data": data}
            )

    except websockets.exceptions.ConnectionClosed:
        logger.info("Pinnacle parser disconnected.")
    except Exception as e:
        logger.error(f"An error occurred in pinnacle_handler: {e}")


async def poll_polymarket_data():
    """
    Periodically polls the Polymarket API for event data.
    """
    async with httpx.AsyncClient() as client:
        while True:
            try:
                # Parameters to fetch live sports events, using the correct series_id values
                params = [
                    ("series_id", "10187"),
                    ("series_id", "3"),
                    ("series_id", "10210"),
                    ("series_id", "10105"),
                    ("series_id", "10188"),
                    ("series_id", "10193"),
                    ("series_id", "10194"),
                    ("series_id", "10195"),
                    ("series_id", "10203"),
                    ("series_id", "10189"),
                    ("series_id", "10204"),
                    ("series_id", "10209"),
                    ("series_id", "10238"),
                    ("series_id", "10240"),
                    ("series_id", "10243"),
                    ("series_id", "10244"),
                    ("series_id", "10246"),
                    ("series_id", "10245"),
                    ("series_id", "10242"),
                    ("limit", "500"),
                    ("closed", "false"),
                    ("include_chat", "true"),
                ]
                response = await client.get(
                    POLYMARKET_API_URL, params=params, timeout=10.0
                )
                response.raise_for_status()
                events = response.json()

                new_polymarket_data = {}
                for event in events:
                    # New "smart" live filter:
                    # An event is considered live if it has the 'live' flag,
                    # or if there's an active score, or elapsed time.
                    is_live = (
                        event.get("live") is True
                        or event.get("score") not in [None, "", "0-0"]
                        or event.get("elapsed") not in [None, ""]
                    )

                    if event.get("active") and not event.get("closed") and is_live:
                        new_polymarket_data[event["id"]] = event

                polymarket_data.clear()
                polymarket_data.update(new_polymarket_data)

                with open("polymarket_data.json", "w") as f:
                    json.dump(polymarket_data, f, indent=4)
                logger.info(
                    f"Polymarket: Polled {len(events)} events. Storing {len(polymarket_data)} live events."
                )

                # Add polled data to history for detailed logging
                if new_polymarket_data:
                    polymarket_history.append(
                        {
                            "timestamp": time.time(),
                            "source": "Polymarket",
                            "data": new_polymarket_data,
                        }
                    )

            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Polymarket API request failed with status {e.response.status_code}"
                )
            except Exception as e:
                logger.error(f"An error occurred while polling Polymarket: {e}")

            await asyncio.sleep(5)


def find_matching_polymarket_event(pinnacle_event_title):
    """
    Finds a matching event on Polymarket using fuzzy string matching.
    """
    if not pinnacle_event_title:
        return None, 0

    best_match = None
    highest_score = 0

    for pm_event in polymarket_data.values():
        pm_title = pm_event.get("title")
        if not pm_title:
            continue

        score = fuzz.token_sort_ratio(pinnacle_event_title, pm_title)

        if score > highest_score:
            highest_score = score
            best_match = pm_event

    if highest_score > 80:  # Confidence threshold
        return best_match, highest_score
    return None, highest_score


def calculate_decimal_odds(price_str):
    """
    Calculates decimal odds from Polymarket price.
    O_pm = 1 / p_yes
    """
    try:
        price = float(price_str)
        if 0 < price < 1:
            return 1 / price
    except (ValueError, TypeError):
        return None
    return None


async def comparison_logic():
    """
    Compares data from Pinnacle and Polymarket to find arbitrage opportunities.
    """
    while True:
        # Create a copy of pinnacle_data to avoid modifying it while iterating
        current_pinnacle_data = pinnacle_data.copy()

        # --- TEST MODE INJECTION ---
        if TEST_MODE and polymarket_data:
            logger.warning(
                "TEST MODE ACTIVE: Attempting to inject a fake Pinnacle event..."
            )
            test_event_created = False
            # Iterate through all available Polymarket events until a valid test event can be created.
            for pm_event in polymarket_data.values():
                test_event = create_test_pinnacle_event(pm_event)
                if test_event:
                    current_pinnacle_data[test_event["MatchId"]] = test_event
                    test_event_created = True
                    logger.info(
                        f"Successfully injected test event based on: {pm_event.get('title')}"
                    )
                    break  # Exit the loop once one test event is created

            if not test_event_created:
                logger.warning(
                    "Could not create a test event from any of the current live Polymarket markets."
                )
        # --- END TEST MODE ---

        # Log current state
        logger.info(
            f"Running comparison logic with {len(current_pinnacle_data)} Pinnacle events and {len(polymarket_data)} Polymarket events."
        )

        for pin_event_id, pin_event in current_pinnacle_data.items():
            pin_title = pin_event.get("match")

            pm_event, best_score = find_matching_polymarket_event(pin_title)

            if pm_event:
                logger.info(
                    f"Match found: '{pin_title}' <=> '{pm_event.get('title')}' (Score: {best_score}%)"
                )

                # --- NEW: Extract pinnacle odds from the correct nested structure ---
                pin_odds_list = []
                periods = pin_event.get("Periods")
                if periods and len(periods) > 0 and "Win1x2" in periods[0]:
                    win1x2 = periods[0]["Win1x2"]
                    home_name = pin_event.get("homeName")
                    away_name = pin_event.get("awayName")

                    if (
                        home_name
                        and win1x2.get("Win1")
                        and win1x2.get("Win1", {}).get("value") > 0
                    ):
                        pin_odds_list.append(
                            {"name": home_name, "price": win1x2["Win1"]["value"]}
                        )
                    if (
                        away_name
                        and win1x2.get("Win2")
                        and win1x2.get("Win2", {}).get("value") > 0
                    ):
                        pin_odds_list.append(
                            {"name": away_name, "price": win1x2["Win2"]["value"]}
                        )
                    # Handle Draw outcome for sports like Soccer
                    if (
                        win1x2.get("WinNone")
                        and win1x2.get("WinNone", {}).get("value") > 0
                    ):
                        pin_odds_list.append(
                            {"name": "Draw", "price": win1x2["WinNone"]["value"]}
                        )
                # --- END NEW ---

                pm_markets = pm_event.get("markets", [])

                if not pm_markets or not pin_odds_list:
                    continue

                # Try explicit moneyline market first
                moneyline_market = find_polymarket_moneyline_market(pm_event)

                if moneyline_market:
                    pm_outcomes = json.loads(moneyline_market.get("outcomes", "[]"))
                    pm_prices = json.loads(moneyline_market.get("outcomePrices", "[]"))

                    # Compare each outcome from explicit moneyline
                    for i, pm_outcome_name in enumerate(pm_outcomes):
                        # Find corresponding pinnacle outcome by name
                        pin_outcome = next(
                            (
                                o
                                for o in pin_odds_list
                                if fuzz.partial_ratio(
                                    o.get("name", "").lower(), pm_outcome_name.lower()
                                )
                                > 80
                            ),
                            None,
                        )

                        if pin_outcome and i < len(pm_prices):
                            o_pin = pin_outcome.get("price")
                            o_pm = calculate_decimal_odds(pm_prices[i])
                            try:
                                polymarket_price = float(pm_prices[i])
                            except Exception:
                                polymarket_price = None

                            # Resolve token_id for cooldown and logging
                            pm_outcome_tokens = json.loads(
                                moneyline_market.get("clobTokenIds", "[]")
                            )
                            try:
                                outcome_index = pm_outcomes.index(pm_outcome_name)
                            except ValueError:
                                logger.warning(
                                    f"Could not find outcome '{pm_outcome_name}' in Polymarket outcomes: {pm_outcomes}"
                                )
                                continue

                            if outcome_index >= len(pm_outcome_tokens):
                                logger.warning(
                                    f"Mismatch between outcomes and token IDs for market {moneyline_market['id']}"
                                )
                                continue

                            polymarket_token_id = pm_outcome_tokens[outcome_index]

                            # Orderbook-based liquidity summary up to threshold price
                            avail_shares_at_th = avail_usd_at_th = wavg_price_at_th = None
                            ratio = (o_pm / o_pin) if (o_pin and o_pm) else None
                            edge_pct = ((ratio - 1.0) * 100.0) if ratio else None
                            if o_pin:
                                p_threshold = 1.0 / (o_pin * 1.12)
                                book = await fetch_order_book(polymarket_token_id)
                                if book:
                                    s, u, w = summarize_liquidity_to_price(book, p_threshold)
                                    avail_shares_at_th, avail_usd_at_th, wavg_price_at_th = s, u, w

                            # Log opportunity change (INFO)
                            log_opportunity_change(
                                mkey=pin_title or str(pin_event_id),
                                okey=pm_outcome_name,
                                o_pin=o_pin or 0.0,
                                p_yes=polymarket_price or 0.0,
                                o_pm=o_pm or 0.0,
                                ratio=ratio or 0.0,
                                edge_pct=edge_pct,
                                liquidity=float(moneyline_market.get("liquidityNum", 0) or 0.0),
                                pm_market_id=moneyline_market["id"],
                                token_id=polymarket_token_id,
                                trigger_type="INFO",
                                reason="scan",
                                avail_shares_at_th=avail_shares_at_th,
                                avail_usd_at_th=avail_usd_at_th,
                                wavg_price_at_th=wavg_price_at_th,
                            )

                            # Check arbitrage condition and cooldown by token
                            if o_pin and o_pm and o_pm >= o_pin * 1.12 and polymarket_price is not None:
                                trade_allowed = check_trade_cooldown(
                                    cooldown_key=polymarket_token_id,
                                    current_price=polymarket_price,
                                )

                                if trade_allowed:
                                    # Final check on liquidity before placing trade
                                    liquidity = float(moneyline_market.get("liquidityNum", 0) or 0.0)
                                    bet_amount = BET_AMOUNT_USD  # default $5 via env

                                    if liquidity < bet_amount:
                                        logger.warning(
                                            f"Skipping trade for {pm_outcome_name}. Not enough liquidity (${liquidity:.2f}) for a ${bet_amount:.2f} bet."
                                        )
                                        continue
                                    # Register paper position for SELL evaluation (всегда можно регистрировать для анализа)
                                    if SELL_MODE in {"paper", "both"} and polymarket_token_id not in PAPER_POSITIONS:
                                        if polymarket_price and 0.0 < polymarket_price < 1.0:
                                            shares = BET_AMOUNT_USD / polymarket_price
                                            PAPER_POSITIONS[polymarket_token_id] = {
                                                "entry_ts": time.time(),
                                                "mkey": pin_title or str(pin_event_id),
                                                "okey": pm_outcome_name,
                                                "pm_market_id": moneyline_market["id"],
                                                "token_id": polymarket_token_id,
                                                "entry_price": polymarket_price,
                                                "target_usd": BET_AMOUNT_USD,
                                                "shares": shares,
                                            }
                                    # Если режим только paper — не отправляем лайв-ордер
                                    if SELL_MODE == "paper":
                                        continue

                                    if not (0.001 <= polymarket_price <= 0.999):
                                        logger.warning(
                                            f"Skipping trade for {pm_outcome_name}. Price {polymarket_price} is outside Polymarket's valid range [0.001, 0.999]."
                                        )
                                        continue

                                    # Require sufficient orderbook depth up to threshold for $1
                                    if avail_usd_at_th is not None and avail_usd_at_th < bet_amount:
                                        logger.warning(
                                            f"Skipping trade for {pm_outcome_name}. Not enough depth at threshold (${avail_usd_at_th:.2f} < ${bet_amount:.2f})."
                                        )
                                        continue

                                    # Log ARBITRAGE event (for threshold crossing visibility)
                                    log_opportunity_change(
                                        mkey=pin_title or str(pin_event_id),
                                        okey=pm_outcome_name,
                                        o_pin=o_pin or 0.0,
                                        p_yes=polymarket_price or 0.0,
                                        o_pm=o_pm or 0.0,
                                        ratio=(o_pm / o_pin) if (o_pin and o_pm) else 0.0,
                                        edge_pct=((o_pm / o_pin - 1.0) * 100.0) if (o_pin and o_pm) else None,
                                        liquidity=liquidity,
                                        pm_market_id=moneyline_market["id"],
                                        token_id=polymarket_token_id,
                                        trigger_type="ARBITRAGE",
                                        reason="threshold",
                                        avail_shares_at_th=avail_shares_at_th,
                                        avail_usd_at_th=avail_usd_at_th,
                                        wavg_price_at_th=wavg_price_at_th,
                                    )

                                    trade_details = {
                                        "timestamp_utc": time.time(),
                                        "pinnacle_match_id": pin_event_id,
                                        "polymarket_event_id": pm_event["id"],
                                        "polymarket_market_id": moneyline_market["id"],
                                        "polymarket_token_id": polymarket_token_id,
                                        "match_title": pin_title,
                                        "outcome_name": pm_outcome_name,
                                        "pinnacle_odds": o_pin,
                                        "polymarket_price": polymarket_price,
                                        "calculated_polymarket_odds": o_pm,
                                        "liquidity_available": liquidity,
                                        "bet_amount_usd": bet_amount,
                                        "size_shares": (bet_amount / polymarket_price) if polymarket_price else None,
                                    }

                                    await place_polymarket_trade(trade_details)
                else:
                    # Fallback: build from three binary markets
                    ml = build_moneyline_from_binary_markets(pm_event, home_name, away_name)
                    if not ml:
                        logger.debug("No binary markets suitable to build moneyline.")
                        continue

                    # Map Pinnacle outcomes to corresponding binary markets
                    mapping = [
                        ("home", next((o for o in pin_odds_list if o.get("name") == home_name), None), home_name),
                        ("draw", next((o for o in pin_odds_list if o.get("name") == "Draw"), None), "Draw"),
                        ("away", next((o for o in pin_odds_list if o.get("name") == away_name), None), away_name),
                    ]

                    for key, pin_outcome, label in mapping:
                        if key not in ml or not pin_outcome:
                            continue

                        # Resolve market info early (used by orderbook and logging)
                        market = ml[key]["market"]
                        market_id = market.get("id")
                        token_id = ml[key]["token_id"]
                        liquidity = ml[key]["liquidity"]

                        o_pin = pin_outcome.get("price")
                        p_yes = ml[key]["p_yes"]
                        o_pm = 1.0 / p_yes if p_yes else None

                        # Log opportunity (INFO) with orderbook summary
                        ratio = (o_pm / o_pin) if (o_pin and o_pm) else None
                        edge_pct = ((ratio - 1.0) * 100.0) if ratio else None
                        avail_shares_at_th = avail_usd_at_th = wavg_price_at_th = None
                        if o_pin and token_id:
                            p_threshold = 1.0 / (o_pin * 1.12)
                            book = await fetch_order_book(token_id)
                            if book:
                                s, u, w = summarize_liquidity_to_price(book, p_threshold)
                                avail_shares_at_th, avail_usd_at_th, wavg_price_at_th = s, u, w
                        log_opportunity_change(
                            mkey=pin_title or str(pin_event_id),
                            okey=label,
                            o_pin=o_pin or 0.0,
                            p_yes=p_yes or 0.0,
                            o_pm=(1.0 / p_yes) if p_yes else 0.0,
                            ratio=ratio or 0.0,
                            edge_pct=edge_pct,
                            liquidity=liquidity,
                            pm_market_id=market_id,
                            token_id=token_id or "",
                            trigger_type="INFO",
                            reason="scan",
                            avail_shares_at_th=avail_shares_at_th,
                            avail_usd_at_th=avail_usd_at_th,
                            wavg_price_at_th=wavg_price_at_th,
                        )

                        if not (o_pin and o_pm and o_pm >= o_pin * 1.12):
                            continue

                        # market variables already resolved above
                        # order_min_size = ml[key]["order_min_size"]

                        # FIX: Add the missing trade cooldown check.
                        trade_allowed = check_trade_cooldown(
                            cooldown_key=token_id or f"{market_id}:{key}", current_price=p_yes
                        )
                        if not trade_allowed:
                            continue

                        # Respect Polymarket min order size; skip if it exceeds our cap
                        bet_amount = BET_AMOUNT_USD
                        # bet_amount = 5.0
                        # try:
                        #     if order_min_size is not None and float(order_min_size) > bet_amount:
                        #         logger.warning(
                        #             f"Skipping trade for {label}: orderMinSize={order_min_size} exceeds test cap ${bet_amount:.2f}."
                        #         )
                        #         continue
                        # except Exception:
                        #     pass

                        if liquidity < bet_amount:
                            logger.warning(
                                f"Skipping trade for {label}. Not enough liquidity (${liquidity:.2f}) for a ${bet_amount:.2f} bet."
                            )
                            continue

                        polymarket_price = p_yes
                        if not (0.001 <= polymarket_price <= 0.999):
                            continue

                        # Require sufficient orderbook depth up to threshold for our amount
                        bet_amount = BET_AMOUNT_USD
                        if avail_usd_at_th is not None and avail_usd_at_th < bet_amount:
                            logger.warning(
                                f"Skipping trade for {label}. Not enough depth at threshold (${avail_usd_at_th:.2f} < ${bet_amount:.2f})."
                            )
                            continue

                        # Log ARBITRAGE event (for threshold crossing visibility)
                        log_opportunity_change(
                            mkey=pin_title or str(pin_event_id),
                            okey=label,
                            o_pin=o_pin or 0.0,
                            p_yes=polymarket_price or 0.0,
                            o_pm=o_pm or 0.0,
                            ratio=(o_pm / o_pin) if (o_pin and o_pm) else 0.0,
                            edge_pct=((o_pm / o_pin - 1.0) * 100.0) if (o_pin and o_pm) else None,
                            liquidity=liquidity,
                            pm_market_id=market_id,
                            token_id=token_id or "",
                            trigger_type="ARBITRAGE",
                            reason="threshold",
                            avail_shares_at_th=avail_shares_at_th,
                            avail_usd_at_th=avail_usd_at_th,
                            wavg_price_at_th=wavg_price_at_th,
                        )

                        trade_details = {
                            "timestamp_utc": time.time(),
                            "pinnacle_match_id": pin_event_id,
                            "polymarket_event_id": pm_event["id"],
                            "polymarket_market_id": market_id,
                            "polymarket_token_id": token_id,
                            "match_title": pin_title,
                            "outcome_name": label,
                            "pinnacle_odds": o_pin,
                            "polymarket_price": polymarket_price,
                            "calculated_polymarket_odds": o_pm,
                            "liquidity_available": liquidity,
                            "bet_amount_usd": bet_amount,
                            "size_shares": (bet_amount / polymarket_price) if polymarket_price else None,
                        }

                        await place_polymarket_trade(trade_details)

        await asyncio.sleep(2)  # Run comparison logic frequently


async def main():
    """
    Starts all the concurrent tasks.
    """
    # Ensure log directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure opportunity CSV logs are initialized
    ensure_opportunity_logs_initialized()
    ensure_paper_trades_log_initialized()

    port = 8765
    logger.info(f"Starting WebSocket server on ws://localhost:{port}")

    server = await websockets.serve(pinnacle_handler, "localhost", port)

    main_tasks = {
        asyncio.create_task(comparison_logic()),
        asyncio.create_task(poll_polymarket_data()),
        asyncio.create_task(paper_sell_strategy()),
    }

    try:
        await asyncio.gather(*main_tasks)
    except asyncio.CancelledError:
        logger.info("Main tasks cancelled.")
    finally:
        server.close()
        await server.wait_closed()
        logger.info("WebSocket server shut down.")

        if background_tasks:
            logger.warning(
                f"Waiting for {len(background_tasks)} log-saving tasks to complete..."
            )
            await asyncio.gather(*background_tasks, return_exceptions=True)
            logger.info("All log-saving tasks have finished.")


# --- Paper SELL strategy monitor ---
async def paper_sell_strategy():
    """Мониторит бумажные позиции и закрывает их по тейк-профиту, фиксируя результат в CSV.

    Логика минимальная и безопасная: ищем лучшую bid-цену и оцениваем, можно ли реализовать
    выход на сумму BET_AMOUNT_USD по цене не ниже entry + TAKE_PROFIT_ABS. SL не используется
    для простоты начальной оценки.
    """
    ensure_paper_trades_log_initialized()
    while True:
        try:
            if not PAPER_POSITIONS:
                await asyncio.sleep(2)
                continue
            # Итерируем копию, чтобы иметь возможность удалять элементы
            for token_id, pos in list(PAPER_POSITIONS.items()):
                book = await fetch_order_book(token_id)
                if not book:
                    continue
                entry_price = pos.get("entry_price")
                target_usd = pos.get("target_usd", BET_AMOUNT_USD)
                mkey = pos.get("mkey", "")
                okey = pos.get("okey", "")
                pm_market_id = pos.get("pm_market_id", "")
                entry_ts = pos.get("entry_ts", time.time())

                # Условие тейк-профита
                tp_price = min(0.999, (entry_price or 0) + TAKE_PROFIT_ABS)
                best_bid = get_best_bid_price(book)
                if best_bid is None or best_bid < tp_price:
                    continue

                # Оценить возможное исполнение по цене >= tp_price на сумму target_usd
                filled_usd, filled_shares, wavg_exit = estimate_fill_on_bids(book, tp_price, target_usd)
                if filled_usd <= 0 or filled_shares <= 0 or not wavg_exit:
                    continue

                # Рассчитать PnL на реально заполняемую долю
                entry_usd = (entry_price or 0) * filled_shares
                pnl_usd = filled_usd - entry_usd

                # Записать результат и удалить позицию
                try:
                    with open(PAPER_TRADES_LOG_FILE, "a", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(
                            [
                                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(entry_ts)),
                                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                                mkey,
                                okey,
                                pm_market_id,
                                token_id,
                                f"{entry_price:.4f}" if entry_price else "",
                                f"{wavg_exit:.4f}",
                                f"{filled_shares:.6f}",
                                f"{pnl_usd:.2f}",
                                "TP",
                                "paper",
                            ]
                        )
                    logger.success(
                        f"[PAPER SELL] Closed paper position {token_id} at avg {wavg_exit:.4f} (+{pnl_usd:.2f} USD)."
                    )
                except Exception as e:
                    logger.error(f"Failed to write paper trade: {e}")

                # Удалить бумажную позицию
                PAPER_POSITIONS.pop(token_id, None)

        except Exception as e:
            logger.debug(f"paper_sell_strategy loop error: {e}")
        finally:
            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down.")
