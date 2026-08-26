import os
import logging
import requests
import asyncio
from datetime import datetime

logger = logging.getLogger("Telegram")

def sync_completed_trade_to_sheet(trade_data: dict):
    webhook_url = os.getenv("GSHEET_WEBHOOK_URL", "").strip().strip("\"").strip("\x27")
    if not webhook_url:
        return
    try:
        resp = requests.post(webhook_url, json=trade_data, timeout=8)
        logger.info(f"✅ Real-time trade plotted to Google Sheet: {trade_data.get('symbol')} {trade_data.get('points_captured')} pts (HTTP {resp.status_code})")
    except Exception as e:
        logger.warning(f"Google Sheet live plot warning: {e}")

class Telegram:
    def __init__(self):
        self.enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
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
        side = kwargs.get("side", trade.get("side", getattr(trade, "side", args if len(args) > 1 else "LONG")))
        fill = kwargs.get("fill", kwargs.get("fill_price", trade.get("fill_price", trade.get("entry_price", getattr(trade, "fill_price", getattr(trade, "entry_price", args if len(args) > 2 else 0.0))))))
        sl = kwargs.get("sl", kwargs.get("sl_price", trade.get("sl_price", trade.get("sl", getattr(trade, "sl_price", getattr(trade, "sl", args if len(args) > 3 else 0.0))))))
        tp = kwargs.get("tp", kwargs.get("tp_price", trade.get("tp_price", trade.get("tp", getattr(trade, "tp_price", getattr(trade, "tp", args if len(args) > 4 else 0.0))))))
        lots = kwargs.get("lots", trade.get("lots", getattr(trade, "lots", 950 if "PAXG" in str(symbol) else 285)))
        atr = kwargs.get("atr", trade.get("atr", getattr(trade, "atr", 5.87 if "PAXG" in str(symbol) else 332.0)))
        rr = kwargs.get("rr", kwargs.get("r_multiple", trade.get("rr", getattr(trade, "rr", 3.0))))
        
        try:
            fill, sl, tp, atr, rr = float(fill), float(sl), float(tp), float(atr), float(rr)
        except Exception:
            pass

        diff_sl = abs(fill - sl) if fill > 0 and sl > 0 else 5.20
        diff_tp = abs(tp - fill) if fill > 0 and tp > 0 else 15.00
        sym_tag = "PAXG" if "PAXG" in str(symbol).upper() else "BTC"
        
        lines = [
            f"🟢 <b>[{sym_tag}] ENTRY — {str(side).upper()}</b> | {lots} lots",
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</code>",
            "",
            f"<b>Fill</b>  : ${fill:,.2f}",
            f"<b>SL</b>    : ${sl:,.2f}  (-{diff_sl:.2f})",
            f"<b>TP</b>    : ${tp:,.2f}  (+{diff_tp:.2f})",
            f"<b>ATR</b>   : {atr:.2f}  |  R:R {rr:.2f}"
        ]
        return await self.send("\n".join(lines))

    async def notify_exit(self, *args, **kwargs):
        trade = args[0] if len(args) == 1 and hasattr(args[0], "__dict__") else (args[0] if len(args) == 1 and isinstance(args[0], dict) else {})
        
        symbol = kwargs.get("symbol", trade.get("symbol", getattr(trade, "symbol", args[0] if len(args) > 0 and isinstance(args[0], str) else "BTC/USD:USD")))
        side = kwargs.get("side", trade.get("side", getattr(trade, "side", args if len(args) > 1 else "LONG")))
        entry = kwargs.get("entry", kwargs.get("entry_price", trade.get("entry_price", getattr(trade, "entry_price", args if len(args) > 2 else 0.0))))
        exit_p = kwargs.get("exit", kwargs.get("exit_price", trade.get("exit_price", getattr(trade, "exit_price", args if len(args) > 3 else 0.0))))
        points = kwargs.get("points", kwargs.get("pnl_points", trade.get("pnl_points", getattr(trade, "pnl_points", args if len(args) > 4 else 0.0))))
        gross = kwargs.get("gross_pnl", trade.get("gross_pnl", getattr(trade, "gross_pnl", args if len(args) > 5 else 0.0)))
        lots = kwargs.get("lots", trade.get("lots", getattr(trade, "lots", 950 if "PAXG" in str(symbol) else 285)))
        reason = kwargs.get("reason", trade.get("reason", getattr(trade, "reason", "Closed")))
        
        try:
            entry, exit_p, points, gross = float(entry), float(exit_p), float(points), float(gross)
        except Exception:
            pass

        if points == 0.0 and entry > 0 and exit_p > 0:
            points = round(exit_p - entry if "LONG" in str(side).upper() or "BUY" in str(side).upper() else entry - exit_p, 2)
            asset_size = float(lots) * 0.001
            gross = round(points * asset_size, 4)

        emoji = "💰" if points > 0 else "🔻"
        sign = "+" if points > 0 else ""
        sym_tag = "PAXG" if "PAXG" in str(symbol).upper() else "BTC"
        
        lines = [
            f"{emoji} <b>[{sym_tag}] EXIT — {str(side).upper()}</b> | {lots} lots",
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</code>",
            "",
            f"<b>Entry</b>     : ${entry:,.2f}",
            f"<b>Exit</b>      : ${exit_p:,.2f}",
            f"<b>Points</b>    : {sign}{points:.2f}",
            f"<b>Gross P&L</b> : {sign}${gross:.4f} USD",
            f"<b>Reason</b>    : {reason}"
        ]
        
        # 1. Send Telegram Alert
        res = await self.send("\n".join(lines))
        
        # 2. REAL-TIME GOOGLE SHEET PLOTTER (Runs on Every Completed Trade)
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
                "btc_size": float(lots) * 0.001,
                "gross_pnl": float(gross),
                "fees": round(float(entry + exit_p) * (float(lots) * 0.001) * 0.00035, 2),
                "net_pnl": round(float(gross) - round(float(entry + exit_p) * (float(lots) * 0.001) * 0.00035, 2), 2),
                "net_inr": round((float(gross) - round(float(entry + exit_p) * (float(lots) * 0.001) * 0.00035, 2)) * 84.0, 2),
                "balance": 10000.0,
                "status": "CLOSED",
                "notes": str(reason)
            }
            sync_completed_trade_to_sheet(trade_payload)
        except Exception as e:
            logger.warning(f"GSheet live sync warning: {e}")
            
        return res
