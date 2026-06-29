import argparse
import csv
import json
import math
import re
import sqlite3
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from statistics import pstdev
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "pregame_feature_family_lift_preview"

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
    "2026": "2026-03-27",
}

OUTCOMES = [
    "team_won",
    "team_runs_4plus",
    "team_runs_5plus",
    "opponent_runs_4plus",
    "opponent_runs_5plus",
    "game_total_9plus",
    "game_total_10plus",
    "f5_total_4plus",
    "f5_total_5plus",
    "team_f5_runs_2plus",
    "team_post5_runs_2plus",
    "team_trailed_early_1to3",
    "team_early_deficit_tied_or_led_later",
    "team_early_deficit_scored_next2",
    "opponent_blew_early_small_lead",
]

FEATURE_FAMILIES = {
    "team_quality": [
        "home_away",
        "team_strength_bucket",
        "opponent_strength_bucket",
        "team_strength_gap_bucket",
        "team_context_confidence",
    ],
    "offense_consistency": [
        "l10_rpg_bucket",
        "l10_scored4_rate_bucket",
        "l10_scored5_rate_bucket",
        "l10_scored2minus_rate_bucket",
        "offense_form_bucket",
    ],
    "opponent_vulnerability": [
        "opponent_l10_allowed4_rate_bucket",
        "opponent_l10_allowed5_rate_bucket",
        "opponent_l10_allowed2minus_rate_bucket",
        "opponent_run_prevention_bucket",
    ],
    "starter_quality": [
        "starter_confidence",
        "opponent_starter_confidence",
        "opponent_starter_ra9_bucket",
        "opponent_starter_ip_bucket",
        "opponent_starter_kbb_bucket",
        "opponent_starter_xfip_bucket",
        "starter_xfip_gap_bucket",
        "starter_quality_gap_bucket",
    ],
    "starter_volatility": [
        "opponent_starter_bad_start_rate_bucket",
        "opponent_starter_blowup_rate_bucket",
        "opponent_starter_early_exit_rate_bucket",
        "opponent_starter_ra_std_bucket",
    ],
    "f5_post5_identity": [
        "team_l10_f5_rpg_bucket",
        "team_l10_post5_rpg_bucket",
        "opponent_l10_f5_allowed_bucket",
        "opponent_l10_post5_allowed_bucket",
        "f5_style_bucket",
    ],
    "combo_tags": [
        "tag_home_scoring_spot",
        "tag_strong_offense_vs_weak_opp",
        "tag_strong_offense_vs_vulnerable_starter",
        "tag_weak_leader_fade_watch",
        "tag_live_rebound_watch",
        "tag_low_run_environment_risk",
        "tag_short_leash_bullpen_exposure",
    ],
}

TWO_FEATURE_COMBOS = [
    ("home_away", "l10_rpg_bucket"),
    ("home_away", "opponent_strength_bucket"),
    ("l10_rpg_bucket", "opponent_strength_bucket"),
    ("l10_scored4_rate_bucket", "opponent_l10_allowed4_rate_bucket"),
    ("offense_form_bucket", "opponent_starter_ra9_bucket"),
    ("l10_rpg_bucket", "opponent_starter_xfip_bucket"),
    ("team_strength_bucket", "opponent_strength_bucket"),
    ("team_l10_f5_rpg_bucket", "opponent_l10_f5_allowed_bucket"),
    ("team_l10_post5_rpg_bucket", "opponent_l10_post5_allowed_bucket"),
    ("opponent_starter_ip_bucket", "opponent_l10_post5_allowed_bucket"),
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


def bucket_gap(v: float | None) -> str:
    if v is None:
        return "missing"
    if v <= -10:
        return "minus_10_or_worse"
    if v <= -5:
        return "minus_5_to_10"
    if v < 5:
        return "neutral_minus5_plus5"
    if v < 10:
        return "plus_5_to_10"
    return "plus_10_plus"


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


def bucket_rate(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 0.30:
        return "low_lt_30"
    if v < 0.45:
        return "below_avg_30_45"
    if v < 0.60:
        return "avg_45_60"
    if v < 0.75:
        return "high_60_75"
    return "very_high_75_plus"


def bucket_ip(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 4.3:
        return "short_lt_4_3"
    if v < 5.0:
        return "below_avg_4_3_5_0"
    if v < 5.8:
        return "normal_5_0_5_8"
    if v < 6.4:
        return "deep_5_8_6_4"
    return "workhorse_6_4_plus"


def bucket_ra9(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 3.5:
        return "excellent_lt_3_5"
    if v < 4.25:
        return "good_3_5_4_25"
    if v < 5.0:
        return "avg_4_25_5_0"
    if v < 6.0:
        return "bad_5_0_6_0"
    return "very_bad_6_plus"


def bucket_xfip(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 3.75:
        return "excellent_lt_3_75"
    if v < 4.25:
        return "good_3_75_4_25"
    if v < 4.75:
        return "avg_4_25_4_75"
    if v < 5.25:
        return "bad_4_75_5_25"
    return "very_bad_5_25_plus"


def bucket_kbb(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 0.08:
        return "weak_lt_8"
    if v < 0.13:
        return "below_avg_8_13"
    if v < 0.18:
        return "solid_13_18"
    if v < 0.23:
        return "strong_18_23"
    return "elite_23_plus"


def bucket_std(v: float | None) -> str:
    if v is None:
        return "missing"
    if v < 1.25:
        return "steady_lt_1_25"
    if v < 2.0:
        return "normal_1_25_2_0"
    if v < 3.0:
        return "volatile_2_0_3_0"
    return "chaotic_3_plus"


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


def ctx_float(ctx: dict | None, field: str) -> float | None:
    if not ctx:
        return None
    return as_float(ctx.get(field))


def context_confidence(ctx: dict | None) -> str:
    if not ctx:
        return "missing"
    return ctx.get("context_confidence") or ctx.get("confidence") or "unknown"


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def pick_col(cols: list[str], candidates: list[str]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


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


def build_event_query(conn: sqlite3.Connection) -> tuple[str, dict]:
    cols = table_columns(conn, "mlb_play_events")
    pitcher_id_col = pick_col(cols, ["pitcher_id", "pitcher_mlbam", "pitcher", "pitcherId"])
    pitcher_name_col = pick_col(cols, ["pitcher_name", "player_name", "pitcher_full_name"])
    batter_id_col = pick_col(cols, ["batter_id", "batter_mlbam", "batter", "batterId"])
    event_time_col = pick_col(cols, ["event_time", "created_at", "timestamp", "play_time"])
    event_type_col = pick_col(cols, ["event_type", "type", "result", "event"])
    desc_col = pick_col(cols, ["description", "play_description", "des", "details_description"])
    inning_col = pick_col(cols, ["inning"])
    half_col = pick_col(cols, ["inning_half", "half_inning", "top_bottom"])
    away_score_col = pick_col(cols, ["away_score"])
    home_score_col = pick_col(cols, ["home_score"])
    game_pk_col = pick_col(cols, ["game_pk", "game_id"])
    raw_json_col = pick_col(cols, ["raw_json", "raw", "json"])

    required = {
        "game_pk": game_pk_col,
        "inning": inning_col,
        "inning_half": half_col,
        "away_score": away_score_col,
        "home_score": home_score_col,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"mlb_play_events missing required columns: {missing}. Found columns: {cols}")

    select_parts = [
        f"{game_pk_col} AS game_pk",
        f"{event_time_col} AS event_time" if event_time_col else "NULL AS event_time",
        f"{inning_col} AS inning",
        f"{half_col} AS inning_half",
        f"{event_type_col} AS event_type" if event_type_col else "NULL AS event_type",
        f"{desc_col} AS description" if desc_col else "NULL AS description",
        f"{away_score_col} AS away_score",
        f"{home_score_col} AS home_score",
        f"{pitcher_id_col} AS pitcher_id" if pitcher_id_col else "NULL AS pitcher_id",
        f"{pitcher_name_col} AS pitcher_name" if pitcher_name_col else "NULL AS pitcher_name",
        f"{batter_id_col} AS batter_id" if batter_id_col else "NULL AS batter_id",
        f"{raw_json_col} AS raw_json" if raw_json_col else "NULL AS raw_json",
    ]
    query = "SELECT " + ", ".join(select_parts) + " FROM mlb_play_events"
    meta = {
        "pitcher_id_col": pitcher_id_col or "",
        "pitcher_name_col": pitcher_name_col or "",
        "raw_json_col": raw_json_col or "",
        "event_type_col": event_type_col or "",
        "description_col": desc_col or "",
    }
    return query, meta


def normalize_pitcher_key(pitcher_id: str | None, pitcher_name: str | None) -> str:
    if pitcher_id:
        return f"id:{str(pitcher_id).strip()}"
    name = str(pitcher_name or "").strip().lower()
    if not name:
        return ""
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return f"name:{name}"


def pitcher_from_raw_json(raw: Any) -> tuple[str, str]:
    if not raw:
        return "", ""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return "", ""

    candidates = []

    def walk(x):
        if isinstance(x, dict):
            candidates.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)

    for d in candidates:
        if "pitcher" in d and isinstance(d.get("pitcher"), dict):
            p = d["pitcher"]
            pid = str(p.get("id") or p.get("mlbam") or "").strip()
            pname = str(p.get("fullName") or p.get("name") or p.get("displayName") or "").strip()
            if pid or pname:
                return pid, pname
        if d.get("type") == "pitcher" or d.get("role") == "pitcher":
            pid = str(d.get("id") or d.get("mlbam") or "").strip()
            pname = str(d.get("fullName") or d.get("name") or d.get("displayName") or "").strip()
            if pid or pname:
                return pid, pname
    return "", ""


def load_events(conn: sqlite3.Connection, games: dict) -> tuple[dict, dict]:
    base_query, meta = build_event_query(conn)
    game_pks = list(games.keys())
    events_by_game = defaultdict(list)
    if not game_pks:
        return events_by_game, meta

    chunk_size = 500
    for i in range(0, len(game_pks), chunk_size):
        chunk = game_pks[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            {base_query}
            WHERE game_pk IN ({placeholders})
              AND inning IS NOT NULL
              AND away_score IS NOT NULL
              AND home_score IS NOT NULL
            ORDER BY game_pk, inning, COALESCE(event_time, '')
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

            pitcher_id = str(r[8]).strip() if r[8] not in {None, ""} else ""
            pitcher_name = str(r[9]).strip() if r[9] not in {None, ""} else ""
            raw_pitcher_id, raw_pitcher_name = pitcher_from_raw_json(r[11] if len(r) > 11 else None)
            if not pitcher_id and raw_pitcher_id:
                pitcher_id = raw_pitcher_id
            if not pitcher_name and raw_pitcher_name:
                pitcher_name = raw_pitcher_name

            pitcher_key = normalize_pitcher_key(pitcher_id, pitcher_name)

            events_by_game[game_pk].append({
                "event_index": len(events_by_game[game_pk]),
                "game_pk": game_pk,
                "event_time": r[1],
                "inning": inning,
                "inning_half": r[3],
                "event_type": str(r[4] or ""),
                "description": str(r[5] or ""),
                "away_score": away_score,
                "home_score": home_score,
                "pitcher_id": pitcher_id,
                "pitcher_name": pitcher_name,
                "pitcher_key": pitcher_key,
                "batter_id": str(r[10]).strip() if r[10] not in {None, ""} else "",
            })
    return events_by_game, meta


def pitching_team_for_event(game: dict, event: dict) -> str | None:
    half = str(event.get("inning_half") or "").lower()
    if "top" in half or half in {"t", "away"}:
        return game["home_abbr"]
    if "bottom" in half or half in {"bot", "b", "home"}:
        return game["away_abbr"]
    return None


def batting_team_for_event(game: dict, event: dict) -> str | None:
    half = str(event.get("inning_half") or "").lower()
    if "top" in half or half in {"t", "away"}:
        return game["away_abbr"]
    if "bottom" in half or half in {"bot", "b", "home"}:
        return game["home_abbr"]
    return None


def event_text(event: dict) -> str:
    return f"{event.get('event_type','')} {event.get('description','')}".lower()


def estimated_outs(event: dict) -> int:
    txt = event_text(event)
    et = str(event.get("event_type") or "").lower()
    if "triple play" in txt:
        return 3
    if "double play" in txt:
        return 2
    if any(x in et for x in ["strikeout", "groundout", "flyout", "lineout", "popup", "pop out", "forceout", "fielders choice out", "sac fly", "sac bunt"]):
        return 1
    if " out" in txt and not any(x in txt for x in ["walk", "single", "double", "triple", "home run", "hit by pitch"]):
        return 1
    return 0


def contact_bucket(event: dict) -> str:
    txt = event_text(event)
    if "home run" in txt or "homer" in txt:
        return "home_run"
    if "ground" in txt:
        return "ground_ball"
    if "line" in txt:
        return "line_drive"
    if "popup" in txt or "pop out" in txt or "pop fly" in txt:
        return "popup"
    if "fly" in txt or "sac fly" in txt:
        return "fly_ball"
    return "unknown"


def pitcher_event_flags(event: dict) -> dict:
    txt = event_text(event)
    et = str(event.get("event_type") or "").lower()
    contact = contact_bucket(event)
    return {
        "outs": estimated_outs(event),
        "strikeout": 1 if "strikeout" in et or "strikes out" in txt else 0,
        "walk": 1 if ("walk" in et or "walks" in txt or "intent walk" in txt) and "hit by pitch" not in txt else 0,
        "hbp": 1 if "hit by pitch" in txt or "hit_by_pitch" in et else 0,
        "home_run": 1 if contact == "home_run" else 0,
        "fly_ball": 1 if contact == "fly_ball" else 0,
        "ground_ball": 1 if contact == "ground_ball" else 0,
        "line_drive": 1 if contact == "line_drive" else 0,
        "popup": 1 if contact == "popup" else 0,
        "batted_ball": 1 if contact in {"home_run", "fly_ball", "ground_ball", "line_drive", "popup"} else 0,
    }


def inning_run_splits(events: list[dict], final_away: int, final_home: int) -> dict:
    inning_runs = defaultdict(lambda: {"away": 0, "home": 0})
    prev_away = 0
    prev_home = 0
    for ev in sorted(events, key=lambda x: (x["inning"], str(x.get("event_time") or ""), x["event_index"])):
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
    return {
        "f5_away": f5_away,
        "f5_home": f5_home,
        "f5_total": f5_total,
        "post5_away": final_away - f5_away,
        "post5_home": final_home - f5_home,
        "post5_runs": full_total - f5_total,
    }


def team_actuals(game: dict, split: dict, team: str) -> dict:
    is_home = team == game["home_abbr"]
    runs = game["final_home_score"] if is_home else game["final_away_score"]
    allowed = game["final_away_score"] if is_home else game["final_home_score"]
    f5_runs = split["f5_home"] if is_home else split["f5_away"]
    f5_allowed = split["f5_away"] if is_home else split["f5_home"]
    post5_runs = split["post5_home"] if is_home else split["post5_away"]
    post5_allowed = split["post5_away"] if is_home else split["post5_home"]
    return {
        "runs": runs,
        "allowed": allowed,
        "f5_runs": f5_runs,
        "f5_allowed": f5_allowed,
        "post5_runs": post5_runs,
        "post5_allowed": post5_allowed,
        "won": 1 if runs > allowed else 0,
    }


def early_deficit_outcomes(game: dict, events: list[dict], team: str) -> dict:
    is_home = team == game["home_abbr"]
    side = "home" if is_home else "away"
    early_states = []
    for idx, ev in enumerate(events):
        if ev["inning"] > 3:
            continue
        team_score = ev["home_score"] if is_home else ev["away_score"]
        opp_score = ev["away_score"] if is_home else ev["home_score"]
        deficit = opp_score - team_score
        if 1 <= deficit <= 3:
            later = events[idx + 1:]
            next2 = [x for x in later if x["inning"] <= ev["inning"] + 2]
            later_margins = []
            next2_team_scored = 0
            for x in later:
                ts = x["home_score"] if is_home else x["away_score"]
                os = x["away_score"] if is_home else x["home_score"]
                later_margins.append(ts - os)
            for x in next2:
                ts = x["home_score"] if is_home else x["away_score"]
                if ts > team_score:
                    next2_team_scored = 1
                    break
            early_states.append({
                "deficit": deficit,
                "tied_or_led_later": 1 if any(m >= 0 for m in later_margins) else 0,
                "scored_next2": next2_team_scored,
            })

    if not early_states:
        return {
            "team_trailed_early_1to3": 0,
            "team_early_deficit_tied_or_led_later": 0,
            "team_early_deficit_scored_next2": 0,
            "opponent_blew_early_small_lead": 0,
        }

    return {
        "team_trailed_early_1to3": 1,
        "team_early_deficit_tied_or_led_later": 1 if any(s["tied_or_led_later"] for s in early_states) else 0,
        "team_early_deficit_scored_next2": 1 if any(s["scored_next2"] for s in early_states) else 0,
        "opponent_blew_early_small_lead": 1 if any(s["tied_or_led_later"] for s in early_states) else 0,
    }


def aggregate_pitching_by_game_team(games: dict, events_by_game: dict) -> tuple[dict, dict]:
    lines = {}
    starters = {}

    for game_pk, game in games.items():
        events = events_by_game.get(game_pk, [])
        if not events:
            continue

        prev_away = 0
        prev_home = 0
        line_by_team_pitcher = defaultdict(lambda: {
            "outs": 0, "runs_allowed": 0, "strikeouts": 0, "walks": 0, "hbp": 0,
            "home_runs": 0, "fly_balls": 0, "ground_balls": 0, "line_drives": 0,
            "popups": 0, "batted_balls": 0, "events": 0, "pitcher_name": "",
        })

        for ev in sorted(events, key=lambda x: (x["inning"], str(x.get("event_time") or ""), x["event_index"])):
            pitching_team = pitching_team_for_event(game, ev)
            batting_team = batting_team_for_event(game, ev)
            pitcher_key = ev.get("pitcher_key") or ""
            pitcher_name = ev.get("pitcher_name") or ""

            da = max(0, ev["away_score"] - prev_away)
            dh = max(0, ev["home_score"] - prev_home)
            prev_away = max(prev_away, ev["away_score"])
            prev_home = max(prev_home, ev["home_score"])

            if not pitching_team or not pitcher_key:
                continue

            if (game_pk, pitching_team) not in starters:
                starters[(game_pk, pitching_team)] = {"pitcher_key": pitcher_key, "pitcher_name": pitcher_name}

            flags = pitcher_event_flags(ev)
            runs_allowed = dh if batting_team == game["home_abbr"] else da

            line = line_by_team_pitcher[(pitching_team, pitcher_key)]
            line["pitcher_name"] = pitcher_name or line["pitcher_name"]
            line["outs"] += flags["outs"]
            line["runs_allowed"] += runs_allowed
            line["strikeouts"] += flags["strikeout"]
            line["walks"] += flags["walk"]
            line["hbp"] += flags["hbp"]
            line["home_runs"] += flags["home_run"]
            line["fly_balls"] += flags["fly_ball"]
            line["ground_balls"] += flags["ground_ball"]
            line["line_drives"] += flags["line_drive"]
            line["popups"] += flags["popup"]
            line["batted_balls"] += flags["batted_ball"]
            line["events"] += 1

        for (team, pitcher_key), line in line_by_team_pitcher.items():
            lines[(game_pk, team, pitcher_key)] = {
                **line,
                "game_pk": game_pk,
                "game_date": game["game_date"],
                "team": team,
                "pitcher_key": pitcher_key,
                "pitcher_name": line["pitcher_name"],
                "is_starter": 1 if starters.get((game_pk, team), {}).get("pitcher_key") == pitcher_key else 0,
            }
    return lines, starters


def starter_context_from_history(hist: list[dict], league_hr_per_fb: float, xfip_constant: float) -> dict:
    if not hist:
        return {
            "starter_history_starts": 0,
            "starter_history_outs": 0,
            "starter_ip_per_start": None,
            "starter_ra9": None,
            "starter_k_pct": None,
            "starter_bb_pct": None,
            "starter_kbb_pct": None,
            "starter_hr_per_fb": None,
            "starter_gb_pct": None,
            "starter_xfip": None,
            "starter_bad_start_rate": None,
            "starter_blowup_rate": None,
            "starter_early_exit_rate": None,
            "starter_ra_std": None,
            "starter_confidence": "none",
        }

    starts = len(hist)
    outs = sum(h["outs"] for h in hist)
    ip = outs / 3 if outs else 0
    events = sum(h["events"] for h in hist)
    batted = sum(h["batted_balls"] for h in hist)
    fb = sum(h["fly_balls"] for h in hist)
    gb = sum(h["ground_balls"] for h in hist)
    hr = sum(h["home_runs"] for h in hist)
    k = sum(h["strikeouts"] for h in hist)
    bb = sum(h["walks"] for h in hist)
    hbp = sum(h["hbp"] for h in hist)
    runs = sum(h["runs_allowed"] for h in hist)

    ip_per_start = ip / starts if starts else None
    ra9 = (runs * 9 / ip) if ip else None
    k_pct = k / events if events else None
    bb_pct = (bb + hbp) / events if events else None
    kbb_pct = k_pct - bb_pct if k_pct is not None and bb_pct is not None else None
    hr_fb = hr / fb if fb else None
    gb_pct = gb / batted if batted else None

    per_start_runs = [h["runs_allowed"] for h in hist]
    per_start_outs = [h["outs"] for h in hist]
    bad_start_rate = sum(1 for x in per_start_runs if x >= 3) / starts if starts else None
    blowup_rate = sum(1 for x in per_start_runs if x >= 5) / starts if starts else None
    early_exit_rate = sum(1 for x in per_start_outs if x < 15) / starts if starts else None
    ra_std = pstdev(per_start_runs) if len(per_start_runs) >= 2 else 0.0

    xfip = None
    if ip:
        xhr = fb * league_hr_per_fb
        raw = ((13 * xhr) + 3 * (bb + hbp) - 2 * k) / ip
        xfip = raw + xfip_constant

    if starts >= 5 and outs >= 60:
        conf = "high"
    elif starts >= 3 and outs >= 36:
        conf = "medium"
    elif starts >= 1:
        conf = "low"
    else:
        conf = "none"

    return {
        "starter_history_starts": starts,
        "starter_history_outs": outs,
        "starter_ip_per_start": round(ip_per_start, 3) if ip_per_start is not None else None,
        "starter_ra9": round(ra9, 3) if ra9 is not None else None,
        "starter_k_pct": round(k_pct, 4) if k_pct is not None else None,
        "starter_bb_pct": round(bb_pct, 4) if bb_pct is not None else None,
        "starter_kbb_pct": round(kbb_pct, 4) if kbb_pct is not None else None,
        "starter_hr_per_fb": round(hr_fb, 4) if hr_fb is not None else None,
        "starter_gb_pct": round(gb_pct, 4) if gb_pct is not None else None,
        "starter_xfip": round(xfip, 3) if xfip is not None else None,
        "starter_bad_start_rate": round(bad_start_rate, 4) if bad_start_rate is not None else None,
        "starter_blowup_rate": round(blowup_rate, 4) if blowup_rate is not None else None,
        "starter_early_exit_rate": round(early_exit_rate, 4) if early_exit_rate is not None else None,
        "starter_ra_std": round(ra_std, 3),
        "starter_confidence": conf,
    }


def team_context_from_history(hist: list[dict]) -> dict:
    if not hist:
        return {
            "l10_games": 0,
            "l10_rpg": None,
            "l10_allowed_pg": None,
            "l10_scored4_rate": None,
            "l10_scored5_rate": None,
            "l10_scored2minus_rate": None,
            "l10_allowed4_rate": None,
            "l10_allowed5_rate": None,
            "l10_allowed2minus_rate": None,
            "l10_f5_rpg": None,
            "l10_f5_allowed_pg": None,
            "l10_post5_rpg": None,
            "l10_post5_allowed_pg": None,
        }

    n = len(hist)
    runs = [h["runs"] for h in hist]
    allowed = [h["allowed"] for h in hist]
    f5_runs = [h["f5_runs"] for h in hist]
    f5_allowed = [h["f5_allowed"] for h in hist]
    post5_runs = [h["post5_runs"] for h in hist]
    post5_allowed = [h["post5_allowed"] for h in hist]

    return {
        "l10_games": n,
        "l10_rpg": round(sum(runs) / n, 3),
        "l10_allowed_pg": round(sum(allowed) / n, 3),
        "l10_scored4_rate": round(sum(1 for x in runs if x >= 4) / n, 4),
        "l10_scored5_rate": round(sum(1 for x in runs if x >= 5) / n, 4),
        "l10_scored2minus_rate": round(sum(1 for x in runs if x <= 2) / n, 4),
        "l10_allowed4_rate": round(sum(1 for x in allowed if x >= 4) / n, 4),
        "l10_allowed5_rate": round(sum(1 for x in allowed if x >= 5) / n, 4),
        "l10_allowed2minus_rate": round(sum(1 for x in allowed if x <= 2) / n, 4),
        "l10_f5_rpg": round(sum(f5_runs) / n, 3),
        "l10_f5_allowed_pg": round(sum(f5_allowed) / n, 3),
        "l10_post5_rpg": round(sum(post5_runs) / n, 3),
        "l10_post5_allowed_pg": round(sum(post5_allowed) / n, 3),
    }


def build_rows_for_season(conn: sqlite3.Connection, season: str, rolling_games: int, rolling_starts: int) -> tuple[list[dict], dict]:
    regular_start = DEFAULT_REGULAR_START.get(str(season))
    context_map, context_path = load_context(str(season))
    games = load_final_games(conn, str(season), regular_start)
    events_by_game, event_meta = load_events(conn, games)
    pitching_lines, starter_by_game_team = aggregate_pitching_by_game_team(games, events_by_game)

    total_hr = sum(v["home_runs"] for v in pitching_lines.values())
    total_fb = sum(v["fly_balls"] for v in pitching_lines.values())
    total_runs = sum(v["runs_allowed"] for v in pitching_lines.values())
    total_outs = sum(v["outs"] for v in pitching_lines.values())
    total_walk_hbp = sum(v["walks"] + v["hbp"] for v in pitching_lines.values())
    total_k = sum(v["strikeouts"] for v in pitching_lines.values())
    league_hr_per_fb = total_hr / total_fb if total_fb else 0.11
    league_era = (total_runs * 27 / total_outs) if total_outs else 4.5
    if total_outs:
        ip = total_outs / 3
        xhr = total_fb * league_hr_per_fb
        raw = ((13 * xhr) + 3 * total_walk_hbp - 2 * total_k) / ip
    else:
        raw = 0.0
    xfip_constant = league_era - raw

    # Process all games by date. Features for all games on date are created before updating histories.
    games_by_date = defaultdict(list)
    for g in games.values():
        games_by_date[g["game_date"]].append(g)

    team_hist = defaultdict(lambda: deque(maxlen=rolling_games))
    starter_hist = defaultdict(lambda: deque(maxlen=rolling_starts))
    output_rows = []

    for game_date in sorted(games_by_date):
        date_games = sorted(games_by_date[game_date], key=lambda g: (str(g.get("game_start_time_utc") or ""), g["game_pk"]))

        # First create rows with histories as of PRIOR dates only.
        for game in date_games:
            events = events_by_game.get(game["game_pk"], [])
            if not events:
                continue

            split = inning_run_splits(events, game["final_away_score"], game["final_home_score"])
            actual_total = game["final_total"]
            for team, opponent, is_home in [
                (game["away_abbr"], game["home_abbr"], False),
                (game["home_abbr"], game["away_abbr"], True),
            ]:
                side_actual = team_actuals(game, split, team)
                opponent_actual = team_actuals(game, split, opponent)
                live_watch_actuals = early_deficit_outcomes(game, events, team)

                team_ctx = context_map.get((game["game_pk"], team))
                opp_ctx = context_map.get((game["game_pk"], opponent))
                team_roll = team_context_from_history(list(team_hist[team]))
                opp_roll = team_context_from_history(list(team_hist[opponent]))

                starter_info = starter_by_game_team.get((game["game_pk"], team), {})
                opp_starter_info = starter_by_game_team.get((game["game_pk"], opponent), {})
                starter_key = starter_info.get("pitcher_key", "")
                opp_starter_key = opp_starter_info.get("pitcher_key", "")

                starter_pre = starter_context_from_history(list(starter_hist[starter_key]), league_hr_per_fb, xfip_constant) if starter_key else starter_context_from_history([], league_hr_per_fb, xfip_constant)
                opp_starter_pre = starter_context_from_history(list(starter_hist[opp_starter_key]), league_hr_per_fb, xfip_constant) if opp_starter_key else starter_context_from_history([], league_hr_per_fb, xfip_constant)

                team_strength = ctx_float(team_ctx, "team_strength_proxy")
                opp_strength = ctx_float(opp_ctx, "team_strength_proxy")
                offense_form = ctx_float(team_ctx, "offense_form_proxy")
                opp_run_prev = ctx_float(opp_ctx, "run_prevention_proxy")
                strength_gap = (team_strength - opp_strength) if team_strength is not None and opp_strength is not None else None

                starter_xfip_gap = None
                if starter_pre.get("starter_xfip") is not None and opp_starter_pre.get("starter_xfip") is not None:
                    # Negative means team's starter has better xFIP than opponent starter, so invert for batting/team advantage.
                    starter_xfip_gap = as_float(opp_starter_pre.get("starter_xfip")) - as_float(starter_pre.get("starter_xfip"))

                starter_quality_gap = None
                if starter_pre.get("starter_ra9") is not None and opp_starter_pre.get("starter_ra9") is not None:
                    # Positive means opponent starter is more vulnerable than team's starter.
                    starter_quality_gap = as_float(opp_starter_pre.get("starter_ra9")) - as_float(starter_pre.get("starter_ra9"))

                f5_style = "missing"
                team_f5 = as_float(team_roll.get("l10_f5_rpg"))
                team_post5 = as_float(team_roll.get("l10_post5_rpg"))
                if team_f5 is not None and team_post5 is not None:
                    if team_f5 >= 2.5 and team_post5 >= 2.0:
                        f5_style = "early_and_late_scoring"
                    elif team_f5 >= 2.5:
                        f5_style = "early_scoring"
                    elif team_post5 >= 2.0:
                        f5_style = "late_scoring"
                    else:
                        f5_style = "low_scoring_profile"

                tag_home_scoring_spot = is_home and bucket_rpg(as_float(team_roll.get("l10_rpg"))) in {"high_4_5_5_5", "very_high_5_5_plus"}
                tag_strong_offense_vs_weak_opp = (
                    bucket_rate(as_float(team_roll.get("l10_scored4_rate"))) in {"high_60_75", "very_high_75_plus"}
                    and bucket_rate(as_float(opp_roll.get("l10_allowed4_rate"))) in {"high_60_75", "very_high_75_plus"}
                )
                tag_strong_offense_vs_vulnerable_starter = (
                    bucket_rate(as_float(team_roll.get("l10_scored4_rate"))) in {"high_60_75", "very_high_75_plus"}
                    and bucket_ra9(as_float(opp_starter_pre.get("starter_ra9"))) in {"bad_5_0_6_0", "very_bad_6_plus"}
                )
                tag_weak_leader_fade_watch = (
                    bucket_rating(opp_strength) in {"lt_40", "40_45"}
                    and bucket_rate(as_float(team_roll.get("l10_scored4_rate"))) in {"high_60_75", "very_high_75_plus"}
                )
                tag_live_rebound_watch = (
                    is_home
                    and bucket_rating(team_strength) in {"50_55", "55_60", "60_plus"}
                    and bucket_rating(opp_strength) in {"lt_40", "40_45", "45_50"}
                )
                tag_low_run_environment_risk = (
                    bucket_rate(as_float(team_roll.get("l10_scored2minus_rate"))) in {"high_60_75", "very_high_75_plus"}
                    or bucket_rate(as_float(opp_roll.get("l10_allowed2minus_rate"))) in {"high_60_75", "very_high_75_plus"}
                )
                tag_short_leash_bullpen_exposure = (
                    bucket_ip(as_float(opp_starter_pre.get("starter_ip_per_start"))) in {"short_lt_4_3", "below_avg_4_3_5_0"}
                    and bucket_rpg(as_float(opp_roll.get("l10_post5_allowed_pg"))) in {"high_4_5_5_5", "very_high_5_5_plus"}
                )

                row = {
                    "season": season,
                    "game_pk": game["game_pk"],
                    "game_date": game["game_date"],
                    "game_id": f"{game['away_abbr']}@{game['home_abbr']}",
                    "team": team,
                    "opponent": opponent,
                    "home_away": "home" if is_home else "away",

                    # outcomes
                    "team_won": side_actual["won"],
                    "team_runs_4plus": 1 if side_actual["runs"] >= 4 else 0,
                    "team_runs_5plus": 1 if side_actual["runs"] >= 5 else 0,
                    "opponent_runs_4plus": 1 if side_actual["allowed"] >= 4 else 0,
                    "opponent_runs_5plus": 1 if side_actual["allowed"] >= 5 else 0,
                    "game_total_9plus": 1 if actual_total >= 9 else 0,
                    "game_total_10plus": 1 if actual_total >= 10 else 0,
                    "f5_total_4plus": 1 if split["f5_total"] >= 4 else 0,
                    "f5_total_5plus": 1 if split["f5_total"] >= 5 else 0,
                    "team_f5_runs_2plus": 1 if side_actual["f5_runs"] >= 2 else 0,
                    "team_post5_runs_2plus": 1 if side_actual["post5_runs"] >= 2 else 0,
                    **live_watch_actuals,

                    # raw actuals
                    "team_runs": side_actual["runs"],
                    "opponent_runs": side_actual["allowed"],
                    "f5_team_runs": side_actual["f5_runs"],
                    "f5_opponent_runs": side_actual["f5_allowed"],
                    "post5_team_runs": side_actual["post5_runs"],
                    "post5_opponent_runs": side_actual["post5_allowed"],
                    "full_total": actual_total,
                    "f5_total": split["f5_total"],

                    # context raw
                    "team_strength": team_strength,
                    "opponent_strength": opp_strength,
                    "team_strength_gap": strength_gap,
                    "offense_form": offense_form,
                    "opponent_run_prevention": opp_run_prev,
                    "team_context_confidence": context_confidence(team_ctx),
                    "opponent_context_confidence": context_confidence(opp_ctx),

                    # rolling raw
                    **{f"team_{k}": v for k, v in team_roll.items()},
                    **{f"opponent_{k}": v for k, v in opp_roll.items()},

                    # starter raw
                    "starter_key": starter_key,
                    "starter_name": starter_info.get("pitcher_name", ""),
                    "opponent_starter_key": opp_starter_key,
                    "opponent_starter_name": opp_starter_info.get("pitcher_name", ""),
                    **{f"starter_{k}": v for k, v in starter_pre.items()},
                    **{f"opponent_{k}": v for k, v in opp_starter_pre.items()},
                    "starter_xfip_gap": starter_xfip_gap,
                    "starter_quality_gap": starter_quality_gap,

                    # feature buckets
                    "team_strength_bucket": bucket_rating(team_strength),
                    "opponent_strength_bucket": bucket_rating(opp_strength),
                    "team_strength_gap_bucket": bucket_gap(strength_gap),
                    "offense_form_bucket": bucket_rating(offense_form),
                    "opponent_run_prevention_bucket": bucket_rating(opp_run_prev),
                    "l10_rpg_bucket": bucket_rpg(as_float(team_roll.get("l10_rpg"))),
                    "l10_scored4_rate_bucket": bucket_rate(as_float(team_roll.get("l10_scored4_rate"))),
                    "l10_scored5_rate_bucket": bucket_rate(as_float(team_roll.get("l10_scored5_rate"))),
                    "l10_scored2minus_rate_bucket": bucket_rate(as_float(team_roll.get("l10_scored2minus_rate"))),
                    "opponent_l10_allowed4_rate_bucket": bucket_rate(as_float(opp_roll.get("l10_allowed4_rate"))),
                    "opponent_l10_allowed5_rate_bucket": bucket_rate(as_float(opp_roll.get("l10_allowed5_rate"))),
                    "opponent_l10_allowed2minus_rate_bucket": bucket_rate(as_float(opp_roll.get("l10_allowed2minus_rate"))),
                    "team_l10_f5_rpg_bucket": bucket_rpg(as_float(team_roll.get("l10_f5_rpg"))),
                    "team_l10_post5_rpg_bucket": bucket_rpg(as_float(team_roll.get("l10_post5_rpg"))),
                    "opponent_l10_f5_allowed_bucket": bucket_rpg(as_float(opp_roll.get("l10_f5_allowed_pg"))),
                    "opponent_l10_post5_allowed_bucket": bucket_rpg(as_float(opp_roll.get("l10_post5_allowed_pg"))),
                    "f5_style_bucket": f5_style,

                    "starter_confidence": starter_pre.get("starter_confidence"),
                    "opponent_starter_confidence": opp_starter_pre.get("starter_confidence"),
                    "opponent_starter_ra9_bucket": bucket_ra9(as_float(opp_starter_pre.get("starter_ra9"))),
                    "opponent_starter_ip_bucket": bucket_ip(as_float(opp_starter_pre.get("starter_ip_per_start"))),
                    "opponent_starter_kbb_bucket": bucket_kbb(as_float(opp_starter_pre.get("starter_kbb_pct"))),
                    "opponent_starter_xfip_bucket": bucket_xfip(as_float(opp_starter_pre.get("starter_xfip"))),
                    "starter_xfip_gap_bucket": bucket_gap(starter_xfip_gap),
                    "starter_quality_gap_bucket": bucket_gap(starter_quality_gap),
                    "opponent_starter_bad_start_rate_bucket": bucket_rate(as_float(opp_starter_pre.get("starter_bad_start_rate"))),
                    "opponent_starter_blowup_rate_bucket": bucket_rate(as_float(opp_starter_pre.get("starter_blowup_rate"))),
                    "opponent_starter_early_exit_rate_bucket": bucket_rate(as_float(opp_starter_pre.get("starter_early_exit_rate"))),
                    "opponent_starter_ra_std_bucket": bucket_std(as_float(opp_starter_pre.get("starter_ra_std"))),

                    # combo tags
                    "tag_home_scoring_spot": "yes" if tag_home_scoring_spot else "no",
                    "tag_strong_offense_vs_weak_opp": "yes" if tag_strong_offense_vs_weak_opp else "no",
                    "tag_strong_offense_vs_vulnerable_starter": "yes" if tag_strong_offense_vs_vulnerable_starter else "no",
                    "tag_weak_leader_fade_watch": "yes" if tag_weak_leader_fade_watch else "no",
                    "tag_live_rebound_watch": "yes" if tag_live_rebound_watch else "no",
                    "tag_low_run_environment_risk": "yes" if tag_low_run_environment_risk else "no",
                    "tag_short_leash_bullpen_exposure": "yes" if tag_short_leash_bullpen_exposure else "no",
                }
                output_rows.append(row)

        # Now update histories after all games on this date are already scored into feature rows.
        for game in date_games:
            events = events_by_game.get(game["game_pk"], [])
            if not events:
                continue
            split = inning_run_splits(events, game["final_away_score"], game["final_home_score"])
            for team in (game["away_abbr"], game["home_abbr"]):
                actual = team_actuals(game, split, team)
                team_hist[team].append(actual)

            # update starter histories with current game starter lines
            for team in (game["away_abbr"], game["home_abbr"]):
                starter_info = starter_by_game_team.get((game["game_pk"], team), {})
                starter_key = starter_info.get("pitcher_key", "")
                if not starter_key:
                    continue
                line = pitching_lines.get((game["game_pk"], team, starter_key))
                if line:
                    starter_hist[starter_key].append(line)

    meta = {
        "season": season,
        "regular_start": regular_start,
        "context_path": str(context_path) if context_path else "",
        "final_games_loaded": len(games),
        "games_with_events": len(events_by_game),
        "team_game_rows": len(output_rows),
        "pitching_lines": len(pitching_lines),
        "starter_lines": sum(1 for x in pitching_lines.values() if x.get("is_starter") == 1),
        "league_hr_per_fb": round(league_hr_per_fb, 4),
        "league_era_proxy": round(league_era, 3),
        "xfip_constant": round(xfip_constant, 3),
        **event_meta,
    }
    return output_rows, meta


def build_final_state(
    conn: sqlite3.Connection,
    season: str,
    rolling_games: int,
    rolling_starts: int,
) -> tuple:
    """
    Return (team_hist, starter_hist, league_hr_per_fb, xfip_constant) after processing all
    completed games for a season. Used by score_today_slate.py to score today's unplayed games
    without building output rows. No lookahead — only completed games (final scores present).
    """
    regular_start = DEFAULT_REGULAR_START.get(str(season))
    games = load_final_games(conn, str(season), regular_start)
    events_by_game, _ = load_events(conn, games)
    pitching_lines, starter_by_game_team = aggregate_pitching_by_game_team(games, events_by_game)

    total_hr = sum(v["home_runs"] for v in pitching_lines.values())
    total_fb = sum(v["fly_balls"] for v in pitching_lines.values())
    total_runs = sum(v["runs_allowed"] for v in pitching_lines.values())
    total_outs = sum(v["outs"] for v in pitching_lines.values())
    total_walk_hbp = sum(v["walks"] + v["hbp"] for v in pitching_lines.values())
    total_k = sum(v["strikeouts"] for v in pitching_lines.values())
    league_hr_per_fb = total_hr / total_fb if total_fb else 0.11
    league_era = (total_runs * 27 / total_outs) if total_outs else 4.5
    if total_outs:
        ip = total_outs / 3
        xhr = total_fb * league_hr_per_fb
        raw = ((13 * xhr) + 3 * total_walk_hbp - 2 * total_k) / ip
    else:
        raw = 0.0
    xfip_constant = league_era - raw

    games_by_date: dict = defaultdict(list)
    for g in games.values():
        games_by_date[g["game_date"]].append(g)

    team_hist: dict = defaultdict(lambda: deque(maxlen=rolling_games))
    starter_hist: dict = defaultdict(lambda: deque(maxlen=rolling_starts))

    for game_date in sorted(games_by_date):
        date_games = sorted(
            games_by_date[game_date],
            key=lambda g: (str(g.get("game_start_time_utc") or ""), g["game_pk"]),
        )
        for game in date_games:
            events = events_by_game.get(game["game_pk"], [])
            if not events:
                continue
            split = inning_run_splits(events, game["final_away_score"], game["final_home_score"])
            for team in (game["away_abbr"], game["home_abbr"]):
                actual = team_actuals(game, split, team)
                team_hist[team].append(actual)
            for team in (game["away_abbr"], game["home_abbr"]):
                starter_info = starter_by_game_team.get((game["game_pk"], team), {})
                starter_key = starter_info.get("pitcher_key", "")
                if not starter_key:
                    continue
                line = pitching_lines.get((game["game_pk"], team, starter_key))
                if line:
                    starter_hist[starter_key].append(line)

    return dict(team_hist), dict(starter_hist), league_hr_per_fb, xfip_constant


def summarize_feature(rows: list[dict], feature: str, family: str, min_count: int) -> list[dict]:
    base_by_season = defaultdict(lambda: defaultdict(list))
    group = defaultdict(list)

    for r in rows:
        season = str(r["season"])
        value = str(r.get(feature) if r.get(feature) not in {None, ""} else "missing")
        for outcome in OUTCOMES:
            val = as_int(r.get(outcome))
            if val is None:
                continue
            base_by_season[(season, outcome)]["vals"].append(val)
            group[(season, family, feature, value, outcome)].append(val)

    out = []
    for (season, fam, feat, value, outcome), vals in group.items():
        if len(vals) < min_count:
            continue
        base_vals = base_by_season[(season, outcome)]["vals"]
        base_rate = rate(sum(base_vals), len(base_vals))
        feature_rate = rate(sum(vals), len(vals))
        out.append({
            "season": season,
            "family": fam,
            "feature": feat,
            "feature_value": value,
            "outcome": outcome,
            "count": len(vals),
            "feature_rate": feature_rate,
            "baseline_rate": base_rate,
            "lift": round(feature_rate - base_rate, 4) if feature_rate is not None and base_rate is not None else None,
        })
    out.sort(key=lambda r: (r["family"], r["feature"], r["feature_value"], r["outcome"], r["season"]))
    return out


def summarize_combo(rows: list[dict], feature_a: str, feature_b: str, min_count: int) -> list[dict]:
    base_by_season = defaultdict(lambda: defaultdict(list))
    group = defaultdict(list)
    combo_name = f"{feature_a}+{feature_b}"

    for r in rows:
        season = str(r["season"])
        va = str(r.get(feature_a) if r.get(feature_a) not in {None, ""} else "missing")
        vb = str(r.get(feature_b) if r.get(feature_b) not in {None, ""} else "missing")
        value = f"{va}__{vb}"
        for outcome in OUTCOMES:
            val = as_int(r.get(outcome))
            if val is None:
                continue
            base_by_season[(season, outcome)]["vals"].append(val)
            group[(season, combo_name, value, outcome)].append(val)

    out = []
    for (season, combo, value, outcome), vals in group.items():
        if len(vals) < min_count:
            continue
        base_vals = base_by_season[(season, outcome)]["vals"]
        base_rate = rate(sum(base_vals), len(base_vals))
        feature_rate = rate(sum(vals), len(vals))
        out.append({
            "season": season,
            "combo": combo,
            "feature_value": value,
            "outcome": outcome,
            "count": len(vals),
            "feature_rate": feature_rate,
            "baseline_rate": base_rate,
            "lift": round(feature_rate - base_rate, 4) if feature_rate is not None and base_rate is not None else None,
        })
    out.sort(key=lambda r: (r["combo"], r["feature_value"], r["outcome"], r["season"]))
    return out


def stability(rows: list[dict], key_cols: list[str], min_seasons: int = 3) -> list[dict]:
    grouped = defaultdict(list)
    for r in rows:
        key = tuple(r.get(c) for c in key_cols)
        grouped[key].append(r)

    out = []
    for key, rs in grouped.items():
        seasons = sorted(set(str(r["season"]) for r in rs))
        if len(seasons) < 2:
            continue

        lifts = [as_float(r.get("lift")) for r in rs if as_float(r.get("lift")) is not None]
        rates = [as_float(r.get("feature_rate")) for r in rs if as_float(r.get("feature_rate")) is not None]
        counts = [as_int(r.get("count")) or 0 for r in rs]
        if not lifts or not rates:
            continue

        label = "mixed_or_noisy"
        if len(seasons) >= min_seasons and min(lifts) >= 0 and sum(lifts) / len(lifts) >= 0.04:
            label = "stable_positive_lift"
        elif len(seasons) >= min_seasons and max(lifts) <= 0 and sum(lifts) / len(lifts) <= -0.04:
            label = "stable_negative_lift"
        elif len(seasons) < min_seasons:
            label = "partial_seasons"

        row = {c: v for c, v in zip(key_cols, key)}
        row.update({
            "seasons_seen": ",".join(seasons),
            "season_count": len(seasons),
            "total_count": sum(counts),
            "min_season_count": min(counts),
            "avg_rate": round(sum(rates) / len(rates), 4),
            "min_rate": min(rates),
            "max_rate": max(rates),
            "rate_range": round(max(rates) - min(rates), 4),
            "avg_lift": round(sum(lifts) / len(lifts), 4),
            "min_lift": min(lifts),
            "max_lift": max(lifts),
            "stability_label": label,
        })
        out.append(row)

    order = {"stable_positive_lift": 0, "stable_negative_lift": 1, "mixed_or_noisy": 2, "partial_seasons": 3}
    out.sort(key=lambda r: (
        order.get(r["stability_label"], 9),
        -(r.get("total_count") or 0),
        str([r.get(c) for c in key_cols])
    ))
    return out


def choose_best_identifiers(stable_rows: list[dict], positive: bool = True) -> list[dict]:
    desired_outcomes = {
        "team_won",
        "team_runs_4plus",
        "team_runs_5plus",
        "game_total_9plus",
        "f5_total_4plus",
        "team_f5_runs_2plus",
        "team_early_deficit_tied_or_led_later",
        "team_early_deficit_scored_next2",
        "opponent_blew_early_small_lead",
    }
    label = "stable_positive_lift" if positive else "stable_negative_lift"
    rows = [
        r for r in stable_rows
        if r.get("stability_label") == label
        and r.get("outcome") in desired_outcomes
        and (r.get("min_season_count") or 0) >= 100
    ]
    rows.sort(key=lambda r: (-(abs(as_float(r.get("avg_lift")) or 0)), -(r.get("total_count") or 0)))
    return rows[:100]


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only pregame feature-family lift preview with strict no-lookahead rolling features.")
    parser.add_argument("--seasons", nargs="+", default=["2023", "2024", "2025"])
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--rolling-games", type=int, default=10)
    parser.add_argument("--rolling-starts", type=int, default=8)
    parser.add_argument("--min-count", type=int, default=100)
    parser.add_argument("--include-team-game-rows", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)

    all_rows = []
    health_rows = []

    for season in args.seasons:
        rows, meta = build_rows_for_season(conn, str(season), args.rolling_games, args.rolling_starts)
        all_rows.extend(rows)
        health_rows.append(meta)
        print(f"{season}: team-game rows={len(rows):,}, games={meta['final_games_loaded']:,}, starter_lines={meta['starter_lines']:,}")

    write_csv(OUT_DIR / "input_health.csv", health_rows)

    if args.include_team_game_rows:
        write_csv(OUT_DIR / "pregame_team_game_feature_rows.csv", all_rows)

    # Single feature lifts
    single_rows = []
    for family, features in FEATURE_FAMILIES.items():
        for feat in features:
            single_rows.extend(summarize_feature(all_rows, feat, family, args.min_count))
    write_csv(OUT_DIR / "single_feature_lift.csv", single_rows)

    single_stability = stability(single_rows, ["family", "feature", "feature_value", "outcome"])
    write_csv(OUT_DIR / "single_feature_stability.csv", single_stability)

    # Pair combos
    combo_rows = []
    for a, b in TWO_FEATURE_COMBOS:
        combo_rows.extend(summarize_combo(all_rows, a, b, args.min_count))
    write_csv(OUT_DIR / "two_feature_combo_lift.csv", combo_rows)

    combo_stability = stability(combo_rows, ["combo", "feature_value", "outcome"])
    write_csv(OUT_DIR / "two_feature_combo_stability.csv", combo_stability)

    stable_positive = [r for r in single_stability + combo_stability if r["stability_label"] == "stable_positive_lift"]
    stable_negative = [r for r in single_stability + combo_stability if r["stability_label"] == "stable_negative_lift"]
    noisy = [r for r in single_stability + combo_stability if r["stability_label"] == "mixed_or_noisy"]

    write_csv(OUT_DIR / "best_pregame_identifiers.csv", choose_best_identifiers(stable_positive, positive=True))
    write_csv(OUT_DIR / "negative_or_avoid_identifiers.csv", choose_best_identifiers(stable_negative, positive=False))
    write_csv(OUT_DIR / "noisy_or_bad_identifiers.csv", noisy[:500])

    # Family summary
    family_summary = []
    for family in FEATURE_FAMILIES:
        fam_rows = [r for r in single_stability if r.get("family") == family]
        family_summary.append({
            "family": family,
            "stable_positive_count": sum(1 for r in fam_rows if r["stability_label"] == "stable_positive_lift"),
            "stable_negative_count": sum(1 for r in fam_rows if r["stability_label"] == "stable_negative_lift"),
            "mixed_or_noisy_count": sum(1 for r in fam_rows if r["stability_label"] == "mixed_or_noisy"),
            "partial_count": sum(1 for r in fam_rows if r["stability_label"] == "partial_seasons"),
            "best_positive_lift": max([as_float(r.get("avg_lift")) or -999 for r in fam_rows if r["stability_label"] == "stable_positive_lift"], default=None),
            "best_negative_lift": min([as_float(r.get("avg_lift")) or 999 for r in fam_rows if r["stability_label"] == "stable_negative_lift"], default=None),
        })
    write_csv(OUT_DIR / "feature_family_summary.csv", family_summary)

    md = []
    md.append("# Pregame Feature Family Lift Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append("## No-Lookahead Guardrail")
    md.append("")
    md.append("- Team context uses historical context rows keyed by game/team, generated before the game date.")
    md.append("- Rolling team features are built by date, and all games on a date are scored before that date updates the history.")
    md.append("- Starter features are built from pitcher appearances before the game date only.")
    md.append("- The current game's events and final score are used only for outcome grading after feature rows are created.")
    md.append("- No Vegas/Kalshi market prices are used here. This is baseball-truth identifier research only.")
    md.append("")
    md.append("## Input Health")
    md.append("")
    for h in health_rows:
        md.append(
            f"- {h['season']}: games {h['final_games_loaded']:,}, team-game rows {h['team_game_rows']:,}, "
            f"pitching lines {h['pitching_lines']:,}, starter lines {h['starter_lines']:,}, "
            f"league HR/FB {h['league_hr_per_fb']}, xFIP constant {h['xfip_constant']}"
        )
    md.append("")
    md.append("## Feature Family Summary")
    md.append("")
    for f in family_summary:
        md.append(
            f"- {f['family']}: stable positive {f['stable_positive_count']}, "
            f"stable negative {f['stable_negative_count']}, noisy {f['mixed_or_noisy_count']}, "
            f"best positive lift {pct(as_float(f['best_positive_lift']))}, "
            f"best negative lift {pct(as_float(f['best_negative_lift']))}"
        )
    md.append("")
    md.append("## Top Positive Identifiers")
    md.append("")
    for r in choose_best_identifiers(stable_positive, positive=True)[:30]:
        name = r.get("feature") or r.get("combo")
        md.append(
            f"- {r.get('family','combo')} / {name}={r['feature_value']} / {r['outcome']}: "
            f"avg rate {pct(as_float(r['avg_rate']))}, avg lift {pct(as_float(r['avg_lift']))}, "
            f"count {r['total_count']:,}, seasons {r['seasons_seen']}"
        )
    md.append("")
    md.append("## Top Negative / Avoid Identifiers")
    md.append("")
    for r in choose_best_identifiers(stable_negative, positive=False)[:30]:
        name = r.get("feature") or r.get("combo")
        md.append(
            f"- {r.get('family','combo')} / {name}={r['feature_value']} / {r['outcome']}: "
            f"avg rate {pct(as_float(r['avg_rate']))}, avg lift {pct(as_float(r['avg_lift']))}, "
            f"count {r['total_count']:,}, seasons {r['seasons_seen']}"
        )
    md.append("")
    md.append("## Interpretation")
    md.append("")
    md.append("- Stable positive lift means the identifier beat the same-season baseline in all three seasons.")
    md.append("- Stable negative lift means the identifier lagged the same-season baseline in all three seasons.")
    md.append("- Use high-lift/high-count identifiers as candidate filters, modifiers, or live-watch tags.")
    md.append("- Use negative identifiers as avoid/downweight filters.")
    md.append("- Treat xFIP and contact-derived stats as research-only until pitcher keys/contact parsing are further validated.")
    md.append("")
    md.append("## Files Written")
    md.append("")
    for name in [
        "feature_family_summary.md",
        "input_health.csv",
        "single_feature_lift.csv",
        "single_feature_stability.csv",
        "two_feature_combo_lift.csv",
        "two_feature_combo_stability.csv",
        "best_pregame_identifiers.csv",
        "negative_or_avoid_identifiers.csv",
        "noisy_or_bad_identifiers.csv",
        "feature_family_summary.csv",
    ]:
        md.append(f"- {name}")

    (OUT_DIR / "feature_family_summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"Rows: {len(all_rows):,}")
    print(f"Stable positive: {len(stable_positive):,}")
    print(f"Stable negative: {len(stable_negative):,}")
    print(f"Summary: {OUT_DIR / 'feature_family_summary.md'}")


if __name__ == "__main__":
    main()
