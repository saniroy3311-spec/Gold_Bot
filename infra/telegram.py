"""Telegram notifications for the multi-symbol trading bot.

FIX 2026-09-05
- Direction is taken from ``is_long`` / signal_type instead of defaulting to LONG.
- SHORT points and P&L are calculated with the correct sign.
- Uses the actual quantity supplied by SymbolRunner; removes fabricated 950/285 lots.
- Displays the actual SL:TP geometry instead of a hard-coded R:R 3.00.
- Uses the real fill passed by the runner; no synthetic fill construction.
- Fee output is explicitly an estimate based on configured commission.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

try:
    from config import COMMISSION_PCT
except Exception:
    COMMISSION_PCT = 0.0005

from risk.lot_sizing import compute_points

logger = logging.getLogger("Telegram")
IST = timezone(timedelta(hours=5, minutes=30))


def sync_completed_trade_to_sheet(trade_data: dict):
    webhook_url = os.getenv("GSHEET_WEBHOOK_URL", "").strip().strip('"').strip("'")
    if not webhook_url:
        return
    try:
        resp = requests.post(webhook_url, json=trade_data, timeout=8)
        logger.info("Google Sheet live plot status: HTTP %s", resp.status_code)
    except Exception as exc:
        logger.warning("Google Sheet live plot warning: %s", exc)


def _extract(trade, kwargs, keys, default=None):
    for key in keys:
        if key in kwargs and kwargs[key] is not None:
            return kwargs[key]
        if isinstance(trade, dict) and key in trade and trade[key] is not None:
            return trade[key]
        if hasattr(trade, key) and getattr(trade, key) is not None:
            return getattr(trade, key)
    return default


def _bool_from_value(value, default=None):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().upper()
    if text in {"LONG", "BUY", "TRUE", "1"}:
        return True
    if text in {"SHORT", "SELL", "FALSE", "0"}:
        return False
    return default


def _direction(trade, kwargs, entry=0.0, sl=0.0, tp=0.0):
    explicit = _extract(trade, kwargs, ["is_long"], None)
    val = _bool_from_value(explicit, None)
    if val is not None:
        return val

    side = _extract(trade, kwargs, ["side", "order_side"], None)
    val = _bool_from_value(side, None)
    if val is not None:
        return val

    signal_type = str(_extract(trade, kwargs, ["signal_type", "signal"], ""))
    if "SHORT" in signal_type.upper():
        return False
    if "LONG" in signal_type.upper():
        return True

    # Final compatibility fallback: infer from valid SL/TP geometry.
    if entry > 0 and sl > 0 and tp > 0:
        if sl < entry < tp:
            return True
        if tp < entry < sl:
            return False
    return True


def _symbol_tag(symbol: str, tag: str = "") -> str:
    raw = f"{tag} {symbol}".upper()
    if "PAXG" in raw or "GOLD" in raw:
        return "PAXG"
    if "BTC" in raw:
        return "BTC"
    cleaned = str(tag).strip("[] ") or str(symbol).split("/")[0]
    return cleaned.upper() or "TRADE"


def _money(value: float, decimals: int = 4) -> str:
    value = float(value)
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.{decimals}f}"


class Telegram:
    def __init__(self):
        self.enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    @staticmethod
    def _now_ist() -> str:
        return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    def _send_sync(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
            resp = requests.post(url, json=payload, timeout=8)
            if resp.status_code != 200:
                logger.warning("Telegram HTTP %s: %s", resp.status_code, resp.text[:300])
            return resp.status_code == 200
        except Exception as exc:
            logger.error("Telegram send error: %s", exc)
            return False

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._send_sync, text, parse_mode)
        except Exception:
            return self._send_sync(text, parse_mode)

    async def notify_entry(self, *args, **kwargs):
        trade = args[0] if len(args) == 1 and (isinstance(args[0], dict) or hasattr(args[0], "__dict__")) else {}

        symbol = str(_extract(trade, kwargs, ["symbol", "ticker"], ""))
        tag = str(_extract(trade, kwargs, ["tag"], ""))
        fill = float(_extract(trade, kwargs, ["fill", "fill_price", "entry", "entry_price", "price"], 0.0) or 0.0)
        sl = float(_extract(trade, kwargs, ["sl", "sl_price", "stop_loss", "stop"], 0.0) or 0.0)
        tp = float(_extract(trade, kwargs, ["tp", "tp_price", "take_profit", "target"], 0.0) or 0.0)
        atr = float(_extract(trade, kwargs, ["atr", "atr_val", "current_atr"], 0.0) or 0.0)
        lots = int(round(float(_extract(trade, kwargs, ["lots", "lot_size", "qty", "quantity"], 0) or 0)))

        is_long = _direction(trade, kwargs, fill, sl, tp)
        side = "LONG" if is_long else "SHORT"
        sym_tag = _symbol_tag(symbol, tag)

        risk_pts = abs(fill - sl) if fill > 0 and sl > 0 else 0.0
        reward_pts = abs(tp - fill) if fill > 0 and tp > 0 else 0.0
        rr = reward_pts / risk_pts if risk_pts > 0 else 0.0

        emoji = "🟢" if is_long else "🔴"
        lines = [
            f"{emoji} <b>[{sym_tag}] ENTRY — {side}</b> | {lots} lots",
            f"<code>{self._now_ist()}</code>",
            "",
            f"<b>Fill</b>   : ${fill:,.2f}",
            f"<b>SL</b>     : ${sl:,.2f}  | Risk {risk_pts:.2f} pts",
            f"<b>TP</b>     : ${tp:,.2f}  | Reward {reward_pts:.2f} pts",
            f"<b>ATR</b>    : {atr:.2f}  |  Actual R:R {rr:.2f}",
        ]
        return await self.send("\n".join(lines))

    async def notify_exit(self, *args, **kwargs):
        trade = args[0] if len(args) == 1 and (isinstance(args[0], dict) or hasattr(args[0], "__dict__")) else {}

        symbol = str(_extract(trade, kwargs, ["symbol", "ticker"], ""))
        tag = str(_extract(trade, kwargs, ["tag"], ""))
        entry = float(_extract(trade, kwargs, ["entry", "entry_price", "fill", "fill_price"], 0.0) or 0.0)
        exit_p = float(_extract(trade, kwargs, ["exit", "exit_price", "close_price"], 0.0) or 0.0)
        lots = int(round(float(_extract(trade, kwargs, ["lots", "lot_size", "qty", "quantity"], 0) or 0)))
        contract_value = float(_extract(trade, kwargs, ["contract_value", "contract_size"], 0.001) or 0.001)
        reason = str(_extract(trade, kwargs, ["reason", "notes", "exit_reason"], "Closed"))

        is_long = _direction(trade, kwargs)
        side = "LONG" if is_long else "SHORT"
        sym_tag = _symbol_tag(symbol, tag)

        points = compute_points(entry, exit_p, is_long)
        gross_supplied = _extract(trade, kwargs, ["gross_pnl", "gross", "realised_pnl", "pnl", "real_pl"], None)
        gross = float(gross_supplied) if gross_supplied is not None else points * lots * contract_value

        entry_notional = abs(entry * lots * contract_value)
        exit_notional = abs(exit_p * lots * contract_value)
        estimated_fees = round((entry_notional + exit_notional) * float(COMMISSION_PCT), 6)
        estimated_net = gross - estimated_fees

        emoji = "💰" if gross > 0 else ("🔻" if gross < 0 else "⚪")
        lines = [
            f"{emoji} <b>[{sym_tag}] EXIT — {side}</b> | {lots} lots",
            f"<code>{self._now_ist()}</code>",
            "",
            f"<b>Entry</b>       : ${entry:,.2f}",
            f"<b>Exit</b>        : ${exit_p:,.2f}",
            f"<b>Points</b>      : {points:+.2f}",
            f"<b>Gross P&L</b>   : {_money(gross)} USD",
            f"<b>Est. fees</b>   : ${estimated_fees:,.4f} USD",
            f"<b>Est. net P&L</b>: {_money(estimated_net, 2)} USD",
            f"<b>Reason</b>      : {reason}",
        ]

        res = await self.send("\n".join(lines))

        try:
            usd_inr = float(os.getenv("USDINR_RATE", "84.0"))
            trade_payload = {
                "trade_id": f"TRD-{datetime.now(IST).strftime('%m%d-%H%M%S')}",
                "timestamp": self._now_ist(),
                "symbol": symbol or sym_tag,
                "engine": str(_extract(trade, kwargs, ["signal_type", "signal"], "")),
                "side": side,
                "entry_price": entry,
                "exit_price": exit_p,
                "points_captured": points,
                "lots": lots,
                # Legacy webhook key kept for compatibility.  For PAXG this
                # is base-asset quantity, despite the historical key name.
                "btc_size": lots * contract_value,
                "contract_value": contract_value,
                "gross_pnl": gross,
                "fees": estimated_fees,
                "estimated_fees": estimated_fees,
                "net_pnl": estimated_net,
                "estimated_net_pnl": estimated_net,
                "net_inr": estimated_net * usd_inr,
                "status": "CLOSED",
                "notes": reason,
            }
            sync_completed_trade_to_sheet(trade_payload)
        except Exception as exc:
            logger.warning("GSheet live sync warning: %s", exc)

        return res
