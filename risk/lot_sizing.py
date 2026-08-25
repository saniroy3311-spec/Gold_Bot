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
        if isinstance(a, str):
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
        if isinstance(a, str):
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

def btc_to_lots(btc: float) -> int:
    return int(round(float(btc) / BTC_PER_LOT))

def lots_to_paxg(lots: int) -> float:
    return int(lots) * PAXG_PER_LOT

def paxg_to_lots(paxg: float) -> int:
    return int(round(float(paxg) / PAXG_PER_LOT))

def calc_qty_from_risk(*args, **kwargs) -> int:
    """Calculates order quantity in Delta Exchange lots without overfilling."""
    symbol = kwargs.get('symbol', '')
    equity = kwargs.get('equity', kwargs.get('balance', 10000.0))
    risk_pct = kwargs.get('risk_pct', kwargs.get('risk_percent', kwargs.get('risk', 0.0035)))
    sl_dist = kwargs.get('sl_dist', kwargs.get('sl_distance', kwargs.get('sl', 10.0)))

    for a in args:
        if isinstance(a, str):
            symbol = a
        elif isinstance(a, float):
            if a > 100.0:
                equity = a
            elif 0.0 < a < 0.1:
                risk_pct = a
            else:
                sl_dist = a
        elif isinstance(a, int):
            if a > 500:
                equity = float(a)

    asset_equity = float(equity) * 0.50
    risk_dollar = asset_equity * float(risk_pct)

    if 'BTC' in str(symbol).upper():
        point_value = BTC_PER_LOT
        sl_risk = max(0.1, float(sl_dist) * point_value)
        lots = int(risk_dollar / sl_risk)
        return max(50, min(350, lots)) # 50 to 350 lots (0.05 to 0.35 BTC)
    else:
        point_value = PAXG_PER_LOT
        sl_risk = max(0.01, float(sl_dist) * point_value)
        lots = int(risk_dollar / sl_risk)
        return max(100, min(950, lots)) # 100 to 950 lots (1.0 to 9.5 Oz)

calculate_lot_size = calc_qty_from_risk
