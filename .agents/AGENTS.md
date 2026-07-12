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


