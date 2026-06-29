import argparse
import csv
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "historical_tier_pattern_audit"

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

COMEBACK_OUTCOMES = [
    "eventually_won",
    "tied_or_led_later",
    "scored_next_1_inning",
    "scored_next_2_innings",
    "reduced_deficit_next_2",
    "within_1_next_2",
]

LEAD_OUTCOMES = [
    "held_to_win",
    "gave_up_tie_or_lead",
    "opponent_scored_next_1_inning",
    "opponent_scored_next_2_innings",
    "lead_reduced_next_2",
    "lead_cut_to_1_next_2",
]


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


def bucket_margin(v: int | None, prefix: str) -> str:
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
    if inning == 6:
        return "middle_6"
    return "late_7_plus"


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


def context_features(ctx: dict | None, prefix: str = "") -> dict:
    if not ctx:
        return {
            f"{prefix}team_strength_proxy": None,
            f"{prefix}team_strength_bucket": "missing",
            f"{prefix}offense_form_proxy": None,
            f"{prefix}offense_form_bucket": "missing",
            f"{prefix}l10_rpg": None,
            f"{prefix}l10_rpg_bucket": "missing",
            f"{prefix}run_prevention_proxy": None,
            f"{prefix}run_prevention_bucket": "missing",
            f"{prefix}context_confidence": "missing",
        }

    ts = as_float(ctx.get("team_strength_proxy"))
    off = as_float(ctx.get("offense_form_proxy"))
    l10 = as_float(ctx.get("l10_rpg"))
    rp = as_float(ctx.get("run_prevention_proxy"))

    return {
        f"{prefix}team_strength_proxy": ts,
        f"{prefix}team_strength_bucket": bucket_rating(ts),
        f"{prefix}offense_form_proxy": off,
        f"{prefix}offense_form_bucket": bucket_rating(off),
        f"{prefix}l10_rpg": l10,
        f"{prefix}l10_rpg_bucket": bucket_rpg(l10),
        f"{prefix}run_prevention_proxy": rp,
        f"{prefix}run_prevention_bucket": bucket_rating(rp),
        f"{prefix}context_confidence": ctx.get("context_confidence") or ctx.get("confidence") or "unknown",
    }


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


def score_next(seq: list[dict], side: str, start_team_score: int) -> int:
    return 1 if any(team_score(x, side) > start_team_score for x in seq) else 0


def opp_score_next(seq: list[dict], side: str, start_opp_score: int) -> int:
    return 1 if any(opp_score(x, side) > start_opp_score for x in seq) else 0


def build_state_rows_for_season(conn: sqlite3.Connection, season: str, regular_start: str | None) -> tuple[list[dict], dict]:
    context, context_path = load_context(season)
    games = load_final_games(conn, season, regular_start)
    events_by_game = load_events(conn, games)

    rows = []
    stats = {
        "season": season,
        "regular_start": regular_start,
        "context_path": str(context_path) if context_path else "",
        "final_games": len(games),
        "games_with_events": len(events_by_game),
        "state_rows": 0,
    }

    for game_pk, game in games.items():
        events = events_by_game.get(game_pk, [])
        if not events:
            continue

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

                ctx = context.get((game_pk, team))
                opp_ctx = context.get((game_pk, opp))
                cf = context_features(ctx, "")
                ocf = context_features(opp_ctx, "opponent_")

                base = {
                    "season": season,
                    "game_pk": game_pk,
                    "game_date": game["game_date"],
                    "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
                    "team": team,
                    "opponent": opp,
                    "side": side,
                    "home_away": "away" if side == "away" else "home",
                    "inning": ev["inning"],
                    "inning_bucket": inning_bucket(ev["inning"]),
                    "inning_half": ev.get("inning_half"),
                    "team_score": t_score,
                    "opponent_score": o_score,
                    "margin": margin,
                    "abs_margin": abs(margin),
                    "final_team_score": final_ts,
                    "final_opp_score": final_os,
                    "final_margin": final_ts - final_os,
                    **cf,
                    **ocf,
                }

                later_margins = [team_score(x, side) - opp_score(x, side) for x in later]
                next2_margins = [team_score(x, side) - opp_score(x, side) for x in next_2]

                if margin < 0:
                    deficit = abs(margin)
                    next2_deficits = [-m for m in next2_margins]
                    min_def_next_2 = min(next2_deficits) if next2_deficits else deficit
                    max_next2_team_score = max([team_score(x, side) for x in next_2], default=t_score)
                    max_next2_opp_score = max([opp_score(x, side) for x in next_2], default=o_score)

                    row = {
                        **base,
                        "state_type": "comeback",
                        "state_bucket": f"{bucket_margin(deficit, 'down')}_{inning_bucket(ev['inning'])}",
                        "deficit": deficit,
                        "deficit_bucket": bucket_margin(deficit, "down"),
                        "lead": None,
                        "lead_bucket": None,
                        "eventually_won": 1 if final_ts > final_os else 0,
                        "tied_or_led_later": 1 if any(m >= 0 for m in later_margins) else 0,
                        "took_lead_later": 1 if any(m > 0 for m in later_margins) else 0,
                        "scored_next_1_inning": score_next(next_1, side, t_score),
                        "scored_next_2_innings": score_next(next_2, side, t_score),
                        "next_2_innings_team_runs": max_next2_team_score - t_score,
                        "next_2_innings_opp_runs": max_next2_opp_score - o_score,
                        "min_deficit_next_2": min_def_next_2,
                        "reduced_deficit_next_2": 1 if min_def_next_2 < deficit else 0,
                        "within_1_next_2": 1 if min_def_next_2 <= 1 else 0,
                        "held_to_win": None,
                        "gave_up_tie_or_lead": None,
                        "opponent_scored_next_1_inning": None,
                        "opponent_scored_next_2_innings": None,
                        "lead_reduced_next_2": None,
                        "lead_cut_to_1_next_2": None,
                    }
                    rows.append(row)

                elif margin > 0:
                    lead = margin
                    min_next2_margin = min(next2_margins) if next2_margins else lead
                    max_next2_team_score = max([team_score(x, side) for x in next_2], default=t_score)
                    max_next2_opp_score = max([opp_score(x, side) for x in next_2], default=o_score)

                    row = {
                        **base,
                        "state_type": "lead",
                        "state_bucket": f"{bucket_margin(lead, 'up')}_{inning_bucket(ev['inning'])}",
                        "deficit": None,
                        "deficit_bucket": None,
                        "lead": lead,
                        "lead_bucket": bucket_margin(lead, "up"),
                        "eventually_won": None,
                        "tied_or_led_later": None,
                        "took_lead_later": None,
                        "scored_next_1_inning": None,
                        "scored_next_2_innings": None,
                        "next_2_innings_team_runs": max_next2_team_score - t_score,
                        "next_2_innings_opp_runs": max_next2_opp_score - o_score,
                        "min_deficit_next_2": None,
                        "reduced_deficit_next_2": None,
                        "within_1_next_2": None,
                        "held_to_win": 1 if final_ts > final_os else 0,
                        "gave_up_tie_or_lead": 1 if any(m <= 0 for m in later_margins) else 0,
                        "opponent_scored_next_1_inning": opp_score_next(next_1, side, o_score),
                        "opponent_scored_next_2_innings": opp_score_next(next_2, side, o_score),
                        "lead_reduced_next_2": 1 if min_next2_margin < lead else 0,
                        "lead_cut_to_1_next_2": 1 if min_next2_margin <= 1 else 0,
                    }
                    rows.append(row)

    stats["state_rows"] = len(rows)
    stats["comeback_rows"] = sum(1 for r in rows if r["state_type"] == "comeback")
    stats["lead_rows"] = sum(1 for r in rows if r["state_type"] == "lead")
    return rows, stats


def summarize(rows: list[dict], group_cols: list[str], outcomes: list[str]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        key = tuple(str(r.get(c) if r.get(c) not in {None, ""} else "missing") for c in group_cols)
        groups[key].append(r)

    out = []
    for key, rs in groups.items():
        row = {c: v for c, v in zip(group_cols, key)}
        row["count"] = len(rs)
        for outcome in outcomes:
            vals = [as_int(r.get(outcome)) for r in rs if as_int(r.get(outcome)) is not None]
            row[f"{outcome}_rate"] = rate(sum(vals), len(vals)) if vals else None
        out.append(row)

    out.sort(key=lambda r: (-(r.get("count") or 0), str([r.get(c) for c in group_cols])))
    return out


def build_baseline_map(summary_rows: list[dict], key_cols: list[str], outcomes: list[str]) -> dict:
    out = {}
    for r in summary_rows:
        key = tuple(r.get(c) for c in key_cols)
        out[key] = {f"{o}_rate": r.get(f"{o}_rate") for o in outcomes}
        out[key]["count"] = r.get("count")
    return out


def add_lift(tier_rows: list[dict], baseline_map: dict, key_cols: list[str], outcomes: list[str]) -> list[dict]:
    out = []
    for r in tier_rows:
        key = tuple(r.get(c) for c in key_cols)
        base = baseline_map.get(key, {})
        row = dict(r)
        row["baseline_count"] = base.get("count")
        for outcome in outcomes:
            rr = r.get(f"{outcome}_rate")
            br = base.get(f"{outcome}_rate")
            row[f"{outcome}_baseline_rate"] = br
            row[f"{outcome}_lift"] = round(rr - br, 4) if rr is not None and br is not None else None
        out.append(row)
    return out


def stable_profile_rows(all_rows: list[dict], state_type: str, tier_col: str, min_count: int) -> list[dict]:
    rows = [r for r in all_rows if r["state_type"] == state_type]
    outcomes = COMEBACK_OUTCOMES if state_type == "comeback" else LEAD_OUTCOMES
    base_key_cols = ["season", "state_bucket"]
    tier_key_cols = ["season", "state_bucket", tier_col]

    baseline = summarize(rows, base_key_cols, outcomes)
    tier_summary = summarize(rows, tier_key_cols, outcomes)
    baseline_map = build_baseline_map(baseline, base_key_cols, outcomes)
    tier_lifts = add_lift(tier_summary, baseline_map, base_key_cols, outcomes)

    return [r for r in tier_lifts if (r.get("count") or 0) >= min_count]


def cross_season_stability(tier_lift_rows: list[dict], tier_col: str, state_type: str, primary_outcome: str) -> list[dict]:
    groups = defaultdict(list)
    for r in tier_lift_rows:
        key = (r.get("state_bucket"), r.get(tier_col))
        groups[key].append(r)

    out = []
    for (state_bucket, tier_value), rs in groups.items():
        seasons = sorted(str(r["season"]) for r in rs)
        if len(seasons) < 2:
            continue

        rates = [as_float(r.get(f"{primary_outcome}_rate")) for r in rs if as_float(r.get(f"{primary_outcome}_rate")) is not None]
        lifts = [as_float(r.get(f"{primary_outcome}_lift")) for r in rs if as_float(r.get(f"{primary_outcome}_lift")) is not None]
        counts = [as_int(r.get("count")) or 0 for r in rs]

        if not rates:
            continue

        avg_rate = round(sum(rates) / len(rates), 4)
        min_rate = min(rates)
        max_rate = max(rates)
        avg_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
        min_lift = min(lifts) if lifts else None
        max_lift = max(lifts) if lifts else None

        if len(seasons) == 3 and avg_lift is not None and avg_lift >= 0.04 and min_lift is not None and min_lift >= 0:
            label = "stable_positive_lift"
        elif len(seasons) == 3 and avg_lift is not None and avg_lift <= -0.04 and max_lift is not None and max_lift <= 0:
            label = "stable_negative_lift"
        elif len(seasons) == 3:
            label = "stable_mixed_or_thin"
        else:
            label = "partial_seasons"

        out.append({
            "state_type": state_type,
            "state_bucket": state_bucket,
            "tier": tier_col,
            "tier_value": tier_value,
            "primary_outcome": primary_outcome,
            "seasons_seen": ",".join(seasons),
            "season_count": len(seasons),
            "total_count": sum(counts),
            "min_season_count": min(counts),
            "avg_rate": avg_rate,
            "min_rate": min_rate,
            "max_rate": max_rate,
            "rate_range": round(max_rate - min_rate, 4),
            "avg_lift": avg_lift,
            "min_lift": min_lift,
            "max_lift": max_lift,
            "stability_label": label,
        })

    out.sort(key=lambda r: (
        {"stable_positive_lift": 0, "stable_negative_lift": 1, "stable_mixed_or_thin": 2, "partial_seasons": 3}.get(r["stability_label"], 9),
        -(r.get("total_count") or 0),
        r["state_bucket"],
        r["tier"],
        r["tier_value"],
    ))
    return out


def build_response_rows(conn: sqlite3.Connection, season: str, regular_start: str | None) -> tuple[list[dict], dict]:
    # Lighter pass focused only on answer-back after allowing runs.
    context, context_path = load_context(season)
    games = load_final_games(conn, season, regular_start)
    events_by_game = load_events(conn, games)
    rows = []

    for game_pk, game in games.items():
        events = events_by_game.get(game_pk, [])
        if not events:
            continue

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
                opp_ctx = context.get((game_pk, opp))
                cf = context_features(ctx, "")
                ocf = context_features(opp_ctx, "opponent_")

                rows.append({
                    "season": season,
                    "game_pk": game_pk,
                    "game_date": game["game_date"],
                    "team": team,
                    "opponent": opp,
                    "home_away": "away" if side == "away" else "home",
                    "inning": ev["inning"],
                    "inning_bucket": inning_bucket(ev["inning"]),
                    "runs_allowed_on_event": allowed,
                    "margin_after_allowed": t_score - o_score,
                    "scored_next_1_inning": score_next(next_1, side, t_score),
                    "scored_next_2_innings": score_next(next_2, side, t_score),
                    "eventually_won": 1 if final_team_score(game, side) > final_opp_score(game, side) else 0,
                    **cf,
                    **ocf,
                })

    stats = {
        "season": season,
        "response_rows": len(rows),
        "context_path": str(context_path) if context_path else "",
    }
    return rows, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only historical tier pattern audit across MLB seasons.")
    parser.add_argument("--seasons", nargs="+", default=["2023", "2024", "2025"], help="Seasons to analyze.")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path.")
    parser.add_argument("--min-count", type=int, default=100, help="Min sample size per season/tier row.")
    parser.add_argument("--include-state-rows", action="store_true", help="Also write all state rows; can be large.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    all_state_rows = []
    all_response_rows = []
    state_stats = []
    response_stats = []

    for season in args.seasons:
        regular_start = DEFAULT_REGULAR_START.get(str(season))
        state_rows, stats = build_state_rows_for_season(conn, str(season), regular_start)
        response_rows, rstats = build_response_rows(conn, str(season), regular_start)

        all_state_rows.extend(state_rows)
        all_response_rows.extend(response_rows)
        state_stats.append(stats)
        response_stats.append(rstats)

        print(f"{season}: states={len(state_rows):,}, responses={len(response_rows):,}, games={stats['final_games']:,}, events_games={stats['games_with_events']:,}")

    # Write optional row-level state data
    if args.include_state_rows:
        write_csv(OUT_DIR / "all_state_rows.csv", all_state_rows)
        write_csv(OUT_DIR / "all_response_after_allowed_rows.csv", all_response_rows)

    write_csv(OUT_DIR / "season_input_health.csv", state_stats)
    write_csv(OUT_DIR / "response_input_health.csv", response_stats)

    tier_cols = [
        "home_away",
        "team_strength_bucket",
        "offense_form_bucket",
        "l10_rpg_bucket",
        "opponent_run_prevention_bucket",
        "opponent_team_strength_bucket",
    ]

    stability_rows = []
    tier_lift_outputs = []

    for state_type, outcomes, primary_outcomes in [
        ("comeback", COMEBACK_OUTCOMES, ["tied_or_led_later", "scored_next_2_innings", "eventually_won"]),
        ("lead", LEAD_OUTCOMES, ["gave_up_tie_or_lead", "opponent_scored_next_2_innings", "held_to_win"]),
    ]:
        rows = [r for r in all_state_rows if r["state_type"] == state_type]
        base_summary = summarize(rows, ["season", "state_bucket"], outcomes)
        write_csv(OUT_DIR / f"{state_type}_baseline_by_season_state.csv", base_summary)

        for tier_col in tier_cols:
            tier_rows = stable_profile_rows(all_state_rows, state_type, tier_col, args.min_count)
            tier_lift_outputs.extend({**r, "state_type": state_type, "tier": tier_col, "tier_value": r.get(tier_col)} for r in tier_rows)

            write_csv(OUT_DIR / f"{state_type}_lift_by_{tier_col}.csv", tier_rows)

            for primary in primary_outcomes:
                stability_rows.extend(cross_season_stability(tier_rows, tier_col, state_type, primary))

    # Response-after-allowed tier audit
    response_outcomes = ["scored_next_1_inning", "scored_next_2_innings", "eventually_won"]
    response_base = summarize(all_response_rows, ["season", "inning_bucket"], response_outcomes)
    response_base_map = build_baseline_map(response_base, ["season", "inning_bucket"], response_outcomes)
    write_csv(OUT_DIR / "response_baseline_by_season_inning.csv", response_base)

    response_tier_outputs = []
    for tier_col in tier_cols:
        tier_summary = summarize(all_response_rows, ["season", "inning_bucket", tier_col], response_outcomes)
        lifted = add_lift(tier_summary, response_base_map, ["season", "inning_bucket"], response_outcomes)
        lifted = [r for r in lifted if (r.get("count") or 0) >= args.min_count]
        response_tier_outputs.extend({**r, "state_type": "response_after_allowed", "tier": tier_col, "tier_value": r.get(tier_col)} for r in lifted)
        write_csv(OUT_DIR / f"response_lift_by_{tier_col}.csv", lifted)

        # Stability for response rows
        groups = defaultdict(list)
        for r in lifted:
            key = (r.get("inning_bucket"), r.get(tier_col))
            groups[key].append(r)

        for primary in ["scored_next_2_innings", "eventually_won"]:
            for (inning_b, tier_value), rs in groups.items():
                seasons = sorted(str(r["season"]) for r in rs)
                if len(seasons) < 2:
                    continue
                rates = [as_float(r.get(f"{primary}_rate")) for r in rs if as_float(r.get(f"{primary}_rate")) is not None]
                lifts = [as_float(r.get(f"{primary}_lift")) for r in rs if as_float(r.get(f"{primary}_lift")) is not None]
                counts = [as_int(r.get("count")) or 0 for r in rs]
                if not rates:
                    continue
                avg_lift = round(sum(lifts) / len(lifts), 4) if lifts else None
                min_lift = min(lifts) if lifts else None
                max_lift = max(lifts) if lifts else None
                if len(seasons) == 3 and avg_lift is not None and avg_lift >= 0.04 and min_lift is not None and min_lift >= 0:
                    label = "stable_positive_lift"
                elif len(seasons) == 3 and avg_lift is not None and avg_lift <= -0.04 and max_lift is not None and max_lift <= 0:
                    label = "stable_negative_lift"
                elif len(seasons) == 3:
                    label = "stable_mixed_or_thin"
                else:
                    label = "partial_seasons"
                stability_rows.append({
                    "state_type": "response_after_allowed",
                    "state_bucket": inning_b,
                    "tier": tier_col,
                    "tier_value": tier_value,
                    "primary_outcome": primary,
                    "seasons_seen": ",".join(seasons),
                    "season_count": len(seasons),
                    "total_count": sum(counts),
                    "min_season_count": min(counts),
                    "avg_rate": round(sum(rates) / len(rates), 4),
                    "min_rate": min(rates),
                    "max_rate": max(rates),
                    "rate_range": round(max(rates) - min(rates), 4),
                    "avg_lift": avg_lift,
                    "min_lift": min_lift,
                    "max_lift": max_lift,
                    "stability_label": label,
                })

    stability_rows.sort(key=lambda r: (
        {"stable_positive_lift": 0, "stable_negative_lift": 1, "stable_mixed_or_thin": 2, "partial_seasons": 3}.get(r["stability_label"], 9),
        r["state_type"],
        r["state_bucket"],
        r["tier"],
        r["tier_value"],
        r["primary_outcome"],
    ))
    write_csv(OUT_DIR / "tier_stability_all.csv", stability_rows)

    stable_positive = [r for r in stability_rows if r["stability_label"] == "stable_positive_lift" and (r.get("min_season_count") or 0) >= args.min_count]
    stable_negative = [r for r in stability_rows if r["stability_label"] == "stable_negative_lift" and (r.get("min_season_count") or 0) >= args.min_count]
    mixed = [r for r in stability_rows if r["stability_label"] == "stable_mixed_or_thin" and (r.get("min_season_count") or 0) >= args.min_count]

    write_csv(OUT_DIR / "stable_positive_tier_lifts.csv", stable_positive)
    write_csv(OUT_DIR / "stable_negative_tier_lifts.csv", stable_negative)
    write_csv(OUT_DIR / "mixed_or_noisy_tier_lifts.csv", mixed)

    # Fair probability seed table: stable baseline patterns plus stable positive tier modifiers
    fair_rows = []
    for state_type, outcomes in [("comeback", COMEBACK_OUTCOMES), ("lead", LEAD_OUTCOMES)]:
        rows = [r for r in all_state_rows if r["state_type"] == state_type]
        base = summarize(rows, ["season", "state_bucket"], outcomes)
        by_state = defaultdict(list)
        for r in base:
            by_state[r["state_bucket"]].append(r)
        for state_bucket, rs in by_state.items():
            if len(rs) < 3:
                continue
            out = {
                "model_layer": "baseline",
                "state_type": state_type,
                "state_bucket": state_bucket,
                "tier": "",
                "tier_value": "",
                "seasons_seen": ",".join(sorted(str(r["season"]) for r in rs)),
                "total_count": sum(as_int(r.get("count")) or 0 for r in rs),
                "min_season_count": min(as_int(r.get("count")) or 0 for r in rs),
            }
            for outcome in outcomes:
                rates = [as_float(r.get(f"{outcome}_rate")) for r in rs if as_float(r.get(f"{outcome}_rate")) is not None]
                if rates:
                    out[f"{outcome}_avg_rate"] = round(sum(rates) / len(rates), 4)
                    out[f"{outcome}_min_rate"] = min(rates)
                    out[f"{outcome}_max_rate"] = max(rates)
                    out[f"{outcome}_range"] = round(max(rates) - min(rates), 4)
            fair_rows.append(out)

    for r in stable_positive:
        fair_rows.append({
            "model_layer": "tier_modifier",
            "state_type": r["state_type"],
            "state_bucket": r["state_bucket"],
            "tier": r["tier"],
            "tier_value": r["tier_value"],
            "primary_outcome": r["primary_outcome"],
            "seasons_seen": r["seasons_seen"],
            "total_count": r["total_count"],
            "min_season_count": r["min_season_count"],
            "avg_rate": r["avg_rate"],
            "avg_lift": r["avg_lift"],
            "min_lift": r["min_lift"],
            "max_lift": r["max_lift"],
        })

    write_csv(OUT_DIR / "fair_probability_seed_table.csv", fair_rows)

    # Markdown summary
    md = []
    md.append("# Historical Tier Pattern Audit")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append(f"- Seasons: {', '.join(args.seasons)}")
    md.append(f"- Min count per season/tier row: {args.min_count}")
    md.append(f"- Total state rows: {len(all_state_rows):,}")
    md.append(f"- Total response-after-allowed rows: {len(all_response_rows):,}")
    md.append("")

    md.append("## Input Health")
    md.append("")
    for s in state_stats:
        md.append(
            f"- {s['season']}: games {s['final_games']:,}, games with events {s['games_with_events']:,}, "
            f"states {s['state_rows']:,}, context `{s['context_path']}`"
        )
    md.append("")

    md.append("## Stable Positive Tier Lifts")
    md.append("")
    if not stable_positive:
        md.append("- None found at current thresholds.")
    else:
        for r in stable_positive[:30]:
            md.append(
                f"- {r['state_type']} / {r['state_bucket']} / {r['tier']}={r['tier_value']} / "
                f"{r['primary_outcome']}: avg {pct(r['avg_rate'])}, avg lift {pct(r['avg_lift'])}, "
                f"range {pct(r['rate_range'])}, count {r['total_count']:,}"
            )
    md.append("")

    md.append("## Stable Negative Tier Lifts")
    md.append("")
    if not stable_negative:
        md.append("- None found at current thresholds.")
    else:
        for r in stable_negative[:30]:
            md.append(
                f"- {r['state_type']} / {r['state_bucket']} / {r['tier']}={r['tier_value']} / "
                f"{r['primary_outcome']}: avg {pct(r['avg_rate'])}, avg lift {pct(r['avg_lift'])}, "
                f"range {pct(r['rate_range'])}, count {r['total_count']:,}"
            )
    md.append("")

    md.append("## Interpretation Guide")
    md.append("")
    md.append("- `stable_positive_lift` means the tier beat its same-season, same-state baseline in all included seasons.")
    md.append("- `stable_negative_lift` means the tier lagged its same-season, same-state baseline in all included seasons.")
    md.append("- These are baseball-truth lifts only. They are not Kalshi EV yet.")
    md.append("- Best use: decide which context tiers deserve to become candidate filters or EV modifiers.")
    md.append("")

    md.append("## Files Written")
    md.append("")
    for name in [
        "tier_audit_summary.md",
        "season_input_health.csv",
        "response_input_health.csv",
        "comeback_baseline_by_season_state.csv",
        "lead_baseline_by_season_state.csv",
        "response_baseline_by_season_inning.csv",
        "tier_stability_all.csv",
        "stable_positive_tier_lifts.csv",
        "stable_negative_tier_lifts.csv",
        "mixed_or_noisy_tier_lifts.csv",
        "fair_probability_seed_table.csv",
    ]:
        md.append(f"- {name}")

    (OUT_DIR / "tier_audit_summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {OUT_DIR / 'tier_audit_summary.md'}")
    print(f"State rows: {len(all_state_rows):,}")
    print(f"Response rows: {len(all_response_rows):,}")
    print(f"Stable positive lifts: {len(stable_positive):,}")
    print(f"Stable negative lifts: {len(stable_negative):,}")


if __name__ == "__main__":
    main()
