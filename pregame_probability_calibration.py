"""
pregame_probability_calibration.py

Read-only research script. Calibrates pregame brain scores into lane-specific
historical hit rates using 2023-2025 pregame_identifier_cards.csv.

Outputs:
  outputs/pregame_probability_calibration/calibration_bins.csv
  outputs/pregame_probability_calibration/calibration_loo_summary.csv  (--loo only)
  outputs/pregame_probability_calibration/calibration_summary.md
  outputs/pregame_probability_calibration/latest_calibration_bins.csv

No trades. No paper entries. Read-only.
"""
import argparse
import csv
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

CARD_CSV = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
OUT_DIR  = Path("outputs/pregame_probability_calibration")

# Fixed score bins: (label, lo_inclusive, hi_exclusive)
SCORE_BINS: list[tuple[str, float, float]] = [
    ("<0.00",     -math.inf, 0.00),
    ("0.00-0.10",  0.00,     0.10),
    ("0.10-0.20",  0.10,     0.20),
    ("0.20-0.30",  0.20,     0.30),
    ("0.30-0.40",  0.30,     0.40),
    ("0.40+",      0.40,     math.inf),
]

# Lane configs: score_col, actual_col, hit_value (1=outcome occurred, 0=outcome did not)
LANE_CONFIGS: list[dict] = [
    {
        "lane":       "side",
        "score_col":  "side_score",
        "actual_col": "actual_team_won",
        "hit_value":  1,
    },
    {
        "lane":       "side_fade",
        "score_col":  "side_fade_score",
        "actual_col": "actual_team_won",
        "hit_value":  0,   # fade succeeds when faded team loses
    },
    {
        "lane":       "team_runs_4plus",
        "score_col":  "team_runs_4plus_score",
        "actual_col": "actual_team_runs_4plus",
        "hit_value":  1,
    },
    {
        "lane":       "team_runs_5plus_no",
        "score_col":  "team_runs_5plus_no_score",
        "actual_col": "actual_team_runs_5plus",
        "hit_value":  0,   # "no" signal succeeds when team does NOT score 5+
    },
    {
        "lane":       "team_f5_runs_2plus",
        "score_col":  "team_f5_runs_2plus_score",
        "actual_col": "actual_team_f5_runs_2plus",
        "hit_value":  1,
    },
    {
        "lane":       "full_total_avoid",
        "score_col":  "full_total_avoid_score",
        "actual_col": "actual_game_total_9plus",
        "hit_value":  0,   # avoid succeeds when total does NOT go 9+
    },
]

HISTORICAL_SEASONS = {"2023", "2024", "2025"}  # default — updated by --seasons flag

CSV_FIELDS = [
    "lane", "score_bin", "min_score", "max_score",
    "sample_size", "hits", "hit_rate",
    "baseline_rate", "lift_vs_baseline",
    "confidence", "conservative_probability",
]

LOO_FIELDS = [
    "test_season", "lane", "score_bin",
    "train_sample", "train_conservative_prob",
    "test_sample", "test_hits", "test_hit_rate",
    "train_baseline", "error_vs_conservative",
]


# ── Pure functions (unit-tested) ──────────────────────────────────────────────

def assign_bin(score: float, bins: list[tuple[str, float, float]]) -> str:
    for label, lo, hi in bins:
        if lo <= score < hi:
            return label
    return bins[-1][0]


def conservative_probability(hits: int, n: int, baseline: float, shrink_n: int) -> float:
    """Shrink raw hit rate toward baseline. Returns baseline when n=0."""
    if n == 0:
        return baseline
    return (hits + baseline * shrink_n) / (n + shrink_n)


def confidence_label(n: int) -> str:
    if n < 30:    return "very_low"
    if n < 100:   return "low"
    if n < 300:   return "medium"
    if n < 1000:  return "high"
    return "very_high"


def lookup_calibration(
    calib: dict[tuple[str, str], dict],
    lane: str,
    score: float,
    bins: list[tuple[str, float, float]] | None = None,
) -> dict | None:
    _bins = bins if bins is not None else SCORE_BINS
    bin_label = assign_bin(score, _bins)
    return calib.get((lane, bin_label))


# ── Data helpers ──────────────────────────────────────────────────────────────

def _as_float(v: Any) -> float | None:
    try:
        s = str(v).strip()
        if not s or s.lower() in {"nan", "none", "null", ""}:
            return None
        return float(s)
    except Exception:
        return None


def _as_int(v: Any) -> int | None:
    f = _as_float(v)
    return None if f is None else int(round(f))


def load_cards(path: Path, seasons: set[str] | None = None) -> list[dict]:
    """
    Load card rows with filled actuals. If seasons is provided, filter to those seasons.
    Otherwise loads HISTORICAL_SEASONS only.
    """
    if not path.exists():
        raise FileNotFoundError(f"Card CSV not found: {path}")
    allowed = seasons if seasons is not None else HISTORICAL_SEASONS
    rows = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("season", "") not in allowed:
                continue
            if r.get("actual_team_won", "") in ("", "None", "nan"):
                continue
            rows.append(r)
    return rows


# ── Core calibration ──────────────────────────────────────────────────────────

def compute_baseline(rows: list[dict], actual_col: str, hit_value: int) -> float:
    if not rows:
        return 0.0
    hits = sum(1 for r in rows if _as_int(r.get(actual_col)) == hit_value)
    return hits / len(rows)


def compute_lane_bins(
    rows: list[dict],
    lane_cfg: dict,
    bins: list[tuple[str, float, float]],
    shrink_n: int,
) -> list[dict]:
    score_col  = lane_cfg["score_col"]
    actual_col = lane_cfg["actual_col"]
    hit_value  = lane_cfg["hit_value"]

    baseline = compute_baseline(rows, actual_col, hit_value)

    bucketed: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        s = _as_float(r.get(score_col))
        if s is None:
            continue
        bucketed[assign_bin(s, bins)].append(r)

    result = []
    for label, lo, hi in bins:
        b_rows = bucketed[label]
        n      = len(b_rows)
        hits   = sum(1 for r in b_rows if _as_int(r.get(actual_col)) == hit_value)
        hr     = hits / n if n > 0 else None
        cons   = conservative_probability(hits, n, baseline, shrink_n)
        lift   = (hr - baseline) if hr is not None else None

        result.append({
            "lane":                     lane_cfg["lane"],
            "score_bin":                label,
            "min_score":                "" if math.isinf(lo) else lo,
            "max_score":                "" if math.isinf(hi) else hi,
            "sample_size":              n,
            "hits":                     hits,
            "hit_rate":                 round(hr, 4)   if hr   is not None else "",
            "baseline_rate":            round(baseline, 4),
            "lift_vs_baseline":         round(lift, 4) if lift is not None else "",
            "confidence":               confidence_label(n),
            "conservative_probability": round(cons, 4),
        })
    return result


# ── Leave-one-season-out ──────────────────────────────────────────────────────

def run_loo(
    all_rows: list[dict],
    bins: list[tuple[str, float, float]],
    shrink_n: int,
) -> list[dict]:
    seasons = sorted(HISTORICAL_SEASONS)
    loo_rows = []
    for test_season in seasons:
        train_rows = [r for r in all_rows if r.get("season") != test_season]
        test_rows  = [r for r in all_rows if r.get("season") == test_season]
        for lane_cfg in LANE_CONFIGS:
            train_bins = compute_lane_bins(train_rows, lane_cfg, bins, shrink_n)
            bin_map    = {b["score_bin"]: b for b in train_bins}

            score_col  = lane_cfg["score_col"]
            actual_col = lane_cfg["actual_col"]
            hit_value  = lane_cfg["hit_value"]

            for label, lo, hi in bins:
                t_rows = [
                    r for r in test_rows
                    if _as_float(r.get(score_col)) is not None
                    and lo <= (_as_float(r.get(score_col)) or 0) < hi
                ]
                if not t_rows:
                    continue
                t_hits = sum(1 for r in t_rows if _as_int(r.get(actual_col)) == hit_value)
                t_hr   = t_hits / len(t_rows)
                train  = bin_map.get(label, {})
                tc_prob = train.get("conservative_probability", "")
                error = (
                    round(t_hr - float(tc_prob), 4)
                    if tc_prob not in ("", None) else ""
                )
                loo_rows.append({
                    "test_season":             test_season,
                    "lane":                    lane_cfg["lane"],
                    "score_bin":               label,
                    "train_sample":            train.get("sample_size", 0),
                    "train_conservative_prob": tc_prob,
                    "test_sample":             len(t_rows),
                    "test_hits":               t_hits,
                    "test_hit_rate":           round(t_hr, 4),
                    "train_baseline":          train.get("baseline_rate", ""),
                    "error_vs_conservative":   error,
                })
    return loo_rows


# ── Output writers ────────────────────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _md_lane_section(lines: list[str], bins_for_label: list[dict], label: str) -> None:
    """Append one lane's bin table to lines."""
    nonempty = [b for b in bins_for_label if b["sample_size"] > 0]
    baseline = nonempty[0]["baseline_rate"] if nonempty else "?"
    total_n  = sum(b["sample_size"] for b in bins_for_label)
    lines.append(f"### {label}  (baseline={baseline}, total_n={total_n})")
    lines.append("")
    lines.append(f"{'Bin':<14} {'n':>6} {'Hits':>6} {'HitRate':>8} {'Lift':>7} {'ConservProb':>12} Confidence")
    lines.append("-" * 72)
    for b in bins_for_label:
        if b["sample_size"] == 0:
            continue
        lines.append(
            f"{b['score_bin']:<14} {b['sample_size']:>6} {b['hits']:>6} "
            f"{float(b['hit_rate'] or 0):>8.3f} "
            f"{float(b['lift_vs_baseline'] or 0):>+7.3f} "
            f"{float(b['conservative_probability']):>12.4f}  {b['confidence']}"
        )
    lines.append("")


def write_markdown(
    path: Path,
    all_bins: list[dict],
    loo_rows: list[dict] | None = None,
    shrink_n: int = 100,
    season_bins_map: dict[str, list[dict]] | None = None,
    seasons_used: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    seasons_str = ", ".join(seasons_used) if seasons_used else "2023-2025"
    lines = [
        "# Pregame Probability Calibration",
        f"Generated: {today}  |  shrink_n={shrink_n}  |  seasons={seasons_str}",
        "",
        "**Formula:** `conservative_prob = (hits + baseline * shrink_n) / (n + shrink_n)`",
        "**Confidence:** very_low <30 · low 30–99 · medium 100–299 · high 300–999 · very_high 1000+",
        "",
    ]

    # Primary calibration bins (all included seasons combined)
    lines.append(f"## Primary Calibration ({seasons_str})")
    lines.append("")
    for lane_cfg in LANE_CONFIGS:
        lane = lane_cfg["lane"]
        lbins = [b for b in all_bins if b["lane"] == lane]
        _md_lane_section(lines, lbins, lane)

    # Per-season comparison sections
    if season_bins_map:
        for label, sbins in season_bins_map.items():
            lines.append(f"## {label} Evaluation")
            lines.append("")
            for lane_cfg in LANE_CONFIGS:
                lane = lane_cfg["lane"]
                lbins = [b for b in sbins if b["lane"] == lane]
                if any(b["sample_size"] > 0 for b in lbins):
                    _md_lane_section(lines, lbins, lane)

    if loo_rows:
        lines.append("## Leave-One-Season-Out Validation")
        lines.append("")
        lines.append(
            f"{'Season':<8} {'Lane':<22} {'Bin':<14} "
            f"{'TrainP':>8} {'TestRate':>9} {'Error':>8} {'TestN':>6}"
        )
        lines.append("-" * 78)
        for r in loo_rows:
            if not r.get("test_sample"):
                continue
            tc = str(r.get("train_conservative_prob", ""))
            err = str(r.get("error_vs_conservative", ""))
            lines.append(
                f"{r['test_season']:<8} {r['lane']:<22} {r['score_bin']:<14} "
                f"{tc[:6]:>8} {float(r['test_hit_rate']):>9.3f} "
                f"{err[:7]:>8} {r['test_sample']:>6}"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_lane_bins(lane_bins: list[dict]) -> None:
    total_n  = sum(b["sample_size"] for b in lane_bins)
    nonempty = [b for b in lane_bins if b["sample_size"] > 0 and b["score_bin"] != "<0.00"]
    lane = lane_bins[0]["lane"] if lane_bins else "?"
    print(f"{lane:22}  total={total_n}")
    for b in nonempty:
        lift = f"{float(b['lift_vs_baseline'] or 0):+.3f}" if b["lift_vs_baseline"] != "" else "  —  "
        print(
            f"  {b['score_bin']:12}  n={b['sample_size']:5}  "
            f"rate={b['hit_rate']}  lift={lift}  "
            f"cons={b['conservative_probability']}  [{b['confidence']}]"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate pregame brain scores into lane hit rates. Read-only."
    )
    parser.add_argument("--card-csv",  default=str(CARD_CSV))
    parser.add_argument("--out-dir",   default=str(OUT_DIR))
    parser.add_argument("--shrink-n",  type=int, default=100,
                        help="Shrinkage weight toward lane baseline (default 100)")
    parser.add_argument("--loo",       action="store_true",
                        help="Also run leave-one-season-out cross-validation")
    parser.add_argument("--seasons",   default=None,
                        help="Comma-separated seasons to include (e.g. 2023,2024,2025,2026). "
                             "Default: 2023,2024,2025 plus any season with ≥10 filled actuals.")
    args = parser.parse_args()

    card_path = Path(args.card_csv)
    out_dir   = Path(args.out_dir)

    # Resolve which seasons to use for the primary calibration
    if args.seasons:
        primary_seasons = set(args.seasons.split(","))
    else:
        # Auto-include any season beyond 2023-2025 that has ≥ 10 rows with actuals
        primary_seasons = set(HISTORICAL_SEASONS)
        import csv as _csv
        with card_path.open(encoding="utf-8") as f:
            all_rows_check = list(_csv.DictReader(f))
        extra = {}
        for r in all_rows_check:
            s = r.get("season", "")
            if s not in primary_seasons and r.get("actual_team_won", "") not in ("", "None", "nan"):
                extra[s] = extra.get(s, 0) + 1
        for s, n in extra.items():
            if n >= 10:
                primary_seasons.add(s)
                print(f"  Auto-including season {s}: {n} rows with actuals")

    print(f"Loading cards: {card_path}")
    rows = load_cards(card_path, seasons=primary_seasons)
    print(f"  Rows with actuals: {len(rows)}")
    seasons_in_data = sorted({r["season"] for r in rows})
    print(f"  Seasons: {seasons_in_data}")
    print()

    # ── Primary calibration (all included seasons) ────────────────────────────
    print("=== Primary calibration ===")
    all_bins: list[dict] = []
    for lane_cfg in LANE_CONFIGS:
        lane_bins = compute_lane_bins(rows, lane_cfg, SCORE_BINS, args.shrink_n)
        all_bins.extend(lane_bins)
        _print_lane_bins(lane_bins)

    # ── Per-season comparison (if multiple seasons) ───────────────────────────
    season_bins_map: dict[str, list[dict]] = {}
    extra_seasons = [s for s in seasons_in_data if s not in HISTORICAL_SEASONS]
    if extra_seasons:
        # 2023-2025 only
        rows_hist = [r for r in rows if r["season"] in HISTORICAL_SEASONS]
        hist_bins: list[dict] = []
        for lane_cfg in LANE_CONFIGS:
            hist_bins.extend(compute_lane_bins(rows_hist, lane_cfg, SCORE_BINS, args.shrink_n))
        season_bins_map["2023-2025"] = hist_bins

        # Each extra season in isolation
        for es in extra_seasons:
            es_rows = [r for r in rows if r["season"] == es]
            if len(es_rows) >= 10:
                es_bins: list[dict] = []
                for lane_cfg in LANE_CONFIGS:
                    es_bins.extend(compute_lane_bins(es_rows, lane_cfg, SCORE_BINS, args.shrink_n))
                season_bins_map[es] = es_bins
                print(f"=== {es} evaluation only ({len(es_rows)} rows) ===")
                for lane_cfg in LANE_CONFIGS:
                    lb = [b for b in es_bins if b["lane"] == lane_cfg["lane"]]
                    _print_lane_bins(lb)

    loo_rows = None
    if args.loo:
        print("Running leave-one-season-out...")
        loo_rows = run_loo(rows, SCORE_BINS, args.shrink_n)
        write_csv(out_dir / "calibration_loo_summary.csv", loo_rows, LOO_FIELDS)
        print(f"  LOO rows written: {len(loo_rows)}")
        print()

    write_csv(out_dir / "calibration_bins.csv",        all_bins, CSV_FIELDS)
    write_csv(out_dir / "latest_calibration_bins.csv", all_bins, CSV_FIELDS)
    write_markdown(
        out_dir / "calibration_summary.md",
        all_bins, loo_rows,
        shrink_n=args.shrink_n,
        season_bins_map=season_bins_map if season_bins_map else None,
        seasons_used=seasons_in_data,
    )

    print(f"WROTE: {out_dir}/calibration_bins.csv")
    print(f"WROTE: {out_dir}/latest_calibration_bins.csv")
    print(f"WROTE: {out_dir}/calibration_summary.md")


if __name__ == "__main__":
    main()
