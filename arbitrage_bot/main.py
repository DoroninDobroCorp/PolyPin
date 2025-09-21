from __future__ import annotations

import asyncio
import pathlib
import sys

import websockets
from loguru import logger

try:
    from . import config, data_sources, strategy, approvals, matching
    from .logging_utils import (
        configure_logging,
        ensure_opportunity_log_headers,
        ensure_paper_trades_log_headers,
    )
    from .state import BotState
    from .trading import paper_sell_strategy
except ImportError:  # pragma: no cover - fallback for "python arbitrage_bot/main.py"
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))
    from arbitrage_bot import config, data_sources, strategy, approvals, matching
    from arbitrage_bot.logging_utils import (
        configure_logging,
        ensure_opportunity_log_headers,
        ensure_paper_trades_log_headers,
    )
    from arbitrage_bot.state import BotState
    from arbitrage_bot.trading import paper_sell_strategy


async def main() -> None:
    configure_logging(config.settings.log_level)
    ensure_opportunity_log_headers()
    ensure_paper_trades_log_headers()

    state = BotState()
    matching.match_approver.set_pending_handler(lambda cand: state.approval_queue.put_nowait(cand))

    port = 8765
    logger.info("Starting WebSocket server on ws://localhost:%s", port)
    pinnacle_handler = data_sources.create_pinnacle_handler(state)
    server = await websockets.serve(pinnacle_handler, "localhost", port)

    tasks = {
        asyncio.create_task(strategy.run_strategy(state)),
        asyncio.create_task(data_sources.poll_polymarket_data(state)),
        asyncio.create_task(approvals.bootstrap_pending_queue(state)),
        asyncio.create_task(approvals.approval_prompt_loop(state)),
    }
    if config.settings.sell_mode in {"paper", "both"}:
        tasks.add(asyncio.create_task(paper_sell_strategy(state)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Main tasks cancelled.")
    finally:
        server.close()
        await server.wait_closed()
        if state.background_tasks:
            logger.warning("Waiting for %s log tasks to finish...", len(state.background_tasks))
            await asyncio.gather(*state.background_tasks, return_exceptions=True)
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down on keyboard interrupt.")
