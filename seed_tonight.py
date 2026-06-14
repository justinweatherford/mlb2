"""
seed_tonight.py — Fetch tonight's MLB games + details and refresh team context.

Run once after games end (or during — partial data is fine).
Populates: mlb_games, mlb_play_events, mlb_inning_scores, mlb_game_states,
           then refreshes mlb_team_context.

Usage:
    python seed_tonight.py
    python seed_tonight.py --date 2026-06-12
"""
import argparse
import os
from datetime import datetime

from db.schema import init_db
from mlb.game_store import fetch_and_store_game, fetch_and_store_schedule
from mlb.team_context import refresh_team_context

DB_PATH = os.environ.get("DB_PATH", "kalshi_mlb.db")

HISTORY_DATES = [
    "2026-06-05", "2026-06-06", "2026-06-07",
    "2026-06-08", "2026-06-09", "2026-06-10",
    "2026-06-11",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Date to seed (default: today)")
    parser.add_argument("--history", action="store_true",
                        help="Also fetch the past week of schedule data")
    args = parser.parse_args()

    conn = init_db(DB_PATH)

    dates_to_fetch = ([args.date] if not args.history
                      else HISTORY_DATES + [args.date])
    dates_to_fetch = sorted(set(dates_to_fetch))

    print(f"=== Fetching schedules for {dates_to_fetch} ===")
    all_game_pks = []
    for d in dates_to_fetch:
        r = fetch_and_store_schedule(d, conn=conn)
        status = "OK" if r["fetched"] else "FAIL"
        print(f"  {d}: {status}  games={r['games_seen']}  errors={r['errors']}")
        conn.commit()

    # Fetch detail for all final games on the target date
    final_pks = [
        row["game_pk"]
        for row in conn.execute(
            "SELECT game_pk FROM mlb_games WHERE is_final = 1 AND game_date = ?",
            (args.date,),
        ).fetchall()
    ]
    in_progress_pks = [
        row["game_pk"]
        for row in conn.execute(
            "SELECT game_pk FROM mlb_games WHERE is_final = 0 AND game_date = ?",
            (args.date,),
        ).fetchall()
    ]

    print(f"\n=== Fetching game detail ({len(final_pks)} final, {len(in_progress_pks)} in-progress) ===")
    innings_total = plays_total = 0
    for pk in final_pks + in_progress_pks:
        r = fetch_and_store_game(pk, conn=conn)
        innings_total += r.get("innings_inserted", 0)
        plays_total   += r.get("plays_inserted", 0)
        label = "final" if pk in final_pks else "live"
        game_row = conn.execute(
            "SELECT game_id, final_away_score, final_home_score FROM mlb_games WHERE game_pk = ?",
            (pk,),
        ).fetchone()
        gid = game_row["game_id"] if game_row else pk
        score = (f"{game_row['final_away_score']}-{game_row['final_home_score']}"
                 if game_row and game_row["final_away_score"] is not None else "?-?")
        print(f"  game_pk={pk}  {gid}  [{label}]  score={score}  "
              f"innings={r.get('innings_inserted',0)}  plays={r.get('plays_inserted',0)}")
        conn.commit()

    print(f"\n  Total: innings={innings_total}  plays={plays_total}")

    print("\n=== Refreshing team context ===")
    result = refresh_team_context("2026", conn=conn)
    print(f"  Teams refreshed: {result['team_count']}")
    for t in sorted(result["teams"]):
        print(f"    {t}")
    if result["errors"]:
        print(f"  Errors: {result['errors']}")

    conn.commit()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
