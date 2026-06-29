import argparse
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "comeback_context_training_historical_context"

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


def rating_bucket(v: float | None) -> str:
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


def rpg_bucket(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 3.5:
        return "low_lt_3_5"
    if v < 4.5:
        return "mid_3_5_4_5"
    if v < 5.5:
        return "high_4_5_5_5"
    return "very_high_5_5_plus"


def run_prevention_bucket(v: float | None) -> str:
    # Higher run_prevention_proxy = better run prevention.
    if v is None:
        return "missing"
    if v < 45:
        return "weak_lt_45"
    if v < 50:
        return "below_avg_45_50"
    if v < 55:
        return "above_avg_50_55"
    return "strong_55_plus"


def opponent_vulnerability_bucket(v: float | None) -> str:
    # For opponent run prevention, lower = more vulnerable.
    if v is None:
        return "missing"
    if v < 45:
        return "very_vulnerable_lt_45"
    if v < 50:
        return "vulnerable_45_50"
    if v < 55:
        return "solid_50_55"
    return "strong_55_plus"


def deficit_bucket(deficit: int | None) -> str:
    if deficit is None:
        return "missing"
    if deficit <= 1:
        return "down_1"
    if deficit == 2:
        return "down_2"
    if deficit == 3:
        return "down_3"
    if deficit == 4:
        return "down_4"
    return "down_5_plus"


def inning_bucket(inning: int | None) -> str:
    if inning is None:
        return "missing"
    if inning <= 3:
        return "early_1_3"
    if inning <= 6:
        return "middle_4_6"
    return "late_7_plus"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def resolve_context_path(path_arg: str | None, season: str) -> Path:
    candidates = []
    if path_arg:
        candidates.append(Path(path_arg))
    candidates.extend([
        Path("outputs") / "historical_team_context_preview_v2" / f"historical_team_context_{season}_clean.csv",
        Path("outputs") / "historical_team_context_preview" / f"historical_team_context_{season}.csv",
        Path(f"historical_team_context_{season}_clean.csv"),
        Path(f"historical_team_context_{season}.csv"),
    ])
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Could not find historical team context CSV. Pass --context PATH."
    )


def load_context(path: Path) -> dict:
    rows = read_csv_rows(path)
    by_game_team = {}
    for r in rows:
        game_pk = str(r.get("game_pk") or "").strip()
        team = norm_team(r.get("team_abbr"))
        if game_pk and team:
            by_game_team[(game_pk, team)] = r
    return by_game_team


def load_games(conn: sqlite3.Connection, season: str, regular_start: str | None) -> dict:
    rows = conn.execute(
        """
        SELECT
            game_pk,
            game_date,
            away_abbr,
            home_abbr,
            final_away_score,
            final_home_score,
            final_total,
            is_final
        FROM mlb_games
        WHERE substr(game_date, 1, 4) = ?
          AND final_away_score IS NOT NULL
          AND final_home_score IS NOT NULL
          AND away_abbr IS NOT NULL
          AND home_abbr IS NOT NULL
        ORDER BY game_date, game_pk
        """,
        [season],
    ).fetchall()

    games = {}
    for r in rows:
        game_date = str(r[1])
        away = norm_team(r[2])
        home = norm_team(r[3])
        away_score = as_int(r[4])
        home_score = as_int(r[5])

        if regular_start and game_date < regular_start:
            continue
        if away not in MLB_TEAMS or home not in MLB_TEAMS:
            continue
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
            "final_total": as_int(r[6]),
            "is_final": r[7],
        }
    return games


def load_events(conn: sqlite3.Connection, games: dict) -> dict:
    if not games:
        return {}

    game_pks = list(games.keys())
    events_by_game = defaultdict(list)

    # Chunk to avoid massive parameter lists on some SQLite builds.
    chunk_size = 500
    for i in range(0, len(game_pks), chunk_size):
        chunk = game_pks[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)

        rows = conn.execute(f"""
            SELECT
                game_pk,
                event_time,
                inning,
                inning_half,
                event_type,
                description,
                away_score,
                home_score
            FROM mlb_play_events
            WHERE game_pk IN ({placeholders})
              AND away_score IS NOT NULL
              AND home_score IS NOT NULL
              AND inning IS NOT NULL
            ORDER BY game_pk, inning, event_time
        """, chunk).fetchall()

        for r in rows:
            game_pk = str(r[0])
            inning = as_int(r[2])
            away_score = as_int(r[6])
            home_score = as_int(r[7])
            if inning is None or away_score is None or home_score is None:
                continue

            events_by_game[game_pk].append({
                "event_index": len(events_by_game[game_pk]),
                "game_pk": game_pk,
                "event_time": r[1],
                "inning": inning,
                "inning_half": r[3],
                "event_type": r[4],
                "description": r[5],
                "away_score": away_score,
                "home_score": home_score,
            })

    for game_pk in events_by_game:
        events_by_game[game_pk].sort(key=lambda x: (x["inning"], str(x["event_time"] or ""), x["event_index"]))

    return events_by_game


def team_score(event: dict, side: str) -> int:
    return event["away_score"] if side == "away" else event["home_score"]


def opp_score(event: dict, side: str) -> int:
    return event["home_score"] if side == "away" else event["away_score"]


def final_team_score(game: dict, side: str) -> int | None:
    return game["final_away_score"] if side == "away" else game["final_home_score"]


def final_opp_score(game: dict, side: str) -> int | None:
    return game["final_home_score"] if side == "away" else game["final_away_score"]


def summarize(rows: list[dict], group_cols: list[str]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        key = tuple(r.get(c, "missing") or "missing" for c in group_cols)
        groups[key].append(r)

    out = []
    for key, rs in groups.items():
        count = len(rs)

        def rate(col: str) -> float | None:
            vals = [as_int(r.get(col)) for r in rs if as_int(r.get(col)) is not None]
            if not vals:
                return None
            return round(sum(vals) / len(vals), 4)

        final_margins = [as_float(r.get("final_margin")) for r in rs if as_float(r.get("final_margin")) is not None]
        min_deficits = [as_float(r.get("min_deficit_next_2")) for r in rs if as_float(r.get("min_deficit_next_2")) is not None]

        row = {col: val for col, val in zip(group_cols, key)}
        row.update({
            "count": count,
            "eventually_won_rate": rate("eventually_won"),
            "tied_or_led_later_rate": rate("tied_or_led_later"),
            "took_lead_later_rate": rate("took_lead_later"),
            "scored_next_1_inning_rate": rate("scored_next_1_inning"),
            "scored_next_2_innings_rate": rate("scored_next_2_innings"),
            "reduced_deficit_next_2_rate": rate("reduced_deficit_next_2"),
            "within_1_next_2_rate": rate("within_1_next_2"),
            "avg_final_margin": round(sum(final_margins) / len(final_margins), 3) if final_margins else None,
            "avg_min_deficit_next_2": round(sum(min_deficits) / len(min_deficits), 3) if min_deficits else None,
        })
        out.append(row)

    out.sort(key=lambda r: (-(r.get("count") or 0), str([r.get(c) for c in group_cols])))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only comeback training using historical rolling team context.")
    parser.add_argument("--season", default="2025", help="Season to analyze, default 2025.")
    parser.add_argument("--context", default=None, help="Path to historical_team_context clean CSV.")
    parser.add_argument("--regular-start", default="2025-03-27", help="Exclude games before this date. Empty string disables.")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    regular_start = args.regular_start.strip() or None
    context_path = resolve_context_path(args.context, str(args.season))
    context_by_game_team = load_context(context_path)

    conn = sqlite3.connect(args.db)
    games = load_games(conn, str(args.season), regular_start)
    events_by_game = load_events(conn, games)

    spots = []
    seen = set()
    missing_team_context = 0
    missing_opp_context = 0

    for game_pk, events in events_by_game.items():
        game = games.get(game_pk)
        if not game:
            continue

        for i, ev in enumerate(events):
            for side in ("away", "home"):
                team = game["away_abbr"] if side == "away" else game["home_abbr"]
                opp = game["home_abbr"] if side == "away" else game["away_abbr"]

                t_score = team_score(ev, side)
                o_score = opp_score(ev, side)
                deficit = o_score - t_score
                if deficit <= 0:
                    continue

                spot_key = (
                    game_pk, team, ev["inning"], ev.get("inning_half"),
                    ev["away_score"], ev["home_score"], deficit
                )
                if spot_key in seen:
                    continue
                seen.add(spot_key)

                later = events[i + 1:]
                next_1 = [x for x in later if x["inning"] <= ev["inning"] + 1]
                next_2 = [x for x in later if x["inning"] <= ev["inning"] + 2]

                final_ts = final_team_score(game, side)
                final_os = final_opp_score(game, side)
                if final_ts is None or final_os is None:
                    continue

                def later_deficits(seq: list[dict]) -> list[int]:
                    return [opp_score(x, side) - team_score(x, side) for x in seq]

                def scored_in(seq: list[dict]) -> int:
                    return 1 if any(team_score(x, side) > t_score for x in seq) else 0

                all_later_deficits = later_deficits(later)
                next2_deficits = later_deficits(next_2)

                tied_or_led_later = 1 if any(d <= 0 for d in all_later_deficits) else 0
                took_lead_later = 1 if any(d < 0 for d in all_later_deficits) else 0
                min_deficit_next_2 = min(next2_deficits) if next2_deficits else deficit

                team_ctx = context_by_game_team.get((game_pk, team))
                opp_ctx = context_by_game_team.get((game_pk, opp))
                if not team_ctx:
                    missing_team_context += 1
                if not opp_ctx:
                    missing_opp_context += 1

                team_strength = as_float(team_ctx.get("team_strength_proxy")) if team_ctx else None
                offense_form = as_float(team_ctx.get("offense_form_proxy")) if team_ctx else None
                run_prevention = as_float(team_ctx.get("run_prevention_proxy")) if team_ctx else None
                l1_rpg = as_float(team_ctx.get("l1_rpg")) if team_ctx else None
                l5_rpg = as_float(team_ctx.get("l5_rpg")) if team_ctx else None
                l10_rpg = as_float(team_ctx.get("l10_rpg")) if team_ctx else None
                games_before = as_int(team_ctx.get("games_played_before_game")) if team_ctx else None
                ctx_conf = team_ctx.get("confidence_label") if team_ctx else "missing"

                opp_run_prevention = as_float(opp_ctx.get("run_prevention_proxy")) if opp_ctx else None
                opp_l10_allowed = as_float(opp_ctx.get("l10_runs_allowed")) if opp_ctx else None

                row = {
                    "season": str(args.season),
                    "game_date": game["game_date"],
                    "game_pk": game_pk,
                    "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
                    "trailing_team": team,
                    "opponent_team": opp,
                    "side": side,
                    "home_away": "away" if side == "away" else "home",
                    "inning": ev["inning"],
                    "inning_half": ev.get("inning_half"),
                    "inning_bucket": inning_bucket(ev["inning"]),
                    "away_score": ev["away_score"],
                    "home_score": ev["home_score"],
                    "team_score": t_score,
                    "opponent_score": o_score,
                    "deficit": deficit,
                    "deficit_bucket": deficit_bucket(deficit),

                    "final_team_score": final_ts,
                    "final_opp_score": final_os,
                    "final_margin": final_ts - final_os,
                    "eventually_won": 1 if final_ts > final_os else 0,

                    "tied_or_led_later": tied_or_led_later,
                    "took_lead_later": took_lead_later,
                    "scored_next_1_inning": scored_in(next_1),
                    "scored_next_2_innings": scored_in(next_2),
                    "min_deficit_next_2": min_deficit_next_2,
                    "reduced_deficit_next_2": 1 if min_deficit_next_2 < deficit else 0,
                    "within_1_next_2": 1 if min_deficit_next_2 <= 1 else 0,

                    "team_context_confidence": ctx_conf,
                    "team_games_before": games_before,
                    "team_strength_proxy": team_strength,
                    "team_strength_bucket": rating_bucket(team_strength),
                    "offense_form_proxy": offense_form,
                    "offense_form_bucket": rating_bucket(offense_form),
                    "run_prevention_proxy": run_prevention,
                    "run_prevention_bucket": run_prevention_bucket(run_prevention),
                    "l1_rpg": l1_rpg,
                    "l1_rpg_bucket": rpg_bucket(l1_rpg),
                    "l5_rpg": l5_rpg,
                    "l5_rpg_bucket": rpg_bucket(l5_rpg),
                    "l10_rpg": l10_rpg,
                    "l10_rpg_bucket": rpg_bucket(l10_rpg),
                    "opponent_run_prevention_proxy": opp_run_prevention,
                    "opponent_run_prevention_bucket": run_prevention_bucket(opp_run_prevention),
                    "opponent_vulnerability_bucket": opponent_vulnerability_bucket(opp_run_prevention),
                    "opponent_l10_runs_allowed": opp_l10_allowed,
                    "opponent_l10_allowed_bucket": rpg_bucket(opp_l10_allowed),
                }
                spots.append(row)

    fields = [
        "season", "game_date", "game_pk", "game_id", "trailing_team", "opponent_team",
        "side", "home_away", "inning", "inning_half", "inning_bucket",
        "away_score", "home_score", "team_score", "opponent_score",
        "deficit", "deficit_bucket",
        "final_team_score", "final_opp_score", "final_margin", "eventually_won",
        "tied_or_led_later", "took_lead_later",
        "scored_next_1_inning", "scored_next_2_innings",
        "min_deficit_next_2", "reduced_deficit_next_2", "within_1_next_2",
        "team_context_confidence", "team_games_before",
        "team_strength_proxy", "team_strength_bucket",
        "offense_form_proxy", "offense_form_bucket",
        "run_prevention_proxy", "run_prevention_bucket",
        "l1_rpg", "l1_rpg_bucket",
        "l5_rpg", "l5_rpg_bucket",
        "l10_rpg", "l10_rpg_bucket",
        "opponent_run_prevention_proxy", "opponent_run_prevention_bucket",
        "opponent_vulnerability_bucket",
        "opponent_l10_runs_allowed", "opponent_l10_allowed_bucket",
    ]

    suffix = f"_{args.season}_histctx"
    enriched_path = OUT_DIR / f"comeback_spots{suffix}.csv"
    write_csv(enriched_path, spots, fields)

    summaries = {
        f"summary_by_deficit_bucket{suffix}.csv": ["deficit_bucket"],
        f"summary_by_deficit_and_inning{suffix}.csv": ["deficit_bucket", "inning_bucket"],
        f"summary_by_context_confidence{suffix}.csv": ["team_context_confidence"],
        f"summary_by_team_strength{suffix}.csv": ["team_strength_bucket"],
        f"summary_by_offense_form{suffix}.csv": ["offense_form_bucket"],
        f"summary_by_l10_rpg{suffix}.csv": ["l10_rpg_bucket"],
        f"summary_by_opponent_vulnerability{suffix}.csv": ["opponent_vulnerability_bucket"],
        f"summary_by_opponent_l10_allowed{suffix}.csv": ["opponent_l10_allowed_bucket"],
        f"summary_by_deficit_inning_strength{suffix}.csv": ["deficit_bucket", "inning_bucket", "team_strength_bucket"],
        f"summary_by_deficit_inning_offense{suffix}.csv": ["deficit_bucket", "inning_bucket", "offense_form_bucket"],
        f"summary_by_deficit_inning_opponent_vulnerability{suffix}.csv": ["deficit_bucket", "inning_bucket", "opponent_vulnerability_bucket"],
        f"summary_by_deficit_inning_l10{suffix}.csv": ["deficit_bucket", "inning_bucket", "l10_rpg_bucket"],
    }

    summary_tail = [
        "count",
        "eventually_won_rate",
        "tied_or_led_later_rate",
        "took_lead_later_rate",
        "scored_next_1_inning_rate",
        "scored_next_2_innings_rate",
        "reduced_deficit_next_2_rate",
        "within_1_next_2_rate",
        "avg_final_margin",
        "avg_min_deficit_next_2",
    ]

    for filename, cols in summaries.items():
        rows = summarize(spots, cols)
        write_csv(OUT_DIR / filename, rows, cols + summary_tail)

    md = []
    md.append("# Comeback Context Training With Historical Team Context")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append(f"- Season: {args.season}")
    md.append(f"- Regular start cutoff: {regular_start or 'disabled'}")
    md.append(f"- Historical context source: `{context_path}`")
    md.append(f"- Final games loaded: {len(games):,}")
    md.append(f"- Games with events: {len(events_by_game):,}")
    md.append(f"- Comeback spots: {len(spots):,}")
    md.append(f"- Missing trailing-team context spots: {missing_team_context:,}")
    md.append(f"- Missing opponent context spots: {missing_opp_context:,}")
    md.append("")

    sections = [
        ("Deficit + inning", ["deficit_bucket", "inning_bucket"]),
        ("Team context confidence", ["team_context_confidence"]),
        ("Team strength", ["team_strength_bucket"]),
        ("Offense form", ["offense_form_bucket"]),
        ("L10 RPG", ["l10_rpg_bucket"]),
        ("Opponent vulnerability", ["opponent_vulnerability_bucket"]),
        ("Deficit + inning + team strength", ["deficit_bucket", "inning_bucket", "team_strength_bucket"]),
        ("Deficit + inning + opponent vulnerability", ["deficit_bucket", "inning_bucket", "opponent_vulnerability_bucket"]),
    ]

    for title, cols in sections:
        md.append(f"## {title}")
        md.append("")
        for r in summarize(spots, cols)[:16]:
            label = " / ".join(str(r.get(c)) for c in cols)
            md.append(
                f"- {label}: count {r['count']}, win {r['eventually_won_rate']}, "
                f"tie/lead later {r['tied_or_led_later_rate']}, "
                f"score next 2 {r['scored_next_2_innings_rate']}, "
                f"reduce next 2 {r['reduced_deficit_next_2_rate']}"
            )
        md.append("")

    md.append("## Interpretation")
    md.append("")
    md.append("- This is baseball-truth training only.")
    md.append("- It uses historical rolling team context generated before each game date.")
    md.append("- No Kalshi ROI is calculated here.")
    md.append("- Best rebound targets are spots with low final win rate but strong tie/lead, score-next-2, or reduce-deficit rates.")
    md.append("- No DB writes. No live logic changes.")
    md.append("")

    md.append("## Files Written")
    md.append("")
    md.append(f"- {enriched_path.name}")
    for filename in summaries:
        md.append(f"- {filename}")

    summary_path = OUT_DIR / f"comeback_context_summary{suffix}.md"
    summary_path.write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {summary_path}")
    print(f"  {enriched_path}")
    print(f"Games: {len(games):,}")
    print(f"Spots: {len(spots):,}")
    print(f"Missing team context spots: {missing_team_context:,}")
    print(f"Missing opponent context spots: {missing_opp_context:,}")


if __name__ == "__main__":
    main()
