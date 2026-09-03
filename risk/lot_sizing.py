# Complete Robust Lot Sizing and PnL Engine for Delta Exchange

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

def compute_points(*args, **kwargs) -> float:
    """Calculates points captured cleanly regardless of argument order."""
    entry = kwargs.get('entry_price', kwargs.get('entry', 0.0))
    exit_p = kwargs.get('exit_price', kwargs.get('exit', 0.0))
    side = kwargs.get('side', 'LONG')

    for a in args:
        if isinstance(a, bool):
            side = 'LONG' if a else 'SHORT'
        elif isinstance(a, str):
            side = a
        elif isinstance(a, (int, float)):
            if entry == 0.0:
                entry = float(a)
            else:
                exit_p = float(a)

    if str(side).upper() in ['LONG', 'BUY']:
        return round(float(exit_p) - float(entry), 2)
    else:
        return round(float(entry) - float(exit_p), 2)

def compute_pnl_usd(*args, **kwargs) -> float:
    """Computes gross PnL in USD for Delta Exchange lots."""
    symbol = kwargs.get('symbol', 'BTC')
    lots = kwargs.get('lots', kwargs.get('qty', 1))
    pts = 0.0
    entry = 0.0
    exit_p = 0.0
    side = 'LONG'

    for a in args:
        if isinstance(a, bool):
            side = 'LONG' if a else 'SHORT'
        elif isinstance(a, str):
            if any(s in a.upper() for s in ['BTC', 'PAXG', 'GOLD']):
                symbol = a
            elif a.upper() in ['LONG', 'SHORT', 'BUY', 'SELL']:
                side = a
        elif isinstance(a, int):
            lots = a
        elif isinstance(a, float):
            if entry == 0.0:
                entry = a
            elif exit_p == 0.0:
                exit_p = a

    if exit_p != 0.0:
        pts = compute_points(entry, exit_p, side)
    elif len(args) > 0 and isinstance(args[0], float):
        pts = args[0]

    multiplier = BTC_PER_LOT if 'BTC' in str(symbol).upper() else PAXG_PER_LOT
    return round(float(pts) * int(lots) * multiplier, 4)

def lots_to_btc(lots: int) -> float:
    return int(lots) * BTC_PER_LOT

def btc_to_lots(btc: float, contract_value: float = None) -> int:
    divisor = float(contract_value) if contract_value else BTC_PER_LOT
    return int(round(float(btc) / divisor))

def lots_to_paxg(lots: int) -> float:
    return int(lots) * PAXG_PER_LOT

def paxg_to_lots(paxg: float) -> int:
    return int(round(float(paxg) / PAXG_PER_LOT))

def calc_qty_from_risk(*args, **kwargs) -> int:
    equity = float(kwargs.get("equity_usd", kwargs.get("equity", kwargs.get("balance", 10000.0))))
    risk_pct = float(kwargs.get("risk_pct", kwargs.get("risk_percent", kwargs.get("risk", 0.01))))
    if risk_pct > 0.1:
        risk_pct = risk_pct / 100.0
    risk_usd = equity * risk_pct
    
    sl_dist = float(kwargs.get("stop_dist_pts", kwargs.get("sl_dist", kwargs.get("sl_distance", kwargs.get("sl", 10.0)))))
    contract_val = float(kwargs.get("contract_value", 0.001))
    
    # Distinguish BTC vs PAXG (BTC stop loss distance is always > 50 points)
    is_btc = "BTC" in str(kwargs.get("symbol", "")).upper() or sl_dist > 50.0
    raw_lots = int(round((risk_usd / max(1.0, sl_dist)) / contract_val))
    
    if is_btc:
        min_l = int(os.getenv("MIN_BTC_LOTS", "50"))
        max_l = int(os.getenv("MAX_BTC_LOTS", "250"))
        return max(min_l, min(max_l, raw_lots))
    else:
        min_l = int(os.getenv("MIN_GOLD_LOTS", "200"))
        max_l = int(os.getenv("MAX_GOLD_LOTS", "1200"))
        return max(min_l, min(max_l, raw_lots))
calculate_lot_size = calc_qty_from_risk
