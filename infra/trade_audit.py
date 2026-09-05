"""Append-only CSV trade audit log.

This is intentionally separate from the SQLite/Postgres journal.  It gives the
operator a simple human-readable file for every entry and exit, including the
actual direction, risk geometry, quantity and P&L math used by the bot.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict

try:
    from config import COMMISSION_PCT, PAPER_TRADING
except Exception:
    COMMISSION_PCT = 0.0005
    PAPER_TRADING = False

IST = timezone(timedelta(hours=5, minutes=30))
_FIELDS = [
    "timestamp_ist",
    "event",
    "symbol",
    "side",
    "signal_type",
    "paper_trading",
    "qty_lots",
    "contract_value",
    "entry_price",
    "exit_price",
    "sl",
    "tp",
    "atr",
    "stop_dist_points",
    "reward_points",
    "actual_rr",
    "points_captured",
    "gross_pnl_usd",
    "estimated_fees_usd",
    "estimated_net_pnl_usd",
    "exit_reason",
    "trail_stage",
    "exit_source",
]


class TradeAudit:
    def __init__(self, symbol_id: str, symbol: str):
        base = Path(os.getenv("TRADE_LOG_DIR", "trade_logs"))
        base.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(ch for ch in symbol_id.lower() if ch.isalnum() or ch in "-_") or "trades"
        self.path = base / f"trades_{safe_id}.csv"
        self.symbol = symbol
        self._lock = Lock()
        if not self.path.exists() or self.path.stat().st_size == 0:
            with self.path.open("w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=_FIELDS).writeheader()

    @staticmethod
    def _now() -> str:
        return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    def _append(self, row: Dict[str, Any]) -> None:
        normalized = {key: row.get(key, "") for key in _FIELDS}
        normalized["timestamp_ist"] = normalized["timestamp_ist"] or self._now()
        normalized["symbol"] = normalized["symbol"] or self.symbol
        normalized["paper_trading"] = str(bool(PAPER_TRADING)).lower()
        with self._lock:
            with self.path.open("a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=_FIELDS).writerow(normalized)

    def log_entry(
        self,
        *,
        side: str,
        signal_type: str,
        qty_lots: int,
        contract_value: float,
        entry_price: float,
        sl: float,
        tp: float,
        atr: float,
    ) -> None:
        stop_dist = abs(float(entry_price) - float(sl))
        reward = abs(float(tp) - float(entry_price))
        rr = reward / stop_dist if stop_dist > 0 else 0.0
        self._append({
            "event": "ENTRY",
            "side": side,
            "signal_type": signal_type,
            "qty_lots": int(qty_lots),
            "contract_value": float(contract_value),
            "entry_price": round(float(entry_price), 8),
            "sl": round(float(sl), 8),
            "tp": round(float(tp), 8),
            "atr": round(float(atr), 8),
            "stop_dist_points": round(stop_dist, 8),
            "reward_points": round(reward, 8),
            "actual_rr": round(rr, 6),
        })

    def log_exit(
        self,
        *,
        side: str,
        signal_type: str,
        qty_lots: int,
        contract_value: float,
        entry_price: float,
        exit_price: float,
        sl: float,
        tp: float,
        atr: float,
        gross_pnl: float,
        exit_reason: str,
        trail_stage: int,
        exit_source: str,
    ) -> None:
        is_long = side.upper() == "LONG"
        points = (float(exit_price) - float(entry_price)) if is_long else (float(entry_price) - float(exit_price))
        entry_notional = abs(float(entry_price) * int(qty_lots) * float(contract_value))
        exit_notional = abs(float(exit_price) * int(qty_lots) * float(contract_value))
        fees = (entry_notional + exit_notional) * float(COMMISSION_PCT)
        net = float(gross_pnl) - fees
        stop_dist = abs(float(entry_price) - float(sl))
        reward = abs(float(tp) - float(entry_price))
        rr = reward / stop_dist if stop_dist > 0 else 0.0
        self._append({
            "event": "EXIT",
            "side": side,
            "signal_type": signal_type,
            "qty_lots": int(qty_lots),
            "contract_value": float(contract_value),
            "entry_price": round(float(entry_price), 8),
            "exit_price": round(float(exit_price), 8),
            "sl": round(float(sl), 8),
            "tp": round(float(tp), 8),
            "atr": round(float(atr), 8),
            "stop_dist_points": round(stop_dist, 8),
            "reward_points": round(reward, 8),
            "actual_rr": round(rr, 6),
            "points_captured": round(points, 8),
            "gross_pnl_usd": round(float(gross_pnl), 8),
            "estimated_fees_usd": round(fees, 8),
            "estimated_net_pnl_usd": round(net, 8),
            "exit_reason": exit_reason,
            "trail_stage": int(trail_stage),
            "exit_source": exit_source,
        })
