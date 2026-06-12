from mlb.game_store import fetch_and_store_schedule
from mlb.team_context import refresh_team_context

dates = [
    "2026-06-05", "2026-06-06", "2026-06-07",
    "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11",
]

total_games = 0
for d in dates:
    r = fetch_and_store_schedule(d)
    total_games += r["games_seen"]
    status = "OK" if r["fetched"] else "FAIL"
    print(f"{d}: {status}  games={r['games_seen']}  errors={r['errors']}")

print(f"\nTotal games stored: {total_games}")

print("\nRefreshing team context...")
result = refresh_team_context("2026")
print(f"Teams refreshed: {result['team_count']}")
print(f"Teams: {result['teams']}")
if result["errors"]:
    print(f"Errors: {result['errors']}")
