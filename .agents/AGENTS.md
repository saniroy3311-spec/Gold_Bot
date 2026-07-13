# AGENTS.md — GoldBot Agent Customization Rules & Memory

## Project Memory File
The master repository memory file is located at `BOT_MEMORY.md` in the project root:
[BOT_MEMORY.md](file:///d:/Antigravity/Gold_Bot/goldbot/BOT_MEMORY.md)

## Agent Instructions & Rules
1. **Always Read Memory First:** Before analyzing or modifying code in this codebase, view [BOT_MEMORY.md](file:///d:/Antigravity/Gold_Bot/goldbot/BOT_MEMORY.md) to understand the project architecture, parameters, and design rules.
2. **Keep Memory Updated:** Whenever you make structural changes, add configuration options, update indicator/trailing logic, or modify the dashboard UI, update both `BOT_MEMORY.md` and `AGENTS.md`.
3. **VPS Commands Parity:** When guiding the user on VPS deployment, always provide the standard sequence:
   ```bash
   cd /app/goldbot && git pull origin main && systemctl restart goldbot
   ```
4. **Dashboard Parity:** Maintain clean 90-degree rectangular boxes (no clip-path angled cuts) with solid neon green `#00ff66` borders, neon green monospace typography, uppercase `GOLDBOT`/`BTCBOT` in title, IST time formatting, 100% mobile screen fit (zero horizontal body scroll), prominent neon green `#00ff66` King Bot mascot size (90px/54px) equipped with ear bolts, shoulder armor, pulsing chest energy core, and a gun firing a bullet every 4s cycle (idle for 2s, fires with muzzle flash), extended Equity Curve chart height (`340px`), `Qty` column in the trade history table, and live unrealized P&L row in the POSITION card below the stage bar.

## Changelog & Fixes
- **2026-07-13:** Fixed `ReferenceError` on undefined `ptsStr` in the desktop trade mapping loop in `dashboard.html`. This resolves the issue where trades were plotted on the equity curve but completely absent from the recent trades logs table.
- **2026-07-13:** Enabled symbol-specific EMA overrides in `config.py` and passed them down to the indicator engines in `indicators/engine.py` and `strategy_logic.py`. Configured BTC to use a Fast EMA of 50 and Slow/Trend EMA of 60.
- **2026-07-13:** Updated the Market Regime dashboard card to dynamically display parameter values (e.g. `EMA (50/60)`, `ADX (14)`, `RSI (14)`) inside the labels of the UI card.



