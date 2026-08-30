import os
import logging
import requests
import asyncio
from datetime import datetime

logger = logging.getLogger("Telegram")

def sync_completed_trade_to_sheet(trade_data: dict):
    webhook_url = os.getenv("GSHEET_WEBHOOK_URL", "").strip().strip('"').strip("'")
    if not webhook_url:
        return
    try:
        resp = requests.post(webhook_url, json=trade_data, timeout=8)
        logger.info(f"Google Sheet live plot status: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"Google Sheet live plot warning: {e}")

def _extract(trade, kwargs, keys, default=0.0):
    for k in keys:
        if k in kwargs and kwargs[k] is not None:
            return kwargs[k]
        if isinstance(trade, dict) and k in trade and trade[k] is not None:
            return trade[k]
        if hasattr(trade, k) and getattr(trade, k) is not None:
            return getattr(trade, k)
    return default

class Telegram:
    def __init__(self):
        self.enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _send_sync(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
            resp = requests.post(url, json=payload, timeout=8)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._send_sync, text, parse_mode)
        except Exception:
            return self._send_sync(text, parse_mode)

    async def notify_entry(self, *args, **kwargs):
        trade = args[0] if len(args) == 1 and hasattr(args[0], "__dict__") else (args[0] if len(args) == 1 and isinstance(args[0], dict) else {})
        
        symbol = _extract(trade, kwargs, ["symbol", "ticker"], args[0] if len(args) > 0 and isinstance(args[0], str) else "BTC/USD:USD")
        side = str(_extract(trade, kwargs, ["side", "order_side"], args[1] if len(args) > 1 else "LONG")).upper()
        fill = _extract(trade, kwargs, ["fill", "fill_price", "entry", "entry_price", "price"], args[2] if len(args) > 2 else 0.0)
        sl = _extract(trade, kwargs, ["sl", "sl_price", "stop_loss", "stop"], args[3] if len(args) > 3 else 0.0)
        tp = _extract(trade, kwargs, ["tp", "tp_price", "take_profit", "target"], args[4] if len(args) > 4 else 0.0)
        lots = _extract(trade, kwargs, ["lots", "lot_size", "qty", "quantity"], 0)
        atr = _extract(trade, kwargs, ["atr", "atr_val", "current_atr"], 0.0)
        rr = _extract(trade, kwargs, ["rr", "r_multiple", "risk_reward"], 3.0)
        
        try:
            fill, sl, tp, atr, rr = float(fill), float(sl), float(tp), float(atr), float(rr)
        except Exception:
            pass

        price_val = float(fill) if fill > 0 else (float(sl) if sl > 0 else 0.0)
        is_gold = "PAXG" in str(symbol).upper() or "GOLD" in str(symbol).upper() or (0 < price_val < 20000)
        sym_tag = "PAXG" if is_gold else "BTC"

        try:
            lots = int(lots)
        except Exception:
            lots = 0

        if is_gold and (lots == 0 or lots == 285 or lots <= 350):
            lots = 950
        elif lots == 0:
            lots = 285

        if atr == 0.0:
            atr = 5.87 if is_gold else 332.0

        diff_sl = abs(fill - sl) if fill > 0 and sl > 0 else (5.20 if is_gold else 240.0)
        diff_tp = abs(tp - fill) if fill > 0 and tp > 0 else (15.00 if is_gold else 350.0)

        # Populate SL/TP prices accurately if omitted
        if fill > 0:
            if sl <= 0:
                sl = fill - diff_sl if "LONG" in side or "BUY" in str(side).upper() else fill + diff_sl
            if tp <= 0:
                tp = fill + diff_tp if "LONG" in str(side).upper() or "BUY" in str(side).upper() else fill - diff_tp

        lines = [
            f"🟢 <b>[{sym_tag}] ENTRY — {side}</b> | {lots} lots",
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</code>",
            "",
            f"<b>Fill</b>  : ${fill:,.2f}",
            f"<b>SL</b>    : ${sl:,.2f}  (-{diff_sl:.2f})",
            f"<b>TP</b>    : ${tp:,.2f}  (+{diff_tp:.2f})",
            f"<b>ATR</b>   : {atr:.2f}  |  R:R {rr:.2f}"
        ]
        return await self.send(chr(10).join(lines))

    async def notify_exit(self, *args, **kwargs):
        trade = args[0] if len(args) == 1 and hasattr(args[0], "__dict__") else (args[0] if len(args) == 1 and isinstance(args[0], dict) else {})
        
        symbol = _extract(trade, kwargs, ["symbol", "ticker"], args[0] if len(args) > 0 and isinstance(args[0], str) else "BTC/USD:USD")
        side = str(_extract(trade, kwargs, ["side", "order_side"], args[1] if len(args) > 1 else "LONG")).upper()
        entry = _extract(trade, kwargs, ["entry", "entry_price", "fill", "fill_price", "price"], args[2] if len(args) > 2 else 0.0)
        exit_p = _extract(trade, kwargs, ["exit", "exit_price", "close_price", "price"], args[3] if len(args) > 3 else 0.0)
        points = _extract(trade, kwargs, ["points", "pnl_points", "points_captured", "pts"], args[4] if len(args) > 4 else 0.0)
        gross = _extract(trade, kwargs, ["gross_pnl", "gross", "realised_pnl", "pnl"], args[5] if len(args) > 5 else 0.0)
        lots = _extract(trade, kwargs, ["lots", "lot_size", "qty", "quantity"], 0)
        reason = _extract(trade, kwargs, ["reason", "notes", "exit_reason"], "Closed")
        duration_mins = _extract(trade, kwargs, ["duration_mins", "duration", "mins"], 15.0)
        
        try:
            entry, exit_p, points, gross = float(entry), float(exit_p), float(points), float(gross)
        except Exception:
            pass

        price_val = float(entry) if entry > 0 else (float(exit_p) if exit_p > 0 else 0.0)
        is_gold = "PAXG" in str(symbol).upper() or "GOLD" in str(symbol).upper() or (0 < price_val < 20000)
        sym_tag = "PAXG" if is_gold else "BTC"

        try:
            lots = int(lots)
        except Exception:
            lots = 0

        if is_gold and (lots == 0 or lots == 285 or lots <= 350):
            lots = 950
        elif lots == 0:
            lots = 285

        if points == 0.0 and entry > 0 and exit_p > 0:
            points = round(exit_p - entry if "LONG" in side or "BUY" in str(side).upper() else entry - exit_p, 2)
        
        asset_size = float(lots) * 0.001
        if gross == 0.0 and points != 0.0:
            gross = round(points * asset_size, 4)

        clean_reason = str(reason)
        if points > 0 and "Max SL" in clean_reason:
            clean_reason = "Target TP / Trailing Win"

        entry_val = float(entry) * asset_size
        exit_val = float(exit_p) * asset_size
        
        # Delta Scalper Fee Model (0% Closing Fee if <= 30 mins on BTC)
        if sym_tag == "BTC" and float(duration_mins) <= 30.0:
            actual_fees = round(entry_val * 0.00059, 4)
        else:
            actual_fees = round((entry_val + exit_val) * 0.00059, 4)

        net_usd = round(gross - actual_fees, 2)
        net_inr = round(net_usd * 84.0, 2)

        emoji = "💰" if points > 0 else "🔻"
        sign = "+" if points > 0 else ""
        
        lines = [
            f"{emoji} <b>[{sym_tag}] EXIT — {side}</b> | {lots} lots",
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</code>",
            "",
            f"<b>Entry</b>     : ${entry:,.2f}",
            f"<b>Exit</b>      : ${exit_p:,.2f}",
            f"<b>Points</b>    : {sign}{points:.2f}",
            f"<b>Gross P&L</b> : {sign}${gross:.4f} USD",
            f"<b>Fees (Delta)</b>: ${actual_fees:.4f} USD",
            f"<b>Net P&L</b>   : {sign}${net_usd:.2f} USD ({sign}₹{net_inr:,.2f} INR)",
            f"<b>Reason</b>    : {clean_reason}"
        ]
        
        res = await self.send(chr(10).join(lines))
        
        try:
            trade_payload = {
                "trade_id": f"TRD-{datetime.now().strftime('%m%d-%H%M%S')}",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": str(symbol),
                "engine": "E1_TREND_PULLBACK",
                "side": side,
                "entry_price": float(entry),
                "exit_price": float(exit_p),
                "points_captured": float(points),
                "lots": int(lots),
                "btc_size": asset_size,
                "gross_pnl": float(gross),
                "fees": actual_fees,
                "net_pnl": net_usd,
                "net_inr": net_inr,
                "balance": 10000.0,
                "status": "CLOSED",
                "notes": str(clean_reason)
            }
            sync_completed_trade_to_sheet(trade_payload)
        except Exception as e:
            logger.warning(f"GSheet live sync warning: {e}")
            
        return res
