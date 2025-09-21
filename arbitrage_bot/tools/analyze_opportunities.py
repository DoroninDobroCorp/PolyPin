#!/usr/bin/env python3
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Tuple


@dataclass
class OppRow:
    ts: datetime
    mkey: str
    okey: str
    o_pin: Optional[float]
    p_yes: Optional[float]
    o_pm: Optional[float]
    ratio: Optional[float]
    edge_pct: Optional[float]
    liquidity: Optional[float]
    trigger_type: str
    reason: str
    pm_market_id: str
    token_id: str
    avail_shares_at_th: Optional[float]
    avail_usd_at_th: Optional[float]
    wavg_price_at_th: Optional[float]


def parse_float(x: str) -> Optional[float]:
    if x is None:
        return None
    x = str(x).strip()
    if not x:
        return None
    try:
        return float(x)
    except Exception:
        return None


def parse_csv(path: Path) -> List[OppRow]:
    rows: List[OppRow] = []
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                ts = datetime.strptime(r["timestamp_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                # Skip malformed rows
                continue
            rows.append(
                OppRow(
                    ts=ts,
                    mkey=r.get("mkey", ""),
                    okey=r.get("oKey", ""),
                    o_pin=parse_float(r.get("o_pin")),
                    p_yes=parse_float(r.get("p_yes")),
                    o_pm=parse_float(r.get("o_pm")),
                    ratio=parse_float(r.get("ratio")),
                    edge_pct=parse_float(r.get("edge_pct")),
                    liquidity=parse_float(r.get("liquidity")),
                    trigger_type=r.get("trigger_type", ""),
                    reason=r.get("reason", ""),
                    pm_market_id=r.get("pm_market_id", ""),
                    token_id=r.get("token_id", ""),
                    avail_shares_at_th=parse_float(r.get("avail_shares_at_th")),
                    avail_usd_at_th=parse_float(r.get("avail_usd_at_th")),
                    wavg_price_at_th=parse_float(r.get("wavg_price_at_th")),
                )
            )
    return rows


# ---- Paper SELL analysis ----
@dataclass
class PaperTrade:
    entry_ts: datetime
    exit_ts: datetime
    mkey: str
    okey: str
    pm_market_id: str
    token_id: str
    entry_price: Optional[float]
    exit_price: Optional[float]
    shares: Optional[float]
    pnl_usd: Optional[float]
    reason: str
    mode: str


def parse_paper_csv(path: Path) -> List[PaperTrade]:
    trades: List[PaperTrade] = []
    if not path.exists():
        return trades
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                entry_ts = datetime.strptime(r.get("timestamp_entry_utc", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                exit_ts_raw = r.get("timestamp_exit_utc", "").strip()
                if exit_ts_raw:
                    exit_ts = datetime.strptime(exit_ts_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                else:
                    exit_ts = entry_ts
                def _pf(x: str) -> Optional[float]:
                    try:
                        return float(x) if x not in (None, "") else None
                    except Exception:
                        return None
                trades.append(
                    PaperTrade(
                        entry_ts=entry_ts,
                        exit_ts=exit_ts,
                        mkey=r.get("mkey", ""),
                        okey=r.get("oKey", r.get("okey", "")),
                        pm_market_id=r.get("pm_market_id", ""),
                        token_id=r.get("token_id", ""),
                        entry_price=_pf(r.get("entry_price")),
                        exit_price=_pf(r.get("exit_price")),
                        shares=_pf(r.get("shares")),
                        pnl_usd=_pf(r.get("pnl_usd")),
                        reason=r.get("reason", ""),
                        mode=r.get("mode", ""),
                    )
                )
            except Exception:
                continue
    return trades


def summarize_paper_trades(trades: List[PaperTrade], hours_per_day: float = 6.0, days_per_month: int = 30) -> Dict:
    if not trades:
        return {"paper": {"count": 0}}
    pnls = [t.pnl_usd or 0.0 for t in trades]
    count = len(trades)
    wins = sum(1 for x in pnls if x > 0)
    losses = sum(1 for x in pnls if x < 0)
    winrate = (wins / count) * 100.0 if count else 0.0
    avg_pnl = statistics.mean(pnls) if pnls else 0.0
    med_pnl = statistics.median(pnls) if pnls else 0.0
    sum_pnl = sum(pnls)

    # Span and extrapolation
    ts_min = min(t.entry_ts for t in trades)
    ts_max = max(t.exit_ts for t in trades)
    span_hours = max(1e-6, (ts_max - ts_min).total_seconds() / 3600.0)
    trades_per_hour = count / span_hours if span_hours > 0 else 0.0
    pnl_per_hour = sum_pnl / span_hours if span_hours > 0 else 0.0
    monthly_estimate = pnl_per_hour * hours_per_day * days_per_month

    # Avg hold time
    hold_minutes = [max(0.0, (t.exit_ts - t.entry_ts).total_seconds() / 60.0) for t in trades]
    avg_hold_min = statistics.mean(hold_minutes) if hold_minutes else 0.0
    med_hold_min = statistics.median(hold_minutes) if hold_minutes else 0.0

    return {
        "paper": {
            "count": count,
            "winrate_pct": winrate,
            "avg_pnl": avg_pnl,
            "med_pnl": med_pnl,
            "sum_pnl": sum_pnl,
            "trades_per_hour": trades_per_hour,
            "pnl_per_hour": pnl_per_hour,
            "monthly_estimate": monthly_estimate,
            "avg_hold_min": avg_hold_min,
            "med_hold_min": med_hold_min,
        }
    }


@dataclass
class ArbEvent:
    start_ts: datetime
    end_ts: datetime
    mkey: str
    token_id: str
    pm_market_id: str
    best_row: OppRow  # row with maximum EV or maximum avail_usd_at_th (heuristic)


def group_arbitrage_events(rows: List[OppRow], cooldown_sec: int = 120) -> List[ArbEvent]:
    # Consider only rows that represent an arbitrage trigger
    arb_rows = [r for r in rows if r.trigger_type.upper() == "ARBITRAGE" and r.token_id]
    arb_rows.sort(key=lambda r: (r.mkey, r.token_id, r.ts))

    events: List[ArbEvent] = []
    current_key: Tuple[str, str] = ("", "")
    current_event_rows: List[OppRow] = []

    def flush():
        nonlocal current_event_rows
        if not current_event_rows:
            return
        # Choose best row within the event. Heuristic:
        # 1) Prefer maximum avail_usd_at_th
        # 2) Tie-breaker: maximum (p_true - p_yes)
        def ev_per_usd(r: OppRow) -> float:
            if r.o_pin and r.p_yes is not None and r.o_pin > 0:
                p_true = 1.0 / r.o_pin
                return max(0.0, p_true - r.p_yes)
            return 0.0

        best = max(
            current_event_rows,
            key=lambda r: (
                (r.avail_usd_at_th or 0.0),
                ev_per_usd(r),
            ),
        )
        events.append(
            ArbEvent(
                start_ts=current_event_rows[0].ts,
                end_ts=current_event_rows[-1].ts,
                mkey=best.mkey,
                token_id=best.token_id,
                pm_market_id=best.pm_market_id,
                best_row=best,
            )
        )
        current_event_rows = []

    for r in arb_rows:
        key = (r.mkey, r.token_id)
        if key != current_key:
            # New key => flush previous
            flush()
            current_key = key
            current_event_rows = [r]
            continue
        # Same key: check cooldown window
        last_ts = current_event_rows[-1].ts
        if (r.ts - last_ts).total_seconds() > cooldown_sec:
            # Gap => close previous event for this key
            flush()
            current_event_rows = [r]
        else:
            current_event_rows.append(r)
    # Flush at end
    flush()
    return events


def summarize(rows: List[OppRow], events: List[ArbEvent], hours_per_day: float = 6.0, days_per_month: int = 30, bank_usd: Optional[float] = None) -> Dict:
    result: Dict = {}

    if not rows:
        return {"error": "No rows"}

    # Global time span (UTC)
    ts_min = min(r.ts for r in rows)
    ts_max = max(r.ts for r in rows)
    span_hours = max(1e-6, (ts_max - ts_min).total_seconds() / 3600.0)

    # Opportunity counts
    total_info = sum(1 for r in rows if r.trigger_type.upper() == "INFO")
    total_arb = len(events)

    # Available USD at threshold per event
    avail_usd_list = [e.best_row.avail_usd_at_th for e in events if e.best_row.avail_usd_at_th is not None]
    avg_avail_usd = statistics.mean(avail_usd_list) if avail_usd_list else 0.0
    med_avail_usd = statistics.median(avail_usd_list) if avail_usd_list else 0.0
    sum_avail_usd = sum(avail_usd_list) if avail_usd_list else 0.0

    # EV per event (hold-to-settle approximation): EV_per_$ = p_true - p_yes
    # Potential profit if using all available USD at threshold: EV_event = EV_per_$ * avail_usd_at_th
    def ev_per_usd(r: OppRow) -> float:
        if r.o_pin and r.p_yes is not None and r.o_pin > 0:
            return max(0.0, (1.0 / r.o_pin) - r.p_yes)
        return 0.0

    ev_all_unlimited = [ev_per_usd(e.best_row) * (e.best_row.avail_usd_at_th or 0.0) for e in events]
    total_ev_unlimited = sum(ev_all_unlimited)
    avg_ev_unlimited = statistics.mean(ev_all_unlimited) if ev_all_unlimited else 0.0
    med_ev_unlimited = statistics.median(ev_all_unlimited) if ev_all_unlimited else 0.0

    # Limited by bank size (optional)
    total_ev_limited = None
    if bank_usd is not None:
        ev_all_limited = [ev_per_usd(e.best_row) * min(bank_usd, (e.best_row.avail_usd_at_th or 0.0)) for e in events]
        total_ev_limited = sum(ev_all_limited)

    # Rates per hour and extrapolation to month
    events_per_hour = total_arb / span_hours if span_hours > 0 else 0.0
    ev_per_hour = total_ev_unlimited / span_hours if span_hours > 0 else 0.0
    monthly_estimate = ev_per_hour * hours_per_day * days_per_month

    # Per-match aggregation
    per_match: Dict[str, Dict] = defaultdict(lambda: {
        "events": 0,
        "sum_avail_usd": 0.0,
        "avg_avail_usd": 0.0,
        "med_avail_usd": 0.0,
        "sum_ev_unlimited": 0.0,
    })
    for e in events:
        key = e.mkey
        per_match[key]["events"] += 1
        per_match[key]["sum_avail_usd"] += (e.best_row.avail_usd_at_th or 0.0)
        per_match[key]["sum_ev_unlimited"] += ev_per_usd(e.best_row) * (e.best_row.avail_usd_at_th or 0.0)

    # Compute avg/med per match
    for key, agg in per_match.items():
        # Collect list of avail for this match
        vals = [ev.best_row.avail_usd_at_th or 0.0 for ev in events if ev.mkey == key]
        agg["avg_avail_usd"] = statistics.mean(vals) if vals else 0.0
        agg["med_avail_usd"] = statistics.median(vals) if vals else 0.0

    # Derive "average match" estimates
    match_count = len(per_match)
    avg_match_ev = (sum(agg["sum_ev_unlimited"] for agg in per_match.values()) / match_count) if match_count else 0.0
    avg_match_avail = (sum(agg["sum_avail_usd"] for agg in per_match.values()) / match_count) if match_count else 0.0

    result.update({
        "time_span_hours": span_hours,
        "total_rows": len(rows),
        "total_info_logs": total_info,
        "total_arbitrage_events": total_arb,
        "events_per_hour": events_per_hour,
        "avg_avail_usd_per_event": avg_avail_usd,
        "med_avail_usd_per_event": med_avail_usd,
        "sum_avail_usd_all_events": sum_avail_usd,
        "total_ev_unlimited": total_ev_unlimited,
        "avg_ev_unlimited_per_event": avg_ev_unlimited,
        "med_ev_unlimited_per_event": med_ev_unlimited,
        "total_ev_limited_bank": total_ev_limited,
        "hours_per_day": hours_per_day,
        "days_per_month": days_per_month,
        "monthly_estimate_unlimited": monthly_estimate,
        "avg_match_ev_unlimited": avg_match_ev,
        "avg_match_available_usd": avg_match_avail,
    })

    # Per-match dump
    result["per_match"] = per_match
    return result


def main():
    parser = argparse.ArgumentParser(description="Analyze Polymarket opportunities CSV and estimate profits.")
    parser.add_argument("--file", default="arbitrage_bot/opportunity_logs/opportunities_changes.csv", help="Path to opportunities_changes.csv")
    parser.add_argument("--hours-per-day", type=float, default=6.0, help="Assumed active hours per day for extrapolation")
    parser.add_argument("--days-per-month", type=int, default=30, help="Assumed active days per month for extrapolation")
    parser.add_argument("--bank-usd", type=float, default=None, help="Optional account bank size to cap fills per event")
    parser.add_argument("--cooldown-sec", type=int, default=120, help="Cooldown used to cluster ARBITRAGE rows into events")
    parser.add_argument("--out-json", default=None, help="Optional path to write JSON summary")
    parser.add_argument("--out-match-csv", default=None, help="Optional path to write per-match CSV summary")
    parser.add_argument("--paper-file", default="arbitrage_bot/trade_logs/paper_trades.csv", help="Path to paper_trades.csv for SELL analysis")

    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    rows = parse_csv(path)
    events = group_arbitrage_events(rows, cooldown_sec=args.cooldown_sec)
    summary = summarize(rows, events, hours_per_day=args.hours_per_day, days_per_month=args.days_per_month, bank_usd=args.bank_usd)

    # Print human-readable summary
    print("=== Opportunities Analysis Summary ===")
    if "error" in summary:
        print("No data found in CSV (no rows parsed).")
        print("Collect more data by running the bot for 60–120 minutes, then re-run this script.")
        return 0

    print(f"Time span (hours): {summary.get('time_span_hours', 0.0):.2f}")
    print(f"Rows total: {summary.get('total_rows', 0)}")
    print(f"INFO rows: {summary.get('total_info_logs', 0)}")
    print(f"Arbitrage events (clustered): {summary.get('total_arbitrage_events', 0)}")
    print(f"Events per hour: {summary.get('events_per_hour', 0.0):.2f}")
    print(f"Avg available USD per event: {summary.get('avg_avail_usd_per_event', 0.0):.2f}")
    print(f"Median available USD per event: {summary.get('med_avail_usd_per_event', 0.0):.2f}")
    print(f"Sum available USD across events: {summary.get('sum_avail_usd_all_events', 0.0):.2f}")
    print(f"Total EV (unlimited): {summary.get('total_ev_unlimited', 0.0):.2f}")
    if summary.get("total_ev_limited_bank") is not None:
        print(f"Total EV (limited by bank ${args.bank_usd:.2f}): {summary['total_ev_limited_bank']:.2f}")
    print(f"Monthly estimate (unlimited, {args.hours_per_day}h/day, {args.days_per_month}d/mo): {summary.get('monthly_estimate_unlimited', 0.0):.2f}")
    print(f"Average match EV (unlimited): {summary.get('avg_match_ev_unlimited', 0.0):.2f}")
    print(f"Average match available USD: {summary.get('avg_match_available_usd', 0.0):.2f}")

    # SELL strategy (paper) analysis
    paper_path = Path(args.paper_file)
    paper_trades = parse_paper_csv(paper_path)
    paper_summary = summarize_paper_trades(paper_trades, hours_per_day=args.hours_per_day, days_per_month=args.days_per_month)
    if paper_summary.get("paper", {}).get("count", 0) > 0:
        p = paper_summary["paper"]
        print("\n=== SELL Strategy (Paper) Summary ===")
        print(f"Trades: {p['count']}")
        print(f"Win rate: {p['winrate_pct']:.1f}%")
        print(f"Avg PnL per trade: {p['avg_pnl']:.2f}")
        print(f"Median PnL per trade: {p['med_pnl']:.2f}")
        print(f"Sum PnL: {p['sum_pnl']:.2f}")
        print(f"Trades per hour: {p['trades_per_hour']:.2f}")
        print(f"PnL per hour: {p['pnl_per_hour']:.2f}")
        print(f"Monthly estimate (paper SELL, {args.hours_per_day}h/day, {args.days_per_month}d/mo): {p['monthly_estimate']:.2f}")
        print(f"Avg hold time (min): {p['avg_hold_min']:.1f}, median: {p['med_hold_min']:.1f}")

    # Optional JSON output
    if args.out_json:
        out_p = Path(args.out_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        full = {**summary}
        full.update(paper_summary)
        with out_p.open("w") as jf:
            json.dump(full, jf, indent=2, default=str)
        print(f"Saved JSON summary to: {out_p}")

    # Optional per-match CSV output
    if args.out_match_csv:
        out_c = Path(args.out_match_csv)
        out_c.parent.mkdir(parents=True, exist_ok=True)
        with out_c.open("w", newline="") as cf:
            writer = csv.writer(cf)
            writer.writerow(["mkey", "events", "sum_avail_usd", "avg_avail_usd", "med_avail_usd", "sum_ev_unlimited"])
            for mkey, agg in summary.get("per_match", {}).items():
                writer.writerow([
                    mkey,
                    agg["events"],
                    f"{agg['sum_avail_usd']:.2f}",
                    f"{agg['avg_avail_usd']:.2f}",
                    f"{agg['med_avail_usd']:.2f}",
                    f"{agg['sum_ev_unlimited']:.2f}",
                ])
        print(f"Saved per-match CSV to: {out_c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
