import argparse
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "historical_team_context_preview_v2"

MLB_TEAMS = {
    "ARI", "AZ", "ATL", "BAL", "BOS", "CHC", "CIN", "CLE", "COL",
    "CWS", "CHW", "DET", "HOU", "KC", "KCR", "LAA", "LAD", "MIA",
    "MIL", "MIN", "NYM", "NYY", "ATH", "OAK", "PHI", "PIT", "SD",
    "SEA", "SF", "STL", "TB", "TEX", "TOR", "WSN", "WSH",
}

TEAM_NORMALIZE = {
    "ARI": "AZ",
    "KCR": "KC",
    "CHW": "CWS",
    "OAK": "ATH",
    "WSH": "WSN",
}


def norm_team(team: Any) -> str:
    t = str(team or "").strip().upper()
    return TEAM_NORMALIZE.get(t, t)


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        s = str(value).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        return int(round(float(s)))
    except Exception:
        return None


def avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def rolling_avg(history: list[dict], key: str, n: int) -> float | None:
    vals = [g[key] for g in history[-n:] if g.get(key) is not None]
    return avg(vals)


def rolling_win_pct(history: list[dict], n: int) -> float | None:
    vals = [1 if g["won"] else 0 for g in history[-n:]]
    return avg(vals)


def safe_round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_final_games(conn: sqlite3.Connection, season: str, regular_start: str | None, include_postseason: bool) -> tuple[list[dict], list[dict]]:
    rows = conn.execute(
        """
        SELECT
            game_pk,
            game_date,
            away_abbr,
            home_abbr,
            final_away_score,
            final_home_score,
            game_start_time_utc
        FROM mlb_games
        WHERE substr(game_date, 1, 4) = ?
          AND final_away_score IS NOT NULL
          AND final_home_score IS NOT NULL
          AND away_abbr IS NOT NULL
          AND home_abbr IS NOT NULL
        ORDER BY game_date, COALESCE(game_start_time_utc, ''), game_pk
        """,
        [season],
    ).fetchall()

    games = []
    excluded = []

    for r in rows:
        game_date = str(r[1])
        away = norm_team(r[2])
        home = norm_team(r[3])
        away_score = as_int(r[4])
        home_score = as_int(r[5])

        reason = None
        if away_score is None or home_score is None:
            reason = "missing_score"
        elif away not in MLB_TEAMS or home not in MLB_TEAMS:
            reason = "non_mlb_team"
        elif regular_start and game_date < regular_start:
            reason = "before_regular_start"

        if reason:
            excluded.append({
                "game_pk": str(r[0]),
                "game_date": game_date,
                "away_abbr": away,
                "home_abbr": home,
                "reason": reason,
            })
            continue

        games.append({
            "game_pk": str(r[0]),
            "game_date": game_date,
            "away_abbr": away,
            "home_abbr": home,
            "final_away_score": away_score,
            "final_home_score": home_score,
            "game_start_time_utc": r[6],
        })

    return games, excluded


def build_context_row(game: dict, team: str, opponent: str, side: str, history: list[dict], league_avg_rpg: float) -> dict:
    games_played = len(history)
    wins = sum(1 for g in history if g["won"])
    losses = games_played - wins

    runs_for = [g["runs_for"] for g in history]
    runs_allowed = [g["runs_allowed"] for g in history]
    run_diffs = [g["run_diff"] for g in history]

    win_pct = (wins / games_played) if games_played else None
    rpg = avg(runs_for)
    rapg = avg(runs_allowed)
    run_diff_pg = avg(run_diffs)

    l1_rpg = rolling_avg(history, "runs_for", 1)
    l5_rpg = rolling_avg(history, "runs_for", 5)
    l10_rpg = rolling_avg(history, "runs_for", 10)

    l1_allowed = rolling_avg(history, "runs_allowed", 1)
    l5_allowed = rolling_avg(history, "runs_allowed", 5)
    l10_allowed = rolling_avg(history, "runs_allowed", 10)

    l5_win_pct = rolling_win_pct(history, 5)
    l10_win_pct = rolling_win_pct(history, 10)

    if games_played == 0:
        team_strength_proxy = 50.0
        offense_form_proxy = 50.0
        run_prevention_proxy = 50.0
        confidence_label = "none"
        notes = "no_prior_games"
    else:
        wp_component = ((win_pct or 0.5) - 0.5) * 40.0
        rd_component = (run_diff_pg or 0.0) * 3.0
        team_strength_proxy = clamp(50.0 + wp_component + rd_component)

        offense_source = l10_rpg if l10_rpg is not None else rpg
        allowed_source = l10_allowed if l10_allowed is not None else rapg

        offense_form_proxy = clamp(50.0 + ((offense_source or league_avg_rpg) - league_avg_rpg) * 6.0)
        run_prevention_proxy = clamp(50.0 - ((allowed_source or league_avg_rpg) - league_avg_rpg) * 6.0)

        if games_played >= 30:
            confidence_label = "high"
        elif games_played >= 10:
            confidence_label = "medium"
        else:
            confidence_label = "low"

        notes = ""

    return {
        "game_date": game["game_date"],
        "game_pk": game["game_pk"],
        "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
        "team_abbr": team,
        "opponent_abbr": opponent,
        "side": side,
        "games_played_before_game": games_played,
        "wins_before_game": wins,
        "losses_before_game": losses,
        "win_pct_before_game": safe_round(win_pct),
        "season_rpg_before_game": safe_round(rpg),
        "season_runs_allowed_before_game": safe_round(rapg),
        "season_run_diff_pg_before_game": safe_round(run_diff_pg),
        "l1_rpg": safe_round(l1_rpg),
        "l5_rpg": safe_round(l5_rpg),
        "l10_rpg": safe_round(l10_rpg),
        "l1_runs_allowed": safe_round(l1_allowed),
        "l5_runs_allowed": safe_round(l5_allowed),
        "l10_runs_allowed": safe_round(l10_allowed),
        "l5_win_pct": safe_round(l5_win_pct),
        "l10_win_pct": safe_round(l10_win_pct),
        "league_avg_rpg_before_game": safe_round(league_avg_rpg),
        "team_strength_proxy": safe_round(team_strength_proxy),
        "offense_form_proxy": safe_round(offense_form_proxy),
        "run_prevention_proxy": safe_round(run_prevention_proxy),
        "confidence_label": confidence_label,
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only historical rolling team context preview v2.")
    parser.add_argument("--season", default="2025", help="Season to analyze, default 2025.")
    parser.add_argument("--regular-start", default="2025-03-27", help="Exclude games before this date. Use empty string to disable.")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    regular_start = args.regular_start.strip() or None

    conn = sqlite3.connect(args.db)
    games, excluded = load_final_games(conn, str(args.season), regular_start, include_postseason=True)

    by_date = defaultdict(list)
    for g in games:
        by_date[g["game_date"]].append(g)

    team_history: dict[str, list[dict]] = defaultdict(list)
    league_runs_before = 0
    league_team_games_before = 0
    context_rows = []

    for game_date in sorted(by_date.keys()):
        date_games = by_date[game_date]
        league_avg_rpg = (league_runs_before / league_team_games_before) if league_team_games_before else 4.5

        for g in date_games:
            away = g["away_abbr"]
            home = g["home_abbr"]

            context_rows.append(build_context_row(g, away, home, "away", team_history[away], league_avg_rpg))
            context_rows.append(build_context_row(g, home, away, "home", team_history[home], league_avg_rpg))

        for g in date_games:
            away = g["away_abbr"]
            home = g["home_abbr"]
            away_score = g["final_away_score"]
            home_score = g["final_home_score"]

            team_history[away].append({
                "game_pk": g["game_pk"],
                "game_date": g["game_date"],
                "opponent": home,
                "side": "away",
                "runs_for": away_score,
                "runs_allowed": home_score,
                "run_diff": away_score - home_score,
                "won": away_score > home_score,
            })
            team_history[home].append({
                "game_pk": g["game_pk"],
                "game_date": g["game_date"],
                "opponent": away,
                "side": "home",
                "runs_for": home_score,
                "runs_allowed": away_score,
                "run_diff": home_score - away_score,
                "won": home_score > away_score,
            })

            league_runs_before += away_score + home_score
            league_team_games_before += 2

    fieldnames = [
        "game_date", "game_pk", "game_id", "team_abbr", "opponent_abbr", "side",
        "games_played_before_game", "wins_before_game", "losses_before_game",
        "win_pct_before_game", "season_rpg_before_game", "season_runs_allowed_before_game",
        "season_run_diff_pg_before_game",
        "l1_rpg", "l5_rpg", "l10_rpg",
        "l1_runs_allowed", "l5_runs_allowed", "l10_runs_allowed",
        "l5_win_pct", "l10_win_pct",
        "league_avg_rpg_before_game",
        "team_strength_proxy", "offense_form_proxy", "run_prevention_proxy",
        "confidence_label", "notes",
    ]

    context_path = OUT_DIR / f"historical_team_context_{args.season}_clean.csv"
    write_csv(context_path, context_rows, fieldnames)

    excluded_path = OUT_DIR / f"excluded_games_{args.season}.csv"
    write_csv(excluded_path, excluded, ["game_pk", "game_date", "away_abbr", "home_abbr", "reason"])

    latest_rows = []
    for team, hist in sorted(team_history.items()):
        if not hist:
            continue
        games_played = len(hist)
        wins = sum(1 for g in hist if g["won"])
        losses = games_played - wins
        rpg = avg([g["runs_for"] for g in hist])
        rapg = avg([g["runs_allowed"] for g in hist])
        rd = avg([g["run_diff"] for g in hist])
        latest_rows.append({
            "team_abbr": team,
            "games_played": games_played,
            "wins": wins,
            "losses": losses,
            "win_pct": safe_round(wins / games_played if games_played else None),
            "runs_per_game": safe_round(rpg),
            "runs_allowed_per_game": safe_round(rapg),
            "run_diff_per_game": safe_round(rd),
            "l5_rpg": safe_round(rolling_avg(hist, "runs_for", 5)),
            "l10_rpg": safe_round(rolling_avg(hist, "runs_for", 10)),
            "l5_runs_allowed": safe_round(rolling_avg(hist, "runs_allowed", 5)),
            "l10_runs_allowed": safe_round(rolling_avg(hist, "runs_allowed", 10)),
        })

    latest_path = OUT_DIR / f"team_context_latest_{args.season}_clean.csv"
    write_csv(
        latest_path,
        latest_rows,
        [
            "team_abbr", "games_played", "wins", "losses", "win_pct",
            "runs_per_game", "runs_allowed_per_game", "run_diff_per_game",
            "l5_rpg", "l10_rpg", "l5_runs_allowed", "l10_runs_allowed",
        ],
    )

    confidence_counts = defaultdict(int)
    for r in context_rows:
        confidence_counts[r["confidence_label"]] += 1

    summary_path = OUT_DIR / f"historical_team_context_summary_{args.season}_clean.md"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# Historical Team Context Preview v2 Clean\n\n")
        f.write(f"Generated: {datetime.utcnow().isoformat()} UTC\n\n")
        f.write(f"- Season: {args.season}\n")
        f.write(f"- Regular start cutoff: {regular_start or 'disabled'}\n")
        f.write(f"- Final games included: {len(games):,}\n")
        f.write(f"- Excluded games: {len(excluded):,}\n")
        f.write(f"- Context rows written: {len(context_rows):,}\n")
        f.write(f"- Teams: {len(team_history):,}\n\n")

        f.write("## Confidence Counts\n\n")
        for k in ["none", "low", "medium", "high"]:
            f.write(f"- {k}: {confidence_counts[k]:,}\n")

        f.write("\n## Top Final Team Context Rows\n\n")
        for r in sorted(latest_rows, key=lambda x: x["run_diff_per_game"] or 0, reverse=True)[:15]:
            f.write(
                f"- {r['team_abbr']}: W-L {r['wins']}-{r['losses']}, "
                f"R/G {r['runs_per_game']}, RA/G {r['runs_allowed_per_game']}, "
                f"RD/G {r['run_diff_per_game']}\n"
            )

        f.write("\n## Interpretation\n\n")
        f.write("- This version excludes non-MLB team abbreviations and games before the regular-start cutoff.\n")
        f.write("- It uses only games completed before the game date.\n")
        f.write("- Same-day games are excluded from pregame context to avoid lookahead.\n")
        f.write("- No DB writes. No live logic changes.\n\n")

        f.write("## Files Written\n\n")
        f.write(f"- {context_path.name}\n")
        f.write(f"- {latest_path.name}\n")
        f.write(f"- {excluded_path.name}\n")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {summary_path}")
    print(f"  {context_path}")
    print(f"  {latest_path}")
    print(f"  {excluded_path}")
    print(f"Included games: {len(games):,}")
    print(f"Excluded games: {len(excluded):,}")
    print(f"Teams: {len(team_history):,}")
    print(f"Context rows: {len(context_rows):,}")
    print("Confidence:", dict(confidence_counts))


if __name__ == "__main__":
    main()
