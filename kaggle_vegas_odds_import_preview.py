"""
Kaggle Vegas Odds Import Preview
=================================
Read-only diagnostic script. No DB writes. No model changes. No trades.

Merges oddsDataMLB.csv (stats + odds) with oddsData.csv (home/away + gameNumber),
normalizes schema, computes derived fields, and writes diagnostic outputs.

Amendments applied:
- game_id: stable cross-row game identifier (date + sorted team pair + game_number)
- dh_assignment_reliable: 0 for ambiguous_position rows (exclude from home/away analysis)
- team_abbreviation_report.csv: unique team abbr diagnostics by season
"""
import csv
import collections
import statistics
from pathlib import Path

MLB_CSV  = Path("data/external/kaggle_mlb_odds/oddsDataMLB.csv")
ODDS_CSV = Path("data/external/kaggle_mlb_odds/oddsData.csv")
OUT_DIR  = Path("outputs/vegas_odds_import_preview")

NORMALIZED_COLS = [
    "game_id",
    "game_date", "season", "game_number", "team", "opponent",
    "is_home", "dh_merge_method", "dh_assignment_reliable",
    "runs", "opp_runs",
    "moneyline", "opponent_moneyline",
    "run_line", "run_line_odds",
    "game_total", "over_odds", "under_odds",
    "projected_runs",
    "park_name", "total_runs", "run_differential",
    "team_win", "team_runs_4plus", "team_runs_5plus", "team_runs_under_5",
    "game_over", "game_under", "total_push",
    "moneyline_implied_probability_raw",
    "moneyline_implied_probability_no_vig",
    "source_file",
]


def _safe_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _safe_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _ml_implied(ml_str):
    ml = _safe_int(ml_str)
    if ml is None or ml_str in ("NA", ""):
        return None
    if ml < 0:
        return (-ml) / ((-ml) + 100)
    else:
        return 100 / (ml + 100)


def load_csvs():
    with open(MLB_CSV, encoding="utf-8") as f:
        mlb = list(csv.DictReader(f))
    with open(ODDS_CSV, encoding="utf-8") as f:
        odds = list(csv.DictReader(f))
    return mlb, odds


def build_odds_index(odds):
    """(date, team) -> list of odds rows sorted by gameNumber."""
    idx = collections.defaultdict(list)
    for r in odds:
        idx[(r["date"], r["team"])].append(r)
    for key in idx:
        idx[key].sort(key=lambda r: int(r["gameNumber"]))
    return idx


def merge_rows(mlb, odds_idx):
    """
    Merge each MLB row with its matching odds row.
    DH disambiguation cascade:
      1. moneyLine == line (primary)
      2. runLine + total combination (fallback)
      3. file-position order, flagged ambiguous_position
    Returns list of (mlb_row, odds_row, method).
    """
    merged = []
    seen = collections.defaultdict(int)

    for r in mlb:
        key = (r["date"], r["team"])
        candidates = odds_idx.get(key, [])

        if len(candidates) == 0:
            odds_row = {}
            method = "no_match"
        elif len(candidates) == 1:
            odds_row = candidates[0]
            method = "direct"
        else:
            # DH: try moneyLine first
            ml_matches = [c for c in candidates if c["line"] == r["moneyLine"]]
            if len(ml_matches) == 1:
                odds_row = ml_matches[0]
                method = "moneyline"
            else:
                # Fallback: runLine + total
                rl_tot = [
                    c for c in candidates
                    if c["runLine"] == r["runLine"] and c["total"] == r["total"]
                ]
                if len(rl_tot) == 1:
                    odds_row = rl_tot[0]
                    method = "runline_total"
                else:
                    pos = seen[key]
                    odds_row = candidates[pos] if pos < len(candidates) else candidates[0]
                    method = "ambiguous_position"

        seen[key] += 1
        merged.append((r, odds_row, method))

    return merged


def make_game_id(game_date, team, opponent, game_number):
    """
    Stable cross-row identifier: same value on both the team and opponent rows.
    Format: YYYY-MM-DD_AAA_BBB_N  where AAA < BBB lexicographically.
    """
    t1 = min(team, opponent)
    t2 = max(team, opponent)
    gn = game_number if game_number else "1"
    return f"{game_date}_{t1}_{t2}_{gn}"


def normalize_row(mlb_r, odds_r, method):
    """Return a normalized output dict with all derived fields."""
    runs       = _safe_int(mlb_r.get("runs", ""))
    opp_runs   = _safe_int(mlb_r.get("oppRuns", ""))
    total_runs = _safe_int(mlb_r.get("totalRuns", ""))
    game_total = _safe_float(mlb_r.get("total", ""))

    game_number = odds_r.get("gameNumber", "")
    team        = mlb_r.get("team", "")
    opponent    = mlb_r.get("opponent", "")
    game_date   = mlb_r.get("date", "")

    # Stable game ID
    game_id = make_game_id(game_date, team, opponent, game_number)

    # dh_assignment_reliable: 0 means this row's is_home / game_number
    # may be swapped with the other game in the doubleheader.
    # Downstream callers should exclude these rows from home/away-sensitive analysis.
    dh_assignment_reliable = 0 if method == "ambiguous_position" else 1

    # is_home
    at = odds_r.get("at", "")
    is_home = 1 if at == "H" else (0 if at == "V" else "")

    # team_win
    if runs is not None and opp_runs is not None:
        if runs > opp_runs:
            team_win = 1
        elif runs < opp_runs:
            team_win = 0
        else:
            team_win = ""  # tie (rare rain/suspended games)
    else:
        team_win = ""

    # team scoring thresholds
    team_runs_4plus  = (1 if runs >= 4 else 0) if runs is not None else ""
    team_runs_5plus  = (1 if runs >= 5 else 0) if runs is not None else ""
    team_runs_under5 = (1 if runs < 5  else 0) if runs is not None else ""

    # game total result
    if total_runs is not None and game_total is not None:
        if total_runs > game_total:
            game_over  = 1
            game_under = 0
            total_push = 0
        elif total_runs < game_total:
            game_over  = 0
            game_under = 1
            total_push = 0
        else:
            game_over  = ""
            game_under = ""
            total_push = 1
    else:
        game_over  = ""
        game_under = ""
        total_push = 0

    # moneyline implied probabilities
    p_raw = _ml_implied(mlb_r.get("moneyLine", ""))
    p_opp = _ml_implied(mlb_r.get("oppMoneyLine", ""))
    p_no_vig = (
        round(p_raw / (p_raw + p_opp), 6)
        if p_raw is not None and p_opp is not None and (p_raw + p_opp) > 0
        else ""
    )
    p_raw_out = round(p_raw, 6) if p_raw is not None else ""

    return {
        "game_id":      game_id,
        "game_date":    game_date,
        "season":       mlb_r.get("season", ""),
        "game_number":  game_number,
        "team":         team,
        "opponent":     opponent,
        "is_home":      is_home,
        "dh_merge_method":       method,
        "dh_assignment_reliable": dh_assignment_reliable,
        "runs":         mlb_r.get("runs", ""),
        "opp_runs":     mlb_r.get("oppRuns", ""),
        "moneyline":    mlb_r.get("moneyLine", ""),
        "opponent_moneyline": mlb_r.get("oppMoneyLine", ""),
        "run_line":     mlb_r.get("runLine", ""),
        "run_line_odds": mlb_r.get("runLineOdds", ""),
        "game_total":   mlb_r.get("total", ""),
        "over_odds":    mlb_r.get("overOdds", ""),
        "under_odds":   mlb_r.get("underOdds", ""),
        "projected_runs": mlb_r.get("projectedRuns", ""),
        "park_name":    mlb_r.get("parkName", ""),
        "total_runs":   mlb_r.get("totalRuns", ""),
        "run_differential": mlb_r.get("runDif", ""),
        "team_win":     team_win,
        "team_runs_4plus":   team_runs_4plus,
        "team_runs_5plus":   team_runs_5plus,
        "team_runs_under_5": team_runs_under5,
        "game_over":    game_over,
        "game_under":   game_under,
        "total_push":   total_push,
        "moneyline_implied_probability_raw":    p_raw_out,
        "moneyline_implied_probability_no_vig": p_no_vig,
        "source_file":  "oddsDataMLB.csv+oddsData.csv",
    }


def write_normalized_preview(rows, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=NORMALIZED_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"  Wrote {len(rows):,} rows -> {out_path}")


# ---------------------------------------------------------------------------
# Data quality report (per season)
# ---------------------------------------------------------------------------

def compute_data_quality(normalized_rows):
    by_season = collections.defaultdict(lambda: collections.defaultdict(int))
    fields_to_check = [
        "run_line", "run_line_odds", "over_odds", "under_odds",
        "projected_runs", "park_name",
    ]
    for row in normalized_rows:
        s = row["season"]
        by_season[s]["total"] += 1
        for f in fields_to_check:
            v = row.get(f, "")
            if v in ("", "NA", "NaN", None):
                by_season[s][f"missing_{f}"] += 1

    dq_rows = []
    for season in sorted(by_season):
        d = by_season[season]
        total = d["total"]
        dq_rows.append({
            "season":                    season,
            "total_rows":                total,
            "missing_run_line":          d.get("missing_run_line", 0),
            "missing_run_line_odds":     d.get("missing_run_line_odds", 0),
            "missing_over_odds":         d.get("missing_over_odds", 0),
            "missing_under_odds":        d.get("missing_under_odds", 0),
            "missing_projected_runs":    d.get("missing_projected_runs", 0),
            "missing_park_name":         d.get("missing_park_name", 0),
            "run_line_pct_present":      f"{100*(total - d.get('missing_run_line',0))/total:.1f}%",
            "run_line_odds_pct_present": f"{100*(total - d.get('missing_run_line_odds',0))/total:.1f}%",
        })
    return dq_rows


def write_data_quality_report(dq_rows, out_path):
    if not dq_rows:
        return
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(dq_rows[0].keys()))
        w.writeheader()
        w.writerows(dq_rows)
    print(f"  Wrote {len(dq_rows)} rows -> {out_path}")


# ---------------------------------------------------------------------------
# Team abbreviation diagnostics
# ---------------------------------------------------------------------------

def compute_team_abbr_diagnostics(normalized_rows):
    """
    For every unique team abbreviation: total rows, first/last season,
    season list, unique opponent count.
    """
    abbr_data = collections.defaultdict(lambda: {
        "total_rows": 0,
        "seasons": set(),
        "opponents": set(),
    })
    for row in normalized_rows:
        t = row["team"]
        abbr_data[t]["total_rows"] += 1
        abbr_data[t]["seasons"].add(row["season"])
        abbr_data[t]["opponents"].add(row["opponent"])

    rows = []
    for abbr in sorted(abbr_data):
        d = abbr_data[abbr]
        seasons = sorted(d["seasons"])
        rows.append({
            "abbreviation":      abbr,
            "total_rows":        d["total_rows"],
            "first_season":      seasons[0],
            "last_season":       seasons[-1],
            "seasons_active":    ",".join(seasons),
            "seasons_count":     len(seasons),
            "unique_opponents":  len(d["opponents"]),
            "note": (
                "partial_seasons" if len(seasons) < 10 else ""
            ),
        })
    return rows


def write_team_abbr_report(abbr_rows, out_path):
    if not abbr_rows:
        return
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(abbr_rows[0].keys()))
        w.writeheader()
        w.writerows(abbr_rows)
    print(f"  Wrote {len(abbr_rows)} rows -> {out_path}")


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def compute_summary_stats(normalized_rows):
    n = len(normalized_rows)
    wins   = sum(1 for r in normalized_rows if r["team_win"] == 1)
    losses = sum(1 for r in normalized_rows if r["team_win"] == 0)
    ties   = sum(1 for r in normalized_rows if r["team_win"] == "")
    r4plus = sum(1 for r in normalized_rows if r["team_runs_4plus"] == 1)
    r5plus = sum(1 for r in normalized_rows if r["team_runs_5plus"] == 1)
    overs  = sum(1 for r in normalized_rows if r["game_over"] == 1)
    unders = sum(1 for r in normalized_rows if r["game_under"] == 1)
    pushes = sum(1 for r in normalized_rows if r["total_push"] == 1)
    dh_ambiguous  = sum(1 for r in normalized_rows if r["dh_merge_method"] == "ambiguous_position")
    no_match      = sum(1 for r in normalized_rows if r["dh_merge_method"] == "no_match")
    by_season     = collections.Counter(r["season"] for r in normalized_rows)
    merge_methods = collections.Counter(r["dh_merge_method"] for r in normalized_rows)

    p_nv_vals = [
        float(r["moneyline_implied_probability_no_vig"])
        for r in normalized_rows
        if r["moneyline_implied_probability_no_vig"] not in ("", None)
    ]
    p_nv_mean = statistics.mean(p_nv_vals) if p_nv_vals else None

    # game_id uniqueness sanity check
    game_ids = [r["game_id"] for r in normalized_rows]
    unique_game_ids = len(set(game_ids))

    return {
        "n": n, "wins": wins, "losses": losses, "ties": ties,
        "r4plus": r4plus, "r5plus": r5plus,
        "overs": overs, "unders": unders, "pushes": pushes,
        "dh_ambiguous": dh_ambiguous, "no_match": no_match,
        "by_season": dict(sorted(by_season.items())),
        "merge_methods": dict(merge_methods),
        "p_nv_mean": p_nv_mean,
        "unique_game_ids": unique_game_ids,
    }


# ---------------------------------------------------------------------------
# Import summary markdown
# ---------------------------------------------------------------------------

def write_import_summary(stats, abbr_rows, out_path):
    n = stats["n"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = [r for r in abbr_rows if r["note"] == "partial_seasons"]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Kaggle Vegas Odds Import Preview\n\n")
        f.write("> **Diagnostic analysis only.**  \n")
        f.write("> No EV calculations. No trades. No paper entries. No candidate generation changes.\n\n")
        f.write("---\n\n")

        f.write("## Source Files\n\n")
        f.write("| File | Rows | Role |\n|---|---|---|\n")
        f.write("| `oddsDataMLB.csv` | 45,530 | Rich stats + odds (no home/away, no gameNumber) |\n")
        f.write("| `oddsData.csv`    | 45,530 | Home/away flag + gameNumber (no result stats) |\n\n")
        f.write("**Date range:** 2012-03-28 to 2021-10-03  \n")
        f.write("**Seasons:** 2012-2021 (10 seasons; 2020 shortened to ~898 games)  \n\n")
        f.write("---\n\n")

        f.write("## Normalized Output\n\n")
        f.write(f"- Total rows: **{n:,}**\n")
        f.write(f"- Win rate: {stats['wins']:,}/{n:,} = {100*stats['wins']/n:.1f}%\n")
        f.write(f"- Tied games (no outcome): {stats['ties']:,}\n")
        f.write(f"- Unique game_ids: {stats['unique_game_ids']:,} ")
        f.write(f"(expected ~{n//2:,} — each game produces 2 rows)\n\n")

        f.write("### Season Coverage\n\n")
        f.write("| Season | Rows |\n|---|---|\n")
        for s, cnt in stats["by_season"].items():
            f.write(f"| {s} | {cnt:,} |\n")
        f.write("\n---\n\n")

        f.write("## game_id Field\n\n")
        f.write("Format: `YYYY-MM-DD_AAA_BBB_N` where `AAA < BBB` lexicographically.\n\n")
        f.write("- Stable across both team rows for the same game.\n")
        f.write("- Doubleheader game 1 and game 2 produce distinct IDs via the `_N` suffix.\n")
        f.write("- For `ambiguous_position` DH rows, the `_1` / `_2` suffix may be swapped.\n")
        f.write("  See `dh_assignment_reliable` below.\n\n")
        f.write("---\n\n")

        f.write("## Merge Quality\n\n")
        f.write("| Method | Count | Description |\n|---|---|---|\n")
        for method, cnt in sorted(stats["merge_methods"].items(), key=lambda x: -x[1]):
            descs = {
                "direct":             "Single game for (date, team) — direct join",
                "moneyline":          "DH — disambiguated by moneyLine == line",
                "runline_total":      "DH — disambiguated by runLine + total combination",
                "ambiguous_position": "DH — all identifying fields identical; assigned by file order",
                "no_match":           "No matching row found in oddsData.csv",
            }
            f.write(f"| `{method}` | {cnt:,} | {descs.get(method, '')} |\n")
        f.write("\n")

        f.write("### dh_assignment_reliable\n\n")
        f.write("| Value | Meaning |\n|---|---|\n")
        f.write("| `1` | `is_home` and `game_number` are reliably assigned |\n")
        f.write("| `0` | `ambiguous_position` — `is_home` and `game_number` may be swapped |\n\n")
        if stats["dh_ambiguous"] > 0:
            f.write(f"> **{stats['dh_ambiguous']:,} rows have `dh_assignment_reliable = 0`.**  \n")
            f.write("> Exclude these rows from any analysis that depends on `is_home` or `game_number`  \n")
            f.write("> (e.g., home-field advantage studies, DH game-split analysis).  \n")
            f.write("> Run totals, moneylines, and result fields are correct regardless of ordering.\n\n")
        else:
            f.write("> All doubleheader rows were disambiguated reliably.\n\n")
        f.write("---\n\n")

        f.write("## Derived Field Summary\n\n")
        f.write("| Field | Count | Rate |\n|---|---|---|\n")
        f.write(f"| team_win           | {stats['wins']:,}   | {100*stats['wins']/n:.1f}% |\n")
        f.write(f"| team_runs_4plus    | {stats['r4plus']:,} | {100*stats['r4plus']/n:.1f}% |\n")
        f.write(f"| team_runs_5plus    | {stats['r5plus']:,} | {100*stats['r5plus']/n:.1f}% |\n")
        f.write(f"| team_runs_under_5  | {n - stats['r5plus']:,} | {100*(n-stats['r5plus'])/n:.1f}% |\n")
        f.write(f"| game_over          | {stats['overs']:,}  | {100*stats['overs']/n:.1f}% |\n")
        f.write(f"| game_under         | {stats['unders']:,} | {100*stats['unders']/n:.1f}% |\n")
        f.write(f"| total_push         | {stats['pushes']:,} | {100*stats['pushes']/n:.1f}% |\n\n")
        if stats["p_nv_mean"] is not None:
            f.write(f"No-vig win probability (mean across all rows): {stats['p_nv_mean']:.4f}  \n")
            f.write("(Expected ~0.5000 for a balanced two-row-per-game dataset.)\n\n")
        f.write("---\n\n")

        f.write("## Team Abbreviation Diagnostics\n\n")
        f.write(f"Unique abbreviations in `team` column: **{len(abbr_rows)}**  \n")
        f.write("Full list: `team_abbreviation_report.csv`\n\n")
        if partial:
            f.write("### Abbreviations with < 10 seasons (partial coverage flag)\n\n")
            f.write("These teams do not appear across all 10 seasons — ")
            f.write("likely expansions, contractions, or relocated franchises.\n\n")
            f.write("| Abbreviation | Seasons Active | First | Last | Rows |\n|---|---|---|---|---|\n")
            for r in partial:
                f.write(f"| {r['abbreviation']} | {r['seasons_active']} | {r['first_season']} | {r['last_season']} | {r['total_rows']:,} |\n")
            f.write("\n")
        else:
            f.write("All abbreviations appear in all 10 seasons — no partial-coverage flags.\n\n")
        f.write("---\n\n")

        f.write("## Data Quality Flags\n\n")
        f.write("### runLineOdds\n")
        f.write("- **2012 and 2013: 100% missing** (NA). Run line odds not collected for these seasons.\n")
        f.write("- 2014-2021: fully present.\n")
        f.write("- **Run line benchmarking should use 2014-2021 only.**\n\n")
        f.write("### runLine\n")
        f.write("- Some 2012 and 2013 rows missing (likely postponed/cancelled games).\n\n")
        f.write("### projectedRuns\n")
        f.write("- All 45,530 rows present. **Methodology is unconfirmed.**\n")
        f.write("- `projectedRuns` is NOT equal to `total/2` for 95.6% of rows.\n")
        f.write("- Mean absolute error vs actual runs: ~2.38 — consistent with a model projection.\n")
        f.write("- **Do not interpret as a true sportsbook team-total line without confirming the source.**\n\n")
        f.write("---\n\n")

        f.write("## What This Data Can Support\n\n")
        f.write("| Use Case | Coverage | Notes |\n|---|---|---|\n")
        f.write("| Winner / side benchmark | 2012-2021 | Actual outcomes + no-vig ML implied prob |\n")
        f.write("| Team scoring 4+/5+ benchmark | 2012-2021 | `runs` field; threshold columns pre-computed |\n")
        f.write("| Game total over/under benchmark | 2012-2021 | `totalRuns` + `total` line present |\n")
        f.write("| Run line benchmark | 2014-2021 only | `runLineOdds` NA for 2012-2013 |\n")
        f.write("| Moneyline implied probability | 2012-2021 | Both raw and no-vig calculable |\n")
        f.write("| Historical calibration baseline | 2012-2021 | Vegas lines as external benchmark |\n\n")

        f.write("## What This Data Cannot Support\n\n")
        f.write("| Limitation | Reason |\n|---|---|\n")
        f.write("| Kalshi bid/ask execution | This is sportsbook data, not Kalshi market data |\n")
        f.write("| Live EV | Historical final scores only; no in-game odds |\n")
        f.write("| F5 (first 5 innings) markets | Full-game lines only; no split-line data |\n")
        f.write("| True team-total sportsbook lines | `projectedRuns` methodology unconfirmed |\n")
        f.write("| 2022+ seasons | Data ends 2021-10-03 |\n")
        f.write("| Alternate lines or line movement | Single opening/closing line per game |\n\n")
        f.write("---\n\n")

        f.write("> **Diagnostic outputs only. No EV calculations. No trades. No paper entries. No model changes.**\n")

    print(f"  Wrote -> {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Kaggle Vegas Odds Import Preview")
    print(f"  MLB CSV:  {MLB_CSV}")
    print(f"  Odds CSV: {ODDS_CSV}")
    print()

    print("Loading CSVs...")
    mlb, odds = load_csvs()
    print(f"  oddsDataMLB.csv: {len(mlb):,} rows")
    print(f"  oddsData.csv:    {len(odds):,} rows")

    print("Building odds index...")
    odds_idx = build_odds_index(odds)

    print("Merging rows...")
    merged = merge_rows(mlb, odds_idx)
    method_counts = collections.Counter(m for _, _, m in merged)
    print(f"  Merge methods: {dict(method_counts)}")

    print("Normalizing rows...")
    normalized_rows = [normalize_row(r, o, m) for r, o, m in merged]

    print("Computing diagnostics...")
    dq_rows   = compute_data_quality(normalized_rows)
    abbr_rows = compute_team_abbr_diagnostics(normalized_rows)
    stats     = compute_summary_stats(normalized_rows)

    print("Writing outputs...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_normalized_preview(normalized_rows, OUT_DIR / "normalized_preview.csv")
    write_data_quality_report(dq_rows,        OUT_DIR / "data_quality_report.csv")
    write_team_abbr_report(abbr_rows,          OUT_DIR / "team_abbreviation_report.csv")
    write_import_summary(stats, abbr_rows,     OUT_DIR / "import_summary.md")

    print()
    print("=" * 55)
    print("SUMMARY")
    print("=" * 55)
    print(f"  Total rows:        {stats['n']:,}")
    print(f"  Unique game_ids:   {stats['unique_game_ids']:,}  (expect ~{stats['n']//2:,})")
    print(f"  Win rate:          {100*stats['wins']/stats['n']:.1f}%")
    print(f"  Team 4+ runs:      {100*stats['r4plus']/stats['n']:.1f}%")
    print(f"  Team 5+ runs:      {100*stats['r5plus']/stats['n']:.1f}%")
    print(f"  Game overs:        {100*stats['overs']/stats['n']:.1f}%")
    print(f"  Game unders:       {100*stats['unders']/stats['n']:.1f}%")
    print(f"  Pushes:            {stats['pushes']:,}")
    print(f"  DH ambiguous:      {stats['dh_ambiguous']:,}  (dh_assignment_reliable=0)")
    print(f"  No match:          {stats['no_match']:,}  (must be 0)")
    print(f"  Unique team abbrs: {len(abbr_rows)}")
    print()
    print("Outputs ->", OUT_DIR)
    print()
    print("DIAGNOSTIC ONLY. No EV. No trades. No model changes.")


if __name__ == "__main__":
    main()
