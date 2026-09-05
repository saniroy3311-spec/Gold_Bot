"""
orders/manager.py — Shiva Sniper Bot-v10  |  EMERGENCY-BRACKET ARCHITECTURE
══════════════════════════════════════════════════════════════════════════════
ARCHITECTURE (FIX-BRACKET-CHURN):
─────────────────────────────────────────────────────────────────────────────
Previous design pushed every trail SL tighten to Delta via PUT /v2/orders/bracket.
Delta internally replaces the order on each amendment, issuing a new order ID.
The bot's cached _bracket_order_id became stale on every update, triggering a
continuous open_order_not_found → rediscovery loop (~3 API calls per tick for
the entire duration of the trade).
New design:
• Bracket is placed ONCE at entry with the INITIAL SL only (wide safety net).
• Bracket is NEVER amended after placement.
• Python (TrailMonitor) owns all trail/BE/tighten logic and fires exits via
market close_position() on tick.
• The bracket's only job is crash/disconnect protection — if the bot dies,
Delta's bracket catches the worst-case initial SL. No stale IDs, no
amendment API calls, no rediscovery loops.
API surface used:
POST  /v2/orders/bracket   place emergency SL bracket after entry fill
DELETE /v2/orders/bracket  cancel bracket when Python fires a clean exit
Delta Exchange endpoints
─────────────────────────────────────────────────────────────────────────────
Live:    https://api.india.delta.exchange
Testnet: https://testnet-api.india.delta.exchange
Toggle:  DELTA_TESTNET=true in .env
══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import logging
import ssl
import time
import socket
from typing import Any, Optional
import aiohttp
import ccxt.async_support as ccxt
from config import (
    DELTA_API_KEY, DELTA_API_SECRET, DELTA_TESTNET, PAPER_TRADING, PAPER_TRADING_BALANCE,
    SYMBOL, ALERT_QTY,
    BRACKET_SL_WIDEN_MULT, BRACKET_SL_MIN_PTS, MAX_SL_POINTS,
    MAX_EXIT_SLIPPAGE_ATR_PCT,
    CONTRACT_VALUE_OVERRIDE,  # GOLDBOT — instrument-generic contract value
)
from risk.lot_sizing import BTC_PER_LOT

logger = logging.getLogger("orders.manager")

_INDIA_LIVE    = "https://api.india.delta.exchange"
_INDIA_TESTNET = "https://testnet-api.india.delta.exchange"

# Phrases in ccxt / Delta error messages that mean "position is already gone"
_ALREADY_CLOSED_PHRASES = (
    "no_position_for_reduce_only",
    "no open position",
    "position not found",
    "insufficient position",
)

# Phrases that mean "bracket is already gone" (already triggered or removed)
_BRACKET_GONE_PHRASES = (
    "bracket_not_found",
    "no_bracket",
    "no bracket order",
    "bracket order not found",
    "no_open_bracket_order_for_position",
)


class PositionQueryError(RuntimeError):
    """Raised when live position state cannot be verified.

    ``None`` from fetch_open_position means VERIFIED FLAT.  API/auth/network
    failures raise this exception so callers can never confuse UNKNOWN with FLAT.
    """


# ─── Exchange factory ──────────────────────────────────────────────────────────
def build_exchange() -> ccxt.delta:
    """
    Build a ccxt.delta async instance pointed at Delta India.
    Pre-injects an IPv4-only session to bypass dual-stack IPv6 errors.
    """
    base_url = _INDIA_TESTNET if DELTA_TESTNET else _INDIA_LIVE
    ex = ccxt.delta({
        "apiKey":          DELTA_API_KEY,
        "secret":          DELTA_API_SECRET,
        "enableRateLimit": True,
        "urls": {
            "api": {
                "public":  base_url,
                "private": base_url,
            }
        },
    })
    _ssl_ctx   = ssl.create_default_context()
    _connector = aiohttp.TCPConnector(
        family=socket.AF_INET,
        ssl=_ssl_ctx,
        enable_cleanup_closed=True,
    )
    ex.session    = aiohttp.ClientSession(connector=_connector)
    ex.own_session = False   
    return ex

# ─── Retry helper ─────────────────────────────────────────────────────────────
async def _retry(coro_fn, retries: int = 3, delay: float = 1.0):
    """Retry a coroutine-producing callable on network / timeout errors."""
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn()
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            if attempt == retries:
                raise
            wait = delay * (2 ** (attempt - 1))
            logger.warning(
                f"[OM] Retry {attempt}/{retries} after {wait:.1f}s — {exc}"
            )
            await asyncio.sleep(wait)

# ─── Delta India signed REST helper (for bracket endpoints) ──────────────────
def _sign(method: str, ts: str, path: str, body: str) -> str:
    msg = (method + ts + path + body).encode()
    return hmac.new(DELTA_API_SECRET.encode(), msg, hashlib.sha256).hexdigest()

async def _signed_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    body_obj: Optional[dict] = None,
) -> dict:
    """Make a signed HTTP request to Delta India for endpoints not in ccxt."""
    base   = _INDIA_TESTNET if DELTA_TESTNET else _INDIA_LIVE
    url    = base + path
    body   = json.dumps(body_obj) if body_obj is not None else ""
    ts     = str(int(time.time()))
    sig    = _sign(method, ts, path, body)
    headers = {
        "api-key":      DELTA_API_KEY,
        "signature":    sig,
        "timestamp":    ts,
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "User-Agent":   "shiva-sniper-bot-v10",
    }
    async with session.request(method, url, data=body, headers=headers, timeout=10) as resp:
        text = await resp.text()
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {"_raw": text}
        if resp.status >= 400:
            raise ccxt.ExchangeError(
                f"Delta {method} {path} returned {resp.status}: {text}"
            )
        return data


async def _rest_market_order(
    manager: "OrderManager",
    side: str,
    size: int,
    *,
    reduce_only: bool = False,
) -> dict:
    """Place a Delta market order through the documented signed REST API.

    This bypasses ccxt private signing, which has produced Signature Mismatch
    with the pinned ccxt version on Delta India.  Public ccxt market/ticker
    functions are still used elsewhere.
    """
    if manager._product_id is None:
        raise RuntimeError("Cannot place live order: product_id is unresolved")

    body = {
        "product_id": int(manager._product_id),
        "size": int(size),
        "side": str(side).lower(),
        "order_type": "market_order",
        "reduce_only": bool(reduce_only),
    }
    session = await manager._http_session()
    data = await _signed_request(session, "POST", "/v2/orders", body)
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        raise ccxt.ExchangeError(f"Delta POST /v2/orders returned unexpected payload: {data}")

    order_id = result.get("id")
    # Market orders usually return average_fill_price immediately, but poll the
    # order briefly when it is not yet populated so the journal never invents
    # a live execution price.
    for attempt in range(6):
        avg = result.get("average_fill_price") or result.get("average") or result.get("price")
        try:
            fill = float(avg or 0.0)
        except (TypeError, ValueError):
            fill = 0.0
        if fill > 0:
            break
        if not order_id:
            break
        await asyncio.sleep(0.20 * (attempt + 1))
        latest = await _signed_request(session, "GET", f"/v2/orders/{order_id}")
        latest_result = latest.get("result") if isinstance(latest, dict) else None
        if isinstance(latest_result, dict):
            result = latest_result
    else:
        fill = 0.0

    avg = result.get("average_fill_price") or result.get("average") or result.get("price")
    try:
        fill = float(avg or 0.0)
    except (TypeError, ValueError):
        fill = 0.0
    if fill <= 0:
        raise RuntimeError(f"Live market order {order_id!r} has no confirmed average_fill_price")

    requested = int(size)
    try:
        unfilled = int(float(result.get("unfilled_size") or 0))
    except (TypeError, ValueError):
        unfilled = 0
    filled = max(0, requested - unfilled) or requested
    return {
        "id": order_id,
        "average": fill,
        "price": fill,
        "filled": filled,
        "amount": requested,
        "side": str(side).lower(),
        "symbol": manager._symbol,
        "info": result,
    }

# ─── OrderManager ─────────────────────────────────────────────────────────────
class OrderManager:
    """Async Delta Exchange order manager with Phase-2 bracket-order support."""
    def __init__(self, symbol: str = None) -> None:
        self.exchange: ccxt.delta = build_exchange()
        self._symbol = symbol or SYMBOL

        # PHASE-2 state — set on entry fill, cleared on exit.
        self._product_id:    Optional[int]   = None  
        self._product_symbol: Optional[str]  = None  
        self._bracket_active:        bool    = False
        self._current_sl:    Optional[float] = None
        self._current_tp:    Optional[float] = None 
        self._is_long:       Optional[bool]  = None  
        self._entry_price:   Optional[float] = None
        self._current_atr:   float           = 1.0  # For slippage calculation

        # GOLDBOT — resolved at initialize() from exchange market metadata.
        # Base-asset units per 1 lot for the active SYMBOL (0.001 for BTCUSD,
        # a different value for PAXGUSD/gold etc). Source of truth for all
        # qty and P&L math — never hardcode this elsewhere.
        self.contract_value: float = BTC_PER_LOT
        # Qty actually filled at entry, cached so close_position() always
        # closes the EXACT size that was opened (was previously read from
        # the static ALERT_QTY constant, which could silently diverge from
        # a dynamically-sized entry).
        self._current_qty: Optional[int] = None

        # Reusable HTTP session for the signed-bracket endpoints.
        self._http: Optional[aiohttp.ClientSession] = None
        
        # Strong references to prevent background tasks from being garbage collected
        self._background_tasks: set[asyncio.Task] = set()

    # ── Lifecycle ──────────────────────────────────────────────────────────
    async def initialize(self) -> None:
        """Load markets and validate the configured symbol exists."""
        await self.exchange.load_markets()
        if self._symbol not in self.exchange.markets:
            raise ValueError(f"SYMBOL '{self._symbol}' not found on Delta India.")

        market = self.exchange.markets[self._symbol]
        info   = market.get("info") or {}
        pid    = info.get("id") or info.get("product_id") or market.get("id")
        
        try:
            self._product_id = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            self._product_id = None
        self._product_symbol = info.get("symbol") or "BTCUSD"

        # ── Resolve contract_value (base-asset units per 1 lot) ──────────────
        # GOLDBOT: this is what makes the bot instrument-agnostic. Priority:
        #   1. CONTRACT_VALUE_OVERRIDE (.env) — explicit force, use for testing
        #      or if the exchange API is unreachable at boot.
        #   2. ccxt's unified market["contractSize"] field.
        #   3. Delta's raw info["contract_value"] field (same data, ccxt's
        #      normalization sometimes drops it — check both to be safe).
        #   4. BTC_PER_LOT (0.001) — legacy BTCUSD fallback, logged loudly
        #      since silently assuming the wrong multiplier on a new
        #      instrument (e.g. gold) would size positions wrong.
        if CONTRACT_VALUE_OVERRIDE and CONTRACT_VALUE_OVERRIDE > 0:
            self.contract_value = CONTRACT_VALUE_OVERRIDE
            logger.info(f"[OM] contract_value FORCED via .env override = {self.contract_value}")
        else:
            resolved = market.get("contractSize")
            if not resolved or float(resolved) <= 0:
                resolved = info.get("contract_value")
            try:
                resolved = float(resolved) if resolved is not None else None
            except (TypeError, ValueError):
                resolved = None
            if resolved and resolved > 0:
                self.contract_value = resolved
                logger.info(
                    f"[OM] contract_value resolved from exchange for {self._symbol} = {self.contract_value}"
                )
            else:
                self.contract_value = BTC_PER_LOT
                logger.warning(
                    f"[OM] ⚠️ Could not resolve contract_value for {self._symbol} from exchange "
                    f"market metadata — falling back to legacy BTCUSD default "
                    f"({BTC_PER_LOT}). VERIFY THIS IS CORRECT before trading a "
                    f"non-BTC instrument — wrong contract_value = wrong position size."
                )

        if self._product_id is None:
            logger.warning(
                f"[OM] Could not resolve numeric product_id for {self._symbol};  "
                f"bracket orders will be DISABLED for this run."
            )
        else:
            logger.info(
                f"[OM] Resolved product_id={self._product_id}  "
                f"product_symbol={self._product_symbol}"
            )

    async def close_exchange(self) -> None:
        """Close the ccxt session and the bracket-endpoint HTTP session."""
        try:
            await self.exchange.close()
        except Exception as exc:
            logger.warning(f"[OM] close_exchange error (ignored): {exc}")
        if self._http is not None:
            try:
                await self._http.close()
            except Exception as exc:
                logger.warning(f"[OM] http session close error (ignored): {exc}")
            self._http = None

    async def _http_session(self) -> aiohttp.ClientSession:
        """Lazily create the aiohttp session for bracket endpoints."""
        if self._http is None or self._http.closed:
            _connector = aiohttp.TCPConnector(family=socket.AF_INET)
            self._http = aiohttp.ClientSession(connector=_connector)
        return self._http

    # ── Position query ────────────────────────────────────────────────────────
    async def fetch_open_position(self) -> Optional[dict]:
        """Return verified live position, or ``None`` only when verified flat.

        Paper mode uses only local simulated state.  Live mode uses Delta's
        documented real-time ``GET /v2/positions?product_id=...`` endpoint.
        Any API/auth/network failure raises PositionQueryError instead of being
        silently converted into a false FLAT state.
        """
        if PAPER_TRADING:
            if self._is_long is not None and self._entry_price:
                return {
                    "is_long": self._is_long,
                    "entry_price": float(self._entry_price),
                    "contracts": float(self._current_qty or ALERT_QTY),
                }
            return None

        if not DELTA_API_KEY or len(DELTA_API_KEY) < 20:
            raise PositionQueryError("Delta API key missing/invalid length in LIVE mode")
        if self._product_id is None:
            raise PositionQueryError("Cannot verify position: product_id unresolved")

        try:
            session = await self._http_session()
            data = await _signed_request(
                session, "GET", f"/v2/positions?product_id={int(self._product_id)}"
            )
            if not isinstance(data, dict) or data.get("success") is not True:
                raise PositionQueryError(f"Unexpected Delta position payload: {data}")
            result = data.get("result")
            if result is None:
                return None
            if isinstance(result, list):
                result = result[0] if result else None
            if not isinstance(result, dict):
                return None

            size_raw = result.get("size", result.get("contracts", 0))
            size = float(size_raw or 0)
            if abs(size) <= 0:
                return None
            entry_raw = result.get("entry_price") or result.get("entryPrice") or 0.0
            side = str(result.get("side") or "").lower()
            is_long = (side == "long") if side in {"long", "short"} else size > 0
            return {
                "is_long": is_long,
                "entry_price": float(entry_raw or 0.0),
                "contracts": abs(size),
            }
        except PositionQueryError:
            raise
        except Exception as exc:
            logger.error(f"[OM] fetch_open_position UNKNOWN/API ERROR: {exc}")
            raise PositionQueryError(str(exc)) from exc

    async def fetch_position(self) -> Optional[dict]:
        """Backward-compatibility wrapper for legacy layout logic execution passes."""
        return await self.fetch_open_position()

    # ── Order placement ───────────────────────────────────────────────────────
    async def place_entry(
        self,
        is_long: bool,
        sl: float,
        tp: float,
        atr: float = 1.0,
        qty: Optional[int] = None,
    ) -> dict:
        """
        Place market entry order, then attach an WIDE EMERGENCY bracket SL.
        Python trail loop retains complete operational ownership of exits.

        qty: explicit lot quantity for this entry. GOLDBOT — this is what
        makes dynamic risk-based sizing possible: the caller (main.py)
        computes qty fresh at signal time from live equity via
        risk.lot_sizing.calc_qty_from_risk() and passes it here. Defaults
        to the static ALERT_QTY constant for backward compatibility if
        omitted.
        """
        order_qty = qty if qty and qty > 0 else ALERT_QTY
        side = "buy" if is_long else "sell"
        logger.info(
            f"[OM] Placing entry | side={side}  qty={order_qty}   "
            f"sl={sl:.2f}  tp={tp:.2f}"
        )

        is_paper = PAPER_TRADING or not DELTA_API_KEY or len(DELTA_API_KEY) < 20
        if is_paper:
            # Paper trading / simulation mode: calculate simulated fill at market price
            ticker = await self.fetch_ticker()
            fill = float(ticker.get("last") or 0.0) if (ticker and isinstance(ticker, dict)) else float((sl + tp) / 2.0)
            logger.info(
                f"[OM] 📄 [PAPER TRADING] Simulated entry fill | side={side}  qty={order_qty}  fill={fill:.2f}"
            )
            self._is_long        = is_long
            self._entry_price    = fill
            self._current_sl     = float(sl)
            self._current_tp     = float(tp)
            self._bracket_active = True
            self._current_atr    = atr if atr > 0 else 1.0
            self._current_qty    = order_qty
            return {
                "id":        f"paper_{int(time.time()*1000)}",
                "average":   fill,
                "price":     fill,
                "filled":    order_qty,
                "amount":    order_qty,
                "side":      side,
                "symbol":    self._symbol,
            }

        # ── 1. Market entry — direct signed Delta REST (no ccxt private signer) ──
        order = await _rest_market_order(self, side, int(order_qty), reduce_only=False)
        fill = float(order.get("average") or order.get("price") or 0.0)
        self._entry_price = fill
        logger.info(f"[OM] Entry filled | id={order.get('id')}  fill={fill:.2f}")

        # ── 2. Cache state ──
        self._is_long          = is_long
        self._current_sl       = float(sl)
        self._current_tp       = float(tp)
        self._bracket_active   = False 
        self._current_atr      = atr if atr > 0 else 1.0
        # Cache the qty actually used so close_position() closes the exact
        # same size, even if the exchange fill differs slightly or ALERT_QTY
        # changes mid-trade.
        _filled_amt = float(order.get("filled") or order.get("amount") or 0) or order_qty
        self._current_qty      = int(round(_filled_amt)) if _filled_amt > 0 else order_qty

        # ── 3. Emergency bracket SL (Widen by 300 points to absorb normal wick noise) ──
        if self._product_id is None:
            logger.warning("[OM] Emergency bracket disabled (no product_id).")
            return order

        try:
            # Dynamic emergency bracket: clamp(sl_dist * WIDEN_MULT, MIN_PTS, MAX_SL_POINTS)
            # Uses config.py values so it scales with volatility and is tunable via .env
            sl_dist = abs(sl - fill) if fill > 0 else BRACKET_SL_MIN_PTS
            bracket_dist = max(sl_dist * BRACKET_SL_WIDEN_MULT, BRACKET_SL_MIN_PTS)
            bracket_dist = min(bracket_dist, MAX_SL_POINTS)
            emergency_sl = (fill - bracket_dist) if is_long else (fill + bracket_dist)
            
            await self._place_bracket(sl=emergency_sl)
            self._bracket_active = True
            logger.info(
                f"[OM] ✅ Emergency bracket SL placed on Delta |  "
                f"sl={emergency_sl:.2f}  (fill={fill:.2f}  pine_sl={sl:.2f}   "
                f"sl_dist={sl_dist:.1f}  bracket_dist={bracket_dist:.1f}   "
                f"widen={BRACKET_SL_WIDEN_MULT}x  min={BRACKET_SL_MIN_PTS}  max={MAX_SL_POINTS})"
            )
        except Exception as exc:
            logger.error(
                f"[OM] ⚠️  Emergency bracket FAILED — trade is open with no  "
                f"exchange-side safety net. TrailMonitor is sole protection. Error: {exc}"
            )

        return order

    # ── Bracket management ─────────────────────────────────────────────────────
    async def _place_bracket(self, sl: float) -> dict:
        """POST /v2/orders/bracket — emergency SL only, no TP."""
        body = {
            "product_id":     self._product_id,
            "product_symbol": self._product_symbol,
            "stop_loss_order": {
                "order_type": "market_order",
                "stop_price": str(round(sl, 2)),
            },
            "bracket_stop_trigger_method": "last_traded_price",
        }
        session = await self._http_session()
        return await _signed_request(session, "POST", "/v2/orders/bracket", body)

    async def cancel_bracket(self) -> None:
        """DELETE /v2/orders/bracket — remove the safety net from Delta execution layer."""
        if not self._bracket_active or self._product_id is None:
            self._bracket_active = False
            return
        body = {
            "product_id":     self._product_id,
            "product_symbol": self._product_symbol,
        }
        session = await self._http_session()
        try:
            await _signed_request(session, "DELETE", "/v2/orders/bracket", body)
            logger.info("[OM] ✅ Bracket cancelled on Delta")
        except Exception as exc:
            msg = str(exc).lower()
            if any(p in msg for p in _BRACKET_GONE_PHRASES):
                logger.info("[OM] Bracket already cancelled — ignoring")
            else:
                logger.warning(f"[OM] cancel_bracket failed (ignored): {exc}")
        finally:
            # Bracket state is separate from position state.  Do NOT clear
            # _is_long/_entry_price/_current_qty here; close_position() still
            # needs them to close the exact live size.
            self._bracket_active   = False
            self._current_sl       = None
            self._current_tp       = None

    # ── Order management ──────────────────────────────────────────────────────
    async def cancel_all_orders(self) -> None:
        """Cancel all open limit/stop orders and drop active brackets."""
        try:
            if self._product_id is not None:
                body = {
                    "product_id":     self._product_id,
                    "product_symbol": self._product_symbol,
                    "cancel_limit_orders": True,
                    "cancel_stop_orders":  True,
                }
                session = await self._http_session()
                await _signed_request(session, "DELETE", "/v2/orders", body)
                logger.debug("[OM] cancel_all_orders: done")
            else:
                logger.debug("[OM] cancel_all_orders: no product_id yet — skipping")
        except Exception as exc:
            exc_str = str(exc)
            if "bad_schema" in exc_str and "id" in exc_str:
                logger.debug(f"[OM] cancel_all_orders: no open orders on exchange (skipped)")
            else:
                logger.warning(f"[OM] cancel_all_orders failed (ignored): {exc}")
        
        await self.cancel_bracket()

    async def close_position(
        self,
        is_long: bool,
        reason: str = "Exit",
        expected_price: Optional[float] = None,
        qty: Optional[int] = None,
    ) -> dict:
        """
        Close position with reduce-only market order and sweep up safety bracket.

        qty resolution order: explicit qty param → self._current_qty (cached
        at entry, always the real filled size) → ALERT_QTY (last-resort
        legacy fallback). This ensures a full close even if entry qty was
        dynamically sized and differs from ALERT_QTY.
        """
        resolved_qty = qty if qty and qty > 0 else (self._current_qty or ALERT_QTY)
        side = "sell" if is_long else "buy"
        logger.info(f"[OM] Closing position | side={side}  qty={resolved_qty}  reason={reason}")
        
        is_paper = PAPER_TRADING or not DELTA_API_KEY or len(DELTA_API_KEY) < 20
        if is_paper:
            ticker = await self.fetch_ticker()
            fill = float(ticker.get("last") or expected_price or 0.0) if (ticker and isinstance(ticker, dict)) else float(expected_price or 0.0)
            logger.info(
                f"[OM] 📄 [PAPER TRADING] Simulated position close | side={side}  qty={resolved_qty}  reason={reason}  fill={fill:.2f}"
            )
            self._bracket_active = False
            self._current_sl     = None
            self._current_tp     = None
            self._is_long        = None
            self._entry_price    = None
            self._current_qty    = None
            return {
                "id":      f"paper_close_{int(time.time()*1000)}",
                "average": fill,
                "price":   fill,
                "filled":  resolved_qty,
                "amount":  resolved_qty,
                "side":    side,
                "symbol":  self._symbol,
            }
        
        # FIX: Slippage check before closing
        if expected_price and self._current_atr > 0:
            try:
                ticker = await self.fetch_ticker()
                if ticker:
                    current_price = float(ticker.get("last") or ticker.get("markPrice") or 0)
                    if current_price > 0:
                        slippage_pts = abs(current_price - expected_price)
                        slippage_atr_pct = (slippage_pts / self._current_atr) * 100
                        
                        logger.info(f"[OM] Slippage check: {slippage_pts:.2f}pts ({slippage_atr_pct:.1f}% ATR)")
                        
                        if slippage_atr_pct > MAX_EXIT_SLIPPAGE_ATR_PCT:
                            logger.critical(
                                f"[OM] ⚠️ HIGH SLIPPAGE: {slippage_pts:.2f}pts ({slippage_atr_pct:.1f}% ATR) | "
                                f"Expected: {expected_price}, Current: {current_price}"
                            )
            except Exception as e:
                logger.warning(f"[OM] Slippage check failed: {e}")
        
        try:
            order = await _rest_market_order(
                self, side, int(resolved_qty), reduce_only=True
            )
            fill = float(order.get("average") or order.get("price") or 0.0)
            logger.info(f"[OM] Position closed | id={order.get('id')}  fill={fill:.2f}")
            self._is_long = None
            self._entry_price = None
            self._current_qty = None
            self._current_sl = None
            self._current_tp = None
            return order
        except ccxt.ExchangeError as exc:
            msg = str(exc).lower()
            if any(phrase in msg for phrase in _ALREADY_CLOSED_PHRASES):
                logger.info("[OM] close_position: position already gone. Returning sentinel.")
                self._is_long = None
                self._entry_price = None
                self._current_qty = None
                return {"info": "already_closed"}
            raise

    # ── Equity (for dynamic risk-based sizing) ──────────────────────────────────
    async def get_equity_usd(self) -> Optional[float]:
        """Return paper balance or verified live Delta net equity in USD."""
        if PAPER_TRADING:
            # Never touch private account APIs in simulation mode.
            return float(PAPER_TRADING_BALANCE)

        if not DELTA_API_KEY or len(DELTA_API_KEY) < 20:
            logger.error("[OM] get_equity_usd: missing API credentials in LIVE mode")
            return None

        try:
            session = await self._http_session()
            data = await _signed_request(session, "GET", "/v2/wallet/balances")
        except Exception as exc:
            logger.warning(f"[OM] get_equity_usd: signed REST failed: {exc}")
            return None

        try:
            meta = data.get("meta") or {}
            for key in ("net_equity", "robo_trading_equity"):
                value = meta.get(key)
                if value is not None and float(value) > 0:
                    return float(value)

            rows = data.get("result") or []
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                asset = str(row.get("asset_symbol") or "").upper()
                if asset in {"USD", "USDT"}:
                    for key in ("balance", "available_balance"):
                        value = row.get(key)
                        if value is not None and float(value) > 0:
                            return float(value)
        except Exception as exc:
            logger.warning(f"[OM] get_equity_usd: response parse failed: {exc}")

        logger.warning("[OM] get_equity_usd: no positive USD equity found")
        return None

    # ── Price feed / Recovery metrics ──────────────────────────────────────────
    async def fetch_ticker(self) -> Optional[dict]:
        """Fetch current asset quote mark data."""
        try:
            ticker = await _retry(lambda: self.exchange.fetch_ticker(self._symbol))
            return ticker
        except Exception as exc:
            logger.warning(f"[OM] fetch_ticker failed: {exc}")
            return None

    async def fetch_bracket_fill_price(self) -> Optional[float]:
        """Fetch exact executed price data from history if bracket triggered silently."""
        if self._product_symbol is None:
            return None

        session = await self._http_session()
        close_side = "sell" if (self._is_long is not False) else "buy"

        # Layer 1: Fill History Match
        try:
            fills_path = f"/v2/fills?product_symbol={self._product_symbol}&page_size=5"
            data = await _signed_request(session, "GET", fills_path)
            fills = (data.get("result") or [])
            if isinstance(fills, dict):
                fills = fills.get("data") or fills.get("fills") or []
            for fill in fills:
                if not isinstance(fill, dict):
                    continue
                if str(fill.get("side") or " ").lower() == close_side:
                    price_raw = fill.get("fill_price") or fill.get("price") or fill.get("average")
                    if price_raw and float(price_raw) > 0:
                        return float(price_raw)
        except Exception as exc:
            logger.warning(f"[OM] fetch_bracket_fill_price layer-1 failed: {exc}")

        # Layer 2: Order State Audit Match
        try:
            hist_path = f"/v2/history/orders?product_symbol={self._product_symbol}&states=filled&page_size=10"
            data = await _signed_request(session, "GET", hist_path)
            orders = (data.get("result") or [])
            if isinstance(orders, dict):
                orders = orders.get("data") or orders.get("orders") or []
            for order in orders:
                if not isinstance(order, dict):
                    continue
                if str(order.get("side") or " ").lower() == close_side and "fill" in str(order.get("state") or " ").lower():
                    price_raw = order.get("average_fill_price") or order.get("average") or order.get("price")
                    if price_raw and float(price_raw) > 0:
                        return float(price_raw)
        except Exception as exc:
            logger.warning(f"[OM] fetch_bracket_fill_price layer-2 failed: {exc}")

        return None
