from mlb.game_store import fetch_and_store_schedule, fetch_and_store_game
from mlb.team_context import refresh_team_context
from db.schema import init_db
import os

DB_PATH = os.environ.get("DB_PATH", "kalshi_mlb.db")

SCHEDULE_DATES = [
    "2026-06-05", "2026-06-06", "2026-06-07",
    "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11",
]

# Fetch game-level detail (linescore → inning scores) for this many days back.
# Each game = ~4 API calls. Keep small to avoid hammering the API.
DETAIL_DATES = ["2026-06-09", "2026-06-10", "2026-06-11"]

conn = init_db(DB_PATH)

print("=== Fetching schedules ===")
total_games = 0
for d in SCHEDULE_DATES:
    r = fetch_and_store_schedule(d, conn=conn)
    total_games += r["games_seen"]
    status = "OK" if r["fetched"] else "FAIL"
    print(f"  {d}: {status}  games={r['games_seen']}  errors={r['errors']}")
print(f"  Total games stored: {total_games}\n")

print("=== Fetching game details (linescore/inning data) ===")
detail_pks = [
    r["game_pk"]
    for r in conn.execute(
        "SELECT game_pk FROM mlb_games WHERE is_final=1 AND game_date IN ({})".format(
            ",".join("?" * len(DETAIL_DATES))
        ),
        DETAIL_DATES,
    ).fetchall()
]
print(f"  Games to detail-fetch: {len(detail_pks)}")
innings_total = 0
for pk in detail_pks:
    r = fetch_and_store_game(pk, conn=conn)
    innings_total += r.get("innings_inserted", 0)
print(f"  Inning rows inserted: {innings_total}\n")

print("=== Refreshing team context ===")
result = refresh_team_context("2026", conn=conn)
print(f"  Teams refreshed: {result['team_count']}")
print(f"  Teams: {result['teams']}")
if result["errors"]:
    print(f"  Errors: {result['errors']}")

conn.close()
