"""
main.py — Entry point for the live Discord feed scanner.

Usage:
    python main.py             # live mode
    python main.py --dry-run   # parse/classify but never open positions

Requires a .env file (copy .env.example, fill in DISCORD_TOKEN and DISCORD_CHANNEL_ID).
For offline testing use:  python ingest.py  or  streamlit run app.py
"""
import argparse
import logging
import sys

from config import load_config, validate_for_discord
from db.schema import init_db
from game_state.memory import GameStateMemory
from discord_listener.listener import KalshiMLBClient


def main():
    parser = argparse.ArgumentParser(
        description="Kalshi MLB live feed scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and classify messages but do not open paper positions.",
    )
    args = parser.parse_args()

    # ── Load and validate config ─────────────────────────────────────────────
    cfg = load_config()
    if args.dry_run:
        cfg.dry_run = True

    try:
        validate_for_discord(cfg)
    except ValueError:
        # validate_for_discord already printed the human-readable errors
        sys.exit(1)

    # ── Logging ──────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("kalshi_mlb.log"),
        ],
    )
    log = logging.getLogger(__name__)

    # ── Startup sequence ─────────────────────────────────────────────────────
    log.info("[STARTUP] Kalshi MLB feed scanner")
    if cfg.dry_run:
        log.info("[STARTUP] DRY-RUN mode — positions will NOT be opened")

    log.info("[STARTUP] Initializing DB: %s", cfg.db_path)
    try:
        conn = init_db(cfg.db_path)
    except Exception as exc:
        log.error("[STARTUP] DB init failed: %s", exc)
        sys.exit(1)
    log.info("[STARTUP] DB ready")

    log.info("[STARTUP] Initializing game state memory")
    memory = GameStateMemory()
    log.info("[STARTUP] Memory ready")

    log.info("[STARTUP] Connecting to Discord")
    client = KalshiMLBClient(cfg, conn, memory)

    try:
        client.run(cfg.discord_token)
    except KeyboardInterrupt:
        log.info("[SHUTDOWN] Interrupted by user")
    except Exception as exc:
        log.error("[SHUTDOWN] Fatal error: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        conn.close()
        log.info("[SHUTDOWN] DB connection closed")


if __name__ == "__main__":
    main()
