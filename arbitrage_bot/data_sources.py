"""Data ingestion helpers for Pinnacle and Polymarket."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Dict

import httpx
from loguru import logger

from . import config
from .logging_utils import snapshot_json
from .state import BotState

_PINNACLE_SNAPSHOT_PATH = config.DATA_SNAPSHOT_DIR / "pinnacle_data.json"
_POLYMARKET_SNAPSHOT_PATH = config.DATA_SNAPSHOT_DIR / "polymarket_data.json"
_SNAPSHOT_INTERVAL_SEC = 3

_pinnacle_snapshot_at = 0.0
_polymarket_snapshot_at = 0.0


def create_pinnacle_handler(state: BotState):
    async def handler(websocket):
        global _pinnacle_snapshot_at
        logger.info("Pinnacle parser connected: %s", websocket.remote_address)
        try:
            async for message in websocket:
                data = json.loads(message)
                match_id = data.get("MatchId")
                if not match_id or not data.get("homeName") or not data.get("awayName"):
                    continue
                data["match"] = f"{data['homeName']} vs {data['awayName']}"
                state.pinnacle_data[match_id] = data

                now = time.time()
                if now - _pinnacle_snapshot_at > _SNAPSHOT_INTERVAL_SEC:
                    snapshot_json(state.pinnacle_data, _PINNACLE_SNAPSHOT_PATH)
                    _pinnacle_snapshot_at = now

                state.pinnacle_history.append({"timestamp": now, "source": "Pinnacle", "data": data})
        except Exception as exc:
            logger.error("Pinnacle handler error: %s", exc)
        finally:
            logger.info("Pinnacle parser disconnected.")
    return handler


async def poll_polymarket_data(state: BotState) -> None:
    global _polymarket_snapshot_at
    params = [("series_id", sid) for sid in config.POLYMARKET_SERIES_IDS]
    params.extend(
        [
            ("limit", "500"),
            ("closed", "false"),
            ("include_chat", "true"),
        ]
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                response = await client.get(config.POLYMARKET_API_URL, params=params)
                response.raise_for_status()
                events = response.json()
                live_events: Dict[str, dict] = {}
                for event in events:
                    is_live = (
                        event.get("live") is True
                        or event.get("score") not in (None, "", "0-0")
                        or event.get("elapsed") not in (None, "")
                    )
                    if event.get("active") and not event.get("closed") and is_live:
                        live_events[event["id"]] = event

                state.polymarket_data.clear()
                state.polymarket_data.update(live_events)

                now = time.time()
                if now - _polymarket_snapshot_at > _SNAPSHOT_INTERVAL_SEC:
                    snapshot_json(state.polymarket_data, _POLYMARKET_SNAPSHOT_PATH)
                    _polymarket_snapshot_at = now

                if live_events:
                    state.polymarket_history.append(
                        {"timestamp": now, "source": "Polymarket", "data": live_events}
                    )
                logger.info(
                    "Polymarket poll: %s events returned, %s live tracked.",
                    len(events),
                    len(live_events),
                )
            except httpx.HTTPStatusError as exc:
                logger.error("Polymarket API status %s", exc.response.status_code)
            except Exception as exc:
                logger.error("Error polling Polymarket: %s", exc)

            await asyncio.sleep(5)
