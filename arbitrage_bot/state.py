"""Shared mutable state structures for the arbitrage bot."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Set


@dataclass
class BotState:
    pinnacle_data: Dict[str, dict] = field(default_factory=dict)
    polymarket_data: Dict[str, dict] = field(default_factory=dict)
    pinnacle_history: Deque[dict] = field(default_factory=lambda: deque(maxlen=500))
    polymarket_history: Deque[dict] = field(default_factory=lambda: deque(maxlen=500))
    recent_trades: Dict[str, List[dict]] = field(default_factory=lambda: defaultdict(list))
    background_tasks: Set[Any] = field(default_factory=set)
    clob_client: Any | None = None
    approval_queue: asyncio.Queue = field(default_factory=asyncio.Queue)


state = BotState()
