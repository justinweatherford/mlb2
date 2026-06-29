import argparse
import csv
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "pregame_matchup_profile_preview"

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

DEFAULT_REGULAR_START = {
    "2023": "2023-03-30",
    "2024": "2024-03-20",
    "2025": "2025-03-27",
}


def norm_team(team: Any) -> str:
    t = str(team or "").strip().upper()
    return TEAM_NORMALIZE.get(t, t)


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        s = str(value).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        return float(s)
    except Exception:
        return None


def as_int(value: Any) -> int | None:
    f = as_float(value)
    if f is None:
        return None
    return int(round(f))


def rate(num: float, den: float) -> float | None:
    if not den:
        return None
    return round(num / den, 4)


def pct(v: float | None) -> str:
    if v is None:
        return "NA"
    return f"{v * 100:.1f}%"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys = []
        seen = set()
        for r in rows:
            for k in r:
                if k not in seen:
                    keys.append(k)
                    seen.add(k)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def context_path_for_season(season: str) -> Path | None:
    candidates = [
        Path("outputs") / "historical_team_context_preview_v2" / f"historical_team_context_{season}_clean.csv",
        Path("outputs") / "historical_team_context_preview" / f"historical_team_context_{season}.csv",
        Path(f"historical_team_context_{season}_clean.csv"),
        Path(f"historical_team_context_{season}.csv"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_context(season: str) -> tuple[dict, Path | None]:
    path = context_path_for_season(season)
    if not path:
        return {}, None

    rows = read_csv_rows(path)
    by_game_team = {}
    for r in rows:
        game_pk = str(r.get("game_pk") or "").strip()
        team = norm_team(r.get("team_abbr"))
        if game_pk and team:
            by_game_team[(game_pk, team)] = r
    return by_game_team, path


def bucket_rating(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 40:
        return "lt_40"
    if v < 45:
        return "40_45"
    if v < 50:
        return "45_50"
    if v < 55:
        return "50_55"
    if v < 60:
        return "55_60"
    return "60_plus"


def bucket_rpg(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 3.5:
        return "low_lt_3_5"
    if v < 4.5:
        return "mid_3_5_4_5"
    if v < 5.5:
        return "high_4_5_5_5"
    return "very_high_5_5_plus"


def ctx_float(ctx: dict | None, field: str) -> float | None:
    if not ctx:
        return None
    return as_float(ctx.get(field))


def context_confidence(ctx: dict | None) -> str:
    if not ctx:
        return "missing"
    return ctx.get("context_confidence") or ctx.get("confidence") or "unknown"


def load_final_games(conn: sqlite3.Connection, season: str, regular_start: str | None) -> dict:
    rows = conn.execute(
        """
        SELECT
            game_pk, game_date, away_abbr, home_abbr,
            final_away_score, final_home_score, final_total, game_start_time_utc
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

    games = {}
    for r in rows:
        game_date = str(r[1])
        if regular_start and game_date < regular_start:
            continue

        away = norm_team(r[2])
        home = norm_team(r[3])
        if away not in MLB_TEAMS or home not in MLB_TEAMS:
            continue

        away_score = as_int(r[4])
        home_score = as_int(r[5])
        if away_score is None or home_score is None:
            continue

        game_pk = str(r[0])
        games[game_pk] = {
            "game_pk": game_pk,
            "game_date": game_date,
            "away_abbr": away,
            "home_abbr": home,
            "final_away_score": away_score,
            "final_home_score": home_score,
            "final_total": as_int(r[6]) if as_int(r[6]) is not None else away_score + home_score,
            "game_start_time_utc": r[7],
        }
    return games


def load_events(conn: sqlite3.Connection, games: dict) -> dict:
    game_pks = list(games.keys())
    events_by_game = defaultdict(list)
    if not game_pks:
        return events_by_game

    chunk_size = 500
    for i in range(0, len(game_pks), chunk_size):
        chunk = game_pks[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT
                game_pk, event_time, inning, inning_half,
                event_type, description, away_score, home_score
            FROM mlb_play_events
            WHERE game_pk IN ({placeholders})
              AND inning IS NOT NULL
              AND away_score IS NOT NULL
              AND home_score IS NOT NULL
            ORDER BY game_pk, inning, event_time
            """,
            chunk,
        ).fetchall()

        for r in rows:
            game_pk = str(r[0])
            inning = as_int(r[2])
            away_score = as_int(r[6])
            home_score = as_int(r[7])
            if inning is None or away_score is None or home_score is None:
                continue
            events_by_game[game_pk].append({
                "game_pk": game_pk,
                "event_time": r[1],
                "inning": inning,
                "inning_half": r[3],
                "event_type": r[4],
                "description": r[5],
                "away_score": away_score,
                "home_score": home_score,
            })
    return events_by_game


def inning_run_splits(events: list[dict], final_away: int, final_home: int) -> dict:
    # Uses event scores only to derive F5 and post-F5 split.
    # This is allowed for grading actual outcomes only, not model inputs.
    inning_runs = defaultdict(lambda: {"away": 0, "home": 0})
    prev_away = 0
    prev_home = 0

    for ev in sorted(events, key=lambda x: (x["inning"], str(x.get("event_time") or ""))):
        da = max(0, ev["away_score"] - prev_away)
        dh = max(0, ev["home_score"] - prev_home)
        if da:
            inning_runs[ev["inning"]]["away"] += da
        if dh:
            inning_runs[ev["inning"]]["home"] += dh
        prev_away = max(prev_away, ev["away_score"])
        prev_home = max(prev_home, ev["home_score"])

    f5_away = sum(inning_runs[i]["away"] for i in range(1, 6))
    f5_home = sum(inning_runs[i]["home"] for i in range(1, 6))
    f5_total = f5_away + f5_home
    full_total = final_away + final_home
    post5_runs = full_total - f5_total

    return {
        "f5_away": f5_away,
        "f5_home": f5_home,
        "f5_total": f5_total,
        "post5_runs": post5_runs,
    }


def safe_num(v: float | None, default: float = 50.0) -> float:
    return default if v is None else v


def team_pregame_score(team_ctx: dict | None, opp_ctx: dict | None, is_home: bool) -> float:
    # No market data. Simple no-lookahead baseball score from historical context only.
    # 50 is average. Higher is better for a team win/score outlook.
    team_strength = safe_num(ctx_float(team_ctx, "team_strength_proxy"))
    offense = safe_num(ctx_float(team_ctx, "offense_form_proxy"))
    l10 = ctx_float(team_ctx, "l10_rpg")
    run_prev_opp = safe_num(ctx_float(opp_ctx, "run_prevention_proxy"))
    opp_strength = safe_num(ctx_float(opp_ctx, "team_strength_proxy"))

    # If run_prevention_proxy is higher = better defense/prevention, opponent high should hurt scoring.
    l10_component = 0.0 if l10 is None else (l10 - 4.5) * 2.25

    score = (
        0.45 * (team_strength - 50)
        + 0.35 * (offense - 50)
        + 0.20 * (50 - opp_strength)
        + 0.25 * (50 - run_prev_opp)
        + l10_component
        + (1.8 if is_home else -0.5)
    )
    return round(50 + score, 3)


def team_run_projection(team_ctx: dict | None, opp_ctx: dict | None, is_home: bool) -> float:
    offense = safe_num(ctx_float(team_ctx, "offense_form_proxy"))
    l10 = ctx_float(team_ctx, "l10_rpg")
    run_prev_opp = safe_num(ctx_float(opp_ctx, "run_prevention_proxy"))
    opp_strength = safe_num(ctx_float(opp_ctx, "team_strength_proxy"))

    # Rough baseball-only projection, not market-calibrated.
    # Starts near 4.4, then adjusts by offense, recent scoring, opponent prevention/strength, home field.
    proj = 4.4
    proj += (offense - 50) * 0.035
    proj += (50 - run_prev_opp) * 0.03
    proj += (50 - opp_strength) * 0.015
    if l10 is not None:
        proj += (l10 - 4.5) * 0.22
    proj += 0.15 if is_home else -0.05
    return round(max(2.0, min(7.5, proj)), 3)


def confidence_label(edge: float, context_quality: str) -> str:
    if context_quality in {"none", "missing"}:
        return "low_context"
    ae = abs(edge)
    if ae >= 7:
        return "high"
    if ae >= 4:
        return "medium"
    return "low"


def prediction_grade_reason(row: dict) -> str:
    # Heuristic explanation. Uses actual outcome only after grading.
    # This is not a blocker; it is a first-pass diagnostic.
    pred_type = row["prediction_type"]
    correct = row["correct"] == 1

    if correct:
        return "correct"

    if row.get("context_confidence") in {"none", "missing"} or row.get("opponent_context_confidence") in {"none", "missing"}:
        return "thin_context_or_missing_context"

    final_total = as_int(row.get("actual_full_total")) or 0
    projected_total = as_float(row.get("projected_total")) or 0
    f5_total = as_int(row.get("actual_f5_total")) or 0
    post5_runs = as_int(row.get("actual_post5_runs")) or 0
    predicted_winner_score = as_float(row.get("predicted_team_score")) or 50
    model_edge = abs(as_float(row.get("model_edge")) or 0)

    if pred_type == "winner":
        margin = as_int(row.get("actual_margin")) or 0
        if abs(margin) <= 1:
            return "coinflip_close_game"
        if row.get("predicted_home_away") == "away" and row.get("actual_home_won") == 1:
            return "home_field_or_away_underperformed"
        if predicted_winner_score >= 58 and row.get("predicted_team_runs") is not None and as_int(row.get("actual_team_runs")) <= 2:
            return "offense_failed_despite_strong_context"
        if model_edge < 4:
            return "thin_model_edge"
        return "model_missed_team_strength_or_pitching"

    if pred_type == "team_runs_4_plus":
        actual_runs = as_int(row.get("actual_team_runs")) or 0
        if actual_runs == 3:
            return "near_miss_team_total"
        if f5_total <= 3 and actual_runs <= 3:
            return "game_run_environment_came_in_low"
        if actual_runs <= 2:
            return "offense_underperformed"
        return "team_total_model_miss"

    if pred_type == "team_runs_5_plus":
        actual_runs = as_int(row.get("actual_team_runs")) or 0
        if actual_runs == 4:
            return "near_miss_team_total"
        if actual_runs <= 3 and final_total <= 7:
            return "low_run_environment"
        if actual_runs <= 3:
            return "offense_underperformed"
        return "team_total_model_miss"

    if pred_type == "full_total_9_plus":
        actual_yes = row.get("actual_outcome") == 1
        predicted_yes = row.get("predicted_outcome") == 1
        if predicted_yes and not actual_yes:
            if f5_total <= 3:
                return "early_scoring_failed"
            if post5_runs <= 2:
                return "late_scoring_stalled"
            return "total_underperformed_projection"
        if (not predicted_yes) and actual_yes:
            if f5_total >= 6:
                return "early_scoring_explosion"
            if post5_runs >= 5:
                return "late_scoring_explosion"
            return "total_overperformed_projection"

    if pred_type == "f5_total_4_plus":
        actual_yes = row.get("actual_outcome") == 1
        predicted_yes = row.get("predicted_outcome") == 1
        if predicted_yes and not actual_yes:
            return "early_scoring_failed"
        if (not predicted_yes) and actual_yes:
            return "early_scoring_explosion"

    return "unclassified_model_miss"


def summarize_predictions(rows: list[dict], group_cols: list[str]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        key = tuple(str(r.get(c, "missing")) for c in group_cols)
        groups[key].append(r)

    out = []
    for key, rs in groups.items():
        row = {c: v for c, v in zip(group_cols, key)}
        row["count"] = len(rs)
        row["correct"] = sum(as_int(r.get("correct")) or 0 for r in rs)
        row["success_rate"] = rate(row["correct"], row["count"])
        row["avg_model_edge"] = round(sum(abs(as_float(r.get("model_edge")) or 0) for r in rs) / len(rs), 3) if rs else None
        out.append(row)

    out.sort(key=lambda r: (str(r.get("prediction_type", "")), -(r["count"] or 0), str([r.get(c) for c in group_cols])))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only pregame matchup profile preview with no-lookahead context.")
    parser.add_argument("--seasons", nargs="+", default=["2023", "2024", "2025"], help="Seasons to analyze.")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path.")
    parser.add_argument("--winner-edge", type=float, default=3.0, help="Minimum edge for winner prediction.")
    parser.add_argument("--team-runs4-threshold", type=float, default=4.25, help="Projection threshold for team 4+ runs.")
    parser.add_argument("--team-runs5-threshold", type=float, default=4.95, help="Projection threshold for team 5+ runs.")
    parser.add_argument("--full-total-threshold", type=float, default=8.75, help="Projection threshold for full total 9+.")
    parser.add_argument("--f5-total-threshold", type=float, default=4.25, help="Projection threshold for F5 total 4+.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)

    prediction_rows = []
    game_profile_rows = []
    input_health = []

    for season in args.seasons:
        regular_start = DEFAULT_REGULAR_START.get(str(season))
        context, context_path = load_context(str(season))
        games = load_final_games(conn, str(season), regular_start)
        events_by_game = load_events(conn, games)

        season_games_used = 0
        season_predictions = 0

        for game_pk, game in games.items():
            if game_pk not in events_by_game:
                continue

            away = game["away_abbr"]
            home = game["home_abbr"]
            away_ctx = context.get((game_pk, away))
            home_ctx = context.get((game_pk, home))

            if not away_ctx or not home_ctx:
                # still skip because pregame model needs both teams without lookahead
                continue

            split = inning_run_splits(events_by_game[game_pk], game["final_away_score"], game["final_home_score"])

            away_score = team_pregame_score(away_ctx, home_ctx, is_home=False)
            home_score = team_pregame_score(home_ctx, away_ctx, is_home=True)
            away_run_proj = team_run_projection(away_ctx, home_ctx, is_home=False)
            home_run_proj = team_run_projection(home_ctx, away_ctx, is_home=True)
            projected_total = round(away_run_proj + home_run_proj, 3)
            projected_f5_total = round(projected_total * 0.54, 3)

            predicted_winner = home if home_score >= away_score else away
            predicted_loser = away if predicted_winner == home else home
            model_edge = round(abs(home_score - away_score), 3)
            predicted_home_away = "home" if predicted_winner == home else "away"
            actual_winner = home if game["final_home_score"] > game["final_away_score"] else away
            actual_margin = abs(game["final_home_score"] - game["final_away_score"])

            away_conf = context_confidence(away_ctx)
            home_conf = context_confidence(home_ctx)
            combined_conf = "high" if away_conf == "high" and home_conf == "high" else ("medium" if "missing" not in {away_conf, home_conf} else "missing")

            game_profile = {
                "season": season,
                "game_pk": game_pk,
                "game_date": game["game_date"],
                "game_id": f"{away}@{home}",
                "away": away,
                "home": home,
                "away_score_model": away_score,
                "home_score_model": home_score,
                "model_edge": model_edge,
                "predicted_winner": predicted_winner,
                "actual_winner": actual_winner,
                "winner_correct": 1 if predicted_winner == actual_winner else 0,
                "away_run_projection": away_run_proj,
                "home_run_projection": home_run_proj,
                "projected_total": projected_total,
                "projected_f5_total": projected_f5_total,
                "actual_away_runs": game["final_away_score"],
                "actual_home_runs": game["final_home_score"],
                "actual_full_total": game["final_total"],
                "actual_f5_total": split["f5_total"],
                "actual_post5_runs": split["post5_runs"],
                "away_context_confidence": away_conf,
                "home_context_confidence": home_conf,
            }
            game_profile_rows.append(game_profile)
            season_games_used += 1

            # Winner prediction only if edge clears threshold
            if model_edge >= args.winner_edge:
                row = {
                    **game_profile,
                    "prediction_type": "winner",
                    "predicted_team": predicted_winner,
                    "predicted_opponent": predicted_loser,
                    "predicted_home_away": predicted_home_away,
                    "predicted_team_score": home_score if predicted_winner == home else away_score,
                    "predicted_outcome": 1,
                    "actual_outcome": 1 if predicted_winner == actual_winner else 0,
                    "correct": 1 if predicted_winner == actual_winner else 0,
                    "actual_team_runs": game["final_home_score"] if predicted_winner == home else game["final_away_score"],
                    "actual_opponent_runs": game["final_away_score"] if predicted_winner == home else game["final_home_score"],
                    "actual_margin": actual_margin,
                    "actual_home_won": 1 if actual_winner == home else 0,
                    "context_confidence": home_conf if predicted_winner == home else away_conf,
                    "opponent_context_confidence": away_conf if predicted_winner == home else home_conf,
                    "confidence_label": confidence_label(model_edge, combined_conf),
                }
                row["wrong_reason"] = prediction_grade_reason(row)
                prediction_rows.append(row)
                season_predictions += 1

            # Team run projections
            for team, opp, is_home, proj, actual_runs, score_model, conf, opp_conf in [
                (away, home, False, away_run_proj, game["final_away_score"], away_score, away_conf, home_conf),
                (home, away, True, home_run_proj, game["final_home_score"], home_score, home_conf, away_conf),
            ]:
                for pred_type, threshold, actual_threshold in [
                    ("team_runs_4_plus", args.team_runs4_threshold, 4),
                    ("team_runs_5_plus", args.team_runs5_threshold, 5),
                ]:
                    predicted_yes = 1 if proj >= threshold else 0
                    # Include both yes and no predictions if projection is far enough from threshold.
                    edge = abs(proj - threshold)
                    if edge < 0.25:
                        continue

                    actual_yes = 1 if actual_runs >= actual_threshold else 0
                    row = {
                        **game_profile,
                        "prediction_type": pred_type,
                        "predicted_team": team,
                        "predicted_opponent": opp,
                        "predicted_home_away": "home" if is_home else "away",
                        "predicted_team_score": score_model,
                        "predicted_team_runs": proj,
                        "prediction_threshold": threshold,
                        "predicted_outcome": predicted_yes,
                        "actual_outcome": actual_yes,
                        "correct": 1 if predicted_yes == actual_yes else 0,
                        "actual_team_runs": actual_runs,
                        "actual_opponent_runs": game["final_away_score"] if is_home else game["final_home_score"],
                        "model_edge": round(edge, 3),
                        "actual_margin": actual_margin,
                        "actual_home_won": 1 if actual_winner == home else 0,
                        "context_confidence": conf,
                        "opponent_context_confidence": opp_conf,
                        "confidence_label": confidence_label(edge * 5, combined_conf),
                    }
                    row["wrong_reason"] = prediction_grade_reason(row)
                    prediction_rows.append(row)
                    season_predictions += 1

            # Full total 9+ proxy
            total_edge = abs(projected_total - args.full_total_threshold)
            if total_edge >= 0.35:
                predicted_yes = 1 if projected_total >= args.full_total_threshold else 0
                actual_yes = 1 if game["final_total"] >= 9 else 0
                row = {
                    **game_profile,
                    "prediction_type": "full_total_9_plus",
                    "predicted_team": "",
                    "predicted_opponent": "",
                    "predicted_home_away": "",
                    "predicted_team_score": "",
                    "predicted_team_runs": "",
                    "prediction_threshold": args.full_total_threshold,
                    "predicted_outcome": predicted_yes,
                    "actual_outcome": actual_yes,
                    "correct": 1 if predicted_yes == actual_yes else 0,
                    "actual_team_runs": "",
                    "actual_opponent_runs": "",
                    "actual_margin": actual_margin,
                    "actual_home_won": 1 if actual_winner == home else 0,
                    "model_edge": round(total_edge, 3),
                    "context_confidence": combined_conf,
                    "opponent_context_confidence": combined_conf,
                    "confidence_label": confidence_label(total_edge * 4, combined_conf),
                }
                row["wrong_reason"] = prediction_grade_reason(row)
                prediction_rows.append(row)
                season_predictions += 1

            # F5 total 4+ proxy
            f5_edge = abs(projected_f5_total - args.f5_total_threshold)
            if f5_edge >= 0.25:
                predicted_yes = 1 if projected_f5_total >= args.f5_total_threshold else 0
                actual_yes = 1 if split["f5_total"] >= 4 else 0
                row = {
                    **game_profile,
                    "prediction_type": "f5_total_4_plus",
                    "predicted_team": "",
                    "predicted_opponent": "",
                    "predicted_home_away": "",
                    "predicted_team_score": "",
                    "predicted_team_runs": "",
                    "prediction_threshold": args.f5_total_threshold,
                    "predicted_outcome": predicted_yes,
                    "actual_outcome": actual_yes,
                    "correct": 1 if predicted_yes == actual_yes else 0,
                    "actual_team_runs": "",
                    "actual_opponent_runs": "",
                    "actual_margin": actual_margin,
                    "actual_home_won": 1 if actual_winner == home else 0,
                    "model_edge": round(f5_edge, 3),
                    "context_confidence": combined_conf,
                    "opponent_context_confidence": combined_conf,
                    "confidence_label": confidence_label(f5_edge * 5, combined_conf),
                }
                row["wrong_reason"] = prediction_grade_reason(row)
                prediction_rows.append(row)
                season_predictions += 1

        input_health.append({
            "season": season,
            "regular_start": regular_start,
            "context_path": str(context_path) if context_path else "",
            "final_games_loaded": len(games),
            "games_with_events": len(events_by_game),
            "games_used_with_context": season_games_used,
            "prediction_rows": season_predictions,
        })

    # Outputs
    write_csv(OUT_DIR / "pregame_prediction_rows.csv", prediction_rows)
    write_csv(OUT_DIR / "pregame_game_profiles.csv", game_profile_rows)
    write_csv(OUT_DIR / "input_health.csv", input_health)

    summary_by_type = summarize_predictions(prediction_rows, ["prediction_type"])
    summary_by_type_conf = summarize_predictions(prediction_rows, ["prediction_type", "confidence_label"])
    summary_by_season_type = summarize_predictions(prediction_rows, ["season", "prediction_type"])
    summary_by_wrong = summarize_predictions([r for r in prediction_rows if r["correct"] == 0], ["prediction_type", "wrong_reason"])

    write_csv(OUT_DIR / "summary_by_prediction_type.csv", summary_by_type)
    write_csv(OUT_DIR / "summary_by_prediction_type_confidence.csv", summary_by_type_conf)
    write_csv(OUT_DIR / "summary_by_season_prediction_type.csv", summary_by_season_type)
    write_csv(OUT_DIR / "wrong_reason_summary.csv", summary_by_wrong)

    # High confidence misses
    high_conf_misses = [
        r for r in prediction_rows
        if r.get("correct") == 0 and r.get("confidence_label") == "high"
    ]
    high_conf_misses.sort(key=lambda r: (r.get("prediction_type", ""), -(as_float(r.get("model_edge")) or 0)))
    write_csv(OUT_DIR / "high_confidence_misses.csv", high_conf_misses)

    total = len(prediction_rows)
    correct = sum(as_int(r.get("correct")) or 0 for r in prediction_rows)
    overall_rate = rate(correct, total)

    md = []
    md.append("# Pregame Matchup Profile Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append("## No-Lookahead Guardrail")
    md.append("")
    md.append("- Inputs use `historical_team_context_<season>_clean.csv` rows keyed by game/team.")
    md.append("- Those context files are generated before each game date with same-day games excluded.")
    md.append("- Final scores and inning splits are used only to grade predictions after the prediction is made.")
    md.append("- No Vegas/Kalshi market data is used here. This is baseball-truth projection research, not EV.")
    md.append("")
    md.append("## Input Health")
    md.append("")
    for h in input_health:
        md.append(
            f"- {h['season']}: final games {h['final_games_loaded']:,}, games with events {h['games_with_events']:,}, "
            f"games used with context {h['games_used_with_context']:,}, prediction rows {h['prediction_rows']:,}"
        )
    md.append("")
    md.append("## Overall Success")
    md.append("")
    md.append(f"- Predictions graded: {total:,}")
    md.append(f"- Correct: {correct:,}")
    md.append(f"- Success rate: {pct(overall_rate)}")
    md.append("")
    md.append("## Success by Prediction Type")
    md.append("")
    for r in summary_by_type:
        md.append(
            f"- {r['prediction_type']}: {r['correct']}/{r['count']} correct, "
            f"success {pct(r['success_rate'])}, avg edge {r['avg_model_edge']}"
        )
    md.append("")
    md.append("## Common Wrong Reasons")
    md.append("")
    for r in summary_by_wrong[:25]:
        md.append(
            f"- {r['prediction_type']} / {r['wrong_reason']}: {r['count']} misses"
        )
    md.append("")
    md.append("## Interpretation")
    md.append("")
    md.append("- This script is intentionally simple and transparent. It is a first pregame baseball-logic baseline.")
    md.append("- It should reveal which prediction types are even worth improving.")
    md.append("- Wrong-reason labels are heuristic diagnostics, not final truth.")
    md.append("- If a prediction type cannot beat a simple baseline here, it should not become candidate logic yet.")
    md.append("")
    md.append("## Files Written")
    md.append("")
    for name in [
        "pregame_profile_summary.md",
        "input_health.csv",
        "pregame_prediction_rows.csv",
        "pregame_game_profiles.csv",
        "summary_by_prediction_type.csv",
        "summary_by_prediction_type_confidence.csv",
        "summary_by_season_prediction_type.csv",
        "wrong_reason_summary.csv",
        "high_confidence_misses.csv",
    ]:
        md.append(f"- {name}")

    (OUT_DIR / "pregame_profile_summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {OUT_DIR / 'pregame_profile_summary.md'}")
    print(f"Predictions: {total:,}")
    print(f"Correct: {correct:,}")
    print(f"Success rate: {pct(overall_rate)}")


if __name__ == "__main__":
    main()
