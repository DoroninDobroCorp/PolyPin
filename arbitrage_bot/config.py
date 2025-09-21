"""Configuration helpers and constants for the arbitrage bot."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

from dotenv import load_dotenv

BASE_DIR = pathlib.Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "trade_logs"
OPPORTUNITY_LOG_DIR = BASE_DIR / "opportunity_logs"
OPPORTUNITY_LOG_FILE = OPPORTUNITY_LOG_DIR / "opportunities_changes.csv"
PAPER_TRADES_LOG_FILE = LOGS_DIR / "paper_trades.csv"
DATA_SNAPSHOT_DIR = BASE_DIR / "data_cache"

MATCH_REGISTRY_DIR = BASE_DIR / "match_registry"
MATCH_APPROVED_FILE = MATCH_REGISTRY_DIR / "approved_matches.json"
MATCH_PENDING_FILE = MATCH_REGISTRY_DIR / "pending_matches.csv"

POLYMARKET_API_URL = "https://gamma-api.polymarket.com/events"
ARB_RATIO = 1.12
POLYMARKET_SERIES_IDS = (
    "10187",
    "3",
    "10210",
    "10105",
    "10188",
    "10193",
    "10194",
    "10195",
    "10203",
    "10189",
    "10204",
    "10209",
    "10238",
    "10240",
    "10243",
    "10244",
    "10246",
    "10245",
    "10242",
)

# Ensure directories exist early (idempotent)
for path in (LOGS_DIR, OPPORTUNITY_LOG_DIR, MATCH_REGISTRY_DIR, DATA_SNAPSHOT_DIR):
    path.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env", override=False)


def _float_env(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class Settings:
    bet_amount_usd: float = _float_env("BET_AMOUNT_USD", "5")
    take_profit_abs: float = _float_env("TAKE_PROFIT_ABS", "0.01")
    stop_loss_abs: float = _float_env("STOP_LOSS_ABS", "0.03")
    sell_mode: str = (os.getenv("SELL_MODE", "paper") or "paper").lower()
    private_key: str | None = os.getenv("POLY_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
    signature_type: str | None = os.getenv("POLY_SIGNATURE_TYPE")
    proxy_address: str | None = os.getenv("POLY_PROXY_ADDRESS") or os.getenv("FUNDER_ADDRESS")
    test_mode: bool = (os.getenv("TEST_MODE", "false") or "false").lower() in {"1", "true", "yes"}
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
