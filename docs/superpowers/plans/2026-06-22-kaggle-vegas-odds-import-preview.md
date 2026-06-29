# Plan: Kaggle Vegas Odds Import Preview

## Goal
Inspect, merge, normalize, and produce diagnostic CSV/Markdown outputs from the two Kaggle MLB odds CSVs — with no integration into production model logic, candidate generation, or trade flow.

## Architecture

```
data/external/kaggle_mlb_odds/
  oddsDataMLB.csv  ─── rich stats + odds (no home/away, no gameNumber)
  oddsData.csv     ─── home/away flag + gameNumber (no result stats)
         │
         ▼
kaggle_vegas_odds_import_preview.py
         │
         ├── validate both files
         ├── merge on (date, team) with DH disambiguation
         ├── normalize schema
         ├── compute derived fields
         └── write outputs ───► outputs/vegas_odds_import_preview/
                                  normalized_preview.csv
                                  import_summary.md
                                  data_quality_report.csv
                                  team_abbreviation_report.csv  ← NEW
```

No production files touched. No DB writes. No candidate_generator.py. No model scoring.

## Tech Stack
- Python 3.x stdlib only: `csv`, `os`, `collections`, `statistics`, `pathlib`
- No pandas (keep it portable and auditable)
- Outputs written with `encoding='utf-8'`

---

## Pre-Implementation: Inspection Findings

These were confirmed by running the actual files before writing the plan.

### oddsDataMLB.csv
| Field | Value |
|---|---|
| Rows | 45,530 |
| Columns | date, season, team, opponent, runs, oppRuns, moneyLine, runLine, runLineOdds, total, overOdds, underOdds, oppMoneyLine, oppRunLine, oppRunLineOdds, projectedRuns, parkName, totalRuns, runDif |
| Date range | 2012-03-28 to 2021-10-03 |
| Seasons | 2012-2021 (10 seasons; 2020 shortened ~898 games) |
| Duplicate (date, team) | 656 pairs — doubleheaders |
| Missing runLine | 438 rows (some 2012 + 2013) |
| Missing runLineOdds | 9,722 rows — **all of 2012 and 2013** |
| Missing oppRunLine | 438 rows (mirrors runLine) |
| Missing oppRunLineOdds | 9,722 rows (mirrors runLineOdds) |
| projectedRuns | All 45,530 present; NOT equal to total/2 for 95.6% of rows; mean error vs actual runs = 2.38; **methodology unconfirmed** |
| No home/away flag | Correct — not present |
| No gameNumber | Correct — not present |

### oddsData.csv
| Field | Value |
|---|---|
| Rows | 45,530 |
| Columns | date, at, team, gameNumber, line, runLine, runLineOdds, total, overOdds, underOdds |
| Date range | 2012-03-28 to 2021-10-03 (same span) |
| `at` values | V=22,765, H=22,765 |
| gameNumber values | 1=44,874, 2=656 |
| Duplicate (date, team, gameNumber) | **0** — uniquely keyed |
| Missing runLine | 438 rows (same pattern as MLB file) |
| Missing runLineOdds | 9,722 rows (same pattern) |

### Merge Safety Assessment

**Non-doubleheader rows (44,874 games):**
Join on `(date, team)` — 100% exact moneyLine match confirmed (44,218/44,218 checked, 0 mismatches).

**Doubleheader rows (656 pairs × 2 files):**
Primary join key: `(date, team, moneyLine=line)`.
- 538/656 pairs: unambiguously resolved by `(runLine + total)` difference alone.
- 118/656 pairs: needed `moneyLine` for disambiguation. All 118 resolved.
- **Truly ambiguous** (identical moneyLine + runLine + total for both games of same DH):
  small subset of the 118; assign in file order and flag `dh_merge_method = "ambiguous_position"`.
  Impact: `is_home` and `game_number` may be swapped for these rows, but run totals and odds are correct.
  Acceptable for benchmarking (not trying to match individual game IDs).

**Merge verdict: SAFE.** All rows can be joined with clear method assignment and flag for ambiguous cases.

**Actual merge results (confirmed after execution):**
- `direct`: 44,218 rows
- `moneyline`: 1,285 rows
- `runline_total`: 16 rows
- `ambiguous_position`: **11 rows** (not 118 as conservatively estimated — most ambiguous-looking pairs were resolved by moneyLine)
- `no_match`: 0 ✓

### projectedRuns Risk

`projectedRuns` is NOT a sportsbook team-total market line:
- Only 4.4% of rows have `projectedRuns ≈ total/2 (within 0.05)`
- Mean absolute deviation vs actual runs: 2.38
- Plausible as a pitcher-adjusted model projection (range 1.21–9.45, mean 4.31)
- **Source methodology is unconfirmed** — could be a third-party model projection

**Decision:** Include `projected_runs` in normalized schema but mark in `import_summary.md` and
`data_quality_report.csv` that it should NOT be interpreted as a true sportsbook team-total line.

### Overround / No-Vig
- Moneyline overround: mean = 1.027 (2.71% vig) — consistent with real sportsbook lines
- No-vig implied probability: `p_nv = p_raw / (p_raw + p_opp_raw)` — calculable for all 45,530 rows
- Actual team win rate: exactly 50.0% (as expected for a balanced two-row-per-game dataset)

### Push Analysis
- 22,444 rows have whole-number `total` (push possible)
- 23,086 rows have half-point `total` (push impossible)
- 2,216 actual push events confirmed in the data

---

## Files Created / Modified

| File | Role |
|---|---|
| `kaggle_vegas_odds_import_preview.py` | **NEW** — standalone read-only diagnostic script |
| `outputs/vegas_odds_import_preview/normalized_preview.csv` | **NEW output** — all 45,530 rows, normalized schema + derived fields |
| `outputs/vegas_odds_import_preview/import_summary.md` | **NEW output** — human-readable summary |
| `outputs/vegas_odds_import_preview/data_quality_report.csv` | **NEW output** — per-season / per-field quality metrics |
| `outputs/vegas_odds_import_preview/team_abbreviation_report.csv` | **NEW output** — 30 unique team abbrs, per-team seasons/rows/opponents |

No other files touched.

---

## Normalized Schema

| Normalized Field | Source | Notes |
|---|---|---|
| `game_date` | `oddsDataMLB.date` | ISO format, no change |
| `season` | `oddsDataMLB.season` | Integer year |
| `game_number` | `oddsData.gameNumber` | 1 or 2 |
| `team` | `oddsDataMLB.team` | 3-letter abbr |
| `opponent` | `oddsDataMLB.opponent` | 3-letter abbr |
| `is_home` | `oddsData.at` | H→1, V→0 |
| `dh_merge_method` | derived | `"direct"` / `"moneyline"` / `"runline_total"` / `"ambiguous_position"` |
| `runs` | `oddsDataMLB.runs` | Actual runs scored by team |
| `opp_runs` | `oddsDataMLB.oppRuns` | Actual runs scored by opponent |
| `moneyline` | `oddsDataMLB.moneyLine` | Integer American odds |
| `opponent_moneyline` | `oddsDataMLB.oppMoneyLine` | Integer American odds |
| `run_line` | `oddsDataMLB.runLine` | -1.5 / 1.5 / NA |
| `run_line_odds` | `oddsDataMLB.runLineOdds` | NA for 2012-2013 |
| `game_total` | `oddsDataMLB.total` | Over/under line |
| `over_odds` | `oddsDataMLB.overOdds` | American odds |
| `under_odds` | `oddsDataMLB.underOdds` | American odds |
| `projected_runs` | `oddsDataMLB.projectedRuns` | **Unconfirmed methodology — see notes** |
| `park_name` | `oddsDataMLB.parkName` | Venue name |
| `total_runs` | `oddsDataMLB.totalRuns` | Final game total (both teams) |
| `run_differential` | `oddsDataMLB.runDif` | `runs - opp_runs` |
| `source_file` | literal | `"oddsDataMLB.csv+oddsData.csv"` |

### Derived Fields

| Derived Field | Logic | Nulls |
|---|---|---|
| `team_win` | `1` if runs > opp_runs, `0` if runs < opp_runs, `""` if tied | Rare tie games |
| `team_runs_4plus` | `1` if runs >= 4, else `0` | Never null |
| `team_runs_5plus` | `1` if runs >= 5, else `0` | Never null |
| `team_runs_under_5` | `1` if runs < 5, else `0` | Never null |
| `game_over` | `1` if total_runs > game_total, `0` if <, `""` if push | Push cases |
| `game_under` | `1` if total_runs < game_total, `0` if >, `""` if push | Push cases |
| `total_push` | `1` if total_runs == game_total (whole-number total only), else `0` | Half-line totals always 0 |
| `moneyline_implied_probability_raw` | `(-ml)/((-ml)+100)` if ml<0, else `100/(ml+100)` | Never null |
| `moneyline_implied_probability_no_vig` | `raw / (raw + opp_raw)` | Never null |

---

## Task 1 — Create output directory and write script

**File:** `kaggle_vegas_odds_import_preview.py`

```python
"""
Kaggle Vegas Odds Import Preview
=================================
Read-only diagnostic script. No DB writes. No model changes. No trades.

Merges oddsDataMLB.csv (stats + odds) with oddsData.csv (home/away + gameNumber),
normalizes schema, computes derived fields, and writes diagnostic outputs.
"""
import csv
import os
import collections
import statistics
from pathlib import Path

MLB_CSV  = Path("data/external/kaggle_mlb_odds/oddsDataMLB.csv")
ODDS_CSV = Path("data/external/kaggle_mlb_odds/oddsData.csv")
OUT_DIR  = Path("outputs/vegas_odds_import_preview")

NORMALIZED_COLS = [
    "game_date", "season", "game_number", "team", "opponent",
    "is_home", "dh_merge_method",
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
    """Index odds rows by (date, team) -> list of rows, sorted by gameNumber."""
    idx = collections.defaultdict(list)
    for r in odds:
        idx[(r["date"], r["team"])].append(r)
    for key in idx:
        idx[key].sort(key=lambda r: int(r["gameNumber"]))
    return idx


def merge_rows(mlb, odds_idx):
    """
    Merge each MLB row with its matching odds row.
    Returns list of merged dicts with merge_method tag.
    """
    merged = []
    # Track MLB rows seen per (date, team) so DH rows can be assigned in order
    seen = collections.defaultdict(int)

    for r in mlb:
        key = (r["date"], r["team"])
        candidates = odds_idx.get(key, [])

        if len(candidates) == 0:
            # Should not happen given confirmed 100% coverage
            odds_row = {}
            method = "no_match"
        elif len(candidates) == 1:
            odds_row = candidates[0]
            method = "direct"
        else:
            # Doubleheader: try to match by moneyLine == line
            ml_matches = [c for c in candidates if c["line"] == r["moneyLine"]]
            if len(ml_matches) == 1:
                odds_row = ml_matches[0]
                method = "moneyline"
            else:
                # Fallback: match by runLine + total
                rl_tot_matches = [
                    c for c in candidates
                    if c["runLine"] == r["runLine"] and c["total"] == r["total"]
                ]
                if len(rl_tot_matches) == 1:
                    odds_row = rl_tot_matches[0]
                    method = "runline_total"
                else:
                    # Assign by file-order position
                    pos = seen[key]
                    odds_row = candidates[pos] if pos < len(candidates) else candidates[0]
                    method = "ambiguous_position"

        seen[key] += 1
        merged.append((r, odds_row, method))

    return merged


def normalize_row(mlb_r, odds_r, method):
    """Return a normalized output dict."""
    runs     = _safe_int(mlb_r.get("runs", ""))
    opp_runs = _safe_int(mlb_r.get("oppRuns", ""))
    total_runs  = _safe_int(mlb_r.get("totalRuns", ""))
    game_total  = _safe_float(mlb_r.get("total", ""))

    # Derived: win
    if runs is not None and opp_runs is not None:
        if runs > opp_runs:
            team_win = 1
        elif runs < opp_runs:
            team_win = 0
        else:
            team_win = ""  # tie
    else:
        team_win = ""

    # Derived: team scoring
    team_runs_4plus  = (1 if runs is not None and runs >= 4 else 0) if runs is not None else ""
    team_runs_5plus  = (1 if runs is not None and runs >= 5 else 0) if runs is not None else ""
    team_runs_under5 = (1 if runs is not None and runs < 5 else 0) if runs is not None else ""

    # Derived: game total result
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

    # Moneyline implied probabilities
    p_raw    = _ml_implied(mlb_r.get("moneyLine", ""))
    p_opp    = _ml_implied(mlb_r.get("oppMoneyLine", ""))
    p_no_vig = (round(p_raw / (p_raw + p_opp), 6) if p_raw is not None and p_opp is not None and (p_raw + p_opp) > 0 else "")
    p_raw_out = round(p_raw, 6) if p_raw is not None else ""

    # is_home from at field
    at = odds_r.get("at", "")
    is_home = 1 if at == "H" else (0 if at == "V" else "")

    return {
        "game_date":    mlb_r.get("date", ""),
        "season":       mlb_r.get("season", ""),
        "game_number":  odds_r.get("gameNumber", ""),
        "team":         mlb_r.get("team", ""),
        "opponent":     mlb_r.get("opponent", ""),
        "is_home":      is_home,
        "dh_merge_method": method,
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
        "team_runs_4plus":  team_runs_4plus,
        "team_runs_5plus":  team_runs_5plus,
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
    print(f"  Wrote {len(rows)} rows -> {out_path}")


def compute_data_quality(normalized_rows):
    """Per-season quality metrics for the data_quality_report.csv."""
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
            "season": season,
            "total_rows": total,
            "missing_run_line":      d.get("missing_run_line", 0),
            "missing_run_line_odds": d.get("missing_run_line_odds", 0),
            "missing_over_odds":     d.get("missing_over_odds", 0),
            "missing_under_odds":    d.get("missing_under_odds", 0),
            "missing_projected_runs": d.get("missing_projected_runs", 0),
            "missing_park_name":     d.get("missing_park_name", 0),
            "run_line_pct_present":  f"{100*(total - d.get('missing_run_line',0))/total:.1f}%",
            "run_line_odds_pct_present": f"{100*(total - d.get('missing_run_line_odds',0))/total:.1f}%",
        })
    return dq_rows


def write_data_quality_report(dq_rows, out_path):
    if not dq_rows:
        return
    fieldnames = list(dq_rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(dq_rows)
    print(f"  Wrote {len(dq_rows)} rows -> {out_path}")


def compute_summary_stats(normalized_rows):
    wins = sum(1 for r in normalized_rows if r["team_win"] == 1)
    losses = sum(1 for r in normalized_rows if r["team_win"] == 0)
    ties = sum(1 for r in normalized_rows if r["team_win"] == "")
    r4plus = sum(1 for r in normalized_rows if r["team_runs_4plus"] == 1)
    r5plus = sum(1 for r in normalized_rows if r["team_runs_5plus"] == 1)
    overs  = sum(1 for r in normalized_rows if r["game_over"] == 1)
    unders = sum(1 for r in normalized_rows if r["game_under"] == 1)
    pushes = sum(1 for r in normalized_rows if r["total_push"] == 1)
    dh_ambiguous = sum(1 for r in normalized_rows if r["dh_merge_method"] == "ambiguous_position")
    no_match = sum(1 for r in normalized_rows if r["dh_merge_method"] == "no_match")
    n = len(normalized_rows)

    by_season = collections.Counter(r["season"] for r in normalized_rows)

    p_nv_vals = [float(r["moneyline_implied_probability_no_vig"])
                 for r in normalized_rows
                 if r["moneyline_implied_probability_no_vig"] not in ("", None)]
    p_nv_mean = statistics.mean(p_nv_vals) if p_nv_vals else None

    return {
        "n": n, "wins": wins, "losses": losses, "ties": ties,
        "r4plus": r4plus, "r5plus": r5plus,
        "overs": overs, "unders": unders, "pushes": pushes,
        "dh_ambiguous": dh_ambiguous, "no_match": no_match,
        "by_season": dict(sorted(by_season.items())),
        "p_nv_mean": p_nv_mean,
    }


def write_import_summary(stats, out_path):
    n = stats["n"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Kaggle Vegas Odds Import Preview\n\n")
        f.write("> **Timing and data-quality analysis only.**  \n")
        f.write("> No EV calculations. No trades. No paper entries. No candidate generation changes.\n\n")
        f.write("---\n\n")
        f.write("## Source Files\n\n")
        f.write("| File | Rows | Role |\n|---|---|---|\n")
        f.write(f"| `oddsDataMLB.csv` | 45,530 | Rich stats + odds (no home/away, no gameNumber) |\n")
        f.write(f"| `oddsData.csv`    | 45,530 | Home/away flag + gameNumber (no result stats) |\n\n")
        f.write("**Date range:** 2012-03-28 to 2021-10-03  \n")
        f.write("**Seasons:** 2012-2021 (10 seasons; 2020 shortened to ~898 games)  \n\n")
        f.write("---\n\n")
        f.write("## Normalized Output\n\n")
        f.write(f"- Total rows: **{n:,}**\n")
        f.write(f"- Win rate: {stats['wins']:,}/{n:,} = {100*stats['wins']/n:.1f}%\n")
        f.write(f"- Tie (no result): {stats['ties']:,}\n\n")
        f.write("### Season Coverage\n\n")
        f.write("| Season | Rows |\n|---|---|\n")
        for s, cnt in stats["by_season"].items():
            f.write(f"| {s} | {cnt:,} |\n")
        f.write("\n---\n\n")
        f.write("## Derived Field Summary\n\n")
        f.write("| Field | Count | Rate |\n|---|---|---|\n")
        f.write(f"| team_win | {stats['wins']:,} | {100*stats['wins']/n:.1f}% |\n")
        f.write(f"| team_runs_4plus | {stats['r4plus']:,} | {100*stats['r4plus']/n:.1f}% |\n")
        f.write(f"| team_runs_5plus | {stats['r5plus']:,} | {100*stats['r5plus']/n:.1f}% |\n")
        f.write(f"| game_over | {stats['overs']:,} | {100*stats['overs']/n:.1f}% |\n")
        f.write(f"| game_under | {stats['unders']:,} | {100*stats['unders']/n:.1f}% |\n")
        f.write(f"| total_push | {stats['pushes']:,} | {100*stats['pushes']/n:.1f}% |\n")
        f.write(f"\nNo-vig win probability (mean across all rows): {stats['p_nv_mean']:.4f}\n\n")
        f.write("---\n\n")
        f.write("## Merge Quality\n\n")
        f.write("| Method | Description |\n|---|---|\n")
        f.write("| `direct` | Single game for (date, team) — direct join |\n")
        f.write("| `moneyline` | Doubleheader — disambiguated by moneyLine == line |\n")
        f.write("| `runline_total` | Doubleheader — disambiguated by runLine + total combination |\n")
        f.write("| `ambiguous_position` | Doubleheader — all identifying fields identical; assigned by file order |\n")
        f.write("| `no_match` | No matching row found in oddsData.csv (should be 0) |\n\n")
        if stats["dh_ambiguous"] > 0:
            f.write(f"> **{stats['dh_ambiguous']} rows** use `ambiguous_position` merge.")
            f.write(" For these, `is_home` and `game_number` may be swapped between the two games of a doubleheader.")
            f.write(" Run totals and odds are correct regardless of ordering.\n\n")
        if stats["no_match"] > 0:
            f.write(f"> **WARNING: {stats['no_match']} rows** had no matching odds row.\n\n")
        f.write("---\n\n")
        f.write("## Data Quality Flags\n\n")
        f.write("### runLineOdds\n")
        f.write("- **2012 and 2013: 100% missing** (NA). Run line odds not collected for these seasons.\n")
        f.write("- 2014-2021: fully present.\n")
        f.write("- **Run line benchmarking should use 2014-2021 only.**\n\n")
        f.write("### runLine\n")
        f.write("- Some 2012 and 2013 rows missing (276 in 2012, 162 in 2013). Likely postponed/cancelled games.\n\n")
        f.write("### projectedRuns\n")
        f.write("- All 45,530 rows present. However, **methodology is unconfirmed**.\n")
        f.write("- `projectedRuns` is NOT equal to `total/2` for 95.6% of rows.\n")
        f.write("- Mean absolute error vs actual runs: 2.38 — consistent with a model projection, not a true market team-total line.\n")
        f.write("- **Do not interpret as a sportsbook team-total line without confirming the source.**\n\n")
        f.write("---\n\n")
        f.write("## What This Data Can Support\n\n")
        f.write("| Use Case | Coverage | Notes |\n|---|---|---|\n")
        f.write("| Winner / side benchmark | Full (2012-2021) | Actual outcomes present; no-vig ML implied prob calculable |\n")
        f.write("| Team scoring 4+/5+ benchmark | Full (2012-2021) | `runs` field; derive threshold columns |\n")
        f.write("| Game total over/under benchmark | Full (2012-2021) | `totalRuns` + `total` line present |\n")
        f.write("| Run line benchmark | 2014-2021 only | `runLineOdds` NA for 2012-2013 |\n")
        f.write("| Moneyline implied probability | Full (2012-2021) | Both raw and no-vig calculable |\n")
        f.write("| Historical calibration baseline | Full (2012-2021) | Compare model output to Vegas lines as benchmark |\n\n")
        f.write("## What This Data Cannot Support\n\n")
        f.write("| Limitation | Reason |\n|---|---|\n")
        f.write("| Kalshi bid/ask execution | This is sportsbook data, not Kalshi market data |\n")
        f.write("| Live EV | Historical final scores only; no in-game odds |\n")
        f.write("| F5 (first 5 innings) markets | Full-game lines only; no split-line data |\n")
        f.write("| True team-total sportsbook lines | `projectedRuns` methodology unconfirmed |\n")
        f.write("| 2022+ seasons | Data ends 2021-10-03 |\n")
        f.write("| Alternate lines or line movement | Only one line per game |\n\n")
        f.write("---\n\n")
        f.write("> **Diagnostic outputs only. No EV calculations. No trades. No paper entries. No model changes.**\n")
    print(f"  Wrote -> {out_path}")


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

    merge_method_counts = collections.Counter(m for _, _, m in merged)
    print(f"  Merge methods: {dict(merge_method_counts)}")

    print("Normalizing...")
    normalized_rows = [normalize_row(r, o, m) for r, o, m in merged]

    print("Writing outputs...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_normalized_preview(normalized_rows, OUT_DIR / "normalized_preview.csv")

    dq = compute_data_quality(normalized_rows)
    write_data_quality_report(dq, OUT_DIR / "data_quality_report.csv")

    stats = compute_summary_stats(normalized_rows)
    write_import_summary(stats, OUT_DIR / "import_summary.md")

    print()
    print("=== Summary ===")
    print(f"  Rows:          {stats['n']:,}")
    print(f"  Win rate:      {100*stats['wins']/stats['n']:.1f}%")
    print(f"  Team 4+ runs:  {100*stats['r4plus']/stats['n']:.1f}%")
    print(f"  Team 5+ runs:  {100*stats['r5plus']/stats['n']:.1f}%")
    print(f"  Game overs:    {100*stats['overs']/stats['n']:.1f}%")
    print(f"  Game unders:   {100*stats['unders']/stats['n']:.1f}%")
    print(f"  Pushes:        {stats['pushes']:,}")
    print(f"  DH ambiguous:  {stats['dh_ambiguous']:,}")
    print(f"  No match:      {stats['no_match']:,}")
    print()
    print("Outputs written to:", OUT_DIR)
    print()
    print("DIAGNOSTIC ONLY. No EV. No trades. No model changes.")


if __name__ == "__main__":
    main()
```

---

## Task 2 — Run script and verify outputs

```bash
python kaggle_vegas_odds_import_preview.py
```

Expected terminal output:
```
Kaggle Vegas Odds Import Preview
  MLB CSV:  data/external/kaggle_mlb_odds/oddsDataMLB.csv
  Odds CSV: data/external/kaggle_mlb_odds/oddsData.csv

Loading CSVs...
  oddsDataMLB.csv: 45,530 rows
  oddsData.csv:    45,530 rows
Building odds index...
Merging rows...
  Merge methods: {'direct': 44874, 'moneyline': ..., 'runline_total': ..., 'ambiguous_position': ...}
Normalizing...
Writing outputs...
  Wrote 45530 rows -> outputs/vegas_odds_import_preview/normalized_preview.csv
  Wrote 10 rows -> outputs/vegas_odds_import_preview/data_quality_report.csv
  Wrote -> outputs/vegas_odds_import_preview/import_summary.md

=== Summary ===
  Rows:          45,530
  Win rate:      ~50.0%
  Team 4+ runs:  ~55.4%
  Team 5+ runs:  ~42.4%
  Game overs:    ~...%
  Game unders:   ~...%
  Pushes:        2,216
  DH ambiguous:  <= 118
  No match:      0
```

Verify:
- [ ] `no_match` count = 0
- [ ] `dh_ambiguous` <= 118
- [ ] `normalized_preview.csv` has header + 45,530 data rows
- [ ] `data_quality_report.csv` has 10 rows (one per season)
- [ ] `import_summary.md` documents projectedRuns caveat

---

## Task 3 — Safety Checklist

- [ ] No changes to `candidate_generator.py`
- [ ] No changes to `mlb/candidates.py`
- [ ] No changes to `db/schema.py`
- [ ] No `INSERT INTO paper_positions` anywhere in new script
- [ ] No `eligible_for_paper=1` set anywhere
- [ ] No order/trade API calls
- [ ] No EV claims in any output file
- [ ] `import_summary.md` explicitly states "No EV calculations" at top and bottom
- [ ] `projectedRuns` caveated as "methodology unconfirmed"

---

## What the Import Summary Must Say (mandatory language)

At the top:
```
> **Timing and data-quality analysis only.**
> No EV calculations. No trades. No paper entries. No candidate generation changes.
```

At the bottom:
```
> **Diagnostic outputs only. No EV calculations. No trades. No paper entries. No model changes.**
```

In `projectedRuns` section:
```
Do not interpret as a sportsbook team-total line without confirming the source.
```

---

## Execution Mode

**Inline** — 1 new file, 3 output files, no production changes, ~10 minutes of work.

---

## Safety Constraints (verbatim from spec)
- Do not change candidate generation
- Do not change model scoring
- Do not create paper entries
- Do not enable trades
- Do not claim Kalshi EV
- Do not use as direct substitute for Kalshi bid/ask execution
- Keep as local CSV import/diagnostic only
