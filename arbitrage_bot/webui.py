"""Minimal web UI for managing match approvals."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from aiohttp import web
from loguru import logger

from .matching import MatchCandidate, match_approver
from .state import BotState

_INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>PolyPin Match Approvals</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #111; color: #f3f3f3; }
    header { padding: 16px 24px; background: #191919; border-bottom: 1px solid #333; }
    main { padding: 24px; }
    h1 { margin: 0; font-size: 1.4rem; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #2a2a2a; }
    th { text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.75rem; color: #bbb; }
    tr:hover { background: rgba(255, 255, 255, 0.04); }
    button { cursor: pointer; border: none; border-radius: 4px; padding: 8px 12px; font-weight: 600; }
    button.approve { background: #26a269; color: #fff; }
    button.reject { background: #c01c28; color: #fff; margin-left: 8px; }
    button.refresh { background: #2c73d2; color: #fff; }
    #status { margin-top: 12px; font-size: 0.85rem; color: #8f8f8f; }
    .controls { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
    .empty { margin-top: 24px; font-size: 1rem; color: #888; }
    .score { font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; }
  </style>
</head>
<body>
  <header>
    <h1>PolyPin · Match Approvals</h1>
  </header>
  <main>
    <div class="controls">
      <div id="status">Loading…</div>
      <button class="refresh" onclick="refreshPending()">Refresh</button>
    </div>
    <div id="table-container"></div>
  </main>
  <script>
    async function fetchPending() {
      const response = await fetch('/api/pending');
      if (!response.ok) {
        throw new Error('Failed to load pending matches');
      }
      return await response.json();
    }

    function renderTable(pending) {
      const container = document.getElementById('table-container');
      if (!pending.length) {
        container.innerHTML = '<div class="empty">Нет ожидающих подтверждения матчей.</div>';
        return;
      }
      const rows = pending.map(item => `
        <tr>
          <td>${item.score}</td>
          <td>${item.pinnacle_title}</td>
          <td>${item.polymarket_title}</td>
          <td class="score">${item.polymarket_id}</td>
          <td>
            <button class="approve" onclick="decide('${item.key}', 'approve')">Approve</button>
            <button class="reject" onclick="decide('${item.key}', 'reject')">Reject</button>
          </td>
        </tr>
      `).join('');
      container.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>Score</th>
              <th>Pinnacle</th>
              <th>Polymarket</th>
              <th>Event ID</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    }

    async function refreshPending() {
      try {
        document.getElementById('status').textContent = 'Refreshing…';
        const data = await fetchPending();
        renderTable(data.pending);
        document.getElementById('status').textContent = `Pending: ${data.pending.length}`;
      } catch (err) {
        console.error(err);
        document.getElementById('status').textContent = err.message;
      }
    }

    async function decide(key, action) {
      try {
        const response = await fetch(`/api/pending/${encodeURIComponent(key)}/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.message || 'Failed to update match');
        }
        await refreshPending();
      } catch (err) {
        alert(err.message);
      }
    }

    refreshPending();
    setInterval(refreshPending, 8000);
  </script>
</body>
</html>
"""


def _candidate_payload(key: str, candidate: MatchCandidate) -> dict:
    payload = asdict(candidate)
    payload["key"] = key
    return payload


def _not_found(message: str) -> web.Response:
    return web.json_response({"status": "error", "message": message}, status=404)


async def run_web_ui(state: BotState, *, host: str = "127.0.0.1", port: int = 8787) -> None:
    """Launch aiohttp server for match approvals."""

    app = web.Application()

    async def index(_: web.Request) -> web.Response:
        return web.Response(text=_INDEX_HTML, content_type="text/html")

    async def api_pending(_: web.Request) -> web.Response:
        snapshot = list(state.pending_candidates.items())
        pending = [
            _candidate_payload(key, candidate)
            for key, candidate in snapshot
        ]
        pending.sort(key=lambda item: item.get("score", 0), reverse=True)
        return web.json_response({"pending": pending})

    async def api_decide(request: web.Request) -> web.Response:
        key = request.match_info.get("key")
        action = request.match_info.get("action")
        if not key or not action:
            return _not_found("Invalid request")

        candidate = state.pending_candidates.get(key)
        if not candidate:
            return _not_found("Match candidate not found or already processed")

        if action == "approve":
            match_approver.approve(candidate)
            logger.success(
                "Match approved via web UI: '%s' ↔ '%s' (id %s).",
                candidate.pinnacle_title,
                candidate.polymarket_title,
                candidate.polymarket_id,
            )
        elif action == "reject":
            match_approver.reject(candidate)
            logger.info(
                "Match rejected via web UI: '%s' ↔ '%s' (id %s).",
                candidate.pinnacle_title,
                candidate.polymarket_title,
                candidate.polymarket_id,
            )
        else:
            return _not_found("Unsupported action")

        state.pending_candidates.pop(key, None)
        return web.json_response({"status": "ok"})

    app.router.add_get("/", index)
    app.router.add_get("/api/pending", api_pending)
    app.router.add_post("/api/pending/{key}/{action}", api_decide)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info(
        "Approval web UI available at http://%s:%s (mode=%s)",
        host,
        port,
        "web",
    )

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Stopping approval web UI...")
        await runner.cleanup()
        raise
