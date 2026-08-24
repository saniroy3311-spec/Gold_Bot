class RiskCalculator:
    @staticmethod
    def calculate_position_size(equity: float, risk_pct: float, sl_pts: float, is_btc: bool, config) -> int:
        """
        Calculates position size in Delta Exchange LOTS / CONTRACTS:
        • BTC/USD: 1 Lot = 0.001 BTC (1 BTC = 1,000 Lots)
        • PAXG/USD: 1 Lot = 0.01 PAXG (1 PAXG = 100 Lots)
        """
        # Allocate 50% equity pool to each asset
        asset_equity = equity * 0.50
        risk_dollar = asset_equity * risk_pct

        if is_btc:
            raw_btc = risk_dollar / (sl_pts + 1e-5)
            # Safe boundary: 0.05 BTC (50 lots) to 0.35 BTC (350 lots)
            btc_clipped = max(0.05, min(0.35, raw_btc))
            # Convert BTC to Delta Lots (x 1000)
            return int(round(btc_clipped * 1000))
        else:
            raw_paxg = risk_dollar / (sl_pts + 1e-5)
            # Safe boundary: 1.0 Oz (100 lots) to 10.0 Oz (1000 lots)
            paxg_clipped = max(1.0, min(10.0, raw_paxg))
            # Convert Gold/PAXG to Delta Lots (x 100)
            return int(round(paxg_clipped * 100))
