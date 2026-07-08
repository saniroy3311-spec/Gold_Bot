"""
main.py — Shiva Sniper Bot v10  (Live Orchestrator)
══════════════════════════════════════════════════════════════════════════════

Orchestrates multiple SymbolRunner instances running concurrently in the
same asyncio event loop (e.g. PAXG/USD:USD and BTC/USD:USD).

WHAT THIS FILE DOES
───────────────────
  1. Loads SYMBOLS list from config.py.
  2. Creates and initializes a SymbolRunner for each symbol.
  3. Launches the shared dashboard HTTP server.
  4. Starts all runners concurrently.
  5. Manages clean shutdown and lifecycle events.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading as _threading

# ── Canonical module imports ───────────────────────────────────────────────────
from config import SYMBOLS, MULTI_SYMBOL_ENABLED
from symbol_runner import SymbolRunner
from infra.telegram import Telegram
from infra.whatsapp import WhatsApp
from infra.journal import Journal
import server as _dashboard
import infra.heartbeat as _heartbeat

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


class ShivaSniperBot:
    def __init__(self) -> None:
        self._telegram = Telegram()
        self._whatsapp = WhatsApp()
        self._journals = {}
        self._runners = []

        # Create independent journals and runners per symbol
        for cfg in SYMBOLS:
            db_path = cfg["db_file"]
            journal = Journal(db_path=db_path)
            self._journals[cfg["id"]] = journal

            runner = SymbolRunner(
                sym_cfg=cfg,
                telegram=self._telegram,
                dashboard=_dashboard,
                journal=journal,
            )
            self._runners.append(runner)

    async def initialize(self) -> None:
        logger.info("═" * 70)
        logger.info("  Shiva Sniper Bot v10 — Multi-Symbol Orchestrator Starting")
        logger.info(f"  Active Symbols: {[cfg['symbol'] for cfg in SYMBOLS]}")
        logger.info("═" * 70)

        # Wire up dashboard server with the dict of journals
        _dashboard.init(self._journals)

        await self._telegram.send(
            "🟢 <b>Shiva Sniper Multi-Symbol Bot Started</b>\n"
            f"Active: <code>{', '.join(cfg['symbol'] for cfg in SYMBOLS)}</code>"
        )

    async def shutdown(self) -> None:
        logger.info("Shutting down orchestrator...")
        try:
            _dashboard.stop()
        except Exception:
            pass

        # Stop all runners concurrently
        await asyncio.gather(*(r.shutdown() for r in self._runners), return_exceptions=True)

        # Close all journals
        for j in self._journals.values():
            try:
                j.close()
            except Exception:
                pass

        try:
            await asyncio.shield(self._telegram.send("🔴 <b>Shiva Sniper Multi-Symbol Bot Stopped</b>"))
        except Exception:
            pass
        logger.info("Shutdown complete.")

    async def run(self) -> None:
        await self.initialize()

        _dashboard.start()
        _start_client_dashboard()

        # Run all symbol engines concurrently
        try:
            await asyncio.gather(*(r.run() for r in self._runners))
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()


def _start_client_dashboard() -> None:
    """
    Launch the client / billing FastAPI dashboard (dashboard/main.py) on port 8080
    in a daemon thread so it doesn't block the async event loop.
    """
    try:
        import uvicorn
        from dashboard.main import app as _client_app

        cfg = uvicorn.Config(
            _client_app,
            host="0.0.0.0",
            port=int(__import__("os").environ.get("CLIENT_DASHBOARD_PORT", "8080")),
            log_level="warning",
        )
        server = uvicorn.Server(cfg)

        def _run():
            import asyncio
            asyncio.run(server.serve())

        t = _threading.Thread(target=_run, daemon=True, name="client-dashboard")
        t.start()
        logger.info("Client dashboard LIVE → http://0.0.0.0:8080")
    except Exception as exc:
        logger.warning(f"[CLIENT DASH] Could not start client dashboard: {exc}")


async def _main() -> None:
    _heartbeat.start(os.path.dirname(os.path.abspath(__file__)))
    bot  = ShivaSniperBot()
    loop = asyncio.get_running_loop()

    def _handle_signal(sig_num: int) -> None:
        for task in asyncio.all_tasks(loop):
            if task.get_name() != "bot_run":
                task.cancel()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, lambda sn=s: _handle_signal(sn))
        except NotImplementedError:
            pass

    run_task = asyncio.create_task(bot.run(), name="bot_run")
    await run_task

if __name__ == "__main__":
    asyncio.run(_main())
