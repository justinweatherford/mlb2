"""
pregame_daily_learning_report.py

Daily learning report for the pregame brain.

Sections:
  1. Today's card outcomes - game-by-game results
  2. Lane performance - today vs historical baseline
  3. 2026 cumulative - running totals by lane and bin
  4. Calibration movers - bins that shifted since last snapshot
  5. Sample size tracker - how close each bin is to usable

Usage:
    python pregame_daily_learning_report.py
    python pregame_daily_learning_report.py --date 2026-06-22
    python pregame_daily_learning_report.py --date 2026-06-22 --out report.md
"""
import argparse
import csv
import math
from datetime import date, timedelta
from pathlib import Path

CARD_CSV          = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
CALIB_CSV         = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")
SNAP_CSV          = Path("outputs/pregame_probability_calibration/calibration_bins_snapshot.csv")
NEAR_MISS_CSV     = Path("outputs/kalshi_ev_overlay_preview/latest_moneyline_core_near_misses.csv")
NEAR_MISS_HISTORY = Path("outputs/pregame_daily_learning_report/moneyline_near_miss_history.csv")
OUT_DIR           = Path("outputs/pregame_daily_learning_report")

# Historical reference rates for near-miss comparison (from audit, observe only)
_ML_CORE_HOME_ALL_HIST   = 0.634   # HOME + side>=0.40, all (2023-2025)
_ML_CORE_AWAY_ALL_HIST   = 0.566   # AWAY + side>=0.40, all (degraded 2025)
_BASELINE_HOME           = 0.531
_BASELINE_AWAY           = 0.468

# Bucket -> (label, reference_rate, reference_label) for aggregate display
_BUCKET_REF = {
    "below_threshold_home_0.30_to_0.40": (_BASELINE_HOME, "home baseline"),
    "away_score_0.40_plus":              (_ML_CORE_AWAY_ALL_HIST, "away+>=0.40 hist"),
    "weak_leader_suppressed":            (_ML_CORE_HOME_ALL_HIST, "home+>=0.40 hist"),
    "live_rebound_suppressed":           (_ML_CORE_HOME_ALL_HIST, "home+>=0.40 hist"),
    "market_failed_only":                (_ML_CORE_HOME_ALL_HIST, "home+>=0.40 hist"),
    "multiple_failures":                 (_BASELINE_HOME,         "home baseline"),
}

LANE_CONFIGS = [
    {"lane": "side",               "score_col": "side_score",               "actual_col": "actual_team_won",           "hit_value": 1, "threshold": 0.20, "baseline_key": "actual_team_won_1"},
    {"lane": "side_fade",          "score_col": "side_fade_score",          "actual_col": "actual_team_won",           "hit_value": 0, "threshold": 0.20, "baseline_key": "actual_team_won_0"},
    {"lane": "team_runs_4plus",    "score_col": "team_runs_4plus_score",    "actual_col": "actual_team_runs_4plus",    "hit_value": 1, "threshold": 0.15, "baseline_key": "actual_team_runs_4plus_1"},
    {"lane": "team_runs_5plus_no", "score_col": "team_runs_5plus_no_score", "actual_col": "actual_team_runs_5plus",    "hit_value": 0, "threshold": 0.20, "baseline_key": "actual_team_runs_5plus_0"},
    {"lane": "team_f5_runs_2plus", "score_col": "team_f5_runs_2plus_score", "actual_col": "actual_team_f5_runs_2plus", "hit_value": 1, "threshold": 0.20, "baseline_key": "actual_team_f5_runs_2plus_1"},
]

SCORE_BINS = [
    ("<0.00",     -math.inf, 0.00),
    ("0.00-0.10",  0.00,     0.10),
    ("0.10-0.20",  0.10,     0.20),
    ("0.20-0.30",  0.20,     0.30),
    ("0.30-0.40",  0.30,     0.40),
    ("0.40+",      0.40,     math.inf),
]

HISTORICAL_SEASONS = {"2023", "2024", "2025"}

# Thresholds for sample-size tracker
USABLE_N = 30    # low confidence floor
MEDIUM_N = 100

BAR_WIDTH = 20


# -- Helpers -------------------------------------------------------------------

def _f(v) -> float | None:
    try:
        s = str(v).strip()
        return None if not s or s.lower() in {"", "nan", "none"} else float(s)
    except Exception:
        return None


def _i(v) -> int | None:
    f = _f(v)
    return None if f is None else int(round(f))


def _bin(score: float) -> str:
    for label, lo, hi in SCORE_BINS:
        if lo <= score < hi:
            return label
    return SCORE_BINS[-1][0]


def _pct(n, d) -> str:
    if d == 0:
        return "  -  "
    return f"{n/d*100:5.1f}%"


def _bar(n: int, target: int, width: int = BAR_WIDTH) -> str:
    filled = min(width, round(width * n / target)) if target > 0 else 0
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _delta(rate, baseline) -> str:
    if rate is None or baseline is None:
        return "   -  "
    d = (rate - baseline) * 100
    return f"{d:+5.1f}%"


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_calib(path: Path) -> dict[tuple[str, str], dict]:
    rows = load_csv(path)
    return {(r["lane"], r["score_bin"]): r for r in rows}


# -- Baselines from historical data --------------------------------------------

def compute_baselines(all_rows: list[dict]) -> dict[str, float]:
    """Compute baseline hit rate for each lane from historical seasons."""
    hist = [r for r in all_rows if r.get("season") in HISTORICAL_SEASONS]
    out = {}
    for lc in LANE_CONFIGS:
        col, hv = lc["actual_col"], lc["hit_value"]
        valid = [r for r in hist if r.get(col) not in ("", "None", "nan")]
        if valid:
            out[lc["lane"]] = sum(1 for r in valid if _i(r[col]) == hv) / len(valid)
        else:
            out[lc["lane"]] = None
    return out


# -- Report sections -----------------------------------------------------------

def section_game_log(lines: list[str], date_rows: list[dict], report_date: str) -> None:
    lines.append(f"TODAY'S GAME LOG - {report_date}")
    lines.append("-" * 70)

    if not date_rows:
        lines.append("  No card rows found for this date.")
        lines.append("")
        return

    # Group by game, then team
    games: dict[str, list[dict]] = {}
    for r in date_rows:
        gid = r.get("game_id", "?")
        games.setdefault(gid, []).append(r)

    header = f"  {'Game':<12} {'Team':<5} {'HA':<5} {'Side':>5} {'4+':>5} {'5no':>5} {'F5':>5}  {'W/L':<4}  {'Runs':>4}  {'Opp':>4}  {'Tot':>4}"
    lines.append(header)
    lines.append("  " + "-" * 66)

    for gid in sorted(games):
        for r in sorted(games[gid], key=lambda x: x.get("home_away", "")):
            team   = r.get("team", "?")
            ha     = r.get("home_away", "?")[:4]
            won    = _i(r.get("actual_team_won"))
            runs   = _i(r.get("actual_team_runs"))   or _i(r.get("actual_team_runs_from_db"))
            opp    = _i(r.get("actual_opponent_runs"))
            total  = _i(r.get("actual_game_total"))
            wl     = ("WIN " if won == 1 else "loss") if won is not None else "pend"

            # Pull inferred runs from actuals if new field not present
            # Fall back: look at actual_team_runs_4plus to infer ≥4
            s_side = _f(r.get("side_score"))
            s_4p   = _f(r.get("team_runs_4plus_score"))
            s_5no  = _f(r.get("team_runs_5plus_no_score"))
            s_f5   = _f(r.get("team_f5_runs_2plus_score"))

            lines.append(
                f"  {gid:<12} {team:<5} {ha:<5} "
                f"{s_side:>5.2f} {s_4p:>5.2f} {s_5no:>5.2f} {s_f5:>5.2f}  "
                f"{wl:<4}  "
                f"{runs if runs is not None else '-':>4}  "
                f"{opp if opp is not None else '-':>4}  "
                f"{total if total is not None else '-':>4}"
            )
    lines.append("")


def section_today_lanes(
    lines: list[str],
    date_rows: list[dict],
    baselines: dict[str, float],
    report_date: str,
) -> None:
    lines.append(f"LANE PERFORMANCE - {report_date}")
    lines.append("-" * 70)

    if not date_rows:
        lines.append("  No data.")
        lines.append("")
        return

    lines.append(f"  {'Lane':<22} {'Above thresh':>13}  {'Hit rate':>9}  {'Baseline':>9}  {'Delta':>7}")
    lines.append("  " + "-" * 65)

    for lc in LANE_CONFIGS:
        sc, col, hv, thresh = lc["score_col"], lc["actual_col"], lc["hit_value"], lc["threshold"]
        above = [r for r in date_rows if _f(r.get(sc)) is not None and (_f(r.get(sc)) or 0) >= thresh
                 and r.get(col) not in ("", "None", "nan")]
        all_valid = [r for r in date_rows if r.get(col) not in ("", "None", "nan")]
        hits_above = sum(1 for r in above if _i(r[col]) == hv)
        hits_all   = sum(1 for r in all_valid if _i(r[col]) == hv)
        baseline   = baselines.get(lc["lane"])

        rate_above = hits_above / len(above) if above else None
        rate_all   = hits_all / len(all_valid) if all_valid else None

        above_str  = f"{hits_above}/{len(above)}" if above else "none"
        rate_str   = _pct(hits_above, len(above)) if above else "  -  "
        base_str   = f"{baseline*100:5.1f}%" if baseline is not None else "  -  "
        delta_str  = _delta(rate_above, baseline)
        note       = " *" if above and len(above) < 5 else ""

        lines.append(
            f"  {lc['lane']:<22} {above_str:>13}  {rate_str}  {base_str}  {delta_str}{note}"
        )

    lines.append("  * small sample (<5 cards)")
    lines.append("")


def section_cumulative_2026(
    lines: list[str],
    rows_2026: list[dict],
    calib: dict[tuple[str, str], dict],
) -> None:
    n_total = len(rows_2026)
    lines.append(f"2026 CUMULATIVE PERFORMANCE ({n_total} rows with actuals)")
    lines.append("-" * 70)

    if not rows_2026:
        lines.append("  No 2026 actuals yet.")
        lines.append("")
        return

    lines.append(f"  {'Lane':<22} {'Bin':<14} {'n':>5}  {'2026 Rate':>10}  {'Hist Prob':>10}  {'Delta':>7}  Note")
    lines.append("  " + "-" * 72)

    for lc in LANE_CONFIGS:
        sc, col, hv = lc["score_col"], lc["actual_col"], lc["hit_value"]
        printed_lane = False

        for bin_label, lo, hi in SCORE_BINS:
            if bin_label == "<0.00":
                continue
            bin_rows = [
                r for r in rows_2026
                if _f(r.get(sc)) is not None
                and lo <= (_f(r.get(sc)) or 0) < hi
                and r.get(col) not in ("", "None", "nan")
            ]
            if not bin_rows:
                continue

            n = len(bin_rows)
            hits = sum(1 for r in bin_rows if _i(r[col]) == hv)
            rate = hits / n
            calib_row = calib.get((lc["lane"], bin_label))
            hist_prob = _f(calib_row.get("conservative_probability")) if calib_row else None
            delta_str = _delta(rate, hist_prob)
            rate_str  = _pct(hits, n)
            hist_str  = f"{hist_prob*100:5.1f}%" if hist_prob is not None else "  -  "

            note = ""
            if n < USABLE_N:
                note = f"need {USABLE_N - n} more"
            elif n < MEDIUM_N:
                note = "low conf"
            else:
                note = "medium conf"

            lane_str = lc["lane"] if not printed_lane else ""
            printed_lane = True
            lines.append(
                f"  {lane_str:<22} {bin_label:<14} {n:>5}  {rate_str}  {hist_str}  {delta_str}  {note}"
            )

    lines.append("")


def section_calibration_movers(
    lines: list[str],
    calib: dict[tuple[str, str], dict],
    snap: dict[tuple[str, str], dict],
    threshold: float = 0.005,
) -> None:
    lines.append("CALIBRATION MOVERS (vs previous snapshot)")
    lines.append("-" * 70)

    if not snap:
        lines.append("  No previous snapshot found.")
        lines.append("  Snapshot will be saved after this run for future comparisons.")
        lines.append("")
        return

    movers = []
    for key, row in calib.items():
        old = snap.get(key)
        if not old:
            continue
        new_p = _f(row.get("conservative_probability"))
        old_p = _f(old.get("conservative_probability"))
        if new_p is None or old_p is None:
            continue
        delta = new_p - old_p
        if abs(delta) >= threshold:
            movers.append((abs(delta), key[0], key[1], old_p, new_p, delta,
                           int(row.get("sample_size") or 0)))

    if not movers:
        lines.append(f"  No bin moved more than {threshold*100:.1f}% since last snapshot.")
        lines.append("")
        return

    movers.sort(reverse=True)
    lines.append(f"  {'Lane':<22} {'Bin':<14} {'Old':>7}  {'New':>7}  {'Delta':>7}  {'n':>6}")
    lines.append("  " + "-" * 62)
    for _, lane, bin_label, old_p, new_p, delta, n in movers:
        lines.append(
            f"  {lane:<22} {bin_label:<14} "
            f"{old_p*100:6.2f}%  {new_p*100:6.2f}%  {delta*100:+6.2f}%  {n:>6}"
        )
    lines.append("")


def section_sample_tracker(
    lines: list[str],
    rows_2026: list[dict],
) -> None:
    lines.append(f"SAMPLE SIZE TRACKER - target {USABLE_N} per bin for usable calibration")
    lines.append("-" * 70)

    if not rows_2026:
        lines.append("  No 2026 data yet.")
        lines.append("")
        return

    any_printed = False
    for lc in LANE_CONFIGS:
        sc, col, hv = lc["score_col"], lc["actual_col"], lc["hit_value"]
        for bin_label, lo, hi in SCORE_BINS:
            if bin_label == "<0.00":
                continue
            n = sum(
                1 for r in rows_2026
                if _f(r.get(sc)) is not None
                and lo <= (_f(r.get(sc)) or 0) < hi
                and r.get(col) not in ("", "None", "nan")
            )
            if n >= MEDIUM_N:
                status = "MEDIUM CONFIDENCE"
            elif n >= USABLE_N:
                status = "LOW CONFIDENCE - usable"
            elif n >= USABLE_N * 0.5:
                status = f"need {USABLE_N - n} more"
            else:
                status = f"need {USABLE_N - n} more"

            bar = _bar(n, USABLE_N)
            lines.append(
                f"  {lc['lane']:<22} {bin_label:<14} {n:>4}/{USABLE_N}  {bar}  {status}"
            )
            any_printed = True

    if not any_printed:
        lines.append("  No bin data found.")
    lines.append("")


# -- Near-miss grading ---------------------------------------------------------

def _actuals_index(date_rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Build {(game_id, team): row} lookup from card rows with actuals."""
    return {
        (r.get("game_id", ""), r.get("team", "")): r
        for r in date_rows
        if r.get("actual_team_won") not in ("", "None", "nan", None)
    }


def _grade_near_misses(
    near_miss_rows: list[dict],
    actuals: dict[tuple[str, str], dict],
    report_date: str,
) -> list[dict]:
    """Return graded near-miss rows (only those that match report_date and have actuals)."""
    graded = []
    for nm in near_miss_rows:
        if nm.get("game_date") != report_date:
            continue
        key = (nm.get("game_id", ""), nm.get("team", ""))
        actual_row = actuals.get(key)
        if actual_row is None:
            actual_won = None
            moneyline_hit = ""
        else:
            actual_won = _i(actual_row.get("actual_team_won"))
            moneyline_hit = 1 if actual_won == 1 else (0 if actual_won == 0 else "")
        graded.append({
            **nm,
            "actual_team_won":  actual_won,
            "moneyline_hit":    moneyline_hit,
        })
    return graded


def _append_to_history(graded: list[dict]) -> list[dict]:
    """
    Append new graded rows to the cumulative history CSV, avoiding duplicates.
    Returns the full history after appending.
    """
    existing = load_csv(NEAR_MISS_HISTORY)
    existing_keys = {(r.get("game_date"), r.get("game_id"), r.get("team")) for r in existing}
    new_rows = [
        r for r in graded
        if (r.get("game_date"), r.get("game_id"), r.get("team")) not in existing_keys
    ]
    full_history = existing + new_rows
    if new_rows:
        NEAR_MISS_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(full_history[0].keys()) if full_history else []
        NEAR_MISS_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with NEAR_MISS_HISTORY.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(full_history)
    return full_history


def section_near_miss_grading(
    lines: list[str],
    date_rows: list[dict],
    report_date: str,
) -> None:
    lines.append("MONEYLINE CORE NEAR-MISS GRADING")
    lines.append("-" * 70)
    lines.append("  Observe-only diagnostics. Not candidates. Do not act on near misses.")
    lines.append("")

    nm_rows = load_csv(NEAR_MISS_CSV)
    if not NEAR_MISS_CSV.exists():
        lines.append("  Near-miss CSV not found — run kalshi_ev_overlay_preview.py first.")
        lines.append("")
        return
    if not nm_rows:
        lines.append(f"  EV overlay ran but no near misses found (side_score >= 0.30) for any date.")
        lines.append("")
        return

    actuals = _actuals_index(date_rows)
    graded = _grade_near_misses(nm_rows, actuals, report_date)

    if not graded:
        # Check if near misses exist for a different date
        dates_in_nm = {r.get("game_date") for r in nm_rows}
        if dates_in_nm:
            lines.append(
                f"  No near misses found for {report_date}. "
                f"Near-miss CSV contains: {', '.join(sorted(dates_in_nm))}"
            )
        else:
            lines.append(f"  No near misses for {report_date}.")
        lines.append("")
        return

    # Per-row grading
    graded_with_actuals = [r for r in graded if r.get("moneyline_hit") != ""]
    pending_actuals     = [r for r in graded if r.get("moneyline_hit") == ""]

    lines.append(
        f"  {len(graded)} near misses for {report_date} | "
        f"{len(graded_with_actuals)} graded | {len(pending_actuals)} pending actuals"
    )
    lines.append("")

    if graded_with_actuals:
        lines.append(
            f"  {'Team':<6} {'H/A':<5} {'Score':>6}  {'Failed reasons':<35}  {'Result':<6}  Note"
        )
        lines.append("  " + "-" * 80)
        for r in sorted(graded_with_actuals, key=lambda x: -(_f(x.get("side_score")) or 0)):
            result = "hit" if r.get("moneyline_hit") == 1 else "miss"
            # Flag whether the suppressor/filter correctly called the outcome
            failed = r.get("failed_reasons", "")
            note = ""
            if "weak_leader_suppressor" in failed or "live_rebound_suppressor" in failed:
                note = "suppressor correct" if result == "miss" else "suppressor may be too strict"
            elif "below_0.40_threshold" in failed:
                note = "below thresh" + (" hit" if result == "hit" else " miss")
            elif "away_team" in failed:
                note = "away team"
            lines.append(
                f"  {r.get('team','?'):<6} {r.get('home_away','?'):<5} "
                f"{(_f(r.get('side_score')) or 0):>6.3f}  "
                f"{failed:<35}  {result:<6}  {note}"
            )
        lines.append("")

    if pending_actuals:
        lines.append(f"  Pending actuals ({len(pending_actuals)} rows):")
        for r in pending_actuals:
            lines.append(
                f"    {r.get('team','?')} ({r.get('home_away','?')}) "
                f"score={r.get('side_score','?')} bucket={r.get('near_miss_bucket','?')}"
            )
        lines.append("")

    # Persist graded rows to cumulative history
    full_history = _append_to_history(graded_with_actuals)

    # Aggregate stats by bucket (from full history, only rows with results)
    history_with_results = [r for r in full_history if str(r.get("moneyline_hit", "")).strip() not in ("", "None", "nan")]
    MIN_AGG_N = 10   # minimum rows before showing aggregate

    if len(history_with_results) >= MIN_AGG_N:
        lines.append("  AGGREGATE NEAR-MISS STATS (cumulative, observe only):")
        lines.append(
            f"  {'Bucket':<34} {'n':>4}  {'Hit%':>6}  {'Ref%':>6}  {'vs ref':>7}  Note"
        )
        lines.append("  " + "-" * 80)

        buckets = {}
        for r in history_with_results:
            b = r.get("near_miss_bucket", "unknown")
            buckets.setdefault(b, []).append(r)

        for bucket, rows in sorted(buckets.items()):
            n    = len(rows)
            hits = sum(1 for r in rows if str(r.get("moneyline_hit", "")).strip() == "1")
            rate = hits / n if n > 0 else None
            ref_rate, ref_label = _BUCKET_REF.get(bucket, (None, "?"))
            delta = (rate - ref_rate) * 100 if rate is not None and ref_rate is not None else None
            rate_str  = _pct(hits, n) if n > 0 else "  -  "
            ref_str   = f"{ref_rate*100:5.1f}%" if ref_rate is not None else "  -  "
            delta_str = f"{delta:+5.1f}%" if delta is not None else "  -  "
            note = f"CAUTION n<30 ({ref_label})" if n < 30 else ref_label
            lines.append(
                f"  {bucket:<34} {n:>4}  {rate_str}  {ref_str}  {delta_str}  {note}"
            )
        lines.append("")
        lines.append(
            f"  ML Core v1 hist: home+>=0.40={_ML_CORE_HOME_ALL_HIST*100:.1f}% | "
            f"home baseline={_BASELINE_HOME*100:.1f}%"
        )
        lines.append("  Observe only. Do not adjust thresholds from small samples.")
    else:
        lines.append(
            f"  Aggregate stats: need {MIN_AGG_N} graded near-miss rows "
            f"(have {len(history_with_results)} so far)."
        )
    lines.append("")


# -- Save snapshot -------------------------------------------------------------

def save_snapshot(calib_path: Path, snap_path: Path) -> None:
    """Copy current calibration bins to snapshot for next day's mover comparison."""
    if not calib_path.exists():
        return
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_bytes(calib_path.read_bytes())


# -- Main ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Daily pregame brain learning report.")
    parser.add_argument("--date",    default=None,
                        help="Report date YYYY-MM-DD (default: most recent date with actuals)")
    parser.add_argument("--card-csv", default=str(CARD_CSV))
    parser.add_argument("--calib",   default=str(CALIB_CSV))
    parser.add_argument("--out",     default=None, help="Write report to this file too")
    args = parser.parse_args()

    all_rows = load_csv(Path(args.card_csv))
    calib    = load_calib(Path(args.calib))
    snap     = load_calib(SNAP_CSV)

    # Resolve report date
    if args.date:
        report_date = args.date
    else:
        dates_with_actuals = sorted({
            r["game_date"] for r in all_rows
            if r.get("season") not in HISTORICAL_SEASONS
            and r.get("actual_team_won") not in ("", "None", "nan")
        }, reverse=True)
        report_date = dates_with_actuals[0] if dates_with_actuals else date.today().isoformat()

    date_rows  = [r for r in all_rows if r.get("game_date") == report_date]
    date_final = [r for r in date_rows if r.get("actual_team_won") not in ("", "None", "nan")]
    rows_2026  = [
        r for r in all_rows
        if r.get("season") not in HISTORICAL_SEASONS
        and r.get("actual_team_won") not in ("", "None", "nan")
    ]
    baselines = compute_baselines(all_rows)

    n_games  = len({r.get("game_id") for r in date_final})
    n_cards  = len(date_final)
    pending  = len(date_rows) - len(date_final)

    # Build report
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"  PREGAME BRAIN - DAILY LEARNING REPORT - {report_date}")
    lines.append("=" * 70)
    lines.append(
        f"  {n_games} games  ·  {n_cards} card rows with results"
        + (f"  ·  {pending} still pending" if pending else "")
    )
    lines.append(f"  2026 total with actuals: {len(rows_2026)} rows")
    lines.append("")

    section_game_log(lines, date_final, report_date)
    section_today_lanes(lines, date_final, baselines, report_date)
    section_cumulative_2026(lines, rows_2026, calib)
    section_calibration_movers(lines, calib, snap)
    section_sample_tracker(lines, rows_2026)
    section_near_miss_grading(lines, date_final, report_date)

    report = "\n".join(lines)
    print(report)

    # Write to file if requested
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\nReport written to: {args.out}")

    # Save snapshot for tomorrow's mover comparison
    save_snapshot(Path(args.calib), SNAP_CSV)

    # Archive dated copy
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = OUT_DIR / f"learning_report_{report_date}.txt"
    dated_path.write_text(report, encoding="utf-8")
    print(f"Archived to: {dated_path}")


if __name__ == "__main__":
    main()
