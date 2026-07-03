# GoldBot Migration — Bot-v10 → Delta India Gold (PAXG) Perpetuals

Status: **implemented in this repo**. Read the checklist at the bottom before going live.

---

## 1. What changed and why

| Area | Before | After | File(s) |
|---|---|---|---|
| EMA lengths | Hardcoded default 200/50 | Default **60/20**, still `.env`-overridable | `config.py` |
| Indicator lengths (ATR/DI/ADX/RSI) | Hardcoded literals in `config.py` (env vars existed but were ignored) | Fully `.env`-driven | `config.py` |
| `BREAKOUT_BUFFER_PTS` | **Bug**: hardcoded to `0` in two places — the `.env` value had zero effect | Single `.env`-driven definition | `config.py` |
| `COMMISSION_PCT` | Hardcoded `0.05/100` | `.env`-driven via `COMMISSION_PCT_BPS` | `config.py` |
| Body-size filter | Live engine: always on, no toggle. Legacy engine: hardcoded off | `FILTER_BODY_ENABLED` toggle, consistent in both engines, default **true** (matches new Pine script's `bodyConfirm`) | `indicators/engine.py`, `strategy_logic.py`, `config.py` |
| Instrument | BTCUSD-only, `0.001 BTC/lot` hardcoded everywhere | `SYMBOL`/`BASE_ASSET_LABEL` generic; `contract_value` auto-resolved from Delta's exchange metadata at runtime (or `.env` override) | `config.py`, `orders/manager.py` |
| Position sizing | **Static only** — one-time `POSITION_BTC_SIZE → lots` conversion at boot, never touches live equity | New `POSITION_SIZE_MODE=risk` path: qty computed **fresh at every signal** from live equity × `RISK_PCT_PER_TRADE`, mirroring the new Pine script's `qty = riskAmount / stopDist`, corrected for Delta's lot/contract-value units | `risk/lot_sizing.py` (`calc_qty_from_risk`), `orders/manager.py` (`get_equity_usd`), `main.py` |
| P&L math | `calc_gross_pl`/`calc_real_pl`/`calc_pl_breakdown` hardcoded `× 0.001` (BTCUSD-only) | Generalized `contract_value` parameter, correct for any Delta linear perp (BTC, PAXG, etc.) | `risk/calculator.py` |
| Trail stage upgrade / breakeven | **Bar-close only** (deliberate Pine-parity fix from a prior session) | New `TRAIL_STAGE_UPGRADE_MODE` toggle. Default **`intrabar`** per this request — stage upgrades and BE now also react on the *running* (unclosed) candle. Set to `bar_close` to restore the original Pine-exact timing | `monitor/trail_loop.py`, `config.py` |
| Dashboard | Broken — `server.py` looked for `dashboard.html` at repo root, which didn't exist (the 206 KB `dashboard/index.html` belongs to a separate white-label client-billing product, untouched) | New clean **single-screen** dashboard at repo root, backed by a new `/api/live_state` endpoint | `dashboard.html` (new), `server.py`, `main.py` |
| Qty/close consistency | Exchange orders used a module-level `ALERT_QTY` constant; `main.py`'s `self._qty_lots` was a separate, potentially-diverging value | `place_entry(qty=...)` now takes an explicit qty and caches it; `close_position()` always closes that exact cached qty (`self._current_qty`), never a stale constant | `orders/manager.py`, `main.py` |

**Not changed (explicitly out of scope):**
- `execution.py` — confirmed dead code, not on the live import path (`main.py` uses `orders/manager.py` + `monitor/trail_loop.py` instead). Left as-is, flagged in code.
- `backtest.py`, `phase2/*` — offline/backtest tools using the static sizing model. Rewiring them for equity-curve-aware sizing is a separate, larger feature.
- The 5-stage trail engine's stage geometry itself (trigger/points/offset multipliers) — untouched, it's already more sophisticated than the new Pine script's flat trail.
- `dashboard/` (the FastAPI "The Greeks" white-label client-billing app) — a separate product, untouched except for scrubbing one hardcoded password default.

---

## 2. Critical: contract value / position sizing on gold

Delta India's BTCUSD contract is **linear, USD-margined**: `contract_value` (base-asset units per lot) doubles as both the lot-size conversion factor and the P&L-per-point multiplier. The bot now resolves this automatically:

```
OrderManager.initialize()
  → ccxt.load_markets()
  → market["contractSize"]  (or Delta's raw info["contract_value"])
  → self.contract_value
```

**This is the single most important number in the whole system.** If it's wrong, every position is sized wrong by whatever factor you're off by. On boot, the bot logs it loudly:

```
[OM] contract_value resolved from exchange for PAXG/USD:USD = <value>
```

If you ever see the fallback warning instead —

```
[OM] ⚠️ Could not resolve contract_value ... falling back to legacy BTCUSD default (0.001)
```

— **do not trade live** until you've confirmed the real PAXGUSD contract value (check Delta's `/v2/products` API or the ccxt market dict directly) and either fixed the resolution or set `CONTRACT_VALUE_OVERRIDE` in `.env` to the confirmed value.

**Paper-test this specifically before going live on gold**: place one small entry, check the dashboard's "Contract Value" card against Delta's own position page, and confirm the P&L number the bot reports for that trade matches Delta's own.

---

## 3. New architecture

```
                         ┌─────────────────────┐
                         │   config.py (.env)   │  ← single source of truth
                         │  SYMBOL, EMA 60/20,  │     for every tunable
                         │  POSITION_SIZE_MODE, │
                         │  TRAIL_STAGE_MODE …  │
                         └──────────┬───────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
   ┌────────────────┐    ┌──────────────────┐    ┌────────────────────┐
   │ indicators/     │    │ orders/manager.py │    │ monitor/            │
   │ engine.py       │    │ ─ contract_value  │    │ trail_loop.py       │
   │ ─ EMA 60/20     │    │   auto-resolved   │    │ ─ 5-stage trail     │
   │ ─ body filter   │    │ ─ get_equity_usd()│    │ ─ TRAIL_STAGE_      │
   │   toggle        │    │ ─ place_entry(qty)│    │   UPGRADE_MODE      │
   └────────┬────────┘    │ ─ close_position  │    │   (intrabar/        │
            │             │   (cached qty)    │    │    bar_close)       │
            ▼             └─────────┬─────────┘    └──────────┬──────────┘
   ┌────────────────┐               │                         │
   │ strategy/       │              │                         │
   │ signal.py       │              ▼                         │
   │ (signal eval)   │    ┌──────────────────┐                │
   └────────┬────────┘    │ risk/lot_sizing.py│                │
            │             │ calc_qty_from_risk│                │
            ▼             │ (equity × risk% / │                │
   ┌────────────────┐     │  stopDist × cv)   │                │
   │   main.py       │◄───┴──────────────────┘                │
   │ ShivaSniperBot  │◄──────────────────────────────────────┘
   │ orchestrates all│
   │ of the above    │──────► infra/journal.py (SQLite/Turso/Postgres)
   └────────┬────────┘──────► infra/telegram.py, whatsapp.py
            │
            ▼
   ┌────────────────┐
   │  server.py      │──────► dashboard.html (single screen, polls every 5s)
   │  /api/live_state│         Position · P&L · Risk&Sizing · Regime · Filters
   │  /api/summary   │         · Recent Trades
   │  /api/position  │
   │  /api/trades    │
   └────────────────┘
```

**Data flow for one bar close:**
1. `main.py._on_bar_close()` → `indicators.engine.compute()` → indicator snapshot (EMA 60/20, ATR, ADX, RSI, filters).
2. Snapshot pushed to `server.update_live_state()` — dashboard reflects it within 5s.
3. `strategy.signal.evaluate()` → entry/exit decision.
4. If entering: `calc_levels()` → SL/TP. If `POSITION_SIZE_MODE=risk`: `get_equity_usd()` → `calc_qty_from_risk()` → qty. Else: static `self._qty_lots`.
5. `order_mgr.place_entry(qty=...)` → fill → qty cached on the manager.
6. `monitor.trail_loop.TrailMonitor` owns the trade from here: stage upgrades (bar-close or intrabar per `TRAIL_STAGE_UPGRADE_MODE`), breakeven, trail SL, exits.
7. On exit: `order_mgr.close_position()` closes the exact cached qty; `calc_gross_pl(..., contract_value=...)` computes correct P&L; journal + Telegram + dashboard all updated.

---

## 4. New / changed `.env` variables

```bash
# Instrument — change these two to switch BTC ↔ gold
SYMBOL=PAXG/USD:USD
BASE_ASSET_LABEL=PAXG
CONTRACT_VALUE_OVERRIDE=0          # 0 = auto-detect (recommended)

# Position sizing
POSITION_SIZE_MODE=risk            # static | risk
RISK_PCT_PER_TRADE=0.4
MIN_QTY_LOTS=1
MAX_QTY_LOTS=0                     # 0 = unlimited

# Indicators — now all variable, EMA defaults to 60/20
EMA_TREND_LEN=60
EMA_FAST_LEN=20
ATR_LEN=14
DI_LEN=14
ADX_SMOOTH=14
ADX_EMA=5
RSI_LEN=14

# Body filter
FILTER_BODY_ENABLED=true

# Fixed bugs — now actually take effect
BREAKOUT_BUFFER_PTS=0
COMMISSION_PCT_BPS=5               # 5 bps = 0.05%
WS_RECONNECT_SEC=5

# Trail timing
TRAIL_STAGE_UPGRADE_MODE=intrabar  # intrabar | bar_close
```

Full reference: see the updated `.env.example` in this repo.

---

## 5. Single-screen dashboard

`dashboard.html` (repo root) — served automatically by `server.py` at `http://<vps-ip>:<DASHBOARD_PORT>/`. No separate deploy step.

Cards: **Position** (side/entry/qty/SL/TP/stage progress bar) · **P&L** (today/all-time/win-rate) · **Risk & Sizing** (mode, live equity, contract value, last qty) · **Market Regime** (close/EMA/ADX/RSI/regime) · **Entry Filters** (ATR/VOL/BODY pass-fail pills) · **Recent Trades** table. Polls every 5 seconds.

Backed by a new `/api/live_state` endpoint fed by `main.py` after every bar close and every risk-sizing calculation — this data is ephemeral (in-memory only), not persisted.

---

## 6. Checklist before running live on gold

- [ ] Confirm the exact ccxt symbol string Delta India uses for PAXG perpetuals (`SYMBOL` in `.env`) — verify via `exchange.load_markets()` output, don't assume `PAXG/USD:USD` is exactly right without checking.
- [ ] Boot the bot and check the log line `[OM] contract_value resolved from exchange for <SYMBOL> = <value>` — confirm this against Delta's own contract spec page.
- [ ] If sizing mode is `risk`: confirm `get_equity_usd()` returns a sane number (check the dashboard's "Equity" card against your actual Delta wallet balance).
- [ ] Place one small manual test trade (or let the bot take one) and verify the dashboard's P&L for that trade matches Delta's own trade history exactly.
- [ ] Decide `TRAIL_STAGE_UPGRADE_MODE`: `intrabar` (faster stage capture, diverges from Pine) vs `bar_close` (Pine-exact, slower to react). This was explicitly requested as `intrabar` — confirm that's still what you want after seeing it run.
- [ ] Rotate the Delta API key/secret, Telegram token, WhatsApp token, dashboard password, and Turso token that were visible in this conversation — treat all of them as compromised.
- [ ] Run in `DELTA_TESTNET=true` first if Delta India's testnet supports PAXG perpetuals; if not, start with `MIN_QTY_LOTS`/`MAX_QTY_LOTS` set tight and `RISK_PCT_PER_TRADE` low (e.g. 0.1–0.2%) for the first live sessions.
