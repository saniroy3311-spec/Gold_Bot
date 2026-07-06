# 🪙 GOLDBOT — MASTER REPOSITORY MEMORY & ARCHITECTURE GUIDE

This document serves as the persistent, single-source-of-truth memory file for **GoldBot v10**. Any AI coding assistant or subagent working on this repository MUST reference and update this file whenever architecture, settings, or strategy logic are modified.

---

## 1. System Overview & VPS Operational Details

* **Trading Target:** Gold Perpetuals (`PAXG/USD:USD`) on **Delta Exchange India**.
* **GitHub Repository:** `https://github.com/saniroy3311-spec/Gold_Bot.git` (`main` branch).
* **Target VPS Environment:**
  * **IP Address:** `187.127.136.139`
  * **SSH User / Directory:** `root@187.127.136.139:/app/goldbot`
  * **Systemd Service Name:** `goldbot.service`
  * **Web Dashboard URL:** `http://187.127.136.139:10001` (Port `10001` open in UFW & Hostinger hPanel).

### Common VPS Commands:
```bash
# Pull latest code & restart bot
cd /app/goldbot && git pull origin main && systemctl restart goldbot

# View live systemd logs (correct syntax)
journalctl -u goldbot -n 50 --no-pager

# Check service status
systemctl status goldbot
```

---

## 2. Environment Configuration Defaults (`.env` & `config.py`)

| Parameter | Value | Purpose / Notes |
| :--- | :--- | :--- |
| `PAPER_TRADING` | `true` | Simulation mode using live market feeds. Order placement on Delta REST API is blocked. |
| `PAPER_TRADING_BALANCE` | `10000.0` | Base initial equity ($10,000.00 USD). |
| `SYMBOL` | `PAXG/USD:USD` | Gold token perpetual future on Delta Exchange India. |
| `BASE_ASSET_LABEL` | `PAXG` | Contract unit label. |
| `CANDLE_TIMEFRAME` | `15m` | Primary signal timeframe. |
| `POSITION_SIZE_MODE` | `risk` | Dynamic risk-based lot sizing. |
| `RISK_PCT_PER_TRADE` | `0.4` | Risk 0.4% of live account equity per trade (aligned with Pine v2). |
| `EMA_FAST_LEN` | `50` | Fast EMA period. |
| `EMA_TREND_LEN` | `200` | Trend EMA period (aligned with Pine v2). |
| `TREND_ATR_MULT` | `1.2` | Trend initial SL ATR multiplier (aligned with Pine v2). |
| `RANGE_ATR_MULT` | `1.0` | Range initial SL ATR multiplier (aligned with Pine v2). |
| `TREND_RR` | `1.8` | Trend TP Risk/Reward ratio (aligned with Pine v2). |
| `RANGE_RR` | `1.4` | Range TP Risk/Reward ratio (aligned with Pine v2). |
| `BE_MULT` | `1.2` | Breakeven ATR trigger multiplier (aligned with Pine v2). |
| `ADX_TREND_TH` | `22` | ADX threshold for Trend Regime ($>22$). |
| `ADX_RANGE_TH` | `18` | ADX threshold for Range Regime ($<18$). |
| `SL_CONFIRM_TICKS` | `2` | Initial SL 2-tick confirmation requirement. |
| `TRAIL_SL_CONFIRM_TICKS` | `2` | Trailing SL 2-tick confirmation requirement. |
| `TRAIL_SL_BREACH_HOLD_SECS` | `2.0` | 2.0-second continuous breach hold guard before firing trailing exit. |
| `TRAIL_STAGE_UPGRADE_MODE` | `intrabar` | Stage upgrades (1 $\rightarrow$ 5) & BE evaluate tick-by-tick inside running candle. Bot 5-stage trailing stop engine preserved. |
| `DASHBOARD_PORT` | `10001` | Server port for single-screen web dashboard. |

---

## 3. Core Architecture & Module Map

```
/app/goldbot/
├── main.py                    # Main loop — candle boundary check, indicator engine, signal evaluation & execution
├── config.py                  # Master configuration parameters & .env environment resolution
├── server.py                  # Embedded HTTP server for /api/* endpoints and dashboard.html
├── dashboard.html             # Single-screen high-contrast mobile & desktop web dashboard
├── backtest_report.html       # Standalone 1-year backtest HTML report (matching dashboard theme)
├── BOT_MEMORY.md              # Master persistent memory file (this file)
│
├── feed/                      # Market Data Feeds
│   ├── ws_feed.py             # Delta Exchange WebSocket & Binance WebSocket feed managers
│   └── rest_feed.py           # REST fallback candle fetchers
│
├── indicators/                # Technical Analysis Engine
│   ├── engine.py              # EMA (50/60), ADX (14), RSI (14), ATR (14) calculations
│   └── filters.py             # ATR filter (1.4x), Vol filter (1.0x), Body filter (0.5x)
│
├── orders/                    # Order Management
│   └── manager.py             # OrderManager — paper trading simulation & live Delta Exchange order execution
│
├── risk/                      # Risk Management
│   ├── calculator.py          # Dynamic stop loss, take profit, and 5-stage trail state manager
│   └── lot_sizing.py          # calc_qty_from_risk — dynamic lot sizing (Equity * 1% / Stop Distance)
│
├── monitor/                   # Real-time Monitoring Loops
│   └── trail_loop.py          # Real-time tick monitoring loop for SL, TP, breakeven, and 5-stage trail exits
│
└── journal.py                 # SQLite database logger for trade history and performance metrics
```

---

## 4. Trailing Stop Loss Engine & Parity Rules

1. **5-Stage Geometry:** Evaluates dynamic stage triggers based on ATR multiples.
2. **Tick-by-Tick Stage Upgrades (`TRAIL_STAGE_UPGRADE_MODE=intrabar`):** Stage upgrades and Breakeven arming evaluate on every incoming tick inside the running 15m candle.
3. **Wick Protection Guard (`TRAIL_SL_BREACH_HOLD_SECS=2.0` & `TRAIL_SL_CONFIRM_TICKS=2`):**
   * Requires 2 consecutive ticks below the trailing stop level before arming the hold timer.
   * If price remains below the stop level for $\ge 2.0$ wall-clock seconds, the trailing exit fires.
   * If price bounces back above the stop level within 2.0 seconds (a wick), the timer resets and the position remains open.

---

## 5. Web Dashboard Design & UI Specifications (`dashboard.html`)

* **Color Palette & Styling:** High-contrast square geometry (`border-radius: 0px`), 2px solid `#0f172a` borders, 3px/4px sharp drop shadows, and `#ffffff` panel background.
* **Typography:** System font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto`) with solid `#0f172a !important` text and font weight **800/900** for mobile readability.
* **Time Zone:** Displayed in **Indian Standard Time (IST — UTC+5:30)** across header timestamp and trade log table.
* **Equity Curve Chart:**
  * Height: `340px` (Desktop) / `260px` (Mobile).
  * HTML Overlay Badges: `Base: $2,000.00` (top-left) and `Equity: $2,000.00` (top-right) rendered with native CSS typography to avoid SVG squishing.
* **Recent Trades Table:**
  * Columns: `# | Time (IST) | Side | Qty | Entry Price | Exit Price | Realized P&L ($) | Exit Reason`
  * Dual-axis scrolling: Vertical scroll up to `420px` max height + Touch horizontal swipe scroll for mobile devices.
* **Mobile Responsiveness:** Strict `overflow-x: hidden !important` on `html, body, .container` to eliminate horizontal body page scrolling. Single vertical column stack on screens $<768\text{px}$.

---

## 6. Guidelines for AI Assistants & Subagents

1. **Memory Maintenance:** Whenever you modify files in this repository (e.g. adding new parameters, changing UI features, tuning indicator logic), update this `BOT_MEMORY.md` file and `.agents/AGENTS.md` before concluding your turn.
2. **Git Workflow:** After making changes, stage, commit, and push to `https://github.com/saniroy3311-spec/Gold_Bot.git` main branch so the user can deploy to VPS with a simple `git pull origin main`.
3. **Safety:** Never write live exchange credentials to public files. Always verify paper trading simulation guards (`PAPER_TRADING=true`).
