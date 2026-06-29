import argparse
import csv
import math
import sqlite3
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "pregame_pitcher_context_preview"

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
    ]
    query = "SELECT " + ", ".join(select_parts) + " FROM mlb_play_events"
    meta = {
        "pitcher_id_col": pitcher_id_col or "",
        "pitcher_name_col": pitcher_name_col or "",
        "event_type_col": event_type_col or "",
        "description_col": desc_col or "",
    }
    return query, meta


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

        for idx, r in enumerate(rows):
            game_pk = str(r[0])
            inning = as_int(r[2])
            away_score = as_int(r[6])
            home_score = as_int(r[7])
            if inning is None or away_score is None or home_score is None:
                continue

            pitcher_id = str(r[8]).strip() if r[8] not in {None, ""} else ""
            pitcher_name = str(r[9]).strip() if r[9] not in {None, ""} else ""

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
                "batter_id": str(r[10]).strip() if r[10] not in {None, ""} else "",
            })
    return events_by_game, meta


def pitching_team_for_event(game: dict, event: dict) -> str | None:
    half = str(event.get("inning_half") or "").lower()
    # Top means away batting, home pitching. Bottom means home batting, away pitching.
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
    post5_runs = full_total - f5_total

    return {
        "f5_away": f5_away,
        "f5_home": f5_home,
        "f5_total": f5_total,
        "post5_runs": post5_runs,
    }


def aggregate_pitching_by_game_team(games: dict, events_by_game: dict) -> tuple[dict, dict]:
    # Returns pitching lines and starter IDs by game/team. This uses game events for historical training only.
    # Starter ID for a team = first pitcher seen for that team's pitching events in that game.
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
            pitcher_id = ev.get("pitcher_id") or ""
            pitcher_name = ev.get("pitcher_name") or ""

            da = max(0, ev["away_score"] - prev_away)
            dh = max(0, ev["home_score"] - prev_home)
            prev_away = max(prev_away, ev["away_score"])
            prev_home = max(prev_home, ev["home_score"])

            if not pitching_team or not pitcher_id:
                continue

            if pitching_team not in starters:
                pass
            if (game_pk, pitching_team) not in starters:
                starters[(game_pk, pitching_team)] = {"pitcher_id": pitcher_id, "pitcher_name": pitcher_name}

            flags = pitcher_event_flags(ev)
            runs_allowed = dh if batting_team == game["home_abbr"] else da

            line = line_by_team_pitcher[(pitching_team, pitcher_id)]
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

        for (team, pitcher_id), line in line_by_team_pitcher.items():
            lines[(game_pk, team, pitcher_id)] = {
                **line,
                "game_pk": game_pk,
                "game_date": game["game_date"],
                "team": team,
                "pitcher_id": pitcher_id,
                "pitcher_name": line["pitcher_name"],
                "is_starter": 1 if starters.get((game_pk, team), {}).get("pitcher_id") == pitcher_id else 0,
            }

    return lines, starters


def build_pitcher_history(games: dict, events_by_game: dict, rolling_starts: int = 8) -> tuple[dict, dict]:
    pitching_lines, starters = aggregate_pitching_by_game_team(games, events_by_game)

    # Build league HR/fly-ball and ERA constants per season using prior/full-season sample.
    # This is for xFIP preview only. Pregame rows still use pitcher lines strictly before each game date.
    total_hr = sum(v["home_runs"] for v in pitching_lines.values())
    total_fb = sum(v["fly_balls"] for v in pitching_lines.values())
    total_runs = sum(v["runs_allowed"] for v in pitching_lines.values())
    total_outs = sum(v["outs"] for v in pitching_lines.values())
    league_hr_per_fb = total_hr / total_fb if total_fb else 0.11
    league_era = (total_runs * 27 / total_outs) if total_outs else 4.5

    # Raw xFIP without constant. Constant chosen so league xFIP ~= league ERA.
    league_raw_xfip = 0.0
    if total_outs:
        ip = total_outs / 3
        xhr = total_fb * league_hr_per_fb
        league_raw_xfip = ((13 * xhr) + 3 * (sum(v["walks"] + v["hbp"] for v in pitching_lines.values())) - 2 * sum(v["strikeouts"] for v in pitching_lines.values())) / ip
    xfip_constant = league_era - league_raw_xfip

    # Only starter appearances feed starter rolling profile.
    starter_lines = [v for v in pitching_lines.values() if v["is_starter"] == 1]
    starter_lines.sort(key=lambda x: (x["game_date"], x["game_pk"], x["team"]))

    history = defaultdict(deque)
    pregame_by_game_team = {}

    for line in starter_lines:
        key = line["pitcher_id"]
        hist = list(history[key])

        pre = summarize_pitcher_history(hist, league_hr_per_fb, xfip_constant)
        pregame_by_game_team[(line["game_pk"], line["team"])] = {
            **pre,
            "starter_id": line["pitcher_id"],
            "starter_name": line["pitcher_name"],
        }

        history[key].append(line)
        while len(history[key]) > rolling_starts:
            history[key].popleft()

    meta = {
        "league_hr_per_fb": round(league_hr_per_fb, 4),
        "league_era_proxy": round(league_era, 3),
        "xfip_constant": round(xfip_constant, 3),
        "pitching_lines": len(pitching_lines),
        "starter_lines": len(starter_lines),
        "starters_found": len(starters),
    }
    return pregame_by_game_team, meta


def summarize_pitcher_history(hist: list[dict], league_hr_per_fb: float, xfip_constant: float) -> dict:
    if not hist:
        return {
            "starter_history_starts": 0,
            "starter_history_outs": 0,
            "starter_ip_per_start": None,
            "starter_ra9": None,
            "starter_k_pct": None,
            "starter_bb_pct": None,
            "starter_hr_per_fb": None,
            "starter_gb_pct": None,
            "starter_xfip": None,
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
    hr_fb = hr / fb if fb else None
    gb_pct = gb / batted if batted else None

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
        "starter_hr_per_fb": round(hr_fb, 4) if hr_fb is not None else None,
        "starter_gb_pct": round(gb_pct, 4) if gb_pct is not None else None,
        "starter_xfip": round(xfip, 3) if xfip is not None else None,
        "starter_confidence": conf,
    }


def safe_num(v: float | None, default: float) -> float:
    return default if v is None else v


def base_team_score(team_ctx: dict | None, opp_ctx: dict | None, is_home: bool) -> float:
    team_strength = safe_num(ctx_float(team_ctx, "team_strength_proxy"), 50)
    offense = safe_num(ctx_float(team_ctx, "offense_form_proxy"), 50)
    l10 = ctx_float(team_ctx, "l10_rpg")
    run_prev_opp = safe_num(ctx_float(opp_ctx, "run_prevention_proxy"), 50)
    opp_strength = safe_num(ctx_float(opp_ctx, "team_strength_proxy"), 50)
    l10_component = 0.0 if l10 is None else (l10 - 4.5) * 2.25

    score = (
        0.45 * (team_strength - 50)
        + 0.35 * (offense - 50)
        + 0.20 * (50 - opp_strength)
        + 0.25 * (50 - run_prev_opp)
        + l10_component
        + (1.8 if is_home else -0.5)
    )
    return 50 + score


def starter_basic_modifier(opp_starter: dict | None) -> float:
    # Positive means opposing starter looks vulnerable for the team batting.
    if not opp_starter or opp_starter.get("starter_confidence") == "none":
        return 0.0

    mod = 0.0
    ipps = as_float(opp_starter.get("starter_ip_per_start"))
    ra9 = as_float(opp_starter.get("starter_ra9"))
    k_pct = as_float(opp_starter.get("starter_k_pct"))
    bb_pct = as_float(opp_starter.get("starter_bb_pct"))
    gb_pct = as_float(opp_starter.get("starter_gb_pct"))

    if ipps is not None:
        mod += (5.2 - ipps) * 0.8
    if ra9 is not None:
        mod += (ra9 - 4.5) * 0.85
    if k_pct is not None:
        mod += (0.205 - k_pct) * 12.0
    if bb_pct is not None:
        mod += (bb_pct - 0.085) * 10.0
    if gb_pct is not None:
        mod += (0.42 - gb_pct) * 3.0

    conf = opp_starter.get("starter_confidence")
    if conf == "low":
        mod *= 0.45
    elif conf == "medium":
        mod *= 0.75
    return mod


def starter_xfip_modifier(opp_starter: dict | None) -> float:
    if not opp_starter or opp_starter.get("starter_confidence") == "none":
        return 0.0
    xfip = as_float(opp_starter.get("starter_xfip"))
    if xfip is None:
        return 0.0
    mod = (xfip - 4.35) * 1.15
    conf = opp_starter.get("starter_confidence")
    if conf == "low":
        mod *= 0.45
    elif conf == "medium":
        mod *= 0.75
    return mod


def team_score_with_variant(team_ctx: dict | None, opp_ctx: dict | None, opp_starter: dict | None, is_home: bool, variant: str) -> float:
    score = base_team_score(team_ctx, opp_ctx, is_home)
    if variant in {"starter_basic", "starter_basic_plus_xfip"}:
        score += starter_basic_modifier(opp_starter)
    if variant == "starter_basic_plus_xfip":
        score += starter_xfip_modifier(opp_starter)
    return round(score, 3)


def team_run_projection(team_ctx: dict | None, opp_ctx: dict | None, opp_starter: dict | None, is_home: bool, variant: str) -> float:
    offense = safe_num(ctx_float(team_ctx, "offense_form_proxy"), 50)
    l10 = ctx_float(team_ctx, "l10_rpg")
    run_prev_opp = safe_num(ctx_float(opp_ctx, "run_prevention_proxy"), 50)
    opp_strength = safe_num(ctx_float(opp_ctx, "team_strength_proxy"), 50)

    proj = 4.4
    proj += (offense - 50) * 0.035
    proj += (50 - run_prev_opp) * 0.03
    proj += (50 - opp_strength) * 0.015
    if l10 is not None:
        proj += (l10 - 4.5) * 0.22
    proj += 0.15 if is_home else -0.05

    if variant in {"starter_basic", "starter_basic_plus_xfip"}:
        proj += starter_basic_modifier(opp_starter) * 0.09
    if variant == "starter_basic_plus_xfip":
        proj += starter_xfip_modifier(opp_starter) * 0.11

    return round(max(2.0, min(7.75, proj)), 3)


def confidence_label(edge: float, context_quality: str, starter_conf: str) -> str:
    if context_quality in {"none", "missing"}:
        return "low_context"
    if starter_conf == "none":
        starter_penalty = 1.5
    elif starter_conf == "low":
        starter_penalty = 0.75
    else:
        starter_penalty = 0.0

    ae = max(0, abs(edge) - starter_penalty)
    if ae >= 7:
        return "high"
    if ae >= 4:
        return "medium"
    return "low"


def wrong_reason(row: dict) -> str:
    if as_int(row.get("correct")) == 1:
        return "correct"

    pred_type = row.get("prediction_type")
    starter_conf = row.get("starter_confidence", "none")
    opp_starter_conf = row.get("opponent_starter_confidence", "none")
    model_edge = abs(as_float(row.get("model_edge")) or 0)
    f5_total = as_int(row.get("actual_f5_total")) or 0
    post5 = as_int(row.get("actual_post5_runs")) or 0

    if starter_conf in {"none", "low"} or opp_starter_conf in {"none", "low"}:
        return "thin_or_missing_starter_context"

    if pred_type == "winner":
        margin = as_int(row.get("actual_margin")) or 0
        if margin <= 1:
            return "coinflip_close_game"
        if model_edge < 4:
            return "thin_model_edge"
        return "starter_or_team_strength_model_miss"

    if pred_type in {"team_runs_4_plus", "team_runs_5_plus"}:
        actual_runs = as_int(row.get("actual_team_runs")) or 0
        threshold = 4 if pred_type == "team_runs_4_plus" else 5
        if abs(actual_runs - threshold) <= 1:
            return "near_miss_team_total"
        if actual_runs < threshold and f5_total <= 3:
            return "low_run_environment"
        if actual_runs < threshold:
            return "offense_or_starter_suppressed"
        return "unexpected_scoring_burst"

    if pred_type == "full_total_9_plus":
        pred_yes = as_int(row.get("predicted_outcome")) == 1
        actual_yes = as_int(row.get("actual_outcome")) == 1
        if pred_yes and not actual_yes:
            if f5_total <= 3:
                return "early_scoring_failed"
            if post5 <= 2:
                return "late_scoring_stalled"
            return "total_underperformed_projection"
        if (not pred_yes) and actual_yes:
            if f5_total >= 6:
                return "early_scoring_explosion"
            if post5 >= 5:
                return "late_scoring_explosion"
            return "total_overperformed_projection"

    return "unclassified_model_miss"


def summarize_predictions(rows: list[dict], group_cols: list[str]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        key = tuple(str(r.get(c) if r.get(c) not in {None, ""} else "missing") for c in group_cols)
        groups[key].append(r)

    out = []
    for key, rs in groups.items():
        row = {c: v for c, v in zip(group_cols, key)}
        row["count"] = len(rs)
        row["correct"] = sum(as_int(r.get("correct")) or 0 for r in rs)
        row["success_rate"] = rate(row["correct"], row["count"])
        row["avg_model_edge"] = round(sum(abs(as_float(r.get("model_edge")) or 0) for r in rs) / len(rs), 3)

        yes = [r for r in rs if as_int(r.get("predicted_outcome")) == 1]
        no = [r for r in rs if as_int(r.get("predicted_outcome")) == 0]
        row["predicted_yes_count"] = len(yes)
        row["predicted_yes_success_rate"] = rate(sum(as_int(r.get("correct")) or 0 for r in yes), len(yes))
        row["predicted_no_count"] = len(no)
        row["predicted_no_success_rate"] = rate(sum(as_int(r.get("correct")) or 0 for r in no), len(no))
        out.append(row)

    out.sort(key=lambda r: (str([r.get(c) for c in group_cols]), -(r.get("count") or 0)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only pregame pitcher context preview with no-lookahead rolling starter features.")
    parser.add_argument("--seasons", nargs="+", default=["2023", "2024", "2025"], help="Seasons to analyze.")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--rolling-starts", type=int, default=8)
    parser.add_argument("--winner-edge", type=float, default=3.0)
    parser.add_argument("--team-runs4-threshold", type=float, default=4.25)
    parser.add_argument("--team-runs5-threshold", type=float, default=4.95)
    parser.add_argument("--full-total-threshold", type=float, default=8.75)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)

    variants = ["team_context_only", "starter_basic", "starter_basic_plus_xfip"]
    prediction_rows = []
    game_profile_rows = []
    input_health = []
    pitcher_meta_rows = []

    for season in args.seasons:
        regular_start = DEFAULT_REGULAR_START.get(str(season))
        context, context_path = load_context(str(season))
        games = load_final_games(conn, str(season), regular_start)
        events_by_game, event_meta = load_events(conn, games)
        starter_context, pmeta = build_pitcher_history(games, events_by_game, rolling_starts=args.rolling_starts)

        pmeta_row = {"season": season, **event_meta, **pmeta}
        pitcher_meta_rows.append(pmeta_row)

        games_used = 0
        pred_count = 0

        for game_pk, game in games.items():
            events = events_by_game.get(game_pk)
            if not events:
                continue

            away = game["away_abbr"]
            home = game["home_abbr"]
            away_ctx = context.get((game_pk, away))
            home_ctx = context.get((game_pk, home))
            away_starter = starter_context.get((game_pk, away))
            home_starter = starter_context.get((game_pk, home))

            if not away_ctx or not home_ctx:
                continue

            split = inning_run_splits(events, game["final_away_score"], game["final_home_score"])
            actual_winner = home if game["final_home_score"] > game["final_away_score"] else away
            actual_margin = abs(game["final_home_score"] - game["final_away_score"])
            games_used += 1

            for variant in variants:
                away_score = team_score_with_variant(away_ctx, home_ctx, home_starter, is_home=False, variant=variant)
                home_score = team_score_with_variant(home_ctx, away_ctx, away_starter, is_home=True, variant=variant)
                away_run_proj = team_run_projection(away_ctx, home_ctx, home_starter, is_home=False, variant=variant)
                home_run_proj = team_run_projection(home_ctx, away_ctx, away_starter, is_home=True, variant=variant)
                projected_total = round(away_run_proj + home_run_proj, 3)

                predicted_winner = home if home_score >= away_score else away
                predicted_home_away = "home" if predicted_winner == home else "away"
                model_edge = round(abs(home_score - away_score), 3)

                away_conf = context_confidence(away_ctx)
                home_conf = context_confidence(home_ctx)
                combined_conf = "high" if away_conf == "high" and home_conf == "high" else ("medium" if "missing" not in {away_conf, home_conf} else "missing")
                predicted_starter_conf = home_starter.get("starter_confidence", "none") if predicted_winner == home and home_starter else (away_starter.get("starter_confidence", "none") if away_starter else "none")
                opp_starter_conf = away_starter.get("starter_confidence", "none") if predicted_winner == home and away_starter else (home_starter.get("starter_confidence", "none") if home_starter else "none")

                profile = {
                    "season": season,
                    "variant": variant,
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
                    "actual_away_runs": game["final_away_score"],
                    "actual_home_runs": game["final_home_score"],
                    "actual_full_total": game["final_total"],
                    "actual_f5_total": split["f5_total"],
                    "actual_post5_runs": split["post5_runs"],
                    "away_starter_id": away_starter.get("starter_id", "") if away_starter else "",
                    "away_starter_name": away_starter.get("starter_name", "") if away_starter else "",
                    "away_starter_confidence": away_starter.get("starter_confidence", "none") if away_starter else "none",
                    "away_starter_ra9": away_starter.get("starter_ra9") if away_starter else None,
                    "away_starter_xfip": away_starter.get("starter_xfip") if away_starter else None,
                    "home_starter_id": home_starter.get("starter_id", "") if home_starter else "",
                    "home_starter_name": home_starter.get("starter_name", "") if home_starter else "",
                    "home_starter_confidence": home_starter.get("starter_confidence", "none") if home_starter else "none",
                    "home_starter_ra9": home_starter.get("starter_ra9") if home_starter else None,
                    "home_starter_xfip": home_starter.get("starter_xfip") if home_starter else None,
                    "away_context_confidence": away_conf,
                    "home_context_confidence": home_conf,
                }
                game_profile_rows.append(profile)

                if model_edge >= args.winner_edge:
                    row = {
                        **profile,
                        "prediction_type": "winner",
                        "predicted_team": predicted_winner,
                        "predicted_opponent": away if predicted_winner == home else home,
                        "predicted_home_away": predicted_home_away,
                        "predicted_outcome": 1,
                        "actual_outcome": 1 if predicted_winner == actual_winner else 0,
                        "correct": 1 if predicted_winner == actual_winner else 0,
                        "actual_team_runs": game["final_home_score"] if predicted_winner == home else game["final_away_score"],
                        "actual_opponent_runs": game["final_away_score"] if predicted_winner == home else game["final_home_score"],
                        "actual_margin": actual_margin,
                        "starter_confidence": predicted_starter_conf,
                        "opponent_starter_confidence": opp_starter_conf,
                        "confidence_label": confidence_label(model_edge, combined_conf, predicted_starter_conf),
                    }
                    row["wrong_reason"] = wrong_reason(row)
                    prediction_rows.append(row)
                    pred_count += 1

                for team, opp, is_home, proj, score_model, actual_runs, starter, opp_starter, conf, opp_conf in [
                    (away, home, False, away_run_proj, away_score, game["final_away_score"], away_starter, home_starter, away_conf, home_conf),
                    (home, away, True, home_run_proj, home_score, game["final_home_score"], home_starter, away_starter, home_conf, away_conf),
                ]:
                    for pred_type, threshold, actual_threshold in [
                        ("team_runs_4_plus", args.team_runs4_threshold, 4),
                        ("team_runs_5_plus", args.team_runs5_threshold, 5),
                    ]:
                        edge = abs(proj - threshold)
                        if edge < 0.25:
                            continue
                        predicted_yes = 1 if proj >= threshold else 0
                        actual_yes = 1 if actual_runs >= actual_threshold else 0
                        starter_conf = starter.get("starter_confidence", "none") if starter else "none"
                        opp_starter_conf = opp_starter.get("starter_confidence", "none") if opp_starter else "none"
                        row = {
                            **profile,
                            "prediction_type": pred_type,
                            "predicted_team": team,
                            "predicted_opponent": opp,
                            "predicted_home_away": "home" if is_home else "away",
                            "predicted_team_runs": proj,
                            "prediction_threshold": threshold,
                            "predicted_outcome": predicted_yes,
                            "actual_outcome": actual_yes,
                            "correct": 1 if predicted_yes == actual_yes else 0,
                            "actual_team_runs": actual_runs,
                            "actual_opponent_runs": game["final_away_score"] if is_home else game["final_home_score"],
                            "actual_margin": actual_margin,
                            "model_edge": round(edge, 3),
                            "starter_confidence": starter_conf,
                            "opponent_starter_confidence": opp_starter_conf,
                            "confidence_label": confidence_label(edge * 5, conf, opp_starter_conf),
                        }
                        row["wrong_reason"] = wrong_reason(row)
                        prediction_rows.append(row)
                        pred_count += 1

                total_edge = abs(projected_total - args.full_total_threshold)
                if total_edge >= 0.35:
                    predicted_yes = 1 if projected_total >= args.full_total_threshold else 0
                    actual_yes = 1 if game["final_total"] >= 9 else 0
                    starter_conf_mix = "high" if (away_starter and home_starter and away_starter.get("starter_confidence") in {"high", "medium"} and home_starter.get("starter_confidence") in {"high", "medium"}) else "low"
                    row = {
                        **profile,
                        "prediction_type": "full_total_9_plus",
                        "predicted_team": "",
                        "predicted_opponent": "",
                        "predicted_home_away": "",
                        "predicted_team_runs": "",
                        "prediction_threshold": args.full_total_threshold,
                        "predicted_outcome": predicted_yes,
                        "actual_outcome": actual_yes,
                        "correct": 1 if predicted_yes == actual_yes else 0,
                        "actual_team_runs": "",
                        "actual_opponent_runs": "",
                        "actual_margin": actual_margin,
                        "model_edge": round(total_edge, 3),
                        "starter_confidence": starter_conf_mix,
                        "opponent_starter_confidence": starter_conf_mix,
                        "confidence_label": confidence_label(total_edge * 4, combined_conf, starter_conf_mix),
                    }
                    row["wrong_reason"] = wrong_reason(row)
                    prediction_rows.append(row)
                    pred_count += 1

        input_health.append({
            "season": season,
            "regular_start": regular_start,
            "context_path": str(context_path) if context_path else "",
            "final_games_loaded": len(games),
            "games_with_events": len(events_by_game),
            "games_used_with_context": games_used,
            "prediction_rows": pred_count,
        })

    write_csv(OUT_DIR / "pitcher_meta.csv", pitcher_meta_rows)
    write_csv(OUT_DIR / "input_health.csv", input_health)
    write_csv(OUT_DIR / "pregame_pitcher_prediction_rows.csv", prediction_rows)
    write_csv(OUT_DIR / "pregame_pitcher_game_profiles.csv", game_profile_rows)

    summary_by_variant_type = summarize_predictions(prediction_rows, ["variant", "prediction_type"])
    summary_by_variant_type_conf = summarize_predictions(prediction_rows, ["variant", "prediction_type", "confidence_label"])
    summary_by_season_variant_type = summarize_predictions(prediction_rows, ["season", "variant", "prediction_type"])
    wrong_summary = summarize_predictions([r for r in prediction_rows if as_int(r.get("correct")) == 0], ["variant", "prediction_type", "wrong_reason"])
    starter_conf_summary = summarize_predictions(prediction_rows, ["variant", "prediction_type", "starter_confidence", "opponent_starter_confidence"])

    write_csv(OUT_DIR / "summary_by_variant_prediction_type.csv", summary_by_variant_type)
    write_csv(OUT_DIR / "summary_by_variant_prediction_type_confidence.csv", summary_by_variant_type_conf)
    write_csv(OUT_DIR / "summary_by_season_variant_prediction_type.csv", summary_by_season_variant_type)
    write_csv(OUT_DIR / "wrong_reason_summary.csv", wrong_summary)
    write_csv(OUT_DIR / "starter_confidence_summary.csv", starter_conf_summary)

    # Direct variant comparison vs team_context_only by prediction type
    base_map = {
        r["prediction_type"]: r
        for r in summary_by_variant_type
        if r["variant"] == "team_context_only"
    }
    comparison = []
    for r in summary_by_variant_type:
        b = base_map.get(r["prediction_type"])
        row = dict(r)
        if b and r["variant"] != "team_context_only":
            row["baseline_variant_success_rate"] = b.get("success_rate")
            row["delta_vs_team_context_only"] = round((as_float(r.get("success_rate")) or 0) - (as_float(b.get("success_rate")) or 0), 4)
            row["count_delta_vs_team_context_only"] = (as_int(r.get("count")) or 0) - (as_int(b.get("count")) or 0)
        else:
            row["baseline_variant_success_rate"] = None
            row["delta_vs_team_context_only"] = None
            row["count_delta_vs_team_context_only"] = None
        comparison.append(row)
    write_csv(OUT_DIR / "variant_comparison_summary.csv", comparison)

    md = []
    md.append("# Pregame Pitcher Context Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append("## No-Lookahead Guardrail")
    md.append("")
    md.append("- Team context comes from historical no-lookahead team context rows keyed by game/team.")
    md.append("- Starter context is built from pitcher appearances before each game only.")
    md.append("- The game being graded is added to the rolling pitcher history only after its pregame row is created.")
    md.append("- Final scores and inning splits are used only after prediction for grading and wrong-reason diagnostics.")
    md.append("- No Vegas/Kalshi market data is used here. This is baseball-logic research only.")
    md.append("")
    md.append("## Variants Tested")
    md.append("")
    md.append("- `team_context_only`: same style as prior pregame model, no starter adjustment.")
    md.append("- `starter_basic`: adds rolling starter IP/start, RA9, K%, BB/HBP%, GB%, HR/FB style features.")
    md.append("- `starter_basic_plus_xfip`: adds homemade xFIP adjustment on top of starter_basic.")
    md.append("")
    md.append("## Input Health")
    md.append("")
    for h in input_health:
        md.append(
            f"- {h['season']}: final games {h['final_games_loaded']:,}, games with events {h['games_with_events']:,}, "
            f"games used {h['games_used_with_context']:,}, predictions {h['prediction_rows']:,}"
        )
    md.append("")
    md.append("## Pitcher Source Health")
    md.append("")
    for p in pitcher_meta_rows:
        md.append(
            f"- {p['season']}: pitcher column `{p.get('pitcher_id_col')}`, starter lines {p.get('starter_lines')}, "
            f"pitching lines {p.get('pitching_lines')}, league HR/FB {p.get('league_hr_per_fb')}, xFIP constant {p.get('xfip_constant')}"
        )
    md.append("")
    md.append("## Summary by Variant and Prediction Type")
    md.append("")
    for r in summary_by_variant_type:
        md.append(
            f"- {r['variant']} / {r['prediction_type']}: {r['correct']}/{r['count']} correct, "
            f"success {pct(as_float(r.get('success_rate')))}, avg edge {r.get('avg_model_edge')}, "
            f"YES {r.get('predicted_yes_count')} at {pct(as_float(r.get('predicted_yes_success_rate')))}, "
            f"NO {r.get('predicted_no_count')} at {pct(as_float(r.get('predicted_no_success_rate')))}"
        )
    md.append("")
    md.append("## Variant Comparison")
    md.append("")
    for r in comparison:
        if r["variant"] == "team_context_only":
            continue
        md.append(
            f"- {r['variant']} / {r['prediction_type']}: delta vs team_context_only "
            f"{pct(as_float(r.get('delta_vs_team_context_only')))}, success {pct(as_float(r.get('success_rate')))}"
        )
    md.append("")
    md.append("## Interpretation")
    md.append("")
    md.append("- A positive xFIP delta means the homemade xFIP layer helped the simple starter model.")
    md.append("- A negative xFIP delta means xFIP needs recalibration before promotion.")
    md.append("- If starter_basic helps winner but not totals, use it as side context first.")
    md.append("- If starter context mostly helps high-confidence rows, future candidate logic should require pitcher confidence.")
    md.append("")
    md.append("## Files Written")
    md.append("")
    for name in [
        "pregame_pitcher_summary.md",
        "pitcher_meta.csv",
        "input_health.csv",
        "pregame_pitcher_prediction_rows.csv",
        "pregame_pitcher_game_profiles.csv",
        "summary_by_variant_prediction_type.csv",
        "summary_by_variant_prediction_type_confidence.csv",
        "summary_by_season_variant_prediction_type.csv",
        "wrong_reason_summary.csv",
        "starter_confidence_summary.csv",
        "variant_comparison_summary.csv",
    ]:
        md.append(f"- {name}")

    (OUT_DIR / "pregame_pitcher_summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {OUT_DIR / 'pregame_pitcher_summary.md'}")
    print("Variant summary:")
    for r in summary_by_variant_type:
        print(f"  {r['variant']} / {r['prediction_type']}: {pct(as_float(r.get('success_rate')))} ({r['count']})")


if __name__ == "__main__":
    main()
