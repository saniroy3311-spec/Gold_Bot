# Complete Lot Sizing and PnL Engine for Delta Exchange

BTC_PER_LOT  = 0.001
PAXG_PER_LOT = 0.01

class _UsdPerPointLot:
    def __getitem__(self, key):
        return BTC_PER_LOT if 'BTC' in str(key).upper() else PAXG_PER_LOT
    def get(self, key, default=0.001):
        if 'BTC'  in str(key).upper(): return BTC_PER_LOT
        if 'PAXG' in str(key).upper(): return PAXG_PER_LOT
        return default

USD_PER_POINT_LOT = _UsdPerPointLot()

def compute_points(side: str, entry_price: float, exit_price: float) -> float:
    return (exit_price - entry_price) if str(side).upper() == 'LONG' else (entry_price - exit_price)

def compute_pnl_usd(symbol: str, side: str, entry_price: float, exit_price: float, lots: int) -> float:
    pts = compute_points(side, entry_price, exit_price)
    multiplier = BTC_PER_LOT if 'BTC' in str(symbol).upper() else PAXG_PER_LOT
    return pts * lots * multiplier

def lots_to_btc(lots: int)  -> float: return lots * BTC_PER_LOT
def btc_to_lots(btc: float) -> int:   return int(round(btc  / BTC_PER_LOT))
def lots_to_paxg(lots: int) -> float: return lots * PAXG_PER_LOT
def paxg_to_lots(paxg: float) -> int: return int(round(paxg / PAXG_PER_LOT))

def calc_qty_from_risk(symbol: str, equity: float, risk_pct: float, sl_dist: float,
                       min_lots: int = 10, max_lots: int = 1000, *args, **kwargs) -> int:
    risk_dollar = (equity * 0.50) * risk_pct
    if 'BTC' in str(symbol).upper():
        sl_risk = max(0.1,  sl_dist * BTC_PER_LOT)
        return max(50,  min(350, int(risk_dollar / sl_risk)))
    else:
        sl_risk = max(0.01, sl_dist * PAXG_PER_LOT)
        return max(100, min(950, int(risk_dollar / sl_risk)))

calculate_lot_size = calc_qty_from_risk
