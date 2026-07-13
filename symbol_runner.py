"""
symbol_runner.py — GoldBot Multi-Symbol Engine
══════════════════════════════════════════════════════════════════════════════

Per-symbol trading engine extracted from the original single-symbol
ShivaSniperBot class (main.py). Each SymbolRunner instance manages ONE
symbol end-to-end:

  • CandleFeed (WS primary + REST fallback)
  • BinancePriceFeed (aggTrade tick stream for exit monitoring)
  • FillsFeed (Delta fill detection)
  • TrailMonitor (5-stage trail SL, BE, wick protection)
  • OrderManager (Delta Exchange orders, bracket SL)
  • Journal (SQLite trade log — separate .db per symbol)
  • Dashboard state push

Multiple SymbolRunners run concurrently in the same asyncio event loop,
sharing the Telegram notifier and dashboard HTTP server.

Strategy logic (indicators, signal evaluation, risk calculator, trail
geometry) is fully symbol-agnostic — no changes needed there.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional, TYPE_CHECKING

from config import (
    PAPER_TRADING,
    ALERT_QTY, FILTER_VOL_ENABLED,
    POSITION_BTC_SIZE, TREND_ATR_MULT, RANGE_ATR_MULT,
    MIN_QTY_LOTS, MAX_QTY_LOTS,
    TRAIL_STAGES, PINE_MINTICK,
    EMA_TREND_LEN, EMA_FAST_LEN,
    DI_LEN, RSI_LEN,
)
from feed.ws_feed            import CandleFeed
from feed.binance_price_feed import BinancePriceFeed
from feed.fills_feed         import FillsFeed
from indicators.engine       import compute
from strategy.signal         import evaluate, SignalType
from risk.calculator         import (
    RiskLevels, TrailState,
    calc_levels, recalc_levels_from_fill, calc_real_pl, calc_gross_pl,
)
from monitor.trail_loop import TrailMonitor
from orders.manager     import OrderManager
from risk.lot_sizing    import btc_to_lots, calc_qty_from_risk

if TYPE_CHECKING:
    from infra.telegram import Telegram
    from infra.journal  import Journal

logger = logging.getLogger(__name__)

MAX_ENTRY_SLIP_ATR_FRAC = float(os.environ.get("MAX_ENTRY_SLIP_ATR_FRAC", "0.3"))


class SymbolRunner:
    """
    Independent trading engine for a single symbol.

    Args:
        sym_cfg:    dict from config.SYMBOLS with per-symbol settings
        telegram:   shared Telegram notifier (prefixes messages with [ID])
        dashboard:  reference to server module for live state updates
    """

    def __init__(
        self,
        sym_cfg: dict,
        telegram: "Telegram",
        dashboard,                 # server module
        journal: "Journal",
    ) -> None:
        # ── Per-symbol config ──────────────────────────────────────────────
        self.cfg           = sym_cfg
        self.id            = sym_cfg["id"]                         # "paxg" or "btc"
        self.symbol        = sym_cfg["symbol"]                     # "PAXG/USD:USD"
        self.binance_sym   = sym_cfg["binance_symbol"]             # "PAXG/USDT"
        self.ws_pair       = sym_cfg["binance_ws_pair"]            # "paxgusdt"
        self.base_label    = sym_cfg["base_asset_label"]           # "PAXG"
        self.timeframe     = sym_cfg["timeframe"]                  # "1m"
        self.risk_pct      = sym_cfg["risk_pct"]                   # 1.0
        self.paper_balance = sym_cfg["paper_balance"]              # 10000.0
        self.size_mode     = sym_cfg.get("position_size_mode", "risk")
        self.tag           = f"[{self.id.upper()}]"                # "[PAXG]"

        # ── Shared resources ───────────────────────────────────────────────
        self._telegram  = telegram
        self._dashboard = dashboard
        self._journal   = journal

        # ── Per-symbol components ──────────────────────────────────────────
        self._order_mgr = OrderManager(symbol=self.symbol)
        self._trail_mon = TrailMonitor(
            order_mgr = self._order_mgr,
            telegram  = self._telegram,
            journal   = self._journal,
            timeframe = self.timeframe,
            tag       = self.tag,
        )

        self._feed: Optional[CandleFeed]        = None
        self._binance_px_feed: Optional[BinancePriceFeed] = None
        self._fills_feed: Optional[FillsFeed]    = None

        # ── Per-symbol trading state ───────────────────────────────────────
        self._in_position : bool                  = False
        self._risk        : Optional[RiskLevels]  = None
        self._trail_state : Optional[TrailState]  = None
        self._signal_type : str                   = "None"
        self._entry_bar_boundary_ms : int         = 0
        self._qty_lots    : int                   = ALERT_QTY
        self._entry_lock  = asyncio.Lock()
        self._historical_sync_done = False

        self._log = logging.getLogger(f"runner.{self.id}")
        self._log.info(
            f"SymbolRunner created | symbol={self.symbol} "
            f"tf={self.timeframe} risk={self.risk_pct}% "
            f"paper_bal={self.paper_balance}"
        )

    # ══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        """Initialize exchange connection, resolve contract_value, detect
        existing positions."""
        self._log.info("═" * 60)
        self._log.info(f"  {self.tag} Initializing | {self.symbol} · {self.timeframe}")
        self._log.info(f"  Sizing: {self.size_mode} | Risk: {self.risk_pct}%")
        self._log.info("═" * 60)

        await self._order_mgr.initialize()

        # Recompute static qty with resolved contract_value
        try:
            self._qty_lots = btc_to_lots(
                POSITION_BTC_SIZE, contract_value=self._order_mgr.contract_value
            ) if POSITION_BTC_SIZE > 0 else ALERT_QTY
        except Exception as e:
            self._log.warning(f"btc_to_lots failed ({e}) — using ALERT_QTY={ALERT_QTY}")

        # Startup: cancel stale brackets if flat
        try:
            existing_check = await self._order_mgr.fetch_open_position()
            if existing_check is None:
                await self._order_mgr.cancel_all_orders()
                self._log.info(f"{self.tag} Flat on Delta — cancelled stale brackets")
        except Exception as e:
            self._log.warning(f"{self.tag} Bracket cleanup failed (non-fatal): {e}")

        # Startup recovery: adopt any pre-existing open position
        existing = await self._order_mgr.fetch_open_position()

        # Validate local DB vs exchange reality
        try:
            open_row = self._journal.get_open_trade()
            if open_row and not existing:
                self._log.info(f"{self.tag} DB ghost row but Delta is flat — purging")
                self._journal.clear_open_trade()
        except Exception as je:
            self._log.warning(f"{self.tag} Journal state check anomaly: {je}")

        if existing:
            self._log.warning(
                f"{self.tag} Open position detected — resuming trail on next bar close. "
                f"is_long={existing['is_long']} entry={existing['entry_price']:.2f}"
            )
            self._in_position = True
            self._risk = RiskLevels(
                entry_price = existing["entry_price"],
                sl=0.0, tp=0.0, stop_dist=0.0, atr=0.0,
                is_long=existing["is_long"], is_trend=True,
            )
            self._signal_type = "RECOVERED"
            await self._telegram.send(
                f"⚠️ <b>{self.tag} Position Recovery</b>\n"
                f"Bot restarted mid-trade.\n"
                f"Direction: {'LONG' if existing['is_long'] else 'SHORT'}\n"
                f"Entry (approx): {existing['entry_price']:.2f}\n"
                f"Trail management resumes on next bar close."
            )

        sizing_line = (
            f"Sizing: <code>RISK-BASED</code> ({self.risk_pct}% equity/trade)"
            if self.size_mode == "risk" else
            f"Qty: <code>{self._qty_lots} lots</code> ({POSITION_BTC_SIZE} {self.base_label})"
        )
        await self._telegram.send(
            f"🟢 <b>{self.tag} Engine Started</b>\n"
            f"Symbol: <code>{self.symbol}</code>  TF: <code>{self.timeframe}</code>\n"
            f"{sizing_line}\n"
            f"Volume filter: <code>{'ON' if FILTER_VOL_ENABLED else 'OFF'}</code>"
        )

    async def run(self) -> None:
        """Start feeds and run the trading loop. Blocks until cancelled."""
        await self.initialize()

        feed = CandleFeed(
            on_bar_close   = self._on_bar_close,
            on_feed_ready  = self._feed_ready,
            symbol         = self.symbol,
            binance_symbol = self.binance_sym,
            timeframe      = self.timeframe,
        )
        feed.trail_monitor = self._trail_mon
        self._feed = feed

        if os.environ.get("USE_BINANCE_FEED", "true").lower() == "true":
            self._binance_px_feed = BinancePriceFeed(
                self._trail_mon, ws_pair=self.ws_pair
            )
            self._binance_px_feed.start_task()

        self._fills_feed = FillsFeed(
            trail_monitor = self._trail_mon,
            order_manager = self._order_mgr,
        )
        self._fills_feed.start_task()

        try:
            await feed.start()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Stop all feeds and monitors for this runner."""
        self._log.info(f"{self.tag} Shutting down...")
        self._trail_mon.stop()
        if self._binance_px_feed is not None:
            self._binance_px_feed.stop()
        if self._fills_feed is not None:
            self._fills_feed.stop()
        try:
            await self._order_mgr.close_exchange()
        except Exception:
            pass
        self._log.info(f"{self.tag} Shutdown complete.")

    # ══════════════════════════════════════════════════════════════════════
    # Feed callbacks
    # ══════════════════════════════════════════════════════════════════════

    async def _feed_ready(self) -> None:
        self._log.info(f"{self.tag} Feed ready — waiting for first bar close...")

    async def _on_bar_close(self, df) -> None:
        # ── Drift check ───────────────────────────────────────────────────
        if self._in_position and not self._entry_lock.locked():
            try:
                actual = await self._order_mgr.fetch_open_position()
                if actual is None:
                    self._log.warning(
                        f"{self.tag} State drift: in_position=True but Delta flat. "
                        "Bracket SL/TP fired silently — recovering exit."
                    )
                    real_fill: Optional[float] = None
                    try:
                        real_fill = await self._order_mgr.fetch_bracket_fill_price()
                    except Exception as fill_err:
                        self._log.warning(f"{self.tag} fetch_bracket_fill_price failed: {fill_err}")

                    if real_fill is not None:
                        exit_price = real_fill
                    elif self._trail_state is not None:
                        exit_price = float(self._trail_state.current_sl)
                    elif self._risk is not None and self._risk.sl > 0:
                        exit_price = float(self._risk.sl)
                    else:
                        try:
                            exit_price = float(df["close"].iloc[-1])
                        except Exception:
                            exit_price = 0.0

                    if self._trail_mon._running:
                        self._trail_mon.stop()

                    try:
                        await self._on_trail_exit(
                            exit_price=exit_price,
                            reason="Bracket SL/TP (recovered)",
                            source="drift-check",
                            position_already_closed=True,
                        )
                    except Exception as exit_err:
                        self._log.error(f"{self.tag} Drift-recovery exit failed: {exit_err}", exc_info=True)
                        self._in_position = False
                        self._risk        = None
                        self._trail_state = None
                        self._signal_type = "None"
            except Exception as e:
                self._log.warning(f"{self.tag} State sanity check failed: {e}")

        # ── 1. Compute indicators ─────────────────────────────────────────
        try:
            ema_trend = self.cfg.get("ema_trend_len", EMA_TREND_LEN)
            ema_fast  = self.cfg.get("ema_fast_len", EMA_FAST_LEN)
            snap      = compute(df, ema_trend_len=ema_trend, ema_fast_len=ema_fast)
        except ValueError as e:
            self._log.warning(f"{self.tag} Not enough bars: {e}")
            return

        self._log.info(
            f"{self.tag} [BAR] close={snap.close:.2f}  atr={snap.atr:.2f}  "
            f"adx={snap.adx:.1f}  rsi={snap.rsi:.1f}  "
            f"trend={snap.trend_regime}  range={snap.range_regime}  "
            f"filters={'OK' if snap.filters_ok else 'FAIL'}  "
            f"[atr={snap.atr_ok} body={snap.body_ok} vol={snap.vol_ok}]"
        )

        # ── Push live snapshot to dashboard ───────────────────────────────
        try:
            self._dashboard.update_live_state(
                runner_id=self.id,
                symbol=self.symbol, timeframe=self.timeframe,
                base_asset_label=self.base_label,
                position_size_mode=self.size_mode,
                close=snap.close, atr=snap.atr, adx=snap.adx, rsi=snap.rsi,
                ema_trend=getattr(snap, "ema_trend", 0.0),
                ema_fast=getattr(snap, "ema_fast", 0.0),
                ema_trend_len=ema_trend,
                ema_fast_len=ema_fast,
                adx_len=DI_LEN,
                rsi_len=RSI_LEN,
                trend_regime=snap.trend_regime, range_regime=snap.range_regime,
                atr_ok=snap.atr_ok, vol_ok=snap.vol_ok, body_ok=snap.body_ok,
                filters_ok=snap.filters_ok,
                contract_value=self._order_mgr.contract_value,
                qty_lots=self._qty_lots, risk_pct=self.risk_pct,
                last_bar_ts=int(time.time()),
            )
        except Exception as _dbe:
            self._log.debug(f"{self.tag} live_state update skipped: {_dbe}")

        # ── 2. Trail update for open position ─────────────────────────────
        if self._in_position:
            if self._trail_mon._running:
                _is_entry_bar = (
                    self._entry_bar_boundary_ms > 0
                    and int(snap.timestamp) + 1 <= self._entry_bar_boundary_ms
                )
                self._trail_mon.on_bar_close(
                    bar_close=snap.close, bar_high=snap.high,
                    bar_low=snap.low, bar_open=snap.open,
                    current_atr=snap.atr, is_entry_bar=_is_entry_bar,
                )
            else:
                if self._risk is not None and self._risk.stop_dist == 0.0:
                    open_row = None
                    try:
                        open_row = self._journal.get_open_trade()
                    except Exception as _je:
                        self._log.warning(f"{self.tag} Journal read failed: {_je}")

                    if open_row and open_row.get("sl", 0) > 0 and open_row.get("atr", 0) > 0:
                        _orig_sl  = float(open_row["sl"])
                        _orig_tp  = float(open_row["tp"])
                        _orig_atr = float(open_row["atr"])
                        _atr_mult = TREND_ATR_MULT if self._risk.is_trend else RANGE_ATR_MULT

                        if self._risk.is_long:
                            _signal_close = _orig_sl + _atr_mult * _orig_atr
                        else:
                            _signal_close = _orig_sl - _atr_mult * _orig_atr

                        rebuilt = RiskLevels(
                            entry_price=self._risk.entry_price,
                            sl=_orig_sl, tp=_orig_tp,
                            stop_dist=abs(_orig_sl - self._risk.entry_price),
                            atr=_orig_atr,
                            is_long=self._risk.is_long,
                            is_trend=self._risk.is_trend,
                            signal_close=_signal_close,
                        )
                        current_sl = float(open_row.get("current_sl", open_row["sl"]))
                    else:
                        rebuilt = calc_levels(
                            entry_price=self._risk.entry_price,
                            atr=snap.atr,
                            is_long=self._risk.is_long,
                            is_trend=self._risk.is_trend,
                        )
                        rebuilt = recalc_levels_from_fill(rebuilt, self._risk.entry_price)
                        current_sl = rebuilt.sl

                    self._risk = rebuilt
                    _t1_dist = rebuilt.atr * TRAIL_STAGES[0][1] * PINE_MINTICK
                    _pine_init_sl = (
                        (rebuilt.entry_price + _t1_dist)
                        if not rebuilt.is_long
                        else (rebuilt.entry_price - _t1_dist)
                    )
                    _rec_stage = int(open_row.get("trail_stage", 0)) if open_row else 0
                    self._trail_state = TrailState(
                        stage=_rec_stage,
                        current_sl=current_sl if _rec_stage > 0 else _pine_init_sl,
                        peak_price=self._risk.entry_price,
                    )

                    original_wall_ms: Optional[int] = None
                    try:
                        if open_row and open_row.get("opened_at"):
                            from datetime import datetime, timezone as _tz
                            dt = datetime.fromisoformat(str(open_row["opened_at"]))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=_tz.utc)
                            original_wall_ms = int(dt.timestamp() * 1000)
                    except Exception:
                        pass

                    self._trail_mon.start(
                        risk_levels=rebuilt,
                        trail_state=self._trail_state,
                        entry_bar_time_ms=int(time.time() * 1000),
                        on_trail_exit=self._on_trail_exit,
                        entry_wall_ms=original_wall_ms,
                    )
                    await self._telegram.send(
                        f"♻️ <b>{self.tag} Trail Resumed (Recovery)</b>\n"
                        f"Entry: {rebuilt.entry_price:.2f}"
                    )
            return

        # ── 3. Evaluate entry signals (only when flat) ────────────────────
        sig = evaluate(snap, has_position=False)

        # Historical boot guard
        is_historical_boot = not self._historical_sync_done
        self._historical_sync_done = True

        if sig.signal_type == SignalType.NONE:
            return

        if is_historical_boot:
            self._log.info(
                f"{self.tag} [STARTUP GUARD] {sig.signal_type.value} on downloaded history — "
                f"ignoring. Bot will only enter on new live candles."
            )
            return

        self._log.info(
            f"{self.tag} [SIGNAL] {sig.signal_type.value}  "
            f"is_long={sig.is_long}  regime={sig.regime}"
        )

        # ── 4. Place entry ────────────────────────────────────────────────
        if self._entry_lock.locked():
            return

        async with self._entry_lock:
            if self._in_position:
                return

            risk_pre = calc_levels(
                snap.close, snap.atr, sig.is_long, sig.is_trend,
                entry_bar_open=snap.open, signal_close=snap.close,
            )

            # Dynamic risk-based qty
            entry_qty = self._qty_lots
            if self.size_mode == "risk":
                try:
                    equity_usd = await self._order_mgr.get_equity_usd()
                    if equity_usd and equity_usd > 0:
                        entry_qty = calc_qty_from_risk(
                            equity_usd=equity_usd,
                            risk_pct=self.risk_pct,
                            stop_dist_pts=risk_pre.stop_dist,
                            contract_value=self._order_mgr.contract_value,
                            min_lots=MIN_QTY_LOTS,
                            max_lots=MAX_QTY_LOTS,
                        )
                        try:
                            self._dashboard.update_live_state(
                                runner_id=self.id,
                                equity_usd=equity_usd,
                                last_stop_dist=risk_pre.stop_dist,
                            )
                        except Exception:
                            pass
                    else:
                        self._log.warning(
                            f"{self.tag} equity fetch failed/zero — "
                            f"falling back to static qty={self._qty_lots}"
                        )
                except Exception as e:
                    self._log.warning(
                        f"{self.tag} calc_qty_from_risk failed ({e}) — "
                        f"falling back to static qty={self._qty_lots}"
                    )

            try:
                order = await self._order_mgr.place_entry(
                    is_long=sig.is_long, sl=risk_pre.sl,
                    tp=risk_pre.tp, atr=snap.atr, qty=entry_qty,
                )
            except Exception as e:
                self._log.error(f"{self.tag} [ENTRY] Order failed: {e}")
                await self._telegram.send(
                    f"❌ <b>{self.tag} Entry FAILED</b>\n"
                    f"Signal: {sig.signal_type.value}\nError: <code>{e}</code>"
                )
                return

            fill = float(order.get("average") or order.get("price") or snap.close)

            # Read actual filled contracts
            _filled_contracts = float(
                order.get("filled") or order.get("amount")
                or order.get("contracts") or 0
            )
            if _filled_contracts > 0 and abs(_filled_contracts - entry_qty) > 0.01:
                self._log.info(
                    f"{self.tag} Using actual fill qty={_filled_contracts:.0f} "
                    f"(requested {entry_qty}, mode={self.size_mode})"
                )
                self._qty_lots = int(round(_filled_contracts))
            else:
                self._qty_lots = (
                    int(round(_filled_contracts))
                    if _filled_contracts > 0
                    else entry_qty
                )

            slip = (fill - snap.close) if sig.is_long else (snap.close - fill)
            slip_limit = snap.atr * MAX_ENTRY_SLIP_ATR_FRAC

            if slip > slip_limit:
                risk_pre = calc_levels(
                    fill, snap.atr, sig.is_long, sig.is_trend,
                    entry_bar_open=snap.open, signal_close=snap.close,
                )

            risk = RiskLevels(
                entry_price=fill, sl=risk_pre.sl, tp=risk_pre.tp,
                stop_dist=risk_pre.stop_dist, atr=risk_pre.atr,
                is_long=risk_pre.is_long, is_trend=risk_pre.is_trend,
                entry_bar_open=snap.open, signal_close=snap.close,
            )

            self._in_position = True
            self._risk        = risk
            self._signal_type = sig.signal_type.value

            self._trail_state = TrailState(
                stage=0, current_sl=risk.sl, peak_price=fill,
                trail_armed=False, best_price=0.0,
            )

            self._trail_mon.start(
                risk_levels=risk,
                trail_state=self._trail_state,
                entry_bar_time_ms=int(time.time() * 1000),
                on_trail_exit=self._on_trail_exit,
                signal_bar_high=snap.high, signal_bar_low=snap.low,
                signal_bar_open=snap.open, signal_bar_close=snap.close,
            )

            try:
                _tf_str = self.timeframe
                _unit   = _tf_str[-1]
                _n      = int(_tf_str[:-1])
                _mult_ms = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}.get(_unit, 60_000)
                _period_ms     = _n * _mult_ms
                _next_bar_open = int(snap.timestamp) + _period_ms
                self._entry_bar_boundary_ms = _next_bar_open
                self._trail_mon.set_entry_bar_boundary(_next_bar_open)
            except Exception:
                pass

            self._log.info(
                f"{self.tag} [ENTRY] Filled | type={sig.signal_type.value}  "
                f"fill={fill:.2f}  sl={risk.sl:.2f}  tp={risk.tp:.2f}  "
                f"atr={snap.atr:.2f}  stop_dist={risk.stop_dist:.2f}"
            )

            try:
                self._journal.open_trade(
                    signal_type=sig.signal_type.value,
                    is_long=sig.is_long, entry_price=fill,
                    sl=risk.sl, tp=risk.tp, atr=snap.atr,
                    qty=self._qty_lots,
                )
            except Exception:
                pass

            await self._telegram.notify_entry(
                signal_type=sig.signal_type.value,
                entry_price=fill, sl=risk.sl, tp=risk.tp,
                atr=snap.atr, qty=self._qty_lots,
                tag=self.tag,
            )

    # ══════════════════════════════════════════════════════════════════════
    # Exit callback
    # ══════════════════════════════════════════════════════════════════════

    async def _on_trail_exit(
        self,
        exit_price: float,
        reason: str,
        source: str = "tick",
        position_already_closed: bool = False,
    ) -> None:
        if not self._in_position:
            return

        if not position_already_closed:
            self._log.warning(
                f"{self.tag} [EXIT] position_already_closed=False — "
                f"reason={reason} source={source}"
            )

        risk = self._risk
        pl = (
            calc_gross_pl(
                risk.entry_price, exit_price, risk.is_long,
                self._qty_lots,
                contract_value=self._order_mgr.contract_value,
            )
            if risk else 0.0
        )

        self._log.info(
            f"{self.tag} [EXIT] reason={reason}  source={source}  "
            f"entry={risk.entry_price if risk else '?'}  "
            f"exit={exit_price:.2f}  gross_pl={pl:+.6f} USD"
        )

        try:
            if risk:
                self._journal.log_trade(
                    signal_type=self._signal_type,
                    is_long=risk.is_long,
                    entry_price=risk.entry_price,
                    exit_price=exit_price,
                    sl=risk.sl, tp=risk.tp, atr=risk.atr,
                    qty=self._qty_lots, real_pl=pl,
                    exit_reason=reason,
                    trail_stage=self._trail_state.stage if self._trail_state else 0,
                )
                self._journal.close_open_trade()
        except Exception as e:
            self._log.warning(f"{self.tag} log_trade failed: {e}")

        try:
            await self._telegram.notify_exit(
                reason=reason,
                entry_price=risk.entry_price if risk else 0.0,
                exit_price=exit_price,
                real_pl=pl,
                is_long=risk.is_long if risk else True,
                qty=self._qty_lots,
                tag=self.tag,
            )
        except Exception:
            pass

        self._in_position = False
        self._risk        = None
        self._trail_state = None
        self._signal_type = "None"
