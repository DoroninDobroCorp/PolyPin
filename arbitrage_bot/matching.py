"""Helpers for event matching and manual confirmation."""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from loguru import logger
from thefuzz import fuzz

from . import config


@dataclass
class MatchCandidate:
    pinnacle_title: str
    polymarket_title: str
    polymarket_id: str
    score: int

    def as_csv_row(self) -> list[str]:
        return [
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            self.pinnacle_title,
            self.polymarket_title,
            self.polymarket_id,
            str(self.score),
        ]


class MatchApprover:
    """Tracks approved match pairs and surfaces new candidates for manual review."""

    def __init__(
        self,
        approved_path: Path,
        pending_path: Path,
        *,
        on_pending: Optional[Callable[[MatchCandidate], None]] = None,
    ) -> None:
        self.approved_path = approved_path
        self.pending_path = pending_path
        self._approved_keys: set[str] = set()
        self._pending_keys: set[str] = set()
        self._approved_mtime: float = 0.0
        self._pending_handler: Optional[Callable[[MatchCandidate], None]] = on_pending
        self._rejected_keys: set[str] = set()
        self._ensure_pending_headers()
        self._load_approved()

    def _ensure_pending_headers(self) -> None:
        if not self.pending_path.exists():
            with self.pending_path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "timestamp_utc",
                        "pinnacle_title",
                        "polymarket_title",
                        "polymarket_id",
                        "match_score",
                    ]
                )

    def _load_approved(self) -> None:
        if not self.approved_path.exists():
            self._approved_keys.clear()
            self._approved_mtime = 0.0
            return
        mtime = self.approved_path.stat().st_mtime
        if mtime == self._approved_mtime:
            return
        try:
            with self.approved_path.open("r") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse approved matches JSON: %s", exc)
            return

        keys: set[str] = set()
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    pinn = value.get("pinnacle_title")
                    polymarket_id = value.get("polymarket_id") or value.get("polymarket_event_id")
                    if pinn and polymarket_id:
                        keys.add(self._compose_key(pinn, polymarket_id))
                elif isinstance(value, str):
                    keys.add(self._compose_key(key, value))
        elif isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                pinn = entry.get("pinnacle_title")
                polymarket_id = entry.get("polymarket_id") or entry.get("polymarket_event_id")
                if pinn and polymarket_id:
                    keys.add(self._compose_key(pinn, polymarket_id))
        else:
            logger.warning("approved_matches.json has unexpected structure. Expected list or dict.")

        self._approved_keys = keys
        self._approved_mtime = mtime
        logger.info("Loaded %s approved match pairs.", len(self._approved_keys))

    def _compose_key(self, pinnacle_title: str, polymarket_id: str) -> str:
        return f"{pinnacle_title.strip().lower()}::{polymarket_id}"

    def set_pending_handler(self, handler: Optional[Callable[[MatchCandidate], None]]) -> None:
        self._pending_handler = handler

    def is_approved(self, candidate: MatchCandidate) -> bool:
        self._load_approved()
        key = self._compose_key(candidate.pinnacle_title, candidate.polymarket_id)
        if key in self._approved_keys:
            return True
        if key in self._rejected_keys:
            return False

        if key not in self._pending_keys:
            self._register_pending(candidate)
        return False

    def _register_pending(
        self,
        candidate: MatchCandidate,
        *,
        write_row: bool = True,
        log_warning: bool = True,
    ) -> None:
        key = self._compose_key(candidate.pinnacle_title, candidate.polymarket_id)
        if key in self._approved_keys or key in self._pending_keys or key in self._rejected_keys:
            return

        self._pending_keys.add(key)
        if write_row:
            with self.pending_path.open("a", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(candidate.as_csv_row())
        if log_warning:
            logger.warning(
                "Match candidate requires manual approval: '%s' ↔ '%s' (score=%s)."
                " Add to %s to approve.",
                candidate.pinnacle_title,
                candidate.polymarket_title,
                candidate.score,
                self.approved_path.name,
            )
        if self._pending_handler:
            try:
                self._pending_handler(candidate)
            except Exception as exc:
                logger.debug("Pending handler failed: %s", exc)

    def enqueue_pending(self, candidate: MatchCandidate) -> None:
        self._register_pending(candidate, write_row=False, log_warning=False)

    def is_known(self, candidate: MatchCandidate) -> bool:
        key = self._compose_key(candidate.pinnacle_title, candidate.polymarket_id)
        return key in self._approved_keys or key in self._pending_keys or key in self._rejected_keys

    def _load_existing_approvals(self) -> list[dict]:
        if not self.approved_path.exists():
            return []
        try:
            with self.approved_path.open("r") as handle:
                data = json.load(handle)
        except json.JSONDecodeError:
            logger.error("approved_matches.json malformed. Overwriting with a clean list.")
            return []

        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
        if isinstance(data, dict):
            result: list[dict] = []
            for key, value in data.items():
                if isinstance(value, dict):
                    result.append(value)
                elif isinstance(value, str):
                    result.append({"pinnacle_title": key, "polymarket_id": value})
            return result
        logger.warning("approved_matches.json has unexpected structure. Resetting to list format.")
        return []

    def approve(self, candidate: MatchCandidate) -> None:
        entries = self._load_existing_approvals()
        entry = {
            "pinnacle_title": candidate.pinnacle_title,
            "polymarket_id": candidate.polymarket_id,
            "polymarket_title": candidate.polymarket_title,
        }
        if entry not in entries:
            entries.append(entry)
        with self.approved_path.open("w") as handle:
            json.dump(entries, handle, indent=2, ensure_ascii=False)

        key = self._compose_key(candidate.pinnacle_title, candidate.polymarket_id)
        self._approved_keys.add(key)
        self._pending_keys.discard(key)
        self._rejected_keys.discard(key)
        try:
            self._approved_mtime = self.approved_path.stat().st_mtime
        except FileNotFoundError:
            pass
        logger.success(
            "Match approved: '%s' ↔ '%s' (Polymarket id %s).",
            candidate.pinnacle_title,
            candidate.polymarket_title,
            candidate.polymarket_id,
        )

    def reject(self, candidate: MatchCandidate) -> None:
        key = self._compose_key(candidate.pinnacle_title, candidate.polymarket_id)
        self._rejected_keys.add(key)
        logger.info(
            "Match rejected: '%s' ↔ '%s' (id %s).",
            candidate.pinnacle_title,
            candidate.polymarket_title,
            candidate.polymarket_id,
        )


def normalize_title(title: Optional[str]) -> str:
    if not title:
        return ""
    return " ".join(title.lower().split())


def find_matching_polymarket_event(
    pinnacle_event_title: str,
    polymarket_events: Iterable[dict],
    score_threshold: int = 70,
) -> Tuple[Optional[dict], int]:
    """Return best matching Polymarket event for the Pinnacle title."""
    if not pinnacle_event_title:
        return None, 0

    best_score = 0
    best_event: Optional[dict] = None
    normalized_pin = normalize_title(pinnacle_event_title)

    for event in polymarket_events:
        pm_title = event.get("title")
        if not pm_title:
            continue
        score = fuzz.token_sort_ratio(normalized_pin, normalize_title(pm_title))
        if score > best_score:
            best_score = score
            best_event = event

    if best_score >= score_threshold:
        return best_event, best_score
    return None, best_score


match_approver = MatchApprover(config.MATCH_APPROVED_FILE, config.MATCH_PENDING_FILE)

__all__ = [
    "MatchApprover",
    "MatchCandidate",
    "match_approver",
    "find_matching_polymarket_event",
    "normalize_title",
]
