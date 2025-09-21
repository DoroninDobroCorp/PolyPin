"""Interactive approval workflow for Pinnacle ↔ Polymarket сопоставления."""
from __future__ import annotations

import asyncio
import csv

from loguru import logger

from .matching import MatchCandidate, match_approver
from .state import BotState


async def approval_prompt_loop(state: BotState, *, requeue_delay: float = 30.0) -> None:
    """Опрашивает очередь необработанных матчей и просит оператора подтвердить их."""
    loop = asyncio.get_running_loop()

    while True:
        candidate: MatchCandidate = await state.approval_queue.get()

        # Пара могла уже попасть в approved вручную.
        if match_approver.is_approved(candidate):
            state.pending_candidates.pop(candidate.key(), None)
            continue

        prompt = (
            "\n=== Новое сопоставление требует подтверждения ===\n"
            f"  Pinnacle: {candidate.pinnacle_title}\n"
            f"  Polymarket: {candidate.polymarket_title} (id: {candidate.polymarket_id})\n"
            f"  Fuzzy score: {candidate.score}\n"
            "[y] — одобрить, [n] — отклонить, [s/Enter] — отложить на позже: "
        )
        try:
            response = await asyncio.to_thread(input, prompt)
        except (EOFError, KeyboardInterrupt):
            logger.warning("Approval prompt interrupted. Пара останется в очереди.")
            loop.call_later(requeue_delay, state.approval_queue.put_nowait, candidate)
            continue

        decision = (response or "s").strip().lower()

        if decision in {"y", "yes", "да", "д"}:
            match_approver.approve(candidate)
            state.pending_candidates.pop(candidate.key(), None)
        elif decision in {"n", "no", "нет", "н"}:
            match_approver.reject(candidate)
            state.pending_candidates.pop(candidate.key(), None)
        else:
            logger.info(
                "Отложено подтверждение пары '%s' ↔ '%s' (повторим через %.0f c).",
                candidate.pinnacle_title,
                candidate.polymarket_title,
                requeue_delay,
            )
            loop.call_later(requeue_delay, state.approval_queue.put_nowait, candidate)


async def bootstrap_pending_queue(state: BotState) -> None:
    """Проверяет, не накопились ли непроверенные пары во время простоя."""
    # Если файл с pending отсутствует — ничего делать не надо.
    pending_path = match_approver.pending_path
    if not pending_path.exists():
        return

    try:
        with pending_path.open("r", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                pinnacle_title = row.get("pinnacle_title") or ""
                polymarket_title = row.get("polymarket_title") or ""
                polymarket_id = row.get("polymarket_id") or ""
                score_str = row.get("match_score") or "0"
                try:
                    score = int(score_str)
                except ValueError:
                    score = 0
                candidate = MatchCandidate(
                    pinnacle_title=pinnacle_title,
                    polymarket_title=polymarket_title,
                    polymarket_id=polymarket_id,
                    score=score,
                )
                if not polymarket_id:
                    continue
                if match_approver.is_known(candidate):
                    continue
                match_approver.enqueue_pending(candidate)
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.debug("Не удалось загрузить очередь pending_matches: %s", exc)
