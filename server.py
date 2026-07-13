"""
server.py — Shiva Sniper v10  Dashboard HTTP Server (Multi-Symbol Support)
═══════════════════════════════════════════════════════════════════════════════

Serves dashboard.html (Gold), dashboard_btc.html (BTC), and all /api/* endpoints
with support for a `?runner=` query parameter (defaults to "paxg").

Endpoints
─────────
  GET /                        → dashboard.html (Gold)
  GET /btc                     → dashboard_btc.html (BTC)
  GET /api/status              → {"status": "live"} when bot is running
  GET /api/summary?runner=x    → Journal.get_summary() for runner x
  GET /api/trades?runner=x     → Journal.get_trades() for runner x
  GET /api/position?runner=x   → Journal.get_open_trade() for runner x
  GET /api/candles?runner=x    → Binance OHLCV candles for runner x
  GET /api/live_state?runner=x → Ephemeral live state for runner x
"""
from __future__ import annotations

import base64
import errno
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Optional
from urllib.parse import parse_qs, urlparse

import ccxt

if TYPE_CHECKING:
    from infra.journal import Journal

from config import PAPER_TRADING, PAPER_TRADING_BALANCE, SYMBOLS, DASHBOARD_PORT, get_vps_ip

logger = logging.getLogger(__name__)

PORT          = DASHBOARD_PORT
HOST          = "0.0.0.0"
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

_BIND_RETRY_SECONDS = float(os.environ.get("DASHBOARD_BIND_RETRY", "20"))

# ── Basic Auth credentials ────────────────────────────────────────────────────
DASH_USER = os.environ.get("DASHBOARD_USER", "shiva")
DASH_PASS = os.environ.get("DASHBOARD_PASS", "sniper123")
_AUTH_TOKEN = base64.b64encode(f"{DASH_USER}:{DASH_PASS}".encode()).decode()

# ── Shared state (set by main.py before server starts) ────────────────────────
_journals: dict[str, Journal] = {}
_bot_live: bool               = False
_httpd: "ThreadingHTTPServer | None"   = None   # kept so stop() can shut it down cleanly

# ── Candle cache per runner ───────────────────────────────────────────────────
_candle_caches: dict[str, dict] = {}
_CANDLE_CACHE_TTL: float        = 300.0   # 5 minutes

# ── Live engine state per runner ──────────────────────────────────────────────
_live_states: dict[str, dict] = {
    "paxg": {
        "symbol": "", "timeframe": "", "base_asset_label": "",
        "position_size_mode": "static",
        "close": 0.0, "atr": 0.0, "adx": 0.0, "rsi": 0.0,
        "ema_trend": 0.0, "ema_fast": 0.0,
        "ema_trend_len": 60, "ema_fast_len": 20, "adx_len": 14, "rsi_len": 14,
        "trend_regime": False, "range_regime": False,
        "atr_ok": False, "vol_ok": False, "body_ok": False, "filters_ok": False,
        "contract_value": 0.0, "qty_lots": 0,
        "equity_usd": None, "risk_pct": 0.0, "last_stop_dist": 0.0,
        "last_bar_ts": 0,
    },
    "btc": {
        "symbol": "", "timeframe": "", "base_asset_label": "",
        "position_size_mode": "static",
        "close": 0.0, "atr": 0.0, "adx": 0.0, "rsi": 0.0,
        "ema_trend": 0.0, "ema_fast": 0.0,
        "ema_trend_len": 60, "ema_fast_len": 50, "adx_len": 14, "rsi_len": 14,
        "trend_regime": False, "range_regime": False,
        "atr_ok": False, "vol_ok": False, "body_ok": False, "filters_ok": False,
        "contract_value": 0.0, "qty_lots": 0,
        "equity_usd": None, "risk_pct": 0.0, "last_stop_dist": 0.0,
        "last_bar_ts": 0,
    }
}


def get_symbol_config(runner_id: str) -> dict:
    for cfg in SYMBOLS:
        if cfg["id"] == runner_id:
            return cfg
    return SYMBOLS[0]


def update_live_state(runner_id: str = "paxg", **kwargs) -> None:
    """Merge fields into the live-state snapshot. Call from SymbolRunner."""
    if runner_id not in _live_states:
        _live_states[runner_id] = {}
    _live_states[runner_id].update(kwargs)


def init(journals: dict[str, Journal] | Journal) -> None:
    """Call from main.py after Journal is created, before start()."""
    global _journals, _bot_live
    if isinstance(journals, dict):
        _journals = journals
    else:
        _journals = {"paxg": journals}
    _bot_live = True


def set_live(live: bool) -> None:
    global _bot_live
    _bot_live = live


# ── Binance candle fetch ───────────────────────────────────────────────────────

def _fetch_candles_binance(runner_id: str = "paxg", limit: int = 200) -> list:
    """
    Fetch candles from Binance REST for the specified runner.
    """
    global _candle_caches

    now = time.monotonic()
    cache = _candle_caches.get(runner_id)
    if cache and cache.get("candles") and (now - cache.get("ts", 0.0)) < _CANDLE_CACHE_TTL:
        return cache["candles"][-limit:]

def resample_ohlcv_15m_to_45m(ohlcv: list) -> list:
    if not ohlcv:
        return []
    groups = {}
    for item in ohlcv:
        if not item or len(item) < 6:
            continue
        ts = int(item[0])
        boundary = (ts // 2700000) * 2700000
        if boundary not in groups:
            groups[boundary] = []
        groups[boundary].append(item)
    
    resampled = []
    for boundary in sorted(groups.keys()):
        items = groups[boundary]
        items.sort(key=lambda x: x[0])
        open_val   = float(items[0][1])
        high_val   = max(float(x[2]) for x in items)
        low_val    = min(float(x[3]) for x in items)
        close_val  = float(items[-1][4])
        volume_val = sum(float(x[5]) for x in items)
        resampled.append([boundary, open_val, high_val, low_val, close_val, volume_val])
    return resampled

def get_candles(runner_id: str, limit: int = 150) -> list:
    now = time.monotonic()
    cache = _candle_caches.get(runner_id)
    if cache and cache.get("candles") and (now - cache.get("ts", 0.0)) < _CANDLE_CACHE_TTL:
        return cache["candles"][-limit:]

    cfg = get_symbol_config(runner_id)
    binance_symbol = cfg.get("binance_symbol", "BTC/USDT")
    timeframe = cfg.get("timeframe", "1m")

    # Map timeframes if needed (Binance REST supports 1m, 5m, 15m, 30m, 1h, etc.)
    binance_tf = "15m" if timeframe == "45m" else timeframe
    fetch_limit = limit * 3 + 10 if timeframe == "45m" else limit

    try:
        ex = ccxt.binance({"enableRateLimit": True})
        ohlcv = ex.fetch_ohlcv(binance_symbol, binance_tf, limit=fetch_limit)
        if timeframe == "45m":
            ohlcv = resample_ohlcv_15m_to_45m(ohlcv)
        candles = [
            {
                "time":  bar[0] // 1000,   # ms → Unix seconds for LWC
                "open":  bar[1],
                "high":  bar[2],
                "low":   bar[3],
                "close": bar[4],
            }
            for bar in ohlcv
        ]
        _candle_caches[runner_id] = {
            "candles": candles,
            "ts": now
        }
        logger.debug(f"[SERVER] Candles refreshed for {runner_id} ({binance_symbol} {binance_tf}) — {len(candles)} bars")
        return candles[-limit:]
    except Exception as e:
        logger.warning(f"[SERVER] Binance candle fetch failed for {runner_id}: {e}")
        return cache["candles"][-limit:] if cache and cache.get("candles") else []


# ── HTTP handler ───────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Silence default access log spam
        pass

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type",  "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, mime: str) -> None:
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type",   mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, "File not found")

    def do_GET(self):
        global _live_states
        logger.info(f"[SERVER] Request received: {self.path}")
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        # ── Static dashboard routes ───────────────────────────────────────────
        if path in ("/", "/dashboard", "/dashboard.html"):
            self._send_file(
                os.path.join(DASHBOARD_DIR, "dashboard.html"),
                "text/html; charset=utf-8",
            )
            return
        elif path in ("/btc", "/btc.html", "/dashboard_btc.html"):
            self._send_file(
                os.path.join(DASHBOARD_DIR, "dashboard.html"),
                "text/html; charset=utf-8",
            )
            return

        # ── Resolve runner ────────────────────────────────────────────────────
        runner_id = params.get("runner", ["paxg"])[0].lower()
        if runner_id not in _journals:
            runner_id = list(_journals.keys())[0] if _journals else "paxg"

        j = _journals.get(runner_id)
        state = _live_states.get(runner_id, {})

        # ── API routes ────────────────────────────────────────────────────────
        if path == "/api/status":
            self._send_json({
                "status": "live" if _bot_live else "offline",
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S IST", time.gmtime(time.time() + 19800)),
            })

        elif path == "/api/clear_history":
            try:
                if j:
                    j.clear_history()
                    # Reset the global cached state for this runner
                    if runner_id in _live_states:
                        _live_states[runner_id] = {}
                    self._send_json({"status": "success", "message": f"History cleared for {runner_id}"})
                else:
                    self._send_json({"error": "runner not found"}, 400)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/summary":
            data = j.get_summary() if j else {}

            try:
                today = j.get_daily_summary() if j else {}
                if isinstance(today, dict):
                    data = {**data, "today": today}
            except Exception as e:
                logger.debug(f"[SERVER] get_daily_summary failed: {e}")
            self._send_json(data)

        elif path == "/api/trades":
            limit = int(params.get("limit", ["200"])[0])
            data  = j.get_trades(limit=limit) if j else []
            self._send_json(data)

        elif path == "/api/position":
            data = j.get_open_trade() if j else None
            self._send_json(data or {})

        elif path == "/api/candles":
            limit   = int(params.get("limit", ["200"])[0])
            candles = _fetch_candles_binance(runner_id, limit)
            self._send_json(candles)

        elif path == "/api/live_state":
            cfg = get_symbol_config(runner_id)
            default_bal = cfg.get("paper_balance", 10000.0)
            base_cap = PAPER_TRADING_BALANCE if PAPER_TRADING else (state.get("equity_usd") or default_bal)
            self._send_json({
                **state,
                "base_capital": base_cap,
                "paper_trading": PAPER_TRADING,
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S IST", time.gmtime(time.time() + 19800)),
            })

        else:
            self._send_json({"error": "not found"}, 404)


# ── Bind helper ────────────────────────────────────────────────────────────────

def _bind_with_retry() -> ThreadingHTTPServer:
    deadline = time.monotonic() + _BIND_RETRY_SECONDS
    attempt  = 0
    while True:
        attempt += 1
        try:
            return ThreadingHTTPServer((HOST, PORT), _Handler)
        except OSError as e:
            if e.errno != errno.EADDRINUSE or time.monotonic() >= deadline:
                raise
            logger.warning(
                f"[SERVER] Port {PORT} busy (attempt {attempt}) — an old "
                f"instance is probably still shutting down. Retrying in 2s..."
            )
            time.sleep(2)


# ── Public start / stop functions ──────────────────────────────────────────────

def start() -> None:
    global _httpd
    if DASH_PASS == "sniper123" and HOST not in ("127.0.0.1", "localhost"):
        logger.warning(
            "[SERVER] ⚠ Dashboard is using the DEFAULT password on a public "
            "bind. Set DASHBOARD_PASS in your .env."
        )

    _httpd = _bind_with_retry()
    _httpd.daemon_threads = True
    t = threading.Thread(target=_httpd.serve_forever, daemon=True, name="dashboard-server")
    t.start()
    logger.info(f"Dashboard LIVE → http://{get_vps_ip()}:{PORT} (bind: {HOST})")


def stop() -> None:
    global _httpd
    if _httpd is None:
        return
    try:
        _httpd.shutdown()
        _httpd.server_close()
        logger.info("[SERVER] Dashboard stopped, port released.")
    except Exception as e:
        logger.warning(f"[SERVER] Error during dashboard stop: {e}")
    finally:
        _httpd = None
