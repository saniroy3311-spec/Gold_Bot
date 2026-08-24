# Complete Lot Sizing and PnL Engine for Delta Exchange
class UsdPerPointLot(float):
    def __getitem__(self, key):
        if 'BTC' in str(key).upper():
            return 0.001
        return 0.01
    def get(self, key, default=0.001):
        if 'BTC' in str(key).upper():
            return 0.001
        elif 'PAXG' in str(key).upper():
            return 0.01
        return default

USD_PER_POINT_LOT = UsdPerPointLot(0.001)
BTC_PER_LOT = 0.001
PAXG_PER_LOT = 0.01

def compute_points(side: str, entry_price: float, exit_price: float) -> float:
    return (exit_price - entry_price) if str(side).upper() == 'LONG' else (entry_price - exit_price)

def compute_pnl_usd(symbol: str, side: str, entry_price: float, exit_price: float, lots: int) -> float:
    pts = compute_points(side, entry_price, exit_price)
    multiplier = BTC_PER_LOT if 'BTC' in str(symbol).upper() else PAXG_PER_LOT
    return pts * lots * multiplier

def lots_to_btc(lots: int) -> float:
    return lots * BTC_PER_LOT

def btc_to_lots(btc: float) -> int:
    return int(round(btc / BTC_PER_LOT))

def lots_to_paxg(lots: int) -> float:
    return lots * PAXG_PER_LOT

def paxg_to_lots(paxg: float) -> int:
    return int(round(paxg / PAXG_PER_LOT)y

def calc_qty_from_risk(symbol: str, equity: float, risk_pct: float, sl_dist: float, min_lots: int = 10, max_lots: int = 1000, *args, **kwargs) -> int:
    asset_equity = equity * 0.50
    risk_dollar = asset_equity * risk_pct

    if 'BTC' in str(symbol).upper():
        point_value = BTC_PER_LOT
        sl_risk = max(0.1, sl_dist * point_value)
        lots = int(risk_dollar / sl_risk)
        return max(50, min(350, lots))
    else:
        point_value = PAXG_PER_LOT
        sl_risk = max(0.01, sl_dist * point_value)
        lots = int(risk_dollar / sl_risk)
        return max(100, min(950, lots))

calculate_lot_size = calc_qty_from_risk
