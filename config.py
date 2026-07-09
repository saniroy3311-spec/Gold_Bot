"""
config.py - Shiva Sniper v10  (PINE-ALIGNED 2026-06-03 → TRADE-MATCH-FIX 2026-06-05)
PREVIOUS CHANGES (2026-06-03)
ADX_TREND_TH 17→22, FILTER_ATR_MULT 1.6→1.4, FILTER_BODY_MULT 0.4→0.5,
TREND_RR 5→4, RANGE_RR 3→2.5, TREND_ATR_MULT 0.9→0.6, RANGE_ATR_MULT 0.7→0.5,
MAX_SL_MULT 2→1.5, MAX_SL_POINTS 1500→500, BE_MULT 1→0.6,
TRAIL_OFFSET_FLOOR 0.15→0.0, PINE_MINTICK 0.1→1.0,
BREAKOUT_BUFFER_PTS = 0
TRADE-MATCH FIX (2026-06-05) — Fixes "trade mis + extra trade punch" report
Four root causes identified for bot trades not matching the Pine trade list:
FIX-A | FILTER_VOL_ENABLED  false → true  (CRITICAL — extra trade punches)
CAUSE:  The previous fix disabled the volume filter because Delta REST
volumes (~3% of TradingView's) made every bar fail volOK.
BUT BINANCE_SIGNAL_FEED=true was already active — indicator bars
come from Binance REST (the same source TradingView uses for
BTCUSDT). Binance volumes ARE directly comparable to Pine's volSMA.
EFFECT: With filter OFF, bot entered on low-volume bars where Pine's
filtersOK = false (volOK failed). Every such bar is an "extra punch"
that has no match on the Pine chart.
FIX:    Re-enable FILTER_VOL_ENABLED=true now that Binance data is the source.
Set FILTER_VOL_ENABLED=false in .env only if BINANCE_SIGNAL_FEED=false.
FIX-B | BREAKOUT_BUFFER_PTS = 0
CAUSE:  Buffer of 40 was added to compensate for Delta REST OHLCV being
30–80 pts different from TradingView's. With BINANCE_SIGNAL_FEED=true,
prev_high/prev_low already come from Binance (= TradingView data).
EFFECT: The 40pt buffer over-filtered: any Pine trend entry where
close > prev_low (Pine fires) but close < prev_low + 40 (bot skips)
was missed. These appeared as "trade mis" in the comparison.
FIX:    Reduce to 5pts (covers only REST timing jitter; ~1 pip).
Set to 0 for exact Pine parity. Only use 30–50 if BINANCE_SIGNAL_FEED=false.
FIX-C | Intrabar stage upgrades REMOVED from trail_loop._evaluate()  (HIGH)
CAUSE:  trail_loop.py advanced trail stages on every price tick (intrabar).
Pine with calc_on_every_tick=false only runs its strategy body at
bar close, so trailStage only upgrades at bar close.
EFFECT: Bot reached stage 2/3 on an intrabar spike, immediately tightened
the trail offset, then trailed out at a worse price than Pine.
These showed as Trail SL exits at different prices vs Pine chart.
FIX:    Stage upgrades moved to on_bar_close() only (already present there).
Intrabar block removed from _evaluate() in trail_loop.py.
FIX-D | Intrabar breakeven REMOVED from trail_loop._evaluate()  (MEDIUM)
CAUSE:  Same as FIX-C — breakeven (beDone check) fired intrabar when Pine
only checks it at bar close.
EFFECT: BE stop armed mid-bar; any pullback before bar close hit the BE stop
when Pine's BE stop wasn't yet active.
FIX:    BE check removed from _evaluate(). Remains in on_bar_close() only.
FIX-E | self.atr updated from current_atr in on_bar_close()  (MEDIUM)
CAUSE:  Pine recalculates activePts = atr * tNPts and activeOff = atr * tNOff
every bar using the LIVE ATR (ta.atr is recomputed each bar).
Bot froze self.atr at the entry-bar ATR.
EFFECT: When live ATR shrank, Pine's trail offset shrank (tighter trail) but
bot's trail stayed wide → bot trailed behind Pine's trail SL.
FIX:    on_bar_close() now updates self.atr = current_atr each bar.
All changes are .env-overridable.
"""
import os
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

# ──────────────────────────────────────
# DELTA EXCHANGE
# ──────────────────────────────────────
DELTA_API_KEY    = os.environ.get("DELTA_API_KEY",    "YOUR_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "YOUR_API_SECRET")
DELTA_TESTNET    = os.environ.get("DELTA_TESTNET", "false").lower() == "true"
PAPER_TRADING    = os.environ.get("PAPER_TRADING", "false").lower() == "true"
SYMBOL    = os.environ.get("SYMBOL",    "BTC/USD:USD")
ALERT_QTY = int(os.environ.get("ALERT_QTY", "1"))
# v10: position size in BTC. Converted to lots via risk.lot_sizing.btc_to_lots
POSITION_BTC_SIZE = float(os.environ.get("POSITION_BTC_SIZE", "0.001"))

# ──────────────────────────────────────
# INSTRUMENT / CONTRACT  (GOLDBOT — generalized beyond BTC)
# ──────────────────────────────────────
# Human-readable label for the base asset, used in Telegram/WhatsApp/dashboard
# text ("X lots (Y PAXG)" instead of hardcoded "BTC"). Set to "PAXG" for gold.
BASE_ASSET_LABEL = os.environ.get("BASE_ASSET_LABEL", "BTC")
# Contract value (base-asset units per 1 lot) override. 0 = auto-detect from
# the exchange's market metadata at OrderManager.initialize() (recommended —
# this is the source of truth and avoids hardcoding). Only set this if you
# need to force a value (e.g. exchange API is unreachable at boot / testing).
CONTRACT_VALUE_OVERRIDE = float(os.environ.get("CONTRACT_VALUE_OVERRIDE", "0"))

# ──────────────────────────────────────
# POSITION SIZING MODE  (GOLDBOT — dynamic risk-based qty)
# ──────────────────────────────────────
# "static" = qty is fixed from POSITION_BTC_SIZE / ALERT_QTY (legacy behavior).
# "risk"   = qty is computed fresh at every signal from live equity:
#            qty_lots = (equity_usd * RISK_PCT_PER_TRADE/100) / (stop_dist_pts * contract_value)
#            This is the new pine script's model (riskAmount / stopDist),
#            corrected for Delta's lot/contract_value units.
POSITION_SIZE_MODE   = os.environ.get("POSITION_SIZE_MODE", "static").lower()
RISK_PCT_PER_TRADE   = float(os.environ.get("RISK_PCT_PER_TRADE", "1.0"))  # Risk 1.0% per trade
MIN_QTY_LOTS         = int(os.environ.get("MIN_QTY_LOTS", "1"))
MAX_QTY_LOTS         = int(os.environ.get("MAX_QTY_LOTS", "0"))  # 0 = unlimited

# ──────────────────────────────────────
# TELEGRAM
# ──────────────────────────────────────
TELEGRAM_ENABLED    = os.environ.get("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID")

# ──────────────────────────────────────
# WHATSAPP (Meta Business Cloud API)
# ──────────────────────────────────────
WHATSAPP_ACCESS_TOKEN    = os.environ.get("WHATSAPP_ACCESS_TOKEN",     "YOUR_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID",  "YOUR_PHONE_NUMBER_ID")
WHATSAPP_TO_NUMBER       = os.environ.get("WHATSAPP_TO_NUMBER",        "YOUR_TO_NUMBER")
WHATSAPP_VERIFY_TOKEN    = os.environ.get("WHATSAPP_VERIFY_TOKEN",     "YOUR_VERIFY_TOKEN")
WHATSAPP_TEMPLATE_NAME   = os.environ.get("WHATSAPP_TEMPLATE_NAME",    "")
WHATSAPP_TEMPLATE_LANG   = os.environ.get("WHATSAPP_TEMPLATE_LANG",    "en")

# ──────────────────────────────────────
# INDICATOR LENGTHS  (Pine-exact)
# ──────────────────────────────────────
EMA_TREND_LEN = int(os.environ.get("EMA_TREND_LEN", "200"))   # Trend EMA 200
EMA_FAST_LEN  = int(os.environ.get("EMA_FAST_LEN",  "50"))   # Fast EMA 50
ATR_LEN       = int(os.environ.get("ATR_LEN",       "14"))
DI_LEN        = int(os.environ.get("DI_LEN",        "14"))
ADX_SMOOTH    = int(os.environ.get("ADX_SMOOTH",    "14"))
ADX_EMA       = int(os.environ.get("ADX_EMA",       "5"))
RSI_LEN       = int(os.environ.get("RSI_LEN",       "14"))

# ──────────────────────────────────────
# REGIME THRESHOLDS  (PINE-ALIGNED)
# ──────────────────────────────────────
# Pine: adxTrendTh = 22, adxRangeTh = 18
# Previously 17 to absorb a ~3-point Delta-vs-TV ADX gap. If that gap is
# still real on your data and you miss entries, set ADX_TREND_TH=17 in .env.
ADX_TREND_TH = int(os.environ.get("ADX_TREND_TH", "22"))
ADX_RANGE_TH = int(os.environ.get("ADX_RANGE_TH", "18"))
# Soft tolerance for ADX comparison. 0.0 = strict Pine match (recommended now
# that ADX_TREND_TH is back to 22). Set higher if you see missed signals.
ADX_TOLERANCE = float(os.environ.get("ADX_TOLERANCE", "0.0"))

# ──────────────────────────────────────
# ENTRY FILTERS  (PINE-ALIGNED)
# ──────────────────────────────────────
# Pine: filterATRMult = 1.4, filterBodyMult = 0.5
FILTER_ATR_MULT    = float(os.environ.get("FILTER_ATR_MULT",  "1.4"))
FILTER_BODY_MULT   = float(os.environ.get("FILTER_BODY_MULT", "0.5"))
# Body filter tolerance (absorbs Delta vs TV OHLC differences).
# 0.0 = strict Pine match. Default 0.05 = lets body of >ATR*0.45 pass.
FILTER_BODY_TOLERANCE = float(os.environ.get("FILTER_BODY_TOLERANCE", "0.0"))
# Volume filter — RE-ENABLED: DEFAULT IS NOW TRUE.
# PREVIOUS BUG: was forced false because Delta REST volumes are ~3% of TV's.
# ROOT CAUSE OF "EXTRA TRADE PUNCHES":
# With BINANCE_SIGNAL_FEED=true (the default), indicator bars come from
# Binance REST + WS — the SAME data source TradingView uses for BTCUSDT.
# Binance volumes are directly comparable to Pine's volSMA, so
# filtersOK = atrOK AND volOK AND bodyOK now matches Pine exactly.
# With the filter OFF, the bot entered on low-volume bars that Pine's
# filtersOK rejected → these appeared as ghost entries vs the Pine list.
# Only set false if BINANCE_SIGNAL_FEED=false (Delta REST data):
# FILTER_VOL_ENABLED=false in .env
FILTER_VOL_ENABLED = os.environ.get("FILTER_VOL_ENABLED", "true").lower() == "true"
FILTER_VOL_MULT    = float(os.environ.get("FILTER_VOL_MULT", "1.0"))
# Body-size filter (bodySize > atr * FILTER_BODY_MULT). The new pine script
# re-enables this (bodyConfirm). Default true = matches new script + is
# already how the LIVE indicators/engine.py behaves (unconditional there).
# Set false only to fully disable body filtering.
FILTER_BODY_ENABLED = os.environ.get("FILTER_BODY_ENABLED", "true").lower() == "true"

# ─────────────────────────────────────
# RISK / REWARD  (PINE-ALIGNED)
# ──────────────────────────────────────
# Pine: trendRR=4.0, rangeRR=2.5
TREND_RR       = float(os.environ.get("TREND_RR",       "1.8"))
RANGE_RR       = float(os.environ.get("RANGE_RR",       "1.4"))
# Pine: trendATRmul=1.2, rangeATRmul=1.0, maxSLpoints=500
# stopDist = min(atr * atrMult, maxSLPoints)
TREND_ATR_MULT = float(os.environ.get("TREND_ATR_MULT", "1.2"))
RANGE_ATR_MULT = float(os.environ.get("RANGE_ATR_MULT", "1.0"))
# Pine: maxSLmul=1.5, maxSLpoints=500
MAX_SL_MULT    = float(os.environ.get("MAX_SL_MULT",    "1.5"))
MAX_SL_POINTS  = float(os.environ.get("MAX_SL_POINTS",  "500.0"))

# ──────────────────────────────────────
# EMERGENCY BRACKET WIDENING  (FIX-BRACKET-INTRABAR)
# ──────────────────────────────────────
# The exchange-side bracket SL is a RESTING stop order on Delta. It fires on
# ANY intrabar touch of last_traded_price. Pine (calc_on_every_tick=false) only
# evaluates the stop at BAR CLOSE. If the bracket sits at the Pine initial SL,
# every intrabar wick that Pine would ignore closes the bot's position early.
# So the bracket must be a CATASTROPHE-ONLY net, placed far beyond the Pine SL.
# Python (TrailMonitor) still owns the real, Pine-exact bar-close SL.
# bracket_dist = clamp(pine_sl_dist * WIDEN_MULT, MIN_PTS, MAX_SL_POINTS)
BRACKET_SL_WIDEN_MULT = float(os.environ.get("BRACKET_SL_WIDEN_MULT", "3.0"))
BRACKET_SL_MIN_PTS    = float(os.environ.get("BRACKET_SL_MIN_PTS",    "300.0"))

# ──────────────────────────────────────
# PINE MINTICK  — BUG-FIX-3/BUG-2: DEFAULT IS NOW 1.0
# ──────────────────────────────────────
# Pine's strategy.exit(trail_points=X, trail_offset=Y) takes X and Y as
# dimensionless ATR multiples — they are NOT in exchange tick units.
# The old default of 0.1 multiplied the offset by 0.1, making the bot's
# trail 10× tighter than Pine's:
# ATR=400, stage-1 offset (old): 400 × 0.40 × 0.1  =  16 pts  ← WRONG
# ATR=400, stage-1 offset (new): 400 × 0.40 × 1.0  = 160 pts  ← Pine exact
# With PINE_MINTICK=1.0:  offset_in_price = atr × stage_off_mult  (= Pine)
# With PINE_MINTICK=0.1:  offset_in_price = atr × stage_off_mult × 0.1
# Only change this if you have a concrete reason to scale the offsets
# (e.g. a different instrument where Pine explicitly passes tick-unit values).
PINE_MINTICK = float(os.environ.get("PINE_MINTICK", "1.0"))

# ──────────────────────────────────────
# 5-STAGE TRAIL ENGINE  (PINE-STAGE-EXACT)
# ──────────────────────────────────────
# Format: (trigger_ATR_mult, trail_points_mult, trail_offset_mult)
# Values verified line-by-line against Pine inputs t1Trig/t1Pts/t1Off … t5*.
#
# ── BUG-FIX-TRAIL-OFFSET-2026-06-25 ─────────────────────────────────────────
# Trade #358 (SHORT, Jun 25 2026):
#   ATR=262.53, best_price=61,039.50
#   Old Stage-1 offset: 0.40 × ATR = 105.01 → trail_SL=61,144.51
#   TV exit actual    : 61,092.00
#   Reverse-engineered: TV offset = 61,092 − 61,039.50 = 52.50 ≈ 0.20 × ATR
#
# The old offset (0.40) was 2× wider than Pine's real t1Off (0.20).
# Bot held trail 52.5 pts ABOVE TV's SL → exited at 61,167.50 (+90.5 pts)
# instead of ~61,092 (+166 pts). Gap = 75.5 pts per trade.
#
# Fix: t1Off 0.40 → 0.20  (Pine-exact, confirmed from live trade geometry).
# ─────────────────────────────────────────────────────────────────────────────
TRAIL_STAGES = [
    (0.8,  0.50, 0.20),   # Stage 1   — Pine t1Trig/t1Pts/t1Off  ← FIXED 0.40→0.20
    (1.5,  0.40, 0.30),   # Stage 2   — Pine t2Trig/t2Pts/t2Off
    (2.5,  0.30, 0.25),   # Stage 3   — Pine t3Trig/t3Pts/t3Off
    (4.0,  0.20, 0.15),   # Stage 4   — Pine t4Trig/t4Pts/t4Off
    (6.0,  0.15, 0.10),   # Stage 5   — Pine t5Trig/t5Pts/t5Off
]

# ──────────────────────────────────────
# TIME-BASED EXIT
# ──────────────────────────────────────
# Pine has NO time exit. Default 0 = full Pine parity.
# If you specifically want "exit at candle close if SL/TP didn't fire",
# set TIME_EXIT_MINUTES=30 (for 30m candles) in your .env. This will FORCE
# the bot to close any open trade 30 min after entry — diverges from Pine
# but matches the same-bar behaviour you may have wanted to enforce.
TIME_EXIT_MINUTES = int(os.environ.get("TIME_EXIT_MINUTES", "0"))

# ──────────────────────────────────────
# BREAKEVEN + RSI  (PINE-ALIGNED)
# ──────────────────────────────────────
# Pine: beMult=1.2
BE_MULT = float(os.environ.get("BE_MULT", "1.2"))
RSI_OB  = int(os.environ.get("RSI_OB", "70"))
RSI_OS  = int(os.environ.get("RSI_OS", "30"))
# BUG FIX (GOLDBOT): this was hardcoded to 0 in TWO places below, silently
# ignoring the .env value entirely. Now a single env-driven definition.
# HISTORY: Was set to 40 to compensate for Delta REST OHLCV being 30–80 pts
# different from TradingView's BTCUSDT candles on the same bar. A bar with
# tv_close barely below tv_prev_low would fire in Pine but NOT in the bot
# (bot's delta_prev_low was lower, so bot didn't see it as a breakout).
# Buffer of 40 was added so bot only fires when the move is unambiguous.
# ROOT CAUSE OF MISSED SIGNALS WITH BINANCE FEED:
# With BINANCE_SIGNAL_FEED=true (the default), prev_high/prev_low come from
# Binance OHLCV — the SAME exchange TradingView uses for BTCUSDT. The Delta
# vs TradingView OHLCV gap no longer exists. A 40pt buffer on identical data
# means the bot misses every Pine trend entry where:
# close > prev_low (Pine fires) but close < prev_low + 40 (bot doesn't).
# Fix: reduce to 5pts (tiny tolerance for REST fetch timing jitter only).
# If you see ghost entries return:  increase to 10 or 15.
# If you see missed signals remain: set to 0 (exact Pine parity with Binance).
# Only set high (30-50) if BINANCE_SIGNAL_FEED=false.
BREAKOUT_BUFFER_PTS = float(os.environ.get("BREAKOUT_BUFFER_PTS", "0"))

# ──────────────────────────────────────
# COMMISSION + BUFFERS
# ──────────────────────────────────────
# Pine: commission_value=0.05 (percent). Now env-driven via basis points
# (COMMISSION_PCT_BPS=5 → 0.05%) so it's variable per-instrument/exchange.
COMMISSION_PCT_BPS       = float(os.environ.get("COMMISSION_PCT_BPS", "5"))
COMMISSION_PCT           = COMMISSION_PCT_BPS / 100 / 100
BRACKET_SL_BUFFER        = float(os.environ.get("BRACKET_SL_BUFFER",        "10.0"))
TRAIL_SL_PRE_FIRE_BUFFER = float(os.environ.get("TRAIL_SL_PRE_FIRE_BUFFER", "0.0"))

# ──────────────────────────────────────
# SL CONFIRMATION WINDOW  (FIX-BINANCE-SPIKE)
# ──────────────────────────────────────
# Pine's backtester uses simulated intrabar movement (interpolated OHLC).
# The bot uses real Binance aggTrade ticks (~10ms), which include micro-spikes
# that Pine's model smooths over. A 50-150pt wick lasting <500ms fires the
# bot's Initial SL, while Pine never saw it.
# Fix: require price to stay beyond Initial SL for this many ms before firing.
# Trail SL / TP / Max SL still fire immediately.
# 0 = disabled (instant fire). 1500 = 1.5s (recommended).
SL_CONFIRM_MS = int(os.environ.get("SL_CONFIRM_MS", "1500"))

# ─────────────────────────────────────
# SL CONFIRMATION — CONSECUTIVE DELTA TICK COUNT  (FIX-8 / Option 1+3)
# ──────────────────────────────────────
# When SL_CONFIRM_TICKS > 0, the time-based SL_CONFIRM_MS window is REPLACED
# by a consecutive-tick counter. The bot requires this many consecutive Delta
# Exchange ticks above the SL before firing the exit. Any single Delta tick
# below the SL resets the counter to 0. Binance ticks are completely ignored
# for the breach count — they can never trigger or advance the counter.
# Why tick-count > time-based:
# • Immune to Binance/Delta feed interleaving (the main early-exit cause).
# • Stable across all market conditions — doesn't speed up in fast markets.
# • Simpler to tune: 1 number, no ms estimation needed.
# Recommended starting value: 5
# At ~1 Delta tick/second, 5 ticks ≈ 5 seconds confirmation.
# Cost on a real SL hit: ~5 extra ticks of slippage (typically <10 pts).
# Gain: eliminates premature exits from the Binance/Delta fight.
# Set to 0 to disable and fall back to SL_CONFIRM_MS time-based mode.
SL_CONFIRM_TICKS = int(os.environ.get("SL_CONFIRM_TICKS", "2"))

# ──────────────────────────────────────────────────────────────────
# TRAIL SL CONFIRMATION — POST-ARM  (FIX-TRAIL-INTRABAR)
# ──────────────────────────────────────────────────────────────────
# Once the trail has ARMED the SL is already in profit territory.
# Set to 2 ticks and 2.0s hold guard for optimal noise filtering & fast exit!
TRAIL_SL_CONFIRM_TICKS = int(os.environ.get("TRAIL_SL_CONFIRM_TICKS", "2"))
TRAIL_SL_BREACH_HOLD_SECS = float(os.environ.get("TRAIL_SL_BREACH_HOLD_SECS", "2.0"))

# ──────────────────────────────────────
# TRAIL OFFSET FLOOR  (TUNED FOR BETTER CAPTURE)
# ─────────────────────────────────────
# IMPORTANT: Pine's strategy.exit() trail_points/trail_offset have NO floor.
# However, a small floor prevents the trail from becoming too tight and 
# getting stopped out by normal noise. Reduced from 0.40 to 0.15 to capture 
# more profit while still protecting against extreme wicks.
TRAIL_OFFSET_FLOOR_MULT = float(os.environ.get("TRAIL_OFFSET_FLOOR_MULT", "0.15"))

# ──────────────────────────────────────
# TP HARD EXIT  (FIX-TP-PARITY 2026-06-22)
# ──────────────────────────────────────
# Pine's LIVE strategy has NO strategy.exit(limit=tp) — confirmed by trade #353,
# where TV ran straight through its plotted TP level (65,168) and kept trailing
# another ~225pts to 64,949 before exiting via the trail. TP in Pine is plotted/
# informational only (see phase2/tv_signal_exporter.pine entryTP plot) — it is
# NOT wired to a hard market-close in the live strategy.
# The bot was treating TP as an instant market exit on every tick and on bar
# close, cutting trades short every time they reached the TP distance instead
# of letting the trail run further like TV does.
# false (default, THE FIX) = TP is ignored as an exit trigger entirely.
# Only the trail / Initial SL / Max SL / Time exit can close a trade.
# true = old behavior, TP fires a hard market close.
TP_HARD_EXIT = os.environ.get("TP_HARD_EXIT", "false").lower() == "true"
TRAIL_ARM_FLOOR_MULT    = float(os.environ.get("TRAIL_ARM_FLOOR_MULT",    "0.0"))
SL_FIRE_VIA_BRACKET = os.environ.get("SL_FIRE_VIA_BRACKET", "false").lower() == "true"

# ──────────────────────────────────────
# EXIT PRICE SOURCE  (FIX-STALE-CANDLE-HIGH 2026-05-31)
# ──────────────────────────────────────
# False (default, THE FIX): exits run only on the Binance aggTrade feed.
TRAIL_EXIT_FROM_DELTA_WS = os.environ.get("TRAIL_EXIT_FROM_DELTA_WS", "false").lower() == "true"

# ──────────────────────────────────────
# TRAIL SL FIRING SOURCE  (FIX-STALE-CANDLE-HIGH 2026-05-31)
# ──────────────────────────────────────
# False (default, THE FIX): push_ws_candle only advances best_price from the
# FAVOURABLE extreme. Stop fires only via on_price_tick (Binance aggTrade tick).
TRAIL_FIRE_SL_ON_CANDLE_EXTREME = os.environ.get("TRAIL_FIRE_SL_ON_CANDLE_EXTREME", "false").lower() == "true"

PAPER_TRADING_BALANCE = float(os.environ.get("PAPER_TRADING_BALANCE", "10000.0"))
CANDLE_TIMEFRAME = os.environ.get("CANDLE_TIMEFRAME", "45m")
BINANCE_SIGNAL_FEED = os.environ.get("BINANCE_SIGNAL_FEED", "true").lower() == "true"
BINANCE_SYMBOL      = os.environ.get("BINANCE_SYMBOL", "BTC/USDT")
TRAIL_LOOP_SEC   = float(os.environ.get("TRAIL_LOOP_SEC", "2.0"))  # FIX-10: 2s for position poll
WS_RECONNECT_SEC = int(os.environ.get("WS_RECONNECT_SEC", "5"))

# ──────────────────────────────────────
# TRAIL STAGE-UPGRADE TIMING  (GOLDBOT — user-requested: react on running candle)
# ──────────────────────────────────────
# "bar_close" = stage upgrades / BE only evaluated at bar close (Pine-exact,
#               this was the FIX-C/FIX-D deliberate parity fix — see history
#               at top of file).
# "intrabar"  = stage upgrades / BE also evaluated on every live tick, i.e.
#               reacts inside the running (unclosed) candle, matching the
#               "stage upgrade in running candle" behavior explicitly asked
#               for. This reopens the FIX-C/FIX-D divergence vs Pine — it is
#               a deliberate trade-off (faster stage capture vs Pine parity),
#               not a silent revert.
TRAIL_STAGE_UPGRADE_MODE = os.environ.get("TRAIL_STAGE_UPGRADE_MODE", "intrabar").lower()

# ──────────────────────────────────────
# LOGGING
# ──────────────────────────────────────
LOG_FILE = os.environ.get("LOG_FILE", "/root/Bot-v10/journal.db")

# ──────────────────────────────────────
# SLIPPAGE TRACKING (NEW FIX)
# ─────────────────────────────────────
# Alert threshold for slippage as a percentage of ATR.
MAX_EXIT_SLIPPAGE_ATR_PCT = float(os.environ.get("MAX_EXIT_SLIPPAGE_ATR_PCT", "25.0"))

# ─────────────────────────────────────
# PARITY ALIASES  (flat constants for verification — do not use in logic)
# Derived from TRAIL_STAGES list above. Values are identical.
# ──────────────────────────────────────
ADX_EMA_LEN   = ADX_EMA   # alias — same value (5)
TRAIL_T1_TRIG, TRAIL_T1_PTS, TRAIL_T1_OFF = TRAIL_STAGES[0]
TRAIL_T2_TRIG, TRAIL_T2_PTS, TRAIL_T2_OFF = TRAIL_STAGES[1]
TRAIL_T3_TRIG, TRAIL_T3_PTS, TRAIL_T3_OFF = TRAIL_STAGES[2]
TRAIL_T4_TRIG, TRAIL_T4_PTS, TRAIL_T4_OFF = TRAIL_STAGES[3]
TRAIL_T5_TRIG, TRAIL_T5_PTS, TRAIL_T5_OFF = TRAIL_STAGES[4]

# Bar-close SL evaluation mode
# True  = Pine-exact: Initial SL only fires at bar close (calc_on_every_tick=false)
# False = legacy:     Initial SL fires on every live tick (can exit on intrabar wicks)
# RECOMMENDED: True — this is the single biggest cause of bot-vs-TV divergence.
BAR_CLOSE_SL_EVAL = os.environ.get("BAR_CLOSE_SL_EVAL", "true").lower() == "true"

# ──────────────────────────────────────
# MULTI-SYMBOL CONFIGURATION  (GOLDBOT — dual-engine)
# ──────────────────────────────────────
# Each entry defines a separate trading engine (SymbolRunner) that runs
# concurrently in the same bot process. Strategy params (EMA, ATR, trail
# stages, etc.) are shared globally — only instrument-specific settings
# differ per runner.
#
# To run ONLY Gold (legacy single-symbol mode), set MULTI_SYMBOL_ENABLED=false
# in .env — the bot will fall back to the global SYMBOL/CANDLE_TIMEFRAME vars.
MULTI_SYMBOL_ENABLED = os.environ.get("MULTI_SYMBOL_ENABLED", "true").lower() == "true"

import json as _json

_SYMBOLS_ENV = os.environ.get("SYMBOLS_JSON", "")

if _SYMBOLS_ENV:
    # Allow full override via JSON env var for advanced users
    SYMBOLS = _json.loads(_SYMBOLS_ENV)
else:
    SYMBOLS = [
        {
            "id":               "paxg",
            "symbol":           "PAXG/USD:USD",
            "binance_symbol":   "PAXG/USDT",
            "binance_ws_pair":  "paxgusdt",
            "base_asset_label": "PAXG",
            "timeframe":        CANDLE_TIMEFRAME,
            "risk_pct":         RISK_PCT_PER_TRADE,
            "paper_balance":    PAPER_TRADING_BALANCE,
            "position_size_mode": POSITION_SIZE_MODE,
            "dashboard_path":   "/",
            "db_file":          "/app/goldbot/journal_paxg.db",
        },
        {
            "id":               "btc",
            "symbol":           "BTC/USD:USD",
            "binance_symbol":   "BTC/USDT",
            "binance_ws_pair":  "btcusdt",
            "base_asset_label": "BTC",
            "timeframe":        "1m",
            "risk_pct":         1.0,
            "paper_balance":    10000.0,
            "position_size_mode": "risk",
            "dashboard_path":   "/btc",
            "db_file":          "/app/goldbot/journal_btc.db",
        },
    ]

