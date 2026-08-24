"""
risk/lot_sizing.py — Shiva Sniper v10 / GOLDBOT
──────────────────────────────────────────────────────────────────────
GENERALIZED (was BTCUSD-only): contract_value (base-asset units per 1
lot) is now a parameter everywhere instead of a hardcoded 0.001 BTC
constant. This lets the same code correctly size BTCUSD, PAXGUSD (gold),
or any other Delta India linear perpetual.

Delta's /v2/products contract_value field is the source of truth and is
resolved at runtime from the exchange's market metadata in
orders/manager.py (OrderManager.initialize() → self.contract_value),
falling back to config.CONTRACT_VALUE_OVERRIDE if the API is unreachable,
and finally to BTC_PER_LOT (0.001) for full backward compatibility with
the original BTCUSD-only bot.

FORMULA (linear, USD-margined contract — verified against Delta CSV logs):
    P&L (USD)   = points × qty_lots × contract_value
    qty_lots    = base_asset_size / contract_value

EXAMPLES (BTCUSD, contract_value=0.001):
    btc_to_lots(0.001) → 1
    btc_to_lots(0.05)  → 50
    btc_to_lots(0.1)   → 100

RISK-BASED SIZING (new — mirrors the new pine script's
    qty = riskAmount / stopDist
  corrected for Delta's lot/contract_value units):
    qty_lots = (equity_usd × risk_pct/100) / (stop_dist_pts × contract_value)
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# Legacy BTCUSD default — kept as the fallback contract_value when the
# exchange market metadata can't be resolved (backward compatible).
BTC_PER_LOT       = 0.001          # 1 lot = 0.001 BTC face value
USD_PER_POINT_LOT = 0.001          # legacy alias, same value as BTC_PER_LOT
MIN_LOTS          = 1
MAX_LOTS          = 1_000_000      # sanity ceiling


def btc_to_lots(btc_size: float, contract_value: float = BTC_PER_LOT) -> int:
    """
    Convert an intended base-asset position size → Delta lots (contracts).

    Generic across instruments: pass contract_value for the resolved
    symbol (e.g. 0.001 for BTCUSD, whatever PAXGUSD's contract_value is).
    Defaults to BTC_PER_LOT for backward compatibility with existing
    BTCUSD call sites that don't pass contract_value.

    Rule: lots = base_asset_size / contract_value
    Always rounds to the nearest integer lot; clamps to [1, 1_000_000].

    Raises ValueError if base_asset_size <= 0 or contract_value <= 0.
    """
    if btc_size is None or btc_size <= 0:
        raise ValueError(f"base asset size must be > 0, got {btc_size!r}")
    if contract_value is None or contract_value <= 0:
        raise ValueError(f"contract_value must be > 0, got {contract_value!r}")

    raw_lots = btc_size / contract_value
    lots     = int(round(raw_lots))
    lots     = max(MIN_LOTS, min(MAX_LOTS, lots))

    if abs(raw_lots - lots) > 1e-6:
        logger.warning(
            f"btc_to_lots: {btc_size} base units = {raw_lots:.4f} lots → rounded to {lots}"
        )
    return lots


def lots_to_btc(qty_lots: int, contract_value: float = BTC_PER_LOT) -> float:
    """Inverse of btc_to_lots — useful for logging/display."""
    return qty_lots * contract_value


# Backward-compatible alias for the new, instrument-agnostic name.
def lots_to_base(qty_lots: int, contract_value: float) -> float:
    return qty_lots * contract_value


def compute_pnl_usd(entry: float, exit_price: float, qty_lots: int,
                     is_long: bool, contract_value: float = USD_PER_POINT_LOT) -> float:
    """
    Linear, USD-margined contract P&L (verified against Delta CSV logs
    for BTCUSD; same formula applies to any linear perp on Delta, e.g.
    PAXGUSD for gold — just pass the resolved contract_value):
        Points  = (exit - entry) if LONG else (entry - exit)
        P&L USD = Points × qty_lots × contract_value

    Returns the realised P&L in USD before fees.
    """
    points = (exit_price - entry) if is_long else (entry - exit_price)
    return round(points * qty_lots * contract_value, 4)


def compute_points(entry: float, exit_price: float, is_long: bool) -> float:
    """Raw price points captured (positive = profit, negative = loss)."""
    return round((exit_price - entry) if is_long else (entry - exit_price), 2)


def calc_qty_from_risk(
    equity_usd: float,
    risk_pct: float,
    stop_dist_pts: float,
    contract_value: float,
    min_lots: int = MIN_LOTS,
    max_lots: int = 0,
) -> int:
    """
    Risk-based dynamic position sizing — the GOLDBOT architecture change.

    Mirrors the new pine script's model:
        riskAmount = equity * (riskPct / 100)
        qty        = riskAmount / stopDist

    ...corrected for Delta's lot/contract_value units (Pine's raw qty is
    in base-asset units; Delta needs lots):
        risk_amount_usd = equity_usd * (risk_pct / 100)
        qty_lots        = risk_amount_usd / (stop_dist_pts * contract_value)

    Args:
        equity_usd:     live account equity in USD (from OrderManager.get_equity_usd()).
        risk_pct:       % of equity to risk on this trade (config.RISK_PCT_PER_TRADE).
        stop_dist_pts:  planned stop distance in price points for this trade.
        contract_value: base-asset units per 1 lot for the resolved symbol.
        min_lots:       floor (config.MIN_QTY_LOTS). Never returns below this.
        max_lots:       ceiling (config.MAX_QTY_LOTS). 0 = unlimited.

    Returns an integer lot quantity, clamped to [min_lots, max_lots or MAX_LOTS].
    Raises ValueError on non-positive stop_dist_pts, equity_usd, or contract_value
    — a bad qty calculation must never silently produce a 0 or negative order.
    """
    if equity_usd is None or equity_usd <= 0:
        raise ValueError(f"equity_usd must be > 0, got {equity_usd!r}")
    if stop_dist_pts is None or stop_dist_pts <= 0:
        raise ValueError(f"stop_dist_pts must be > 0, got {stop_dist_pts!r}")
    if contract_value is None or contract_value <= 0:
        raise ValueError(f"contract_value must be > 0, got {contract_value!r}")

    risk_amount_usd = equity_usd * (risk_pct / 100.0)
    raw_lots = risk_amount_usd / (stop_dist_pts * contract_value)

    lots = int(round(raw_lots))
    ceiling = max_lots if max_lots and max_lots > 0 else MAX_LOTS
    lots = max(min_lots, min(ceiling, lots))

    logger.info(
        f"[RISK-SIZE] equity=${equity_usd:,.2f} risk={risk_pct}% "
        f"→ risk_amount=${risk_amount_usd:,.2f}  stop_dist={stop_dist_pts:.2f}pts  "
        f"contract_value={contract_value}  raw_lots={raw_lots:.3f} → qty={lots} lots"
    )
    return lots
