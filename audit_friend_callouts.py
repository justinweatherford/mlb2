import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime


CALLOUTS = [
    {"label": "Guardians +108", "type": "moneyline", "team": "CLE"},
    {"label": "Pirates -120", "type": "moneyline", "team": "PIT"},
    {"label": "Mets/Reds under 9 -115", "type": "total_under", "teams": ["NYM", "CIN"], "line": 9},
    {"label": "Phillies -115", "type": "moneyline", "team": "PHI"},
]


def table_exists(conn, name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def columns(conn, table):
    if not table_exists(conn, table):
        return []
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def fetchone_dict(conn, query, params=()):
    conn.row_factory = sqlite3.Row
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def fetchall_dict(conn, query, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def find_game_for_team(conn, date, team):
    return fetchone_dict(
        conn,
        """
        SELECT *
        FROM mlb_games
        WHERE game_date = ?
          AND (away_abbr = ? OR home_abbr = ?)
        ORDER BY game_start_time_utc, game_pk
        LIMIT 1
        """,
        (date, team, team),
    )


def find_game_for_teams(conn, date, teams):
    a, b = teams
    return fetchone_dict(
        conn,
        """
        SELECT *
        FROM mlb_games
        WHERE game_date = ?
          AND (
            (away_abbr = ? AND home_abbr = ?)
            OR
            (away_abbr = ? AND home_abbr = ?)
          )
        ORDER BY game_start_time_utc, game_pk
        LIMIT 1
        """,
        (date, a, b, b, a),
    )


def recent_team_games(conn, date, team, limit=10):
    return fetchall_dict(
        conn,
        """
        SELECT
            game_date,
            game_id,
            away_abbr,
            home_abbr,
            final_away_score,
            final_home_score,
            final_total,
            is_final
        FROM mlb_games
        WHERE game_date < ?
          AND is_final = 1
          AND (away_abbr = ? OR home_abbr = ?)
        ORDER BY game_date DESC
        LIMIT ?
        """,
        (date, team, team, limit),
    )


def summarize_recent(team, games):
    if not games:
        return {
            "games": 0,
            "avg_runs_for": None,
            "avg_runs_allowed": None,
            "avg_total": None,
            "wins": None,
            "losses": None,
        }

    runs_for = []
    runs_allowed = []
    totals = []
    wins = 0
    losses = 0

    for g in games:
        away = g["away_abbr"]
        home = g["home_abbr"]
        away_score = g["final_away_score"]
        home_score = g["final_home_score"]

        if away_score is None or home_score is None:
            continue

        if team == away:
            rf = away_score
            ra = home_score
        elif team == home:
            rf = home_score
            ra = away_score
        else:
            continue

        runs_for.append(rf)
        runs_allowed.append(ra)
        totals.append(rf + ra)

        if rf > ra:
            wins += 1
        else:
            losses += 1

    def avg(vals):
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "games": len(runs_for),
        "avg_runs_for": avg(runs_for),
        "avg_runs_allowed": avg(runs_allowed),
        "avg_total": avg(totals),
        "wins": wins,
        "losses": losses,
    }


def get_team_context(conn, team, season):
    if not table_exists(conn, "mlb_team_context"):
        return None

    cols = columns(conn, "mlb_team_context")
    if "team_abbr" in cols:
        team_col = "team_abbr"
    elif "team" in cols:
        team_col = "team"
    else:
        return None

    season_filter = ""
    params = [team]

    if "season" in cols:
        season_filter = "AND season = ?"
        params.append(season)

    query = f"""
        SELECT *
        FROM mlb_team_context
        WHERE {team_col} = ?
        {season_filter}
        ORDER BY season DESC
        LIMIT 1
    """

    return fetchone_dict(conn, query, tuple(params))


def get_fangraphs_context(conn, team, season):
    if not table_exists(conn, "fangraphs_team_offense"):
        return None

    cols = columns(conn, "fangraphs_team_offense")
    if "team" not in cols:
        return None

    season_filter = ""
    params = [team]

    if "season" in cols:
        season_filter = "AND season = ?"
        params.append(season)

    query = f"""
        SELECT *
        FROM fangraphs_team_offense
        WHERE team = ?
        {season_filter}
        LIMIT 1
    """

    return fetchone_dict(conn, query, tuple(params))


def get_weather(conn, game_pk, date, game_id):
    if not table_exists(conn, "mlb_weather_reference"):
        return None

    cols = columns(conn, "mlb_weather_reference")

    if "game_pk" in cols and game_pk:
        row = fetchone_dict(
            conn,
            "SELECT * FROM mlb_weather_reference WHERE game_pk = ? LIMIT 1",
            (game_pk,),
        )
        if row:
            return row

    if "game_id" in cols:
        row = fetchone_dict(
            conn,
            "SELECT * FROM mlb_weather_reference WHERE game_id = ? LIMIT 1",
            (game_id,),
        )
        if row:
            return row

    if "date" in cols and "game_id" in cols:
        row = fetchone_dict(
            conn,
            "SELECT * FROM mlb_weather_reference WHERE date = ? AND game_id = ? LIMIT 1",
            (date, game_id),
        )
        if row:
            return row

    return None


def get_kalshi_moneyline(conn, game_id, team):
    if not table_exists(conn, "kalshi_markets"):
        return None

    return fetchone_dict(
        conn,
        """
        SELECT
            market_ticker,
            market_type,
            title,
            yes_bid_cents,
            yes_ask_cents,
            last_price_cents,
            game_id,
            away_team,
            home_team,
            selected_team_abbr,
            yes_means,
            no_means,
            contract_direction,
            supported_by_bot,
            candidate_surface,
            is_noisy_market
        FROM kalshi_markets
        WHERE game_id = ?
          AND market_type = 'moneyline'
          AND selected_team_abbr = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (game_id, team),
    )


def get_kalshi_total(conn, game_id, line):
    if not table_exists(conn, "kalshi_markets"):
        return None

    return fetchone_dict(
        conn,
        """
        SELECT
            market_ticker,
            market_type,
            title,
            yes_bid_cents,
            yes_ask_cents,
            last_price_cents,
            game_id,
            away_team,
            home_team,
            line_value,
            yes_means,
            no_means,
            contract_direction,
            supported_by_bot,
            candidate_surface,
            is_noisy_market
        FROM kalshi_markets
        WHERE game_id = ?
          AND market_type = 'full_game_total'
          AND ABS(COALESCE(line_value, -999) - ?) < 0.001
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (game_id, line),
    )


def compact_context(ctx):
    if not ctx:
        return {}

    preferred = [
        "team",
        "team_abbr",
        "season",
        "overall_rating",
        "team_strength",
        "season_offense_rating",
        "season_defense_rating",
        "offense_rating",
        "defense_rating",
        "scoring_form_rating",
        "rolling_l5_rpg",
        "rolling_l10_rpg",
        "bullpen_risk",
        "bullpen_risk_rating",
        "f5_offense_rating",
        "f5_pitching_risk",
        "comeback_rating",
        "updated_at",
    ]

    out = {}
    for k in preferred:
        if k in ctx:
            out[k] = ctx.get(k)

    # Fallback: include first 20 scalar fields so we can inspect schema-specific values.
    if len(out) < 5:
        for k, v in ctx.items():
            if isinstance(v, (str, int, float)) or v is None:
                out[k] = v
            if len(out) >= 20:
                break

    return out


def audit_callout(conn, date, season, callout):
    result = {
        "callout": callout["label"],
        "callout_type": callout["type"],
        "date": date,
        "season": season,
    }

    if callout["type"] == "moneyline":
        team = callout["team"]
        game = find_game_for_team(conn, date, team)
        result["selected_team"] = team
    else:
        game = find_game_for_teams(conn, date, callout["teams"])
        result["selected_team"] = None

    if not game:
        result["status"] = "game_not_found_for_date"
        return result

    away = game["away_abbr"]
    home = game["home_abbr"]
    game_id = game["game_id"]

    result.update(
        {
            "status": "ok",
            "game_pk": game["game_pk"],
            "game_id": game_id,
            "away_abbr": away,
            "home_abbr": home,
            "game_start_time_utc": game.get("game_start_time_utc"),
            "game_status": game.get("status"),
            "is_final": game.get("is_final"),
            "final_away_score": game.get("final_away_score"),
            "final_home_score": game.get("final_home_score"),
            "final_total": game.get("final_total"),
        }
    )

    teams_to_audit = [away, home]

    for team in teams_to_audit:
        recent5 = summarize_recent(team, recent_team_games(conn, date, team, 5))
        recent10 = summarize_recent(team, recent_team_games(conn, date, team, 10))
        result[f"{team}_recent5"] = json.dumps(recent5, sort_keys=True)
        result[f"{team}_recent10"] = json.dumps(recent10, sort_keys=True)
        result[f"{team}_team_context"] = json.dumps(
            compact_context(get_team_context(conn, team, season)),
            sort_keys=True,
        )
        result[f"{team}_fangraphs"] = json.dumps(
            compact_context(get_fangraphs_context(conn, team, season)),
            sort_keys=True,
        )

    weather = get_weather(conn, game.get("game_pk"), date, game_id)
    result["weather_context"] = json.dumps(compact_context(weather), sort_keys=True)

    if callout["type"] == "moneyline":
        result["kalshi_market"] = json.dumps(
            get_kalshi_moneyline(conn, game_id, callout["team"]) or {},
            sort_keys=True,
        )

    if callout["type"] == "total_under":
        result["total_line"] = callout["line"]
        result["kalshi_market"] = json.dumps(
            get_kalshi_total(conn, game_id, callout["line"]) or {},
            sort_keys=True,
        )

    return result


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return

    # Union fieldnames because dynamic team columns differ by game.
    fieldnames = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_md(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    lines = []
    lines.append("# Friend Callout Team Data Audit")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append("")

    for row in rows:
        lines.append("---")
        lines.append("")
        lines.append(f"## {row.get('callout')}")
        lines.append("")
        lines.append(f"- Status: `{row.get('status')}`")
        lines.append(f"- Game: `{row.get('game_id')}`")
        lines.append(f"- Away/Home: `{row.get('away_abbr')} @ {row.get('home_abbr')}`")
        lines.append(f"- Start UTC: `{row.get('game_start_time_utc')}`")
        lines.append(f"- Game status: `{row.get('game_status')}`")
        lines.append("")

        for key in row:
            if key.endswith("_recent5") or key.endswith("_recent10"):
                lines.append(f"### {key}")
                lines.append("```json")
                lines.append(row[key])
                lines.append("```")
                lines.append("")

        for key in row:
            if key.endswith("_team_context") or key.endswith("_fangraphs"):
                lines.append(f"### {key}")
                lines.append("```json")
                lines.append(row[key])
                lines.append("```")
                lines.append("")

        lines.append("### Kalshi Market")
        lines.append("```json")
        lines.append(row.get("kalshi_market") or "{}")
        lines.append("```")
        lines.append("")

        lines.append("### Weather")
        lines.append("```json")
        lines.append(row.get("weather_context") or "{}")
        lines.append("```")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--season", default=None)
    parser.add_argument("--db", default="kalshi_mlb.db")
    args = parser.parse_args()

    season = args.season or args.date[:4]

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    out_dir = os.path.join("outputs", "friend_callout_audit", args.date)
    os.makedirs(out_dir, exist_ok=True)

    rows = [audit_callout(conn, args.date, season, c) for c in CALLOUTS]

    csv_path = os.path.join(out_dir, "friend_callout_team_data_audit.csv")
    md_path = os.path.join(out_dir, "friend_callout_team_data_audit.md")

    write_csv(csv_path, rows)
    write_md(md_path, rows)

    print("WROTE:", out_dir)
    print(" ", csv_path)
    print(" ", md_path)
    print()
    for row in rows:
        print(row.get("callout"), "->", row.get("status"), row.get("game_id"))


if __name__ == "__main__":
    main()