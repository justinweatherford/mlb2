import argparse
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "comeback_context_training_preview"


TEAM_COL_CANDIDATES = ["team_abbr", "team", "abbr", "team_code"]
DATE_COL_CANDIDATES = ["game_date", "date", "as_of_date", "context_date", "snapshot_date"]

METRIC_CANDIDATES = {
    "team_strength": ["team_strength_rating", "overall_context_score"],
    "offense": ["offense_rating", "season_offense_rating", "f5_offense_rating"],
    "bullpen_risk": ["bullpen_risk_rating"],
    "l1_rpg": ["l1_rpg", "l1_runs_per_game", "runs_per_game_l1"],
    "l5_rpg": ["l5_rpg", "l5_runs_per_game", "runs_per_game_l5"],
    "l10_rpg": ["l10_rpg", "l10_runs_per_game", "runs_per_game_l10"],
}


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


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    return None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except sqlite3.Error:
        return []


def pick_col(cols: list[str], candidates: list[str]) -> str | None:
    lower_map = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def get_metric(ctx: dict | None, metric_key: str) -> float | None:
    if not ctx:
        return None
    for col in METRIC_CANDIDATES.get(metric_key, []):
        if col in ctx:
            val = as_float(ctx.get(col))
            if val is not None:
                return val
    return None


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


def bullpen_bucket(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 40:
        return "low_risk_lt_40"
    if v < 50:
        return "slightly_low_40_50"
    if v < 60:
        return "elevated_50_60"
    return "high_risk_60_plus"


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


def load_team_context(conn: sqlite3.Connection) -> tuple[dict, dict]:
    cols = table_columns(conn, "mlb_team_context")
    if not cols:
        return {}, {"rows": 0, "warning": "missing mlb_team_context"}

    team_col = pick_col(cols, TEAM_COL_CANDIDATES)
    date_col = pick_col(cols, DATE_COL_CANDIDATES)

    if not team_col:
        return {}, {"rows": 0, "warning": "no team column", "cols": cols}

    cur = conn.execute("SELECT * FROM mlb_team_context")
    rows = cur.fetchall()
    names = [d[0] for d in cur.description]

    latest_by_team = {}
    for raw in rows:
        r = dict(zip(names, raw))
        team = str(r.get(team_col) or "").strip().upper()
        if not team:
            continue

        row_date = parse_date(r.get(date_col)) if date_col else None
        if team not in latest_by_team:
            latest_by_team[team] = r
        elif row_date:
            prev_date = parse_date(latest_by_team[team].get(date_col)) if date_col else None
            if not prev_date or row_date >= prev_date:
                latest_by_team[team] = r

    return latest_by_team, {
        "rows": len(rows),
        "team_col": team_col,
        "date_col": date_col,
        "cols": cols,
    }


def load_games(conn: sqlite3.Connection, season: str | None) -> dict:
    where = "WHERE final_away_score IS NOT NULL AND final_home_score IS NOT NULL"
    params: list[str] = []

    if season:
        where += " AND substr(game_date, 1, 4) = ?"
        params.append(str(season))

    rows = conn.execute(f"""
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
        {where}
    """, params).fetchall()

    games = {}
    for r in rows:
        game_pk = str(r[0])
        games[game_pk] = {
            "game_pk": game_pk,
            "game_date": r[1],
            "away_abbr": str(r[2] or "").upper(),
            "home_abbr": str(r[3] or "").upper(),
            "final_away_score": as_int(r[4]),
            "final_home_score": as_int(r[5]),
            "final_total": as_int(r[6]),
            "is_final": r[7],
        }
    return games


def load_events(conn: sqlite3.Connection, games: dict) -> dict:
    if not games:
        return {}

    # Keep this simple and SQLite-compatible for Windows local.
    game_pks = list(games.keys())
    placeholders = ",".join("?" for _ in game_pks)

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
    """, game_pks).fetchall()

    events_by_game = defaultdict(list)
    for idx, r in enumerate(rows):
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

    return events_by_game


def team_score(event: dict, side: str) -> int:
    return event["away_score"] if side == "away" else event["home_score"]


def opp_score(event: dict, side: str) -> int:
    return event["home_score"] if side == "away" else event["away_score"]


def final_team_score(game: dict, side: str) -> int | None:
    return game["final_away_score"] if side == "away" else game["final_home_score"]


def final_opp_score(game: dict, side: str) -> int | None:
    return game["final_home_score"] if side == "away" else game["final_away_score"]


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
    parser = argparse.ArgumentParser(description="Read-only comeback context training from MLB play events.")
    parser.add_argument("--season", default="2025", help="Season to analyze, default 2025. Use 'all' for all.")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path.")
    args = parser.parse_args()

    season = None if str(args.season).lower() == "all" else str(args.season)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)

    games = load_games(conn, season)
    events_by_game = load_events(conn, games)
    team_context, ctx_meta = load_team_context(conn)

    spots = []
    seen = set()

    for game_pk, events in events_by_game.items():
        game = games.get(game_pk)
        if not game:
            continue

        for i, ev in enumerate(events):
            for side in ("away", "home"):
                team = game["away_abbr"] if side == "away" else game["home_abbr"]
                opp = game["home_abbr"] if side == "away" else game["away_abbr"]
                if not team or not opp:
                    continue

                t_score = team_score(ev, side)
                o_score = opp_score(ev, side)
                deficit = o_score - t_score

                if deficit <= 0:
                    continue

                # Deduplicate by game/team/inning/half/exact score state.
                spot_key = (
                    game_pk, team, ev["inning"], ev.get("inning_half"),
                    ev["away_score"], ev["home_score"], deficit
                )
                if spot_key in seen:
                    continue
                seen.add(spot_key)

                later = events[i + 1:]
                later_any = later
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

                all_later_deficits = later_deficits(later_any)
                next2_deficits = later_deficits(next_2)

                tied_or_led_later = 1 if any(d <= 0 for d in all_later_deficits) else 0
                took_lead_later = 1 if any(d < 0 for d in all_later_deficits) else 0
                min_deficit_next_2 = min(next2_deficits) if next2_deficits else deficit

                team_ctx = team_context.get(team)
                opp_ctx = team_context.get(opp)

                team_strength = get_metric(team_ctx, "team_strength")
                offense = get_metric(team_ctx, "offense")
                l1_rpg = get_metric(team_ctx, "l1_rpg")
                l5_rpg = get_metric(team_ctx, "l5_rpg")
                l10_rpg = get_metric(team_ctx, "l10_rpg")
                opp_bullpen_risk = get_metric(opp_ctx, "bullpen_risk")

                row = {
                    "season": (game["game_date"] or "")[:4],
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

                    "team_strength": team_strength,
                    "team_strength_bucket": rating_bucket(team_strength),
                    "offense": offense,
                    "offense_bucket": rating_bucket(offense),
                    "l1_rpg": l1_rpg,
                    "l1_rpg_bucket": rpg_bucket(l1_rpg),
                    "l5_rpg": l5_rpg,
                    "l5_rpg_bucket": rpg_bucket(l5_rpg),
                    "l10_rpg": l10_rpg,
                    "l10_rpg_bucket": rpg_bucket(l10_rpg),
                    "opponent_bullpen_risk": opp_bullpen_risk,
                    "opponent_bullpen_risk_bucket": bullpen_bucket(opp_bullpen_risk),
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
        "team_strength", "team_strength_bucket",
        "offense", "offense_bucket",
        "l1_rpg", "l1_rpg_bucket",
        "l5_rpg", "l5_rpg_bucket",
        "l10_rpg", "l10_rpg_bucket",
        "opponent_bullpen_risk", "opponent_bullpen_risk_bucket",
    ]

    suffix = f"_{season}" if season else "_all"
    enriched_path = OUT_DIR / f"comeback_spots{suffix}.csv"
    write_csv(enriched_path, spots, fields)

    summaries = {
        f"summary_by_deficit_bucket{suffix}.csv": ["deficit_bucket"],
        f"summary_by_inning_bucket{suffix}.csv": ["inning_bucket"],
        f"summary_by_deficit_and_inning{suffix}.csv": ["deficit_bucket", "inning_bucket"],
        f"summary_by_home_away{suffix}.csv": ["home_away"],
        f"summary_by_team_strength_bucket{suffix}.csv": ["team_strength_bucket"],
        f"summary_by_offense_bucket{suffix}.csv": ["offense_bucket"],
        f"summary_by_l10_rpg_bucket{suffix}.csv": ["l10_rpg_bucket"],
        f"summary_by_opponent_bullpen_risk_bucket{suffix}.csv": ["opponent_bullpen_risk_bucket"],
        f"summary_by_deficit_inning_strength{suffix}.csv": ["deficit_bucket", "inning_bucket", "team_strength_bucket"],
        f"summary_by_deficit_inning_bullpen{suffix}.csv": ["deficit_bucket", "inning_bucket", "opponent_bullpen_risk_bucket"],
    }

    summary_fields_tail = [
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
        write_csv(OUT_DIR / filename, rows, cols + summary_fields_tail)

    md = []
    md.append("# Comeback Context Training Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append(f"- Season: {season or 'all'}")
    md.append(f"- Final games loaded: {len(games):,}")
    md.append(f"- Games with events: {len(events_by_game):,}")
    md.append(f"- Comeback spots: {len(spots):,}")
    md.append(f"- Team context rows: {ctx_meta.get('rows', 0):,}")
    md.append("")

    sections = [
        ("Deficit bucket", ["deficit_bucket"]),
        ("Inning bucket", ["inning_bucket"]),
        ("Deficit + inning", ["deficit_bucket", "inning_bucket"]),
        ("Team strength", ["team_strength_bucket"]),
        ("Offense", ["offense_bucket"]),
        ("L10 RPG", ["l10_rpg_bucket"]),
        ("Opponent bullpen risk", ["opponent_bullpen_risk_bucket"]),
    ]

    for title, cols in sections:
        md.append(f"## {title}")
        md.append("")
        for r in summarize(spots, cols)[:14]:
            label = " / ".join(str(r.get(c)) for c in cols)
            md.append(
                f"- {label}: count {r['count']}, win {r['eventually_won_rate']}, "
                f"tie/lead later {r['tied_or_led_later_rate']}, "
                f"score next 2 {r['scored_next_2_innings_rate']}, "
                f"reduce next 2 {r['reduced_deficit_next_2_rate']}"
            )
        md.append("")

    md.append("## Important Interpretation")
    md.append("")
    md.append("- This is baseball-truth training only.")
    md.append("- No Kalshi ROI is calculated here.")
    md.append("- This can later be compared against live Kalshi panic prices.")
    md.append("- Good targets are spots with low final win rate but high tie/lead-later or reduce-deficit rates.")
    md.append("")

    md.append("## Files Written")
    md.append("")
    md.append(f"- {enriched_path.name}")
    for filename in summaries.keys():
        md.append(f"- {filename}")

    summary_path = OUT_DIR / f"comeback_context_summary{suffix}.md"
    summary_path.write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {summary_path}")
    print(f"  {enriched_path}")
    print(f"Games: {len(games):,}")
    print(f"Spots: {len(spots):,}")

if __name__ == "__main__":
    main()
