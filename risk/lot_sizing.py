# Complete Lot Sizing and PnL Engine for Delta Exchange
BTC_PER_LOT = 0.001
PAXG_PER_LOT = 0.01

def compute_points(side: str, entry_price: float, exit_price: float) -> float:
    return (exit_price - entry_price) if side.upper() == 'LONG' else (entry_price - exit_price)

def compute_pnl_usd(symbol: str, side: str, entry_price: float, exit_price: float, lots: int) -> float:
    pts = compute_points(side, entry_price, exit_price)
    multiplier = BTC_PER_LOT if 'BTC' in symbol.upper() else PAXG_PER_LOT
    return pts * lots * multiplier

def lots_to_btc(lots: int) -> float:
    return lots * BTC_PER_LOT

def btc_to_lots(btc: float) -> int:
    return int(round(btc / BTC_PER_LOT))

def lots_to_paxg(lots: int) -> float:
    return lots * PAXG_PER_LOT

def paxg_to_lots(paxg: float) -> int:
    return int(round(paxg / PAXG_PER_LOT))

def calculate_lot_size(symbol: str, equity: float, risk_pct: float, sl_dist: float, min_lots: int = 10, max_lots: int = 1000) -> int:
    asset_equity = equity * 0.50
    risk_dollar = asset_equity * risk_pct

    if 'BTC' in symbol.upper():
        point_value = BTC_PER_LOT
        sl_risk = max(0.1, sl_dist * point_value)
        lots = int(risk_dollar / sl_risk)
        return max(50, min(350, lots))
    else:
        point_value = PAXG_PER_LOT
        sl_risk = max(0.01, sl_dist * point_value)
        lots = int(risk_dollar / sl_risk)
        return max(100, min(950, lots))
