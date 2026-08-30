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
        
        symbol = kwargs.get("symbol", trade.get("symbol", getattr(trade, "symbol", args[0] if len(args) > 0 and isinstance(args[0], str) else "BTC/USD:USD")))
        side = kwargs.get("side", trade.get("side", getattr(trade, "side", args[1] if len(args) > 1 else "LONG")))
        fill = kwargs.get("fill", kwargs.get("fill_price", trade.get("fill_price", trade.get("entry_price", getattr(trade, "fill_price", getattr(trade, "entry_price", args[2] if len(args) > 2 else 0.0))))))
        sl = kwargs.get("sl", kwargs.get("sl_price", trade.get("sl_price", trade.get("sl", getattr(trade, "sl_price", getattr(trade, "sl", args[3] if len(args) > 3 else 0.0))))))
        tp = kwargs.get("tp", kwargs.get("tp_price", trade.get("tp_price", trade.get("tp", getattr(trade, "tp_price", getattr(trade, "tp", args[4] if len(args) > 4 else 0.0))))))
        lots = kwargs.get("lots", trade.get("lots", getattr(trade, "lots", 950 if "PAXG" in str(symbol).upper() else 285)))
        atr = kwargs.get("atr", trade.get("atr", getattr(trade, "atr", 5.87 if "PAXG" in str(symbol).upper() else 332.0)))
        rr = kwargs.get("rr", kwargs.get("r_multiple", trade.get("rr", getattr(trade, "rr", 3.0))))
        
        try:
            fill, sl, tp, atr, rr = float(fill), float(sl), float(tp), float(atr), float(rr)
        except Exception:
            pass

        price_val = float(fill) if fill > 0 else (float(sl) if sl > 0 else 0.0)
        sym_tag = "PAXG" if ("PAXG" in str(symbol).upper() or (0 < price_val < 20000)) else "BTC"

        diff_sl = abs(fill - sl) if fill > 0 and sl > 0 else (5.20 if sym_tag == "PAXG" else 240.0)
        diff_tp = abs(tp - fill) if fill > 0 and tp > 0 else (15.00 if sym_tag == "PAXG" else 350.0)
        
        lines = [
            f"🟢 <b>[{sym_tag}] ENTRY — {str(side).upper()}</b> | {lots} lots",
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</code>",
            "",
            f"<b>Fill</b>  : ",
            f"<b>SL</b>    :   (-{diff_sl:.2f})",
            f"<b>TP</b>    :   (+{diff_tp:.2f})",
            f"<b>ATR</b>   : {atr:.2f}  |  R:R {rr:.2f}"
        ]
        return await self.send(chr(10).join(lines))

    async def notify_exit(self, *args, **kwargs):
        trade = args[0] if len(args) == 1 and hasattr(args[0], "__dict__") else (args[0] if len(args) == 1 and isinstance(args[0], dict) else {})
        
        symbol = kwargs.get("symbol", trade.get("symbol", getattr(trade, "symbol", args[0] if len(args) > 0 and isinstance(args[0], str) else "BTC/USD:USD")))
        side = kwargs.get("side", trade.get("side", getattr(trade, "side", args[1] if len(args) > 1 else "LONG")))
        entry = kwargs.get("entry", kwargs.get("entry_price", trade.get("entry_price", getattr(trade, "entry_price", args[2] if len(args) > 2 else 0.0))))
        exit_p = kwargs.get("exit", kwargs.get("exit_price", trade.get("exit_price", getattr(trade, "exit_price", args[3] if len(args) > 3 else 0.0))))
        points = kwargs.get("points", kwargs.get("pnl_points", trade.get("pnl_points", getattr(trade, "pnl_points", args[4] if len(args) > 4 else 0.0))))
        gross = kwargs.get("gross_pnl", trade.get("gross_pnl", getattr(trade, "gross_pnl", args[5] if len(args) > 5 else 0.0)))
        lots = kwargs.get("lots", trade.get("lots", getattr(trade, "lots", 950 if "PAXG" in str(symbol).upper() else 285)))
        reason = kwargs.get("reason", trade.get("reason", getattr(trade, "reason", "Closed")))
        duration_mins = kwargs.get("duration_mins", trade.get("duration_mins", 15.0))
        
        try:
            entry, exit_p, points, gross = float(entry), float(exit_p), float(points), float(gross)
        except Exception:
            pass

        if points == 0.0 and entry > 0 and exit_p > 0:
            points = round(exit_p - entry if "LONG" in str(side).upper() or "BUY" in str(side).upper() else entry - exit_p, 2)
            asset_size = float(lots) * 0.001
            gross = round(points * asset_size, 4)

        price_val = float(entry) if entry > 0 else (float(exit_p) if exit_p > 0 else 0.0)
        sym_tag = "PAXG" if ("PAXG" in str(symbol).upper() or (0 < price_val < 20000)) else "BTC"

        clean_reason = str(reason)
        if points > 0 and "Max SL" in clean_reason:
            clean_reason = "Target TP / Trailing Win"

        asset_size = float(lots) * 0.001
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
            f"{emoji} <b>[{sym_tag}] EXIT — {str(side).upper()}</b> | {lots} lots",
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</code>",
            "",
            f"<b>Entry</b>     : ",
            f"<b>Exit</b>      : ",
            f"<b>Points</b>    : {sign}{points:.2f}",
            f"<b>Gross P&L</b> : {sign} USD",
            f"<b>Fees (Delta)</b>:  USD",
            f"<b>Net P&L</b>   : {sign} USD ({sign}₹{net_inr:,.2f} INR)",
            f"<b>Reason</b>    : {clean_reason}"
        ]
        
        res = await self.send(chr(10).join(lines))
        
        try:
            trade_payload = {
                "trade_id": f"TRD-{datetime.now().strftime('%m%d-%H%M%S')}",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": str(symbol),
                "engine": "E1_TREND_PULLBACK",
                "side": str(side).upper(),
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
