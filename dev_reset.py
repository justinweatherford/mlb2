"""
dev_reset.py — Safely wipe and reinitialize the local dev database.

Usage:
    python dev_reset.py                             # reset kalshi_mlb.db (asks to confirm)
    python dev_reset.py --yes                       # skip confirmation prompt
    python dev_reset.py --reingest transcript.txt   # reset then re-ingest
    python dev_reset.py --db other.db --reingest transcript.txt --yes

Does NOT touch:
    - Streamlit (app.py)
    - Classifier or paper-trading logic
    - Any source files
"""
import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger("dev_reset")


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt + " [y/N] ").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def reset_db(db_path: str) -> None:
    from db.schema import init_db

    p = Path(db_path)
    if p.exists():
        size_kb = p.stat().st_size / 1024
        log.info("Deleting %s (%.1f KB)", p, size_kb)
        p.unlink()
    else:
        log.info("No existing DB at %s — creating fresh.", p)

    log.info("Initializing schema…")
    conn = init_db(db_path)
    conn.close()
    log.info("Schema ready.")


def reingest(db_path: str, transcript_path: str, mode: str) -> None:
    from db.schema import init_db
    from game_state.memory import GameStateMemory
    from ingest import split_transcript, ingest_messages
    from reporting.daily_summary import generate_daily_summary, print_daily_summary
    from trading.fee_calculator import FeeConfig

    log.info("Reading transcript: %s", transcript_path)
    with open(transcript_path, encoding="utf-8") as fh:
        text = fh.read()

    messages = split_transcript(text)
    log.info("Split into %d candidate messages", len(messages))

    conn = init_db(db_path)
    memory = GameStateMemory()
    fee_cfg = FeeConfig()  # uses defaults; override in .env if needed

    stats = ingest_messages(messages, conn, memory, fee_cfg, mode)
    log.info(
        "Done — parsed=%d skipped=%d signals=%d entries=%d "
        "pace_fade_explosions=%d pace_fade_rows=%d",
        stats["parsed"], stats["skipped"], stats["signals"], stats["entries"],
        stats["pace_fade_explosions"], stats["pace_fade_rows"],
    )

    if stats["failures"]:
        log.info("%d unrecognised/failed messages (showing first 5):", len(stats["failures"]))
        for f in stats["failures"][:5]:
            log.info("  [%d] %s — %s", f["index"], f["snippet"][:60], f["reason"])

    summary = generate_daily_summary(conn)
    print_daily_summary(summary)
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wipe and reinitialize the local dev database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python dev_reset.py                              # reset kalshi_mlb.db (confirms first)
  python dev_reset.py --yes                        # reset without confirmation
  python dev_reset.py --reingest transcript.txt    # reset then re-ingest
  python dev_reset.py --db other.db --reingest transcript.txt --yes
        """,
    )
    parser.add_argument("--db", default="kalshi_mlb.db", metavar="PATH",
                        help="SQLite DB path (default: kalshi_mlb.db)")
    parser.add_argument("--reingest", metavar="FILE",
                        help="Transcript file to ingest after reset")
    parser.add_argument("--mode", choices=["realistic", "optimistic"], default="realistic",
                        help="Paper-trading mode for reingest (default: realistic)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose log output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    db_path = args.db
    p = Path(db_path)

    print()
    print("=" * 56)
    print("  dev_reset.py — Local DB Reset")
    print("=" * 56)
    if p.exists():
        size_kb = p.stat().st_size / 1024
        print(f"  DB:      {p.resolve()}")
        print(f"  Size:    {size_kb:.1f} KB")
        print(f"  Action:  DELETE and reinitialize schema")
    else:
        print(f"  DB:      {p.resolve()}")
        print(f"  Action:  Create fresh (no existing file)")
    if args.reingest:
        rf = Path(args.reingest)
        if not rf.exists():
            print(f"\n  ERROR: Transcript file not found: {rf}", file=sys.stderr)
            sys.exit(1)
        print(f"  Reingest: {rf}")
        print(f"  Mode:     {args.mode}")
    print("=" * 56)
    print()

    if not args.yes and not _confirm("Proceed?"):
        print("Aborted.")
        sys.exit(0)

    reset_db(db_path)

    if args.reingest:
        reingest(db_path, args.reingest, args.mode)
    else:
        print()
        print("Reset complete. Ingest data with:")
        print(f"  python ingest.py <transcript.txt> --db {db_path} --summary")
        print(f"  python dev_reset.py --reingest transcript.txt --db {db_path}")
        print()


if __name__ == "__main__":
    main()
