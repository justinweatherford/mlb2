import argparse
import csv
import re
import sqlite3
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

OUT_DIR = Path("outputs") / "beans_offense_defense_lift_preview"

TEAM_NORMALIZE = {"ARI": "AZ", "KCR": "KC", "CHW": "CWS", "OAK": "ATH", "WSH": "WSN"}
MLB_TEAMS = {
    "AZ","ATL","BAL","BOS","CHC","CIN","CLE","COL","CWS","DET","HOU","KC","LAA","LAD",
    "MIA","MIL","MIN","NYM","NYY","ATH","PHI","PIT","SD","SEA","SF","STL","TB","TEX","TOR","WSN"
}
REGULAR_START = {"2023": "2023-03-30", "2024": "2024-03-20", "2025": "2025-03-27"}

OUTCOMES = [
    "team_won",
    "team_runs_4plus",
    "team_runs_5plus",
    "opponent_runs_4plus",
    "opponent_runs_5plus",
    "game_total_9plus",
    "game_total_10plus",
    "f5_total_4plus",
    "team_f5_runs_2plus",
    "team_post5_runs_2plus",
    "opponent_post5_runs_2plus",
]

FEATURES = [
    "BO_bucket",
    "BO_L5_bucket",
    "BO_L10_bucket",
    "BO_event_bucket",
    "BO_gap_bucket",
    "BO_vs_opponent_BD_gap_bucket",
    "BD_bucket",
    "BD_L5_bucket",
    "BD_L10_bucket",
    "BD_chaos_bucket",
    "BD_gap_bucket",
    "error_rate_L10_bucket",
    "big_inning_allowed_rate_L10_bucket",
    "bullpen_outs_last_2d_bucket",
    "reliever_appearances_last_2d_bucket",
    "back_to_back_reliever_count_bucket",
    "starter_short_outing_previous_game",
    "bullpen_heavy_previous_game",
    "extra_innings_previous_game",
    "BO_plus_weak_BD_tag",
    "BO_plus_tired_bullpen_tag",
    "strong_BO_clean_BD_tag",
    "avoid_low_BO_strong_BD_tag",
]


def norm_team(x: Any) -> str:
    t = str(x or "").strip().upper()
    return TEAM_NORMALIZE.get(t, t)


def fnum(x: Any) -> float | None:
    try:
        if x is None or str(x).strip() == "":
            return None
        return float(x)
    except Exception:
        return None


def inum(x: Any) -> int | None:
    y = fnum(x)
    return None if y is None else int(round(y))


def pct(x: float | None) -> str:
    return "NA" if x is None else f"{x * 100:.1f}%"


def rate(n: float, d: float) -> float | None:
    return None if not d else round(n / d, 4)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys, seen = [], set()
        for r in rows:
            for k in r:
                if k not in seen:
                    keys.append(k)
                    seen.add(k)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def bucket_index(v: float | None) -> str:
    if v is None: return "missing"
    if v < 85: return "very_low_lt_85"
    if v < 95: return "low_85_95"
    if v < 105: return "avg_95_105"
    if v < 115: return "high_105_115"
    return "very_high_115_plus"


def bucket_gap(v: float | None) -> str:
    if v is None: return "missing"
    if v <= -15: return "minus_15_or_worse"
    if v <= -7: return "minus_7_to_15"
    if v < 7: return "neutral_minus7_plus7"
    if v < 15: return "plus_7_to_15"
    return "plus_15_plus"


def bucket_rate(v: float | None) -> str:
    if v is None: return "missing"
    if v < 0.25: return "low_lt_25"
    if v < 0.40: return "below_avg_25_40"
    if v < 0.55: return "avg_40_55"
    if v < 0.70: return "high_55_70"
    return "very_high_70_plus"


def bucket_outs(v: float | None) -> str:
    if v is None: return "missing"
    if v <= 0: return "zero"
    if v <= 9: return "light_1_9"
    if v <= 18: return "normal_10_18"
    if v <= 27: return "heavy_19_27"
    return "very_heavy_28_plus"


def bucket_count(v: float | None) -> str:
    if v is None: return "missing"
    if v <= 0: return "zero"
    if v <= 3: return "low_1_3"
    if v <= 6: return "medium_4_6"
    if v <= 10: return "high_7_10"
    return "very_high_11_plus"


def load_games(conn: sqlite3.Connection, season: str) -> dict:
    rows = conn.execute(
        """
        SELECT game_pk, game_date, away_abbr, home_abbr, final_away_score, final_home_score,
               final_total, game_start_time_utc
        FROM mlb_games
        WHERE substr(game_date, 1, 4)=?
          AND final_away_score IS NOT NULL
          AND final_home_score IS NOT NULL
        ORDER BY game_date, COALESCE(game_start_time_utc,''), game_pk
        """,
        [season],
    ).fetchall()
    out = {}
    for r in rows:
        date = str(r[1])
        if date < REGULAR_START.get(season, "0000-00-00"):
            continue
        away, home = norm_team(r[2]), norm_team(r[3])
        if away not in MLB_TEAMS or home not in MLB_TEAMS:
            continue
        a, h = inum(r[4]), inum(r[5])
        if a is None or h is None:
            continue
        pk = str(r[0])
        out[pk] = dict(
            game_pk=pk, game_date=date, away_abbr=away, home_abbr=home,
            final_away_score=a, final_home_score=h,
            final_total=inum(r[6]) if inum(r[6]) is not None else a + h,
            game_start_time_utc=r[7],
        )
    return out


def load_events(conn: sqlite3.Connection, games: dict) -> tuple[dict, dict]:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(mlb_play_events)").fetchall()]
    meta = {
        "has_pitcher_name": "pitcher_name" in cols,
        "has_raw_json": "raw_json" in cols,
        "has_event_type": "event_type" in cols,
        "has_description": "description" in cols,
    }
    pks = list(games)
    by_game = defaultdict(list)
    for i in range(0, len(pks), 500):
        chunk = pks[i:i+500]
        ph = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT game_pk, event_time, inning, inning_half, event_type, description,
                   away_score, home_score, pitcher_name
            FROM mlb_play_events
            WHERE game_pk IN ({ph})
              AND inning IS NOT NULL
              AND away_score IS NOT NULL
              AND home_score IS NOT NULL
            ORDER BY game_pk, inning, COALESCE(event_time,''), id
            """,
            chunk,
        ).fetchall()
        for r in rows:
            pk = str(r[0])
            by_game[pk].append(dict(
                event_index=len(by_game[pk]),
                game_pk=pk,
                event_time=r[1],
                inning=inum(r[2]),
                inning_half=str(r[3] or ""),
                event_type=str(r[4] or ""),
                description=str(r[5] or ""),
                away_score=inum(r[6]) or 0,
                home_score=inum(r[7]) or 0,
                pitcher_name=str(r[8] or ""),
                pitcher_key="name:" + re.sub(r"[^a-z0-9]+", "_", str(r[8] or "").strip().lower()).strip("_") if r[8] else "",
            ))
    return by_game, meta


def batting_team(game: dict, ev: dict) -> str | None:
    half = ev["inning_half"].lower()
    if "top" in half or half in {"t", "away"}:
        return game["away_abbr"]
    if "bottom" in half or half in {"bot", "b", "home"}:
        return game["home_abbr"]
    return None


def fielding_team(game: dict, ev: dict) -> str | None:
    half = ev["inning_half"].lower()
    if "top" in half or half in {"t", "away"}:
        return game["home_abbr"]
    if "bottom" in half or half in {"bot", "b", "home"}:
        return game["away_abbr"]
    return None


def text(ev: dict) -> str:
    return f"{ev.get('event_type','')} {ev.get('description','')}".lower()


def is_error(ev: dict) -> bool:
    s = text(ev)
    return "error" in s or "reached on error" in s or "reaches on error" in s


def is_hit(ev: dict) -> bool:
    s = text(ev)
    return any(x in s for x in ["single", "double", "triple", "home run", "homer"])


def is_walk(ev: dict) -> bool:
    s = text(ev)
    return "walk" in s and "hit by pitch" not in s


def is_hr(ev: dict) -> bool:
    s = text(ev)
    return "home run" in s or "homer" in s


def estimated_outs(ev: dict) -> int:
    s = text(ev)
    et = ev.get("event_type","").lower()
    if "triple play" in s: return 3
    if "double play" in s: return 2
    if any(x in et for x in ["strikeout","groundout","flyout","lineout","popup","forceout","sac fly","sac bunt"]): return 1
    if " out" in s and not any(x in s for x in ["walk","single","double","triple","home run","hit by pitch"]): return 1
    return 0


def split_and_stats(game: dict, events: list[dict]) -> tuple[dict, dict, dict, dict, dict]:
    inning_runs = defaultdict(lambda: {"away":0,"home":0})
    stats = defaultdict(lambda: defaultdict(int))
    pitching = defaultdict(lambda: defaultdict(lambda: {"outs":0,"runs_allowed":0,"events":0}))
    starters = {}
    prev_a, prev_h = 0, 0

    for ev in sorted(events, key=lambda e:(e["inning"], str(e.get("event_time") or ""), e["event_index"])):
        da = max(0, ev["away_score"] - prev_a)
        dh = max(0, ev["home_score"] - prev_h)
        if da: inning_runs[ev["inning"]]["away"] += da
        if dh: inning_runs[ev["inning"]]["home"] += dh

        bat = batting_team(game, ev)
        fld = fielding_team(game, ev)
        pk = ev.get("pitcher_key") or ""

        if bat:
            stats[bat]["hits"] += 1 if is_hit(ev) else 0
            stats[bat]["walks"] += 1 if is_walk(ev) else 0
            stats[bat]["home_runs"] += 1 if is_hr(ev) else 0
            stats[bat]["scoring_events"] += 1 if (da or dh or "scores" in text(ev) or "rbi" in text(ev) or is_hr(ev)) else 0
            stats[bat]["outs_made"] += estimated_outs(ev)

        if fld:
            stats[fld]["errors"] += 1 if is_error(ev) else 0
            stats[fld]["runs_allowed_by_event"] += da + dh
            if pk:
                starters.setdefault(fld, pk)
                pl = pitching[fld][pk]
                pl["outs"] += estimated_outs(ev)
                pl["runs_allowed"] += (dh if bat == game["home_abbr"] else da)
                pl["events"] += 1

        prev_a = max(prev_a, ev["away_score"])
        prev_h = max(prev_h, ev["home_score"])

    f5a = sum(inning_runs[i]["away"] for i in range(1,6))
    f5h = sum(inning_runs[i]["home"] for i in range(1,6))
    max_inning = max(inning_runs) if inning_runs else 9
    split = dict(
        f5_away=f5a, f5_home=f5h, f5_total=f5a+f5h,
        post5_away=game["final_away_score"]-f5a,
        post5_home=game["final_home_score"]-f5h,
        post5_runs=game["final_total"]-(f5a+f5h),
        max_inning=max_inning,
    )
    big_allowed = {
        game["away_abbr"]: 1 if any(v["home"] >= 3 for v in inning_runs.values()) else 0,
        game["home_abbr"]: 1 if any(v["away"] >= 3 for v in inning_runs.values()) else 0,
    }
    return split, stats, big_allowed, pitching, starters


def team_line(game: dict, split: dict, stats: dict, big_allowed: dict, team: str) -> dict:
    home = team == game["home_abbr"]
    runs = game["final_home_score"] if home else game["final_away_score"]
    allowed = game["final_away_score"] if home else game["final_home_score"]
    f5_runs = split["f5_home"] if home else split["f5_away"]
    f5_allowed = split["f5_away"] if home else split["f5_home"]
    post5_runs = split["post5_home"] if home else split["post5_away"]
    post5_allowed = split["post5_away"] if home else split["post5_home"]
    st = stats.get(team, {})
    return dict(
        runs=runs, allowed=allowed, f5_runs=f5_runs, f5_allowed=f5_allowed,
        post5_runs=post5_runs, post5_allowed=post5_allowed, won=1 if runs > allowed else 0,
        errors=st.get("errors",0), big_inning_allowed=big_allowed.get(team,0),
        hits=st.get("hits",0), walks=st.get("walks",0), home_runs=st.get("home_runs",0),
        scoring_events=st.get("scoring_events",0), outs_made=st.get("outs_made",0),
    )


def rolling_summary(hist: list[dict]) -> dict:
    if not hist: return {}
    n = len(hist)
    def avg(k): return sum(h[k] for h in hist) / n
    runs = [h["runs"] for h in hist]
    allowed = [h["allowed"] for h in hist]
    return dict(
        games=n,
        rpg=avg("runs"), allowed_pg=avg("allowed"),
        f5_rpg=avg("f5_runs"), post5_rpg=avg("post5_runs"), post5_allowed_pg=avg("post5_allowed"),
        scored4_rate=sum(x>=4 for x in runs)/n, scored5_rate=sum(x>=5 for x in runs)/n, scored2minus_rate=sum(x<=2 for x in runs)/n,
        allowed4_rate=sum(x>=4 for x in allowed)/n, allowed5_rate=sum(x>=5 for x in allowed)/n, allowed2minus_rate=sum(x<=2 for x in allowed)/n,
        error_rate=avg("errors"), big_inning_allowed_rate=avg("big_inning_allowed"),
        hits_pg=avg("hits"), walks_pg=avg("walks"), hr_pg=avg("home_runs"), scoring_events_pg=avg("scoring_events"),
    )


def league_summary(histories: dict) -> dict:
    all_games = []
    for h in histories.values():
        all_games.extend(list(h))
    if not all_games:
        return dict(rpg=4.5, allowed_pg=4.5, f5_rpg=2.4, post5_rpg=2.1, post5_allowed_pg=2.1,
                    scored4_rate=.55, scored5_rate=.42, scored2minus_rate=.25,
                    allowed4_rate=.55, allowed5_rate=.42, allowed2minus_rate=.25,
                    error_rate=.55, big_inning_allowed_rate=.35, hits_pg=8, walks_pg=3, hr_pg=1.1, scoring_events_pg=4)
    return rolling_summary(all_games)


def idx(value: float | None, league: float | None, invert: bool=False) -> float | None:
    if value is None or league is None or league == 0: return None
    if invert:
        if value == 0: return 150
        ratio = league / value
    else:
        ratio = value / league
    return max(50, min(150, 100 * ratio))


def calc_bo(s: dict, lg: dict) -> tuple[float | None, float | None]:
    if not s: return None, None
    components = [
        idx(s.get("rpg"), lg.get("rpg")),
        idx(s.get("f5_rpg"), lg.get("f5_rpg")),
        idx(s.get("post5_rpg"), lg.get("post5_rpg")),
        idx(s.get("scored4_rate"), lg.get("scored4_rate")),
        idx(s.get("scored5_rate"), lg.get("scored5_rate")),
        idx(s.get("scored2minus_rate"), lg.get("scored2minus_rate"), invert=True),
    ]
    comps = [x for x in components if x is not None]
    event_components = [
        idx(s.get("hits_pg"), lg.get("hits_pg")),
        idx(s.get("walks_pg"), lg.get("walks_pg")),
        idx(s.get("hr_pg"), lg.get("hr_pg")),
        idx(s.get("scoring_events_pg"), lg.get("scoring_events_pg")),
    ]
    ecomps = [x for x in event_components if x is not None]
    return (round(mean(comps),2) if comps else None, round(mean(ecomps),2) if ecomps else None)


def calc_bd(s: dict, lg: dict) -> tuple[float | None, float | None]:
    if not s: return None, None
    components = [
        idx(s.get("allowed_pg"), lg.get("allowed_pg"), invert=True),
        idx(s.get("allowed4_rate"), lg.get("allowed4_rate"), invert=True),
        idx(s.get("allowed5_rate"), lg.get("allowed5_rate"), invert=True),
        idx(s.get("allowed2minus_rate"), lg.get("allowed2minus_rate")),
        idx(s.get("post5_allowed_pg"), lg.get("post5_allowed_pg"), invert=True),
    ]
    comps = [x for x in components if x is not None]
    chaos = [
        idx(s.get("error_rate"), lg.get("error_rate"), invert=True),
        idx(s.get("big_inning_allowed_rate"), lg.get("big_inning_allowed_rate"), invert=True),
    ]
    ccomps = [x for x in chaos if x is not None]
    return (round(mean(comps),2) if comps else None, round(mean(ccomps),2) if ccomps else None)


def summarize_usage(pitching_lines: list[dict], starter_key: str | None) -> tuple[dict, set]:
    bullpen_outs = 0
    apps = 0
    relievers = set()
    starter_outs = None
    for k, line in pitching_lines:
        outs = line["outs"]
        if k == starter_key:
            starter_outs = outs
        else:
            bullpen_outs += outs
            apps += 1
            relievers.add(k)
    return dict(
        bullpen_outs=bullpen_outs,
        reliever_appearances=apps,
        starter_outs=starter_outs,
        starter_short_outing=1 if starter_outs is not None and starter_outs < 15 else 0,
    ), relievers


def build_rows(conn: sqlite3.Connection, season: str) -> tuple[list[dict], dict]:
    games = load_games(conn, season)
    events_by_game, meta = load_events(conn, games)
    by_date = defaultdict(list)
    for g in games.values():
        by_date[g["game_date"]].append(g)

    hist10 = defaultdict(lambda: deque(maxlen=10))
    hist5 = defaultdict(lambda: deque(maxlen=5))
    usage_by_team_date = {}
    relievers_by_team_date = {}
    out = []

    for date in sorted(by_date):
        lg = league_summary(hist10)

        # Create rows before same-date games enter history.
        for game in sorted(by_date[date], key=lambda g:(str(g.get("game_start_time_utc") or ""), g["game_pk"])):
            events = events_by_game.get(game["game_pk"], [])
            if not events: continue
            split, stats, big_allowed, pitching, starters = split_and_stats(game, events)

            for team, opp, is_home in [(game["away_abbr"], game["home_abbr"], False), (game["home_abbr"], game["away_abbr"], True)]:
                actual = team_line(game, split, stats, big_allowed, team)
                s10, s5 = rolling_summary(list(hist10[team])), rolling_summary(list(hist5[team]))
                os10 = rolling_summary(list(hist10[opp]))

                bo10, bo_event = calc_bo(s10, lg)
                bo5, _ = calc_bo(s5, lg)
                bd10, bd_chaos = calc_bd(s10, lg)
                bd5, _ = calc_bd(s5, lg)
                opp_bo, _ = calc_bo(os10, lg)
                opp_bd, _ = calc_bd(os10, lg)

                bo_gap = bo10 - opp_bo if bo10 is not None and opp_bo is not None else None
                bd_gap = bd10 - opp_bd if bd10 is not None and opp_bd is not None else None
                bo_vs_opp_bd = bo10 - opp_bd if bo10 is not None and opp_bd is not None else None

                prev_dates = sorted([d for (t, d) in usage_by_team_date if t == team and d < date])[-2:]
                bullpen_outs_2d = sum(usage_by_team_date[(team,d)].get("bullpen_outs",0) for d in prev_dates)
                apps_2d = sum(usage_by_team_date[(team,d)].get("reliever_appearances",0) for d in prev_dates)
                b2b = 0
                if len(prev_dates) >= 2:
                    b2b = len(relievers_by_team_date.get((team, prev_dates[-1]), set()) & relievers_by_team_date.get((team, prev_dates[-2]), set()))
                last_usage = usage_by_team_date.get((team, prev_dates[-1]), {}) if prev_dates else {}

                row = dict(
                    season=season, game_pk=game["game_pk"], game_date=date, game_id=f"{game['away_abbr']}@{game['home_abbr']}",
                    team=team, opponent=opp, home_away="home" if is_home else "away",
                    team_won=actual["won"],
                    team_runs_4plus=1 if actual["runs"] >= 4 else 0,
                    team_runs_5plus=1 if actual["runs"] >= 5 else 0,
                    opponent_runs_4plus=1 if actual["allowed"] >= 4 else 0,
                    opponent_runs_5plus=1 if actual["allowed"] >= 5 else 0,
                    game_total_9plus=1 if game["final_total"] >= 9 else 0,
                    game_total_10plus=1 if game["final_total"] >= 10 else 0,
                    f5_total_4plus=1 if split["f5_total"] >= 4 else 0,
                    team_f5_runs_2plus=1 if actual["f5_runs"] >= 2 else 0,
                    team_post5_runs_2plus=1 if actual["post5_runs"] >= 2 else 0,
                    opponent_post5_runs_2plus=1 if actual["post5_allowed"] >= 2 else 0,
                    team_runs=actual["runs"], opponent_runs=actual["allowed"],
                    BO=bo10, BO_L5=bo5, BO_L10=bo10, BO_event=bo_event, opponent_BO=opp_bo, BO_gap=bo_gap,
                    BD=bd10, BD_L5=bd5, BD_L10=bd10, BD_chaos=bd_chaos, opponent_BD=opp_bd, BD_gap=bd_gap,
                    BO_vs_opponent_BD_gap=bo_vs_opp_bd,
                    error_rate_L10=s10.get("error_rate"), big_inning_allowed_rate_L10=s10.get("big_inning_allowed_rate"),
                    bullpen_outs_last_2d=bullpen_outs_2d, reliever_appearances_last_2d=apps_2d, back_to_back_reliever_count=b2b,
                    starter_short_outing_previous_game="yes" if last_usage.get("starter_short_outing",0) else "no",
                    bullpen_heavy_previous_game="yes" if last_usage.get("bullpen_outs",0) >= 15 else "no",
                    extra_innings_previous_game="yes" if last_usage.get("extra_innings",0) else "no",
                )
                row.update(
                    BO_bucket=bucket_index(bo10), BO_L5_bucket=bucket_index(bo5), BO_L10_bucket=bucket_index(bo10),
                    BO_event_bucket=bucket_index(bo_event), BO_gap_bucket=bucket_gap(bo_gap),
                    BO_vs_opponent_BD_gap_bucket=bucket_gap(bo_vs_opp_bd),
                    BD_bucket=bucket_index(bd10), BD_L5_bucket=bucket_index(bd5), BD_L10_bucket=bucket_index(bd10),
                    BD_chaos_bucket=bucket_index(bd_chaos), BD_gap_bucket=bucket_gap(bd_gap),
                    error_rate_L10_bucket=bucket_rate(s10.get("error_rate")),
                    big_inning_allowed_rate_L10_bucket=bucket_rate(s10.get("big_inning_allowed_rate")),
                    bullpen_outs_last_2d_bucket=bucket_outs(bullpen_outs_2d),
                    reliever_appearances_last_2d_bucket=bucket_count(apps_2d),
                    back_to_back_reliever_count_bucket=bucket_count(b2b),
                )
                row["BO_plus_weak_BD_tag"] = "yes" if row["BO_bucket"] in {"high_105_115","very_high_115_plus"} and (opp_bd is not None and opp_bd < 95) else "no"
                row["BO_plus_tired_bullpen_tag"] = "yes" if row["BO_bucket"] in {"high_105_115","very_high_115_plus"} and bullpen_outs_2d >= 18 else "no"
                row["strong_BO_clean_BD_tag"] = "yes" if row["BO_bucket"] in {"high_105_115","very_high_115_plus"} and row["BD_bucket"] in {"high_105_115","very_high_115_plus"} else "no"
                row["avoid_low_BO_strong_BD_tag"] = "yes" if row["BO_bucket"] in {"very_low_lt_85","low_85_95"} and (opp_bd is not None and opp_bd >= 105) else "no"
                out.append(row)

        # Update histories after all rows for the date are created.
        for game in by_date[date]:
            events = events_by_game.get(game["game_pk"], [])
            if not events: continue
            split, stats, big_allowed, pitching, starters = split_and_stats(game, events)
            for team in (game["away_abbr"], game["home_abbr"]):
                actual = team_line(game, split, stats, big_allowed, team)
                hist10[team].append(actual)
                hist5[team].append(actual)

                starter_key = starters.get(team)
                plist = [(k, v) for k, v in pitching.get(team, {}).items()]
                usage, relievers = summarize_usage(plist, starter_key)
                usage["extra_innings"] = 1 if split["max_inning"] > 9 else 0
                usage_by_team_date[(team, date)] = usage
                relievers_by_team_date[(team, date)] = relievers

    health = dict(
        season=season, final_games_loaded=len(games), games_with_events=len(events_by_game),
        team_game_rows=len(out), **meta
    )
    return out, health


def summarize_feature(rows: list[dict], feature: str, min_count: int) -> list[dict]:
    base = defaultdict(list)
    group = defaultdict(list)
    for r in rows:
        season = r["season"]
        value = str(r.get(feature) if r.get(feature) not in {None,""} else "missing")
        for outcome in OUTCOMES:
            val = inum(r.get(outcome))
            if val is None: continue
            base[(season,outcome)].append(val)
            group[(season,feature,value,outcome)].append(val)

    out = []
    for (season, feature, value, outcome), vals in group.items():
        if len(vals) < min_count: continue
        b = base[(season,outcome)]
        br = rate(sum(b), len(b))
        fr = rate(sum(vals), len(vals))
        out.append(dict(season=season, feature=feature, feature_value=value, outcome=outcome,
                        count=len(vals), feature_rate=fr, baseline_rate=br,
                        lift=round(fr-br,4) if fr is not None and br is not None else None))
    return out


def stability(rows: list[dict]) -> list[dict]:
    group = defaultdict(list)
    for r in rows:
        group[(r["feature"], r["feature_value"], r["outcome"])].append(r)
    out = []
    for (feature,value,outcome), rs in group.items():
        seasons = sorted(set(r["season"] for r in rs))
        if len(seasons) < 2: continue
        lifts = [fnum(r["lift"]) for r in rs if fnum(r["lift"]) is not None]
        rates = [fnum(r["feature_rate"]) for r in rs if fnum(r["feature_rate"]) is not None]
        counts = [inum(r["count"]) or 0 for r in rs]
        if not lifts: continue
        label = "mixed_or_noisy"
        if len(seasons) >= 3 and min(lifts) >= 0 and mean(lifts) >= .04:
            label = "stable_positive_lift"
        elif len(seasons) >= 3 and max(lifts) <= 0 and mean(lifts) <= -.04:
            label = "stable_negative_lift"
        out.append(dict(feature=feature, feature_value=value, outcome=outcome,
                        seasons_seen=",".join(seasons), season_count=len(seasons),
                        total_count=sum(counts), min_season_count=min(counts),
                        avg_rate=round(mean(rates),4), avg_lift=round(mean(lifts),4),
                        min_lift=round(min(lifts),4), max_lift=round(max(lifts),4),
                        stability_label=label))
    order = {"stable_positive_lift":0, "stable_negative_lift":1, "mixed_or_noisy":2}
    out.sort(key=lambda r:(order.get(r["stability_label"],9), -abs(fnum(r["avg_lift"]) or 0), -r["total_count"]))
    return out


def main():
    p = argparse.ArgumentParser(description="No-lookahead Beans Offense (BO), Beans Defense (BD), errors, and bullpen fatigue lift preview.")
    p.add_argument("--seasons", nargs="+", default=["2023","2024","2025"])
    p.add_argument("--db", default="kalshi_mlb.db")
    p.add_argument("--min-count", type=int, default=100)
    p.add_argument("--write-team-game-rows", action="store_true")
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)

    all_rows, health = [], []
    for season in args.seasons:
        rows, h = build_rows(conn, str(season))
        all_rows.extend(rows)
        health.append(h)
        print(f"{season}: rows={len(rows):,}, games={h['final_games_loaded']:,}")

    write_csv(OUT_DIR / "input_health.csv", health)
    if args.write_team_game_rows:
        write_csv(OUT_DIR / "beans_team_game_feature_rows.csv", all_rows)

    lift = []
    for feat in FEATURES:
        lift.extend(summarize_feature(all_rows, feat, args.min_count))
    lift.sort(key=lambda r:(r["feature"], r["feature_value"], r["outcome"], r["season"]))
    write_csv(OUT_DIR / "beans_feature_lift.csv", lift)

    stab = stability(lift)
    write_csv(OUT_DIR / "beans_feature_stability.csv", stab)

    pos = [r for r in stab if r["stability_label"] == "stable_positive_lift"]
    neg = [r for r in stab if r["stability_label"] == "stable_negative_lift"]
    write_csv(OUT_DIR / "best_beans_positive_identifiers.csv", pos[:150])
    write_csv(OUT_DIR / "best_beans_negative_identifiers.csv", neg[:150])

    md = []
    md.append("# Beans Offense / Defense Lift Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append("## Naming")
    md.append("")
    md.append("- `BO` = Beans Offense. This is our in-house wRC+ style offensive creation index, not official FanGraphs wRC+.")
    md.append("- `BD` = Beans Defense. This is our in-house run prevention and defensive chaos index, not official OAA/fielding.")
    md.append("- 100 is league-average-ish from prior rolling no-lookahead league environment.")
    md.append("- Above 100 is better for both BO and BD.")
    md.append("")
    md.append("## No-Lookahead Guardrail")
    md.append("")
    md.append("- BO/BD are calculated from prior team games only.")
    md.append("- Current date games do not update histories until after rows for that date are created.")
    md.append("- Bullpen usage uses previous dates only.")
    md.append("- Current game score/events are only used for outcome grading.")
    md.append("- No market prices are used.")
    md.append("")
    md.append("## Input Health")
    md.append("")
    for h in health:
        md.append(f"- {h['season']}: games {h['final_games_loaded']:,}, team-game rows {h['team_game_rows']:,}, pitcher_name_col={h.get('has_pitcher_name')}, raw_json_col={h.get('has_raw_json')}")
    md.append("")
    md.append("## Top Stable Positive Identifiers")
    md.append("")
    for r in pos[:40]:
        md.append(f"- {r['feature']}={r['feature_value']} / {r['outcome']}: avg rate {pct(fnum(r['avg_rate']))}, avg lift {pct(fnum(r['avg_lift']))}, count {r['total_count']:,}, seasons {r['seasons_seen']}")
    md.append("")
    md.append("## Top Stable Negative Identifiers")
    md.append("")
    for r in neg[:40]:
        md.append(f"- {r['feature']}={r['feature_value']} / {r['outcome']}: avg rate {pct(fnum(r['avg_rate']))}, avg lift {pct(fnum(r['avg_lift']))}, count {r['total_count']:,}, seasons {r['seasons_seen']}")
    md.append("")
    md.append("## Interpretation")
    md.append("")
    md.append("- Use BO as our own offense rating, mostly for team runs 4+, team runs 5+, F5 scoring, and side strength.")
    md.append("- Use BD as our own defense/run-prevention rating and opponent suppression filter.")
    md.append("- Use BD_chaos and error buckets as instability, not pure fielding talent.")
    md.append("- Use bullpen last-2-days features as late scoring and full-game modifier candidates.")
    md.append("")
    md.append("## Files Written")
    md.append("")
    for name in ["beans_summary.md","input_health.csv","beans_feature_lift.csv","beans_feature_stability.csv","best_beans_positive_identifiers.csv","best_beans_negative_identifiers.csv","beans_team_game_feature_rows.csv if --write-team-game-rows is passed"]:
        md.append(f"- {name}")

    (OUT_DIR / "beans_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(f"WROTE: {OUT_DIR}")
    print(f"Stable positive: {len(pos):,}")
    print(f"Stable negative: {len(neg):,}")

if __name__ == "__main__":
    main()
