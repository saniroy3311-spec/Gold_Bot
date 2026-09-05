"""risk/lot_sizing.py - deterministic sizing and P&L helpers.

FIX 2026-09-05
- Adds the missing ``import os`` that previously caused risk sizing to raise
  NameError and silently fall back to the static quantity.
- Uses the caller supplied contract value and min/max lot limits.
- Removes the stop-distance heuristic that guessed the instrument from price
  geometry.
- Keeps the legacy helper names used elsewhere in the project.
"""
from __future__ import annotations

import math
import os
from typing import Any


class UsdPerPointLot(float):
    def __getitem__(self, key):
        if "BTC" in str(key).upper():
            return 0.001
        return 0.01

    def get(self, key, default=0.001):
        name = str(key).upper()
        if "BTC" in name:
            return 0.001
        if "PAXG" in name or "GOLD" in name:
            return 0.01
        return default


USD_PER_POINT_LOT = UsdPerPointLot(0.001)
BTC_PER_LOT = 0.001
PAXG_PER_LOT = 0.01


def _side_is_long(side: Any) -> bool:
    if isinstance(side, bool):
        return side
    return str(side).strip().upper() in {"LONG", "BUY", "TRUE", "1"}


def compute_points(*args, **kwargs) -> float:
    """Return directional points captured.

    Supported forms include:
      compute_points(entry, exit, is_long)
      compute_points(entry_price=..., exit_price=..., side="SHORT")
    """
    entry = kwargs.get("entry_price", kwargs.get("entry"))
    exit_p = kwargs.get("exit_price", kwargs.get("exit"))
    side = kwargs.get("is_long", kwargs.get("side", "LONG"))

    numeric = []
    for arg in args:
        if isinstance(arg, bool):
            side = arg
        elif isinstance(arg, str):
            if arg.strip().upper() in {"LONG", "SHORT", "BUY", "SELL"}:
                side = arg
        elif isinstance(arg, (int, float)):
            numeric.append(float(arg))

    if entry is None and numeric:
        entry = numeric.pop(0)
    if exit_p is None and numeric:
        exit_p = numeric.pop(0)

    entry = float(entry or 0.0)
    exit_p = float(exit_p or 0.0)
    points = (exit_p - entry) if _side_is_long(side) else (entry - exit_p)
    return round(points, 2)


def compute_pnl_usd(*args, **kwargs) -> float:
    """Return gross USD P&L for a linear Delta contract.

    Preferred form:
      compute_pnl_usd(entry, exit, qty, is_long, contract_value=...)
    """
    entry = kwargs.get("entry_price", kwargs.get("entry"))
    exit_p = kwargs.get("exit_price", kwargs.get("exit"))
    lots = kwargs.get("lots", kwargs.get("qty"))
    side = kwargs.get("is_long", kwargs.get("side", "LONG"))
    contract_value = kwargs.get("contract_value")
    symbol = str(kwargs.get("symbol", "BTC"))

    numeric = []
    for arg in args:
        if isinstance(arg, bool):
            side = arg
        elif isinstance(arg, str):
            up = arg.strip().upper()
            if up in {"LONG", "SHORT", "BUY", "SELL"}:
                side = arg
            elif any(x in up for x in ("BTC", "PAXG", "GOLD")):
                symbol = arg
        elif isinstance(arg, (int, float)):
            numeric.append(arg)

    if entry is None and numeric:
        entry = float(numeric.pop(0))
    if exit_p is None and numeric:
        exit_p = float(numeric.pop(0))
    if lots is None and numeric:
        lots = int(round(float(numeric.pop(0))))

    entry = float(entry or 0.0)
    exit_p = float(exit_p or 0.0)
    lots = int(round(float(lots or 0)))

    if contract_value is None:
        name = symbol.upper()
        contract_value = PAXG_PER_LOT if ("PAXG" in name or "GOLD" in name) else BTC_PER_LOT
    contract_value = float(contract_value)

    points = compute_points(entry, exit_p, side)
    return round(points * lots * contract_value, 6)


def lots_to_btc(lots: int) -> float:
    return int(lots) * BTC_PER_LOT


def btc_to_lots(btc: float, contract_value: float = None) -> int:
    divisor = float(contract_value) if contract_value and float(contract_value) > 0 else BTC_PER_LOT
    return int(round(float(btc) / divisor))


def lots_to_paxg(lots: int) -> float:
    return int(lots) * PAXG_PER_LOT


def paxg_to_lots(paxg: float, contract_value: float = None) -> int:
    divisor = float(contract_value) if contract_value and float(contract_value) > 0 else PAXG_PER_LOT
    return int(round(float(paxg) / divisor))


def calc_qty_from_risk(*args, **kwargs) -> int:
    """Calculate integer contract lots from account risk.

    ``risk_pct`` accepts either a percent (1.0 = 1%) or a fraction
    (0.01 = 1%). The result is floored so rounding cannot exceed the intended
    dollar risk before min/max constraints are applied.
    """
    equity = float(kwargs.get("equity_usd", kwargs.get("equity", kwargs.get("balance", 0.0))))
    risk_pct = float(kwargs.get("risk_pct", kwargs.get("risk_percent", kwargs.get("risk", 0.0))))
    stop_dist = float(kwargs.get("stop_dist_pts", kwargs.get("sl_dist", kwargs.get("sl_distance", 0.0))))
    contract_value = float(kwargs.get("contract_value", 0.0))
    symbol = str(kwargs.get("symbol", "")).upper()

    if equity <= 0:
        raise ValueError("equity_usd must be > 0")
    if risk_pct <= 0:
        raise ValueError("risk_pct must be > 0")
    if stop_dist <= 0:
        raise ValueError("stop_dist_pts must be > 0")
    if contract_value <= 0:
        raise ValueError("contract_value must be > 0")

    risk_fraction = risk_pct if risk_pct <= 0.1 else risk_pct / 100.0
    risk_usd = equity * risk_fraction
    risk_per_lot = stop_dist * contract_value
    raw_lots = max(1, int(math.floor(risk_usd / risk_per_lot)))

    min_lots = kwargs.get("min_lots")
    max_lots = kwargs.get("max_lots")

    if min_lots is None:
        asset = "BTC" if "BTC" in symbol else ("GOLD" if ("PAXG" in symbol or "GOLD" in symbol) else "")
        min_lots = int(os.getenv(f"MIN_{asset}_LOTS", "1")) if asset else 1
    else:
        min_lots = int(min_lots)

    if max_lots is None:
        asset = "BTC" if "BTC" in symbol else ("GOLD" if ("PAXG" in symbol or "GOLD" in symbol) else "")
        max_lots = int(os.getenv(f"MAX_{asset}_LOTS", "0")) if asset else 0
    else:
        max_lots = int(max_lots)

    lots = max(max(1, min_lots), raw_lots)
    if max_lots > 0:
        lots = min(lots, max_lots)
    return int(lots)


calculate_lot_size = calc_qty_from_risk
