import argparse
import csv
import glob
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "team_logic_training_preview"


CANDIDATE_PATTERNS = [
    "outputs/**/candidate_settlement_outcomes*.csv",
    "candidate_settlement_outcomes*.csv",
    "outputs/**/candidate_outcomes*.csv",
    "candidate_outcomes*.csv",
]


TEAM_COL_CANDIDATES = [
    "team_abbr",
    "team",
    "abbr",
    "team_code",
    "selected_team_abbr",
]

DATE_COL_CANDIDATES = [
    "game_date",
    "date",
    "as_of_date",
    "context_date",
    "snapshot_date",
]

GAME_COL_CANDIDATES = [
    "game_pk",
    "mlb_game_pk",
]


METRIC_CANDIDATES = {
    "team_strength": ["team_strength_rating", "overall_context_score"],
    "offense": ["offense_rating", "season_offense_rating", "f5_offense_rating"],
    "recent_form": ["l10_runs_per_game", "l5_runs_per_game", "recent_runs_per_game", "runs_per_game_l10", "runs_per_game_l5"],
    "bullpen_risk": ["bullpen_risk_rating"],
    "runs_per_game": ["runs_per_game"],
    "runs_allowed": ["runs_allowed_per_game"],
}


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        s = str(value).strip()
        if s == "" or s.lower() in {"nan", "none", "null"}:
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
    if not s:
        return None
    # Handles YYYY-MM-DD and ISO timestamps.
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    return None


def latest_file(patterns: list[str]) -> Path:
    matches: list[Path] = []
    for pat in patterns:
        matches.extend(Path(".").glob(pat))
    matches = [p for p in matches if p.is_file()]
    if not matches:
        raise FileNotFoundError(
            "No candidate outcome CSV found. Expected candidate_settlement_outcomes*.csv or candidate_outcomes*.csv."
        )
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_csv_rows(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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


def load_team_context(conn: sqlite3.Connection) -> tuple[dict, dict, dict, dict]:
    cols = table_columns(conn, "mlb_team_context")
    if not cols:
        return {}, {}, {}, {}

    team_col = pick_col(cols, TEAM_COL_CANDIDATES)
    date_col = pick_col(cols, DATE_COL_CANDIDATES)
    game_col = pick_col(cols, GAME_COL_CANDIDATES)

    if not team_col:
        return {}, {}, {}, {"warning": "mlb_team_context exists but no team column was detected", "cols": cols}

    rows = conn.execute("SELECT * FROM mlb_team_context").fetchall()
    names = [d[0] for d in conn.execute("SELECT * FROM mlb_team_context LIMIT 1").description]

    by_game_team = {}
    by_date_team = {}
    latest_by_team = {}
    meta = {
        "team_col": team_col,
        "date_col": date_col,
        "game_col": game_col,
        "cols": cols,
        "rows": len(rows),
    }

    for raw in rows:
        r = dict(zip(names, raw))
        team = str(r.get(team_col) or "").strip().upper()
        if not team:
            continue

        row_date = parse_date(r.get(date_col)) if date_col else None
        game_pk = str(r.get(game_col)).strip() if game_col and r.get(game_col) is not None else None

        if game_pk:
            by_game_team[(game_pk, team)] = r
        if row_date:
            by_date_team[(row_date, team)] = r

        # Keep latest available date per team when dates exist, otherwise last row.
        if team not in latest_by_team:
            latest_by_team[team] = r
        elif row_date:
            prev_date = parse_date(latest_by_team[team].get(date_col)) if date_col else None
            if not prev_date or row_date >= prev_date:
                latest_by_team[team] = r

    return by_game_team, by_date_team, latest_by_team, meta


def get_metric(ctx: dict | None, metric_key: str) -> tuple[float | None, str | None]:
    if not ctx:
        return None, None
    for col in METRIC_CANDIDATES.get(metric_key, []):
        if col in ctx:
            val = as_float(ctx.get(col))
            if val is not None:
                return val, col
    return None, None


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


def recent_runs_bucket(v: float | None) -> str:
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


def runs_needed_bucket(v: float | None) -> str:
    if v is None:
        return "missing"
    if v <= 1:
        return "need_0_1"
    if v <= 2:
        return "need_2"
    if v <= 3:
        return "need_3"
    if v <= 4:
        return "need_4"
    if v <= 5:
        return "need_5"
    return "need_6_plus"


def infer_selected_team_score(row: dict) -> float | None:
    team = (row.get("selected_team_abbr") or "").strip().upper()
    game_id = row.get("game_id") or ""
    away_score = as_float(row.get("score_away_at_candidate"))
    home_score = as_float(row.get("score_home_at_candidate"))

    if "@" in game_id and team:
        away, home = [x.strip().upper() for x in game_id.split("@", 1)]
        if team == away:
            return away_score
        if team == home:
            return home_score

    return None


def infer_runs_needed_team(row: dict) -> float | None:
    line = (
        as_float(row.get("resolved_line_value"))
        if as_float(row.get("resolved_line_value")) is not None
        else as_float(row.get("market_line_value"))
    )
    if line is None:
        line = as_float(row.get("candidate_line_value"))

    score = infer_selected_team_score(row)
    if line is None or score is None:
        return None
    return round(line - score, 2)


def infer_runs_needed_total(row: dict) -> float | None:
    line = (
        as_float(row.get("resolved_line_value"))
        if as_float(row.get("resolved_line_value")) is not None
        else as_float(row.get("market_line_value"))
    )
    if line is None:
        line = as_float(row.get("candidate_line_value"))

    away_score = as_float(row.get("score_away_at_candidate"))
    home_score = as_float(row.get("score_home_at_candidate"))
    if line is None or away_score is None or home_score is None:
        return None
    return round(line - (away_score + home_score), 2)


def settlement_win(row: dict) -> int | None:
    v = as_float(row.get("hold_to_settle_win"))
    if v is None:
        return None
    return 1 if v >= 1 else 0


def pnl_cents(row: dict) -> float | None:
    return as_float(row.get("hold_to_settle_pnl_cents"))


def risk_cents(row: dict) -> float | None:
    # Entry cost is the most realistic risk proxy for long YES/NO paper entries.
    v = as_float(row.get("entry_side_price"))
    if v is not None:
        return v
    v = as_float(row.get("entry_yes_ask"))
    if v is not None:
        return v
    return None


def summarize(rows: list[dict], group_cols: list[str]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        key = tuple(r.get(c, "missing") or "missing" for c in group_cols)
        groups[key].append(r)

    out = []
    for key, rs in groups.items():
        wins = 0
        losses = 0
        settled = 0
        pnl_sum = 0.0
        pnl_count = 0
        risk_sum = 0.0
        risk_count = 0
        spread_sum = 0.0
        spread_count = 0
        entry_sum = 0.0
        entry_count = 0

        for r in rs:
            w = settlement_win(r)
            if w is not None:
                settled += 1
                if w == 1:
                    wins += 1
                else:
                    losses += 1

            p = pnl_cents(r)
            if p is not None:
                pnl_sum += p
                pnl_count += 1

            risk = risk_cents(r)
            if risk is not None:
                risk_sum += risk
                risk_count += 1

            spread = as_float(r.get("spread_cents"))
            if spread is not None:
                spread_sum += spread
                spread_count += 1

            entry = as_float(r.get("entry_side_price"))
            if entry is not None:
                entry_sum += entry
                entry_count += 1

        row = {col: val for col, val in zip(group_cols, key)}
        row.update({
            "count": len(rs),
            "settled_count": settled,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / settled, 4) if settled else None,
            "avg_pnl_cents": round(pnl_sum / pnl_count, 3) if pnl_count else None,
            "total_pnl_cents": round(pnl_sum, 3),
            "total_risk_cents": round(risk_sum, 3),
            "roi_on_risk": round(pnl_sum / risk_sum, 4) if risk_sum else None,
            "avg_entry_price": round(entry_sum / entry_count, 3) if entry_count else None,
            "avg_spread_cents": round(spread_sum / spread_count, 3) if spread_count else None,
        })
        out.append(row)

    out.sort(key=lambda r: (-(r.get("count") or 0), str([r.get(c) for c in group_cols])))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only team logic training preview.")
    parser.add_argument("--candidates", default=None, help="Optional path to candidate outcome CSV.")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    candidate_path = Path(args.candidates) if args.candidates else latest_file(CANDIDATE_PATTERNS)
    candidates = read_csv_rows(candidate_path)

    conn = sqlite3.connect(args.db)
    by_game_team, by_date_team, latest_by_team, ctx_meta = load_team_context(conn)

    enriched = []
    missing_context = 0

    for r in candidates:
        row = dict(r)
        selected_team = (row.get("selected_team_abbr") or "").strip().upper()
        game_pk = str(row.get("game_pk") or "").strip()
        created_date = parse_date(row.get("created_at"))

        ctx = None
        if selected_team and game_pk and (game_pk, selected_team) in by_game_team:
            ctx = by_game_team[(game_pk, selected_team)]
            ctx_source = "game_team"
        elif selected_team and created_date and (created_date, selected_team) in by_date_team:
            ctx = by_date_team[(created_date, selected_team)]
            ctx_source = "date_team"
        elif selected_team and selected_team in latest_by_team:
            ctx = latest_by_team[selected_team]
            ctx_source = "latest_team"
        else:
            ctx_source = "missing"
            missing_context += 1

        team_strength, team_strength_col = get_metric(ctx, "team_strength")
        offense, offense_col = get_metric(ctx, "offense")
        recent_form, recent_form_col = get_metric(ctx, "recent_form")
        bullpen_risk, bullpen_col = get_metric(ctx, "bullpen_risk")
        runs_per_game, rpg_col = get_metric(ctx, "runs_per_game")
        runs_allowed, ra_col = get_metric(ctx, "runs_allowed")

        runs_needed_team = infer_runs_needed_team(row)
        runs_needed_total = infer_runs_needed_total(row)

        row.update({
            "context_source": ctx_source,
            "team_strength_metric": team_strength,
            "team_strength_col": team_strength_col,
            "team_strength_bucket": rating_bucket(team_strength),
            "offense_metric": offense,
            "offense_col": offense_col,
            "offense_bucket": rating_bucket(offense),
            "recent_form_metric": recent_form,
            "recent_form_col": recent_form_col,
            "recent_form_bucket": recent_runs_bucket(recent_form),
            "bullpen_risk_metric": bullpen_risk,
            "bullpen_risk_col": bullpen_col,
            "bullpen_risk_bucket": bullpen_bucket(bullpen_risk),
            "runs_per_game_context": runs_per_game,
            "runs_allowed_context": runs_allowed,
            "runs_needed_team_inferred": runs_needed_team,
            "runs_needed_team_bucket": runs_needed_bucket(runs_needed_team),
            "runs_needed_total_inferred": runs_needed_total,
            "runs_needed_total_bucket": runs_needed_bucket(runs_needed_total),
        })
        enriched.append(row)

    base_fields = list(enriched[0].keys()) if enriched else []
    write_csv(OUT_DIR / "enriched_candidate_team_context.csv", enriched, base_fields)

    summaries = {
        "summary_by_team_strength_bucket.csv": ["team_strength_bucket"],
        "summary_by_offense_bucket.csv": ["offense_bucket"],
        "summary_by_recent_form_bucket.csv": ["recent_form_bucket"],
        "summary_by_bullpen_risk_bucket.csv": ["bullpen_risk_bucket"],
        "summary_by_runs_needed_team_bucket.csv": ["runs_needed_team_bucket"],
        "summary_by_candidate_type_and_team_strength.csv": ["candidate_type", "team_strength_bucket"],
        "summary_by_candidate_type_and_offense.csv": ["candidate_type", "offense_bucket"],
        "summary_by_candidate_type_and_runs_needed_team.csv": ["candidate_type", "runs_needed_team_bucket"],
        "summary_by_market_type_and_team_strength.csv": ["market_type", "team_strength_bucket"],
    }

    for filename, cols in summaries.items():
        s = summarize(enriched, cols)
        fields = cols + [
            "count", "settled_count", "wins", "losses", "win_rate",
            "avg_pnl_cents", "total_pnl_cents", "total_risk_cents",
            "roi_on_risk", "avg_entry_price", "avg_spread_cents",
        ]
        write_csv(OUT_DIR / filename, s, fields)

    suspicious = []
    for r in enriched:
        wr = settlement_win(r)
        pnl = pnl_cents(r)
        if wr == 0 and pnl is not None and pnl <= -40:
            suspicious.append(r)
        elif wr == 1 and pnl is not None and pnl >= 40:
            suspicious.append(r)

    suspicious.sort(key=lambda r: abs(as_float(r.get("hold_to_settle_pnl_cents")) or 0), reverse=True)
    write_csv(OUT_DIR / "suspicious_team_logic_cases.csv", suspicious[:100], base_fields)

    summary_lines = []
    summary_lines.append("# Team Logic Training Preview")
    summary_lines.append("")
    summary_lines.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    summary_lines.append("")
    summary_lines.append(f"- Candidate source: `{candidate_path}`")
    summary_lines.append(f"- Candidate rows: {len(enriched):,}")
    summary_lines.append(f"- Team context rows: {ctx_meta.get('rows', 0):,}" if ctx_meta else "- Team context rows: 0")
    summary_lines.append(f"- Missing team context matches: {missing_context:,}")
    if ctx_meta:
        summary_lines.append(f"- Team context team column: `{ctx_meta.get('team_col')}`")
        summary_lines.append(f"- Team context date column: `{ctx_meta.get('date_col')}`")
        summary_lines.append(f"- Team context game column: `{ctx_meta.get('game_col')}`")
    summary_lines.append("")

    top_sections = [
        ("Candidate type", ["candidate_type"]),
        ("Team strength bucket", ["team_strength_bucket"]),
        ("Offense bucket", ["offense_bucket"]),
        ("Recent form bucket", ["recent_form_bucket"]),
        ("Runs needed team bucket", ["runs_needed_team_bucket"]),
    ]

    for title, cols in top_sections:
        summary_lines.append(f"## {title}")
        summary_lines.append("")
        for row in summarize(enriched, cols)[:12]:
            label = " / ".join(str(row.get(c)) for c in cols)
            summary_lines.append(
                f"- {label}: count {row['count']}, settled {row['settled_count']}, "
                f"win {row['win_rate']}, ROI {row['roi_on_risk']}, avg PnL {row['avg_pnl_cents']}c"
            )
        summary_lines.append("")

    summary_lines.append("## Files Written")
    summary_lines.append("")
    for filename in ["enriched_candidate_team_context.csv", *summaries.keys(), "suspicious_team_logic_cases.csv"]:
        summary_lines.append(f"- {filename}")

    (OUT_DIR / "team_logic_summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {OUT_DIR / 'team_logic_summary.md'}")
    print(f"  {OUT_DIR / 'enriched_candidate_team_context.csv'}")
    print(f"Candidate source: {candidate_path}")
    print(f"Rows: {len(enriched):,}")
    print(f"Missing context: {missing_context:,}")

if __name__ == "__main__":
    main()
