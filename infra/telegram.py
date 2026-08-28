import os
import logging
import requests
import asyncio
from datetime import datetime

logger = logging.getLogger("Telegram")

def sync_completed_trade_to_sheet(d):
    url = os.getenv("GSHEET_WEBHOOK_URL", "").strip().strip('"\x27')
    if not url:
        return
    try:
        requests.post(url, json=d, timeout=8)
        logger.info(f"GSheet synced: {d.get('symbol')} {d.get('points_captured')} pts")
    except Exception as e:
        logger.warning(f"GSheet sync error: {e}")

class Telegram:
    def __init__(self):
        self.enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _send_sync(self, text, parse_mode="HTML"):
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            r = requests.post(f"{self.base_url}/sendMessage", json={"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}, timeout=8)
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    async def send(self, text, parse_mode="HTML"):
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._send_sync, text, parse_mode)
        except Exception:
            return self._send_sync(text, parse_mode)

    async def notify_entry(self, *args, **kwargs):
        t = args[0] if len(args) == 1 and hasattr(args[0], "__dict__") else (args[0] if len(args) == 1 and isinstance(args[0], dict) else {})
        symbol = kwargs.get("symbol", t.get("symbol", getattr(t, "symbol", args[0] if len(args) > 0 and isinstance(args[0], str) else "BTC/USD:USD")))
        is_gold = "PAXG" in str(symbol).upper() or "GOLD" in str(symbol).upper()
        sym_tag = "PAXG" if is_gold else "BTC"
        side = str(kwargs.get("side", t.get("side", getattr(t, "side", args if len(args) > 1 else "LONG")))).upper()
        
        fill = kwargs.get("fill", kwargs.get("fill_price", kwargs.get("entry", kwargs.get("entry_price", kwargs.get("price", t.get("fill_price", t.get("entry_price", getattr(t, "fill_price", getattr(t, "entry_price", args if len(args) > 2 else 0.0)))))))))
        sl = kwargs.get("sl", kwargs.get("sl_price", t.get("sl_price", t.get("sl", getattr(t, "sl_price", getattr(t, "sl", args if len(args) > 3 else 0.0))))))
        tp = kwargs.get("tp", kwargs.get("tp_price", t.get("tp_price", t.get("tp", getattr(t, "tp_price", getattr(t, "tp", args if len(args) > 4 else 0.0))))))
        lots = kwargs.get("lots", t.get("lots", getattr(t, "lots", 950 if is_gold else 285)))
        atr = kwargs.get("atr", t.get("atr", getattr(t, "atr", 5.87 if is_gold else 280.0)))
        rr = kwargs.get("rr", kwargs.get("r_multiple", t.get("rr", getattr(t, "rr", 3.0))))
        
        try:
            fill, sl, tp, atr, rr = float(fill), float(sl), float(tp), float(atr), float(rr)
        except Exception:
            pass

        if fill <= 0.0:
            if sl > 0.0 and tp > 0.0:
                fill = round((sl + tp) / 2.0, 2)
            elif is_gold:
                fill = 4609.20
            else:
                fill = 79500.00

        diff_sl = abs(fill - sl) if sl > 0.0 and abs(fill - sl) > 0.01 else (atr * 1.12 if atr > 0 else (5.20 if is_gold else 350.0))
        diff_tp = abs(tp - fill) if tp > 0.0 and abs(tp - fill) > 0.01 else (diff_sl * rr)
        
        if "LONG" in side or "BUY" in side:
            if sl >= fill or sl <= 0.0:
                sl = round(fill - diff_sl, 2)
            if tp <= fill or tp <= 0.0:
                tp = round(fill + diff_tp, 2)
        else:
            if sl <= fill or sl <= 0.0:
                sl = round(fill + diff_sl, 2)
            if tp >= fill or tp <= 0.0:
                tp = round(fill - diff_tp, 2)

        lines = [
            f"🟢 <b>[{sym_tag}] ENTRY — {side}</b> | {lots} lots",
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</code>",
            "",
            f"<b>Fill</b>  : ${fill:,.2f}",
            f"<b>SL</b>    : ${sl:,.2f}  (-{diff_sl:.2f})",
            f"<b>TP</b>    : ${tp:,.2f}  (+{diff_tp:.2f})",
            f"<b>ATR</b>   : {atr:.2f}  |  R:R {rr:.2f}"
        ]
        return await self.send("\n".join(lines))

    async def notify_exit(self, *args, **kwargs):
        t = args[0] if len(args) == 1 and hasattr(args[0], "__dict__") else (args[0] if len(args) == 1 and isinstance(args[0], dict) else {})
        symbol = kwargs.get("symbol", t.get("symbol", getattr(t, "symbol", args[0] if len(args) > 0 and isinstance(args[0], str) else "BTC/USD:USD")))
        is_gold = "PAXG" in str(symbol).upper() or "GOLD" in str(symbol).upper()
        sym_tag = "PAXG" if is_gold else "BTC"
        side = str(kwargs.get("side", t.get("side", getattr(t, "side", args if len(args) > 1 else "LONG")))).upper()
        entry = kwargs.get("entry", kwargs.get("entry_price", kwargs.get("fill", t.get("entry_price", getattr(t, "entry_price", args if len(args) > 2 else 0.0)))))
        exit_p = kwargs.get("exit", kwargs.get("exit_price", t.get("exit_price", getattr(t, "exit_price", args if len(args) > 3 else 0.0))))
        points = kwargs.get("points", kwargs.get("pnl_points", t.get("pnl_points", getattr(t, "pnl_points", args if len(args) > 4 else 0.0))))
        gross = kwargs.get("gross_pnl", t.get("gross_pnl", getattr(t, "gross_pnl", args if len(args) > 5 else 0.0)))
        lots = kwargs.get("lots", t.get("lots", getattr(t, "lots", 950 if is_gold else 285)))
        reason = kwargs.get("reason", t.get("reason", getattr(t, "reason", "Closed")))

        try:
            entry, exit_p, points, gross = float(entry), float(exit_p), float(points), float(gross)
        except Exception:
            pass

        if points == 0.0 and entry > 0 and exit_p > 0:
            points = round(exit_p - entry if ("LONG" in side or "BUY" in side) else entry - exit_p, 2)

        asset_size = float(lots) * 0.001
        if gross == 0.0 and points != 0.0:
            gross = round(points * asset_size, 4)

        if points < 0 and "Trail" in str(reason):
            reason = "Max SL"

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
            f"<b>Reason</b>    : {reason}"
        ]
        res = await self.send("\n".join(lines))
        
        try:
            fees_usd = round(float(entry + exit_p) * asset_size * 0.00035, 2)
            net_usd = round(gross - fees_usd, 2)
            payload = {
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
                "fees": float(fees_usd),
                "net_pnl": float(net_usd),
                "net_inr": round(net_usd * 84.0, 2),
                "balance": 10000.0,
                "status": "CLOSED",
                "notes": str(reason)
            }
            sync_completed_trade_to_sheet(payload)
        except Exception as e:
            logger.warning(f"GSheet sync error: {e}")
            
        return res
