# Delta Exchange Contract Multiplier Lot Sizer
def calculate_lot_size(symbol: str, equity: float, risk_pct: float, sl_dist: float, min_lots: int = 10, max_lots: int = 1000) -> int:
    """
    Calculates Delta Exchange Lots:
    • BTCUSD: 1 Lot = 0.001 BTC (1 pt move = -bash.001/lot)
    • PAXGUSD: 1 Lot = 0.01 PAXG (1 pt move = -bash.01/lot)
    """
    asset_equity = equity * 0.50
    risk_dollar = asset_equity * risk_pct

    if 'BTC' in symbol.upper():
        point_value_per_lot = 0.001 # 1 lot = 0.001 BTC
        sl_risk_per_lot = max(0.1, sl_dist * point_value_per_lot)
        lots = int(risk_dollar / sl_risk_per_lot)
        return max(50, min(350, lots)) # 50 to 350 lots (0.05 to 0.35 BTC)
    else:
        point_value_per_lot = 0.01 # 1 lot = 0.01 PAXG
        sl_risk_per_lot = max(0.01, sl_dist * point_value_per_lot)
        lots = int(risk_dollar / sl_risk_per_lot)
        return max(100, min(950, lots)) # 100 to 950 lots (1.0 to 9.5 Oz)
