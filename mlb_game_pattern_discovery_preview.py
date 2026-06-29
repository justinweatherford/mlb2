import argparse
import csv
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "mlb_game_pattern_discovery"

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


def rate(num: float, den: float) -> float | None:
    if not den:
        return None
    return round(num / den, 4)


def avg(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def resolve_context_path(path_arg: str | None, season: str) -> Path | None:
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
    return None


def load_context(path: Path | None) -> dict:
    if not path:
        return {}
    rows = read_csv_rows(path)
    by_game_team = {}
    for r in rows:
        game_pk = str(r.get("game_pk") or "").strip()
        team = norm_team(r.get("team_abbr"))
        if game_pk and team:
            by_game_team[(game_pk, team)] = r
    return by_game_team


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


def bucket_diff(v: int | None, prefix: str) -> str:
    if v is None:
        return "missing"
    if v <= 1:
        return f"{prefix}_1"
    if v == 2:
        return f"{prefix}_2"
    if v == 3:
        return f"{prefix}_3"
    if v == 4:
        return f"{prefix}_4"
    return f"{prefix}_5_plus"


def inning_bucket(inning: int | None) -> str:
    if inning is None:
        return "missing"
    if inning <= 3:
        return "early_1_3"
    if inning <= 5:
        return "f5_1_5"
    if inning <= 6:
        return "middle_6"
    return "late_7_plus"


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
            "final_total": as_int(r[6]),
            "game_start_time_utc": r[7],
        }
    return games


def load_events(conn: sqlite3.Connection, games: dict) -> dict:
    if not games:
        return {}

    game_pks = list(games.keys())
    events_by_game = defaultdict(list)
    chunk_size = 500

    for i in range(0, len(game_pks), chunk_size):
        chunk = game_pks[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(f"""
            SELECT
                game_pk, event_time, inning, inning_half,
                event_type, description, away_score, home_score
            FROM mlb_play_events
            WHERE game_pk IN ({placeholders})
              AND inning IS NOT NULL
              AND away_score IS NOT NULL
              AND home_score IS NOT NULL
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


def final_team_score(game: dict, side: str) -> int:
    return game["final_away_score"] if side == "away" else game["final_home_score"]


def final_opp_score(game: dict, side: str) -> int:
    return game["final_home_score"] if side == "away" else game["final_away_score"]


def context_features(ctx: dict | None) -> dict:
    if not ctx:
        return {
            "team_strength_proxy": None,
            "team_strength_bucket": "missing",
            "offense_form_proxy": None,
            "offense_form_bucket": "missing",
            "l10_rpg": None,
            "l10_rpg_bucket": "missing",
            "run_prevention_proxy": None,
            "run_prevention_bucket": "missing",
        }

    ts = as_float(ctx.get("team_strength_proxy"))
    off = as_float(ctx.get("offense_form_proxy"))
    l10 = as_float(ctx.get("l10_rpg"))
    rp = as_float(ctx.get("run_prevention_proxy"))

    return {
        "team_strength_proxy": ts,
        "team_strength_bucket": bucket_rating(ts),
        "offense_form_proxy": off,
        "offense_form_bucket": bucket_rating(off),
        "l10_rpg": l10,
        "l10_rpg_bucket": bucket_rpg(l10),
        "run_prevention_proxy": rp,
        "run_prevention_bucket": bucket_rating(rp),
    }


def summarize(rows: list[dict], group_cols: list[str], outcome_cols: list[str]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        key = tuple(r.get(c, "missing") or "missing" for c in group_cols)
        groups[key].append(r)

    out = []
    for key, rs in groups.items():
        row = {col: val for col, val in zip(group_cols, key)}
        row["count"] = len(rs)
        for col in outcome_cols:
            vals = [as_int(r.get(col)) for r in rs if as_int(r.get(col)) is not None]
            row[f"{col}_rate"] = rate(sum(vals), len(vals)) if vals else None

        numeric_cols = ["final_margin", "next_2_innings_team_runs", "next_2_innings_opp_runs", "post5_runs", "f5_total", "full_total"]
        for col in numeric_cols:
            vals = [as_float(r.get(col)) for r in rs if as_float(r.get(col)) is not None]
            if vals:
                row[f"avg_{col}"] = round(sum(vals) / len(vals), 3)

        out.append(row)

    out.sort(key=lambda r: (-(r.get("count") or 0), str([r.get(c) for c in group_cols])))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MLB game pattern discovery scan.")
    parser.add_argument("--season", default="2025", help="Season, default 2025.")
    parser.add_argument("--regular-start", default="2025-03-27", help="Exclude games before this date. Empty string disables.")
    parser.add_argument("--context", default=None, help="Optional historical context CSV path.")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    regular_start = args.regular_start.strip() or None

    context_path = resolve_context_path(args.context, str(args.season))
    context = load_context(context_path)

    conn = sqlite3.connect(args.db)
    games = load_final_games(conn, str(args.season), regular_start)
    events_by_game = load_events(conn, games)

    scoring_events = []
    state_rows = []
    lead_rows = []
    comeback_rows = []
    response_rows = []
    f5_rows = []
    team_stats = defaultdict(lambda: Counter())

    for game_pk, game in games.items():
        events = events_by_game.get(game_pk, [])
        if not events:
            continue

        inning_runs = defaultdict(lambda: {"away": 0, "home": 0})
        prev_away = 0
        prev_home = 0

        for ev in events:
            da = max(0, ev["away_score"] - prev_away)
            dh = max(0, ev["home_score"] - prev_home)
            if da or dh:
                inning_runs[ev["inning"]]["away"] += da
                inning_runs[ev["inning"]]["home"] += dh
                scoring_events.append({
                    "game_pk": game_pk,
                    "game_date": game["game_date"],
                    "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
                    "inning": ev["inning"],
                    "inning_bucket": inning_bucket(ev["inning"]),
                    "away_runs_scored_on_event": da,
                    "home_runs_scored_on_event": dh,
                    "total_runs_scored_on_event": da + dh,
                    "event_type": ev["event_type"],
                    "description": ev["description"],
                })
            prev_away = max(prev_away, ev["away_score"])
            prev_home = max(prev_home, ev["home_score"])

        f5_away = sum(inning_runs[i]["away"] for i in range(1, 6))
        f5_home = sum(inning_runs[i]["home"] for i in range(1, 6))
        f5_total = f5_away + f5_home
        full_total = game["final_away_score"] + game["final_home_score"]
        post5_runs = full_total - f5_total

        f5_rows.append({
            "game_pk": game_pk,
            "game_date": game["game_date"],
            "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
            "away_abbr": game["away_abbr"],
            "home_abbr": game["home_abbr"],
            "f5_away": f5_away,
            "f5_home": f5_home,
            "f5_total": f5_total,
            "post5_runs": post5_runs,
            "full_total": full_total,
            "f5_low_0_3": 1 if f5_total <= 3 else 0,
            "f5_high_6_plus": 1 if f5_total >= 6 else 0,
            "post5_high_4_plus": 1 if post5_runs >= 4 else 0,
            "full_over_8_5_proxy": 1 if full_total >= 9 else 0,
            "f5_low_then_full_over_proxy": 1 if f5_total <= 3 and full_total >= 9 else 0,
            "f5_high_then_stall_proxy": 1 if f5_total >= 6 and post5_runs <= 2 else 0,
        })

        # team identity at game level
        for side in ("away", "home"):
            team = game["away_abbr"] if side == "away" else game["home_abbr"]
            opp = game["home_abbr"] if side == "away" else game["away_abbr"]
            ts = final_team_score(game, side)
            os = final_opp_score(game, side)
            team_stats[team]["games"] += 1
            team_stats[team]["wins"] += 1 if ts > os else 0
            team_stats[team]["runs_for"] += ts
            team_stats[team]["runs_allowed"] += os
            team_stats[team]["f5_runs_for"] += f5_away if side == "away" else f5_home
            team_stats[team]["f5_runs_allowed"] += f5_home if side == "away" else f5_away
            team_stats[team]["post5_runs_for"] += (ts - (f5_away if side == "away" else f5_home))
            team_stats[team]["post5_runs_allowed"] += (os - (f5_home if side == "away" else f5_away))

        seen_states = set()
        for i, ev in enumerate(events):
            later = events[i + 1:]
            next_1 = [x for x in later if x["inning"] <= ev["inning"] + 1]
            next_2 = [x for x in later if x["inning"] <= ev["inning"] + 2]

            for side in ("away", "home"):
                team = game["away_abbr"] if side == "away" else game["home_abbr"]
                opp = game["home_abbr"] if side == "away" else game["away_abbr"]
                t_score = team_score(ev, side)
                o_score = opp_score(ev, side)
                margin = t_score - o_score
                if margin == 0:
                    continue

                state_key = (game_pk, team, ev["inning"], ev.get("inning_half"), ev["away_score"], ev["home_score"], margin)
                if state_key in seen_states:
                    continue
                seen_states.add(state_key)

                final_ts = final_team_score(game, side)
                final_os = final_opp_score(game, side)
                eventually_won = 1 if final_ts > final_os else 0

                def scored_in(seq: list[dict]) -> int:
                    return 1 if any(team_score(x, side) > t_score for x in seq) else 0

                def opp_scored_in(seq: list[dict]) -> int:
                    return 1 if any(opp_score(x, side) > o_score for x in seq) else 0

                later_margins = [team_score(x, side) - opp_score(x, side) for x in later]
                next2_margins = [team_score(x, side) - opp_score(x, side) for x in next_2]

                ctx = context.get((game_pk, team))
                opp_ctx = context.get((game_pk, opp))
                cf = context_features(ctx)
                ocf = context_features(opp_ctx)

                base = {
                    "game_pk": game_pk,
                    "game_date": game["game_date"],
                    "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
                    "team": team,
                    "opponent": opp,
                    "side": side,
                    "home_away": "away" if side == "away" else "home",
                    "inning": ev["inning"],
                    "inning_half": ev.get("inning_half"),
                    "inning_bucket": inning_bucket(ev["inning"]),
                    "team_score": t_score,
                    "opponent_score": o_score,
                    "margin": margin,
                    "abs_margin": abs(margin),
                    "final_team_score": final_ts,
                    "final_opp_score": final_os,
                    "final_margin": final_ts - final_os,
                    "eventually_won": eventually_won,
                    "team_strength_bucket": cf["team_strength_bucket"],
                    "offense_form_bucket": cf["offense_form_bucket"],
                    "l10_rpg_bucket": cf["l10_rpg_bucket"],
                    "opponent_run_prevention_bucket": ocf["run_prevention_bucket"],
                }

                if margin < 0:
                    deficit = abs(margin)
                    future_deficits = [-m for m in later_margins]  # positive when still trailing
                    next2_deficits = [-m for m in next2_margins]
                    min_def_next_2 = min(next2_deficits) if next2_deficits else deficit

                    row = dict(base)
                    row.update({
                        "deficit": deficit,
                        "deficit_bucket": bucket_diff(deficit, "down"),
                        "tied_or_led_later": 1 if any(m >= 0 for m in later_margins) else 0,
                        "took_lead_later": 1 if any(m > 0 for m in later_margins) else 0,
                        "scored_next_1_inning": scored_in(next_1),
                        "scored_next_2_innings": scored_in(next_2),
                        "next_2_innings_team_runs": max([team_score(x, side) for x in next_2], default=t_score) - t_score,
                        "next_2_innings_opp_runs": max([opp_score(x, side) for x in next_2], default=o_score) - o_score,
                        "min_deficit_next_2": min_def_next_2,
                        "reduced_deficit_next_2": 1 if min_def_next_2 < deficit else 0,
                        "within_1_next_2": 1 if min_def_next_2 <= 1 else 0,
                    })
                    comeback_rows.append(row)
                    state_rows.append(row)
                    team_stats[team]["trailing_states"] += 1
                    team_stats[team]["trailing_states_eventually_won"] += row["eventually_won"]
                    team_stats[team]["trailing_states_tie_or_lead_later"] += row["tied_or_led_later"]
                    team_stats[team]["trailing_states_score_next_2"] += row["scored_next_2_innings"]

                elif margin > 0:
                    lead = margin
                    min_later_margin = min(later_margins) if later_margins else lead
                    min_next2_margin = min(next2_margins) if next2_margins else lead

                    row = dict(base)
                    row.update({
                        "lead": lead,
                        "lead_bucket": bucket_diff(lead, "up"),
                        "held_to_win": eventually_won,
                        "gave_up_tie_or_lead": 1 if any(m <= 0 for m in later_margins) else 0,
                        "opponent_took_lead_later": 1 if any(m < 0 for m in later_margins) else 0,
                        "opponent_scored_next_1_inning": opp_scored_in(next_1),
                        "opponent_scored_next_2_innings": opp_scored_in(next_2),
                        "next_2_innings_team_runs": max([team_score(x, side) for x in next_2], default=t_score) - t_score,
                        "next_2_innings_opp_runs": max([opp_score(x, side) for x in next_2], default=o_score) - o_score,
                        "lead_reduced_next_2": 1 if min_next2_margin < lead else 0,
                        "lead_cut_to_1_next_2": 1 if min_next2_margin <= 1 else 0,
                    })
                    lead_rows.append(row)
                    state_rows.append(row)
                    team_stats[team]["leading_states"] += 1
                    team_stats[team]["leading_states_held_to_win"] += row["held_to_win"]
                    team_stats[team]["leading_states_gave_up_tie_or_lead"] += row["gave_up_tie_or_lead"]

        # response after allowed run: each scoring event where team allowed runs
        prev_away = 0
        prev_home = 0
        for i, ev in enumerate(events):
            da = max(0, ev["away_score"] - prev_away)
            dh = max(0, ev["home_score"] - prev_home)
            prev_away = max(prev_away, ev["away_score"])
            prev_home = max(prev_home, ev["home_score"])
            if not da and not dh:
                continue

            for side, allowed in (("away", dh), ("home", da)):
                if allowed <= 0:
                    continue
                team = game["away_abbr"] if side == "away" else game["home_abbr"]
                opp = game["home_abbr"] if side == "away" else game["away_abbr"]
                t_score = team_score(ev, side)
                o_score = opp_score(ev, side)
                later = events[i + 1:]
                next_1 = [x for x in later if x["inning"] <= ev["inning"] + 1]
                next_2 = [x for x in later if x["inning"] <= ev["inning"] + 2]
                ctx = context.get((game_pk, team))
                cf = context_features(ctx)

                response_rows.append({
                    "game_pk": game_pk,
                    "game_date": game["game_date"],
                    "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
                    "team": team,
                    "opponent": opp,
                    "side": side,
                    "inning": ev["inning"],
                    "inning_bucket": inning_bucket(ev["inning"]),
                    "runs_allowed_on_event": allowed,
                    "team_score_after_allowed": t_score,
                    "opp_score_after_allowed": o_score,
                    "margin_after_allowed": t_score - o_score,
                    "scored_next_1_inning": 1 if any(team_score(x, side) > t_score for x in next_1) else 0,
                    "scored_next_2_innings": 1 if any(team_score(x, side) > t_score for x in next_2) else 0,
                    "eventually_won": 1 if final_team_score(game, side) > final_opp_score(game, side) else 0,
                    "team_strength_bucket": cf["team_strength_bucket"],
                    "offense_form_bucket": cf["offense_form_bucket"],
                    "l10_rpg_bucket": cf["l10_rpg_bucket"],
                })

    # Write raw/enriched discovery tables
    write_csv(OUT_DIR / "comeback_by_state.csv", comeback_rows, list(comeback_rows[0].keys()) if comeback_rows else [])
    write_csv(OUT_DIR / "lead_by_state.csv", lead_rows, list(lead_rows[0].keys()) if lead_rows else [])
    write_csv(OUT_DIR / "response_after_allowed_runs.csv", response_rows, list(response_rows[0].keys()) if response_rows else [])
    write_csv(OUT_DIR / "f5_vs_full_game_profiles.csv", f5_rows, list(f5_rows[0].keys()) if f5_rows else [])
    write_csv(OUT_DIR / "scoring_events_by_inning_raw.csv", scoring_events, list(scoring_events[0].keys()) if scoring_events else [])

    # Summaries
    comeback_outcomes = [
        "eventually_won", "tied_or_led_later", "took_lead_later",
        "scored_next_1_inning", "scored_next_2_innings",
        "reduced_deficit_next_2", "within_1_next_2",
    ]
    lead_outcomes = [
        "held_to_win", "gave_up_tie_or_lead", "opponent_took_lead_later",
        "opponent_scored_next_1_inning", "opponent_scored_next_2_innings",
        "lead_reduced_next_2", "lead_cut_to_1_next_2",
    ]
    response_outcomes = ["scored_next_1_inning", "scored_next_2_innings", "eventually_won"]
    f5_outcomes = ["f5_low_0_3", "f5_high_6_plus", "post5_high_4_plus", "full_over_8_5_proxy", "f5_low_then_full_over_proxy", "f5_high_then_stall_proxy"]

    summary_specs = [
        ("comeback_by_deficit_inning.csv", comeback_rows, ["deficit_bucket", "inning_bucket"], comeback_outcomes),
        ("comeback_by_deficit_inning_strength.csv", comeback_rows, ["deficit_bucket", "inning_bucket", "team_strength_bucket"], comeback_outcomes),
        ("comeback_by_deficit_inning_opponent_run_prevention.csv", comeback_rows, ["deficit_bucket", "inning_bucket", "opponent_run_prevention_bucket"], comeback_outcomes),
        ("lead_protection_by_lead_inning.csv", lead_rows, ["lead_bucket", "inning_bucket"], lead_outcomes),
        ("lead_protection_by_lead_inning_team_strength.csv", lead_rows, ["lead_bucket", "inning_bucket", "team_strength_bucket"], lead_outcomes),
        ("response_after_allowed_by_inning.csv", response_rows, ["inning_bucket"], response_outcomes),
        ("response_after_allowed_by_inning_offense.csv", response_rows, ["inning_bucket", "offense_form_bucket"], response_outcomes),
        ("f5_vs_full_game_summary.csv", f5_rows, ["f5_low_0_3", "f5_high_6_plus"], f5_outcomes),
    ]

    for filename, rows, groups, outcomes in summary_specs:
        s = summarize(rows, groups, outcomes)
        fields = groups + ["count"] + [f"{o}_rate" for o in outcomes] + [
            "avg_final_margin", "avg_next_2_innings_team_runs", "avg_next_2_innings_opp_runs",
            "avg_post5_runs", "avg_f5_total", "avg_full_total",
        ]
        write_csv(OUT_DIR / filename, s, fields)

    # Scoring by inning summary
    inning_summary = defaultdict(lambda: Counter())
    for g in f5_rows:
        pass

    for ev in scoring_events:
        key = ev["inning"]
        inning_summary[key]["scoring_events"] += 1
        inning_summary[key]["total_runs"] += as_int(ev["total_runs_scored_on_event"]) or 0

    scoring_rows = []
    for inning, c in sorted(inning_summary.items()):
        scoring_rows.append({
            "inning": inning,
            "scoring_events": c["scoring_events"],
            "total_runs": c["total_runs"],
        })
    write_csv(OUT_DIR / "scoring_by_inning.csv", scoring_rows, ["inning", "scoring_events", "total_runs"])

    # Team identity summary
    team_rows = []
    for team, c in sorted(team_stats.items()):
        games_n = c["games"]
        team_rows.append({
            "team": team,
            "games": games_n,
            "win_rate": rate(c["wins"], games_n),
            "runs_per_game": round(c["runs_for"] / games_n, 3) if games_n else None,
            "runs_allowed_per_game": round(c["runs_allowed"] / games_n, 3) if games_n else None,
            "f5_runs_per_game": round(c["f5_runs_for"] / games_n, 3) if games_n else None,
            "post5_runs_per_game": round(c["post5_runs_for"] / games_n, 3) if games_n else None,
            "trailing_state_win_rate": rate(c["trailing_states_eventually_won"], c["trailing_states"]),
            "trailing_state_tie_or_lead_rate": rate(c["trailing_states_tie_or_lead_later"], c["trailing_states"]),
            "trailing_state_score_next_2_rate": rate(c["trailing_states_score_next_2"], c["trailing_states"]),
            "leading_state_hold_rate": rate(c["leading_states_held_to_win"], c["leading_states"]),
            "leading_state_give_up_tie_or_lead_rate": rate(c["leading_states_gave_up_tie_or_lead"], c["leading_states"]),
        })
    write_csv(
        OUT_DIR / "team_identity_summary.csv",
        team_rows,
        [
            "team", "games", "win_rate", "runs_per_game", "runs_allowed_per_game",
            "f5_runs_per_game", "post5_runs_per_game",
            "trailing_state_win_rate", "trailing_state_tie_or_lead_rate", "trailing_state_score_next_2_rate",
            "leading_state_hold_rate", "leading_state_give_up_tie_or_lead_rate",
        ],
    )

    # Possible edges: high-lift or interesting base-rate pockets
    possible = []
    for filename, rows, groups, outcomes in summary_specs[:7]:
        s = summarize(rows, groups, outcomes)
        for r in s:
            if r["count"] < 100:
                continue
            note = None
            if "scored_next_2_innings_rate" in r and r.get("scored_next_2_innings_rate") is not None and r["scored_next_2_innings_rate"] >= 0.55:
                note = "high_score_next_2_rebound"
            if "tied_or_led_later_rate" in r and r.get("tied_or_led_later_rate") is not None and r["tied_or_led_later_rate"] >= 0.40:
                note = "high_tie_or_lead_rebound"
            if "gave_up_tie_or_lead_rate" in r and r.get("gave_up_tie_or_lead_rate") is not None and r["gave_up_tie_or_lead_rate"] >= 0.30:
                note = "lead_collapse_watch"
            if "opponent_scored_next_2_innings_rate" in r and r.get("opponent_scored_next_2_innings_rate") is not None and r["opponent_scored_next_2_innings_rate"] >= 0.45:
                note = "leader_under_pressure"
            if note:
                out = {"source": filename, "note": note}
                out.update(r)
                possible.append(out)

    possible.sort(key=lambda r: (r.get("count", 0), str(r.get("note"))), reverse=True)
    possible_fields = sorted({k for row in possible for k in row.keys()})
    write_csv(OUT_DIR / "possible_edges_to_review.csv", possible, possible_fields)

    # MD summary
    md = []
    md.append("# MLB Game Pattern Discovery Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append(f"- Season: {args.season}")
    md.append(f"- Regular start cutoff: {regular_start or 'disabled'}")
    md.append(f"- Context source: `{context_path}`" if context_path else "- Context source: not found")
    md.append(f"- Final games loaded: {len(games):,}")
    md.append(f"- Games with events: {len(events_by_game):,}")
    md.append(f"- Comeback states: {len(comeback_rows):,}")
    md.append(f"- Lead states: {len(lead_rows):,}")
    md.append(f"- Response-after-allowed events: {len(response_rows):,}")
    md.append(f"- F5/full game rows: {len(f5_rows):,}")
    md.append("")

    md.append("## Interesting Broad Reads")
    md.append("")
    for title, filename, rows, groups, outcomes in [
        ("Comeback by deficit/inning", "comeback_by_deficit_inning.csv", comeback_rows, ["deficit_bucket", "inning_bucket"], comeback_outcomes),
        ("Lead protection by lead/inning", "lead_protection_by_lead_inning.csv", lead_rows, ["lead_bucket", "inning_bucket"], lead_outcomes),
        ("Response after allowed runs", "response_after_allowed_by_inning.csv", response_rows, ["inning_bucket"], response_outcomes),
        ("F5 versus full game", "f5_vs_full_game_summary.csv", f5_rows, ["f5_low_0_3", "f5_high_6_plus"], f5_outcomes),
    ]:
        md.append(f"### {title}")
        md.append("")
        for r in summarize(rows, groups, outcomes)[:10]:
            label = " / ".join(str(r.get(c)) for c in groups)
            parts = [f"count {r['count']}"]
            for o in outcomes[:4]:
                if f"{o}_rate" in r:
                    parts.append(f"{o} {r[f'{o}_rate']}")
            md.append(f"- {label}: " + ", ".join(parts))
        md.append("")

    md.append("## Candidate Ideas To Review")
    md.append("")
    for r in possible[:20]:
        label_cols = [k for k in r.keys() if k not in {"source", "note", "count"} and not k.endswith("_rate") and not k.startswith("avg_")]
        label = " / ".join(str(r.get(c)) for c in label_cols[:4])
        md.append(f"- {r.get('note')} from {r.get('source')}: {label}, count {r.get('count')}")
    md.append("")

    md.append("## Files Written")
    md.append("")
    for name in [
        "discovery_summary.md",
        "comeback_by_state.csv",
        "lead_by_state.csv",
        "response_after_allowed_runs.csv",
        "f5_vs_full_game_profiles.csv",
        "scoring_by_inning.csv",
        "team_identity_summary.csv",
        "possible_edges_to_review.csv",
        *[spec[0] for spec in summary_specs],
    ]:
        md.append(f"- {name}")

    (OUT_DIR / "discovery_summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {OUT_DIR / 'discovery_summary.md'}")
    print(f"Games: {len(games):,}")
    print(f"Games with events: {len(events_by_game):,}")
    print(f"Comeback states: {len(comeback_rows):,}")
    print(f"Lead states: {len(lead_rows):,}")
    print(f"Possible edges: {len(possible):,}")


if __name__ == "__main__":
    main()
