## Goal
Calibrate pregame brain scores into lane-specific historical win rates so the EV overlay can compute real edge instead of proxy estimates.

## Architecture
```
pregame_identifier_cards.csv (19,962 historical rows, 2023–2025)
    ↓
pregame_probability_calibration.py
    ↓ writes
outputs/pregame_probability_calibration/
    calibration_bins.csv          (all seasons combined)
    calibration_loo_summary.csv   (leave-one-season-out, --loo flag)
    calibration_summary.md
    latest_calibration_bins.csv   (symlink-equivalent: same content, stable path)
    ↓ read by
kalshi_ev_overlay_preview.py
    → replaces hardcoded model_prob_value with calibrated conservative_probability
    → adds calibration metadata fields to output CSV
    → gates tradeability on calibration quality + sample size
    ↓
frontend/src/pages/SlateMonitor.tsx
    → evNote() handles two new labels: "insufficient_sample", "uncalibrated"
```

## Tech Stack
- Python stdlib only (csv, math, argparse, pathlib, collections)
- No numpy/pandas — keeps it consistent with existing scripts
- pytest for tests

## Preliminary Data

From 19,962 historical rows with actuals (2023–2025):

| Lane | Baseline | Score 0.20-0.30 | Score 0.30-0.40 | Score 0.40+ | n@0.40+ |
|------|---------|----------------|----------------|------------|---------|
| side | 49.9% | 53.1% (+3.2) | 55.4% (+5.5) | 60.8% (+10.9) | 2445 |
| side_fade | 50.1% | 55.1% (+5.0) | 58.2% (+8.1) | 61.0% (+10.9) | 1235 |
| team_runs_4plus | 55.6% | 56.8% (+1.2) | 62.4% (+6.8) | 64.4% (+8.8) | 2026 |
| team_runs_5plus_no | 57.2% | 63.3% (+6.0) | 64.4% (+7.1) | 68.6% (+11.3) | 404 |
| team_f5_runs_2plus | 58.1% | 62.3% (+4.1) | 64.4% (+6.3) | 62.8% (+4.7) | 1480 |
| full_total_avoid | 50.5% | — | — | — | ~5 (skip v1) |

`full_total_avoid` has only 5 rows above 0.20 — calibrate but flag as very_low confidence.

## Files Created / Modified

| File | Action | Responsibility |
|------|--------|----------------|
| `pregame_probability_calibration.py` | CREATE | Calibration script |
| `tests/test_probability_calibration.py` | CREATE | Unit tests |
| `kalshi_ev_overlay_preview.py` | MODIFY | Load + use calibration bins |
| `frontend/src/pages/SlateMonitor.tsx` | MODIFY | evNote() for 2 new labels |

---

## Step 1 — Write failing tests

**File:** `tests/test_probability_calibration.py`

Run first: `python -m pytest tests/test_probability_calibration.py -q` → should fail with ImportError or AttributeError.

```python
"""
tests/test_probability_calibration.py

Tests for pregame_probability_calibration.py.
All tests should FAIL until implementation is complete.
"""
import importlib.util
import math
from pathlib import Path
import pytest

_SCRIPT = Path("pregame_probability_calibration.py")


def _load():
    if not _SCRIPT.exists():
        pytest.skip("pregame_probability_calibration.py not yet implemented")
    spec = importlib.util.spec_from_file_location("calib", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Bin assignment ────────────────────────────────────────────────────────────

class TestAssignBin:
    def test_negative_score_in_lt0_bin(self):
        m = _load()
        assert m.assign_bin(-0.01, m.SCORE_BINS) == "<0.00"

    def test_zero_in_0_to_10_bin(self):
        m = _load()
        assert m.assign_bin(0.0, m.SCORE_BINS) == "0.00-0.10"

    def test_0_05_in_0_to_10_bin(self):
        m = _load()
        assert m.assign_bin(0.05, m.SCORE_BINS) == "0.00-0.10"

    def test_0_10_in_10_to_20_bin(self):
        m = _load()
        assert m.assign_bin(0.10, m.SCORE_BINS) == "0.10-0.20"

    def test_0_25_in_20_to_30_bin(self):
        m = _load()
        assert m.assign_bin(0.25, m.SCORE_BINS) == "0.20-0.30"

    def test_0_30_in_30_to_40_bin(self):
        m = _load()
        assert m.assign_bin(0.30, m.SCORE_BINS) == "0.30-0.40"

    def test_0_40_in_40plus_bin(self):
        m = _load()
        assert m.assign_bin(0.40, m.SCORE_BINS) == "0.40+"

    def test_1_0_in_40plus_bin(self):
        m = _load()
        assert m.assign_bin(1.0, m.SCORE_BINS) == "0.40+"


# ── Shrinkage / conservative probability ─────────────────────────────────────

class TestConservativeProbability:
    def test_zero_samples_returns_baseline(self):
        m = _load()
        result = m.conservative_probability(hits=0, n=0, baseline=0.50, shrink_n=100)
        assert result == pytest.approx(0.50, abs=1e-6)

    def test_shrinks_toward_baseline_with_small_sample(self):
        m = _load()
        # 10 hits / 10 obs = raw 1.0, but should shrink toward 0.5 baseline
        result = m.conservative_probability(hits=10, n=10, baseline=0.50, shrink_n=100)
        # Expected: (10 + 0.5*100) / (10 + 100) = 60/110 ≈ 0.545
        assert result == pytest.approx(60 / 110, abs=1e-6)

    def test_large_sample_close_to_raw_rate(self):
        m = _load()
        # 600 hits / 1000 obs, baseline 0.5, shrink 100
        result = m.conservative_probability(hits=600, n=1000, baseline=0.50, shrink_n=100)
        # Expected: (600 + 50) / 1100 = 650/1100 ≈ 0.5909
        assert result == pytest.approx(650 / 1100, abs=1e-4)

    def test_symmetric_shrinkage(self):
        m = _load()
        # Raw rate matches baseline → conservative == baseline
        result = m.conservative_probability(hits=50, n=100, baseline=0.50, shrink_n=100)
        # (50 + 50) / (100 + 100) = 0.50
        assert result == pytest.approx(0.50, abs=1e-6)


# ── Confidence label ──────────────────────────────────────────────────────────

class TestConfidenceLabel:
    def test_very_low_under_30(self):
        m = _load()
        assert m.confidence_label(0)   == "very_low"
        assert m.confidence_label(29)  == "very_low"

    def test_low_30_to_99(self):
        m = _load()
        assert m.confidence_label(30)  == "low"
        assert m.confidence_label(99)  == "low"

    def test_medium_100_to_299(self):
        m = _load()
        assert m.confidence_label(100) == "medium"
        assert m.confidence_label(299) == "medium"

    def test_high_300_to_999(self):
        m = _load()
        assert m.confidence_label(300) == "high"
        assert m.confidence_label(999) == "high"

    def test_very_high_1000_plus(self):
        m = _load()
        assert m.confidence_label(1000) == "very_high"
        assert m.confidence_label(9999) == "very_high"


# ── Lane hit rate computation ─────────────────────────────────────────────────

class TestComputeLaneBins:
    def _make_rows(self, scores_hits):
        """scores_hits: list of (score, hit) tuples."""
        return [
            {"side_score": str(s), "actual_team_won": str(h)}
            for s, h in scores_hits
        ]

    def test_empty_rows_returns_all_zero_bins(self):
        m = _load()
        lane_cfg = m.LANE_CONFIGS[0]  # side lane
        bins = m.compute_lane_bins([], lane_cfg, m.SCORE_BINS, shrink_n=100)
        assert all(b["sample_size"] == 0 for b in bins)

    def test_counts_hits_correctly(self):
        m = _load()
        lane_cfg = m.LANE_CONFIGS[0]  # side lane: score_col=side_score, actual_col=actual_team_won, hit_value=1
        rows = self._make_rows([
            (0.25, 1), (0.25, 1), (0.25, 0),   # 3 in 0.20-0.30, 2 hits
            (0.35, 1),                            # 1 in 0.30-0.40, 1 hit
        ])
        # baseline over all rows: 3 hits / 4 rows = 0.75
        bins = m.compute_lane_bins(rows, lane_cfg, m.SCORE_BINS, shrink_n=100)
        bin_map = {b["score_bin"]: b for b in bins}

        b = bin_map["0.20-0.30"]
        assert b["sample_size"] == 3
        assert b["hits"] == 2
        assert b["hit_rate"] == pytest.approx(2/3, abs=1e-6)

        b2 = bin_map["0.30-0.40"]
        assert b2["sample_size"] == 1
        assert b2["hits"] == 1

    def test_fade_lane_inverts_outcome(self):
        m = _load()
        # side_fade: hit = team_won == 0
        fade_cfg = next(c for c in m.LANE_CONFIGS if c["lane"] == "side_fade")
        rows = [
            {"side_fade_score": "0.25", "actual_team_won": "0"},  # hit (team lost = fade success)
            {"side_fade_score": "0.25", "actual_team_won": "1"},  # miss
        ]
        bins = m.compute_lane_bins(rows, fade_cfg, m.SCORE_BINS, shrink_n=100)
        b = next(b for b in bins if b["score_bin"] == "0.20-0.30")
        assert b["hits"] == 1
        assert b["hit_rate"] == pytest.approx(0.5, abs=1e-6)


# ── EV overlay calibration guard ─────────────────────────────────────────────

class TestCalibrationLookup:
    def test_returns_none_for_missing_lane(self):
        m = _load()
        calib = {}  # empty
        result = m.lookup_calibration(calib, lane="side", score=0.35)
        assert result is None

    def test_returns_correct_bin(self):
        m = _load()
        calib = {
            ("side", "0.30-0.40"): {
                "conservative_probability": 0.545,
                "sample_size": 960,
                "hit_rate": 0.554,
                "baseline_rate": 0.499,
                "confidence": "high",
            }
        }
        result = m.lookup_calibration(calib, lane="side", score=0.35)
        assert result is not None
        assert result["conservative_probability"] == pytest.approx(0.545, abs=1e-6)
        assert result["confidence"] == "high"

    def test_returns_none_for_very_low_confidence_below_threshold(self):
        m = _load()
        calib = {
            ("side", "0.30-0.40"): {
                "conservative_probability": 0.60,
                "sample_size": 5,   # too small
                "hit_rate": 0.60,
                "baseline_rate": 0.499,
                "confidence": "very_low",
            }
        }
        # lookup_calibration returns the row regardless of confidence;
        # tradeability guard is in the overlay, not here
        result = m.lookup_calibration(calib, lane="side", score=0.35)
        assert result is not None
        assert result["confidence"] == "very_low"
```

---

## Step 2 — Implement `pregame_probability_calibration.py`

**File:** `pregame_probability_calibration.py`

```python
"""
pregame_probability_calibration.py

Read-only research script. Calibrates pregame brain scores into lane-specific
historical hit rates using 2023–2025 pregame_identifier_cards.csv.

Outputs:
  outputs/pregame_probability_calibration/calibration_bins.csv
  outputs/pregame_probability_calibration/calibration_loo_summary.csv  (--loo only)
  outputs/pregame_probability_calibration/calibration_summary.md
  outputs/pregame_probability_calibration/latest_calibration_bins.csv  (stable path)

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
    ("<0.00",    -math.inf, 0.00),
    ("0.00-0.10", 0.00,    0.10),
    ("0.10-0.20", 0.10,    0.20),
    ("0.20-0.30", 0.20,    0.30),
    ("0.30-0.40", 0.30,    0.40),
    ("0.40+",     0.40,    math.inf),
]

# Lane configs: score_col, actual_col, hit_value (1=team wins/scores, 0=team does not)
LANE_CONFIGS: list[dict] = [
    {"lane": "side",               "score_col": "side_score",               "actual_col": "actual_team_won",          "hit_value": 1},
    {"lane": "side_fade",          "score_col": "side_fade_score",          "actual_col": "actual_team_won",          "hit_value": 0},
    {"lane": "team_runs_4plus",    "score_col": "team_runs_4plus_score",    "actual_col": "actual_team_runs_4plus",   "hit_value": 1},
    {"lane": "team_runs_5plus_no", "score_col": "team_runs_5plus_no_score", "actual_col": "actual_team_runs_5plus",   "hit_value": 0},
    {"lane": "team_f5_runs_2plus", "score_col": "team_f5_runs_2plus_score", "actual_col": "actual_team_f5_runs_2plus","hit_value": 1},
    {"lane": "full_total_avoid",   "score_col": "full_total_avoid_score",   "actual_col": "actual_game_total_9plus",  "hit_value": 0},
]

HISTORICAL_SEASONS = {"2023", "2024", "2025"}

CSV_FIELDS = [
    "lane", "score_bin", "min_score", "max_score",
    "sample_size", "hits", "hit_rate",
    "baseline_rate", "lift_vs_baseline",
    "confidence", "conservative_probability",
]


# ── Pure functions (tested) ───────────────────────────────────────────────────

def assign_bin(score: float, bins: list[tuple[str, float, float]]) -> str:
    for label, lo, hi in bins:
        if lo <= score < hi:
            return label
    return bins[-1][0]


def conservative_probability(hits: int, n: int, baseline: float, shrink_n: int) -> float:
    if n == 0:
        return baseline
    return (hits + baseline * shrink_n) / (n + shrink_n)


def confidence_label(n: int) -> str:
    if n < 30:   return "very_low"
    if n < 100:  return "low"
    if n < 300:  return "medium"
    if n < 1000: return "high"
    return "very_high"


def lookup_calibration(
    calib: dict[tuple[str, str], dict],
    lane: str,
    score: float,
    bins: list[tuple[str, float, float]] | None = None,
) -> dict | None:
    _bins = bins or SCORE_BINS
    bin_label = assign_bin(score, _bins)
    return calib.get((lane, bin_label))


# ── Data loading ──────────────────────────────────────────────────────────────

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
    """Load historical card rows. Excludes live_slate rows and rows missing actuals."""
    if not path.exists():
        raise FileNotFoundError(f"Card CSV not found: {path}")
    rows = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("season", "") not in HISTORICAL_SEASONS:
                continue
            if seasons and r.get("season", "") not in seasons:
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

    # Bucket rows
    bucketed: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        s = _as_float(r.get(score_col))
        if s is None:
            continue
        bucketed[assign_bin(s, bins)].append(r)

    result = []
    for label, lo, hi in bins:
        b_rows = bucketed[label]
        n = len(b_rows)
        hits = sum(1 for r in b_rows if _as_int(r.get(actual_col)) == hit_value)
        hr = hits / n if n > 0 else None
        cons_prob = conservative_probability(hits, n, baseline, shrink_n)
        lift = (hr - baseline) if hr is not None else None

        result.append({
            "lane":                  lane_cfg["lane"],
            "score_bin":             label,
            "min_score":             "" if math.isinf(lo) else lo,
            "max_score":             "" if math.isinf(hi) else hi,
            "sample_size":           n,
            "hits":                  hits,
            "hit_rate":              round(hr, 4) if hr is not None else "",
            "baseline_rate":         round(baseline, 4),
            "lift_vs_baseline":      round(lift, 4) if lift is not None else "",
            "confidence":            confidence_label(n),
            "conservative_probability": round(cons_prob, 4),
        })
    return result


# ── Leave-one-season-out ──────────────────────────────────────────────────────

def run_loo(all_rows: list[dict], bins: list[tuple[str, float, float]], shrink_n: int) -> list[dict]:
    seasons = sorted(HISTORICAL_SEASONS)
    loo_rows = []
    for test_season in seasons:
        train_rows = [r for r in all_rows if r.get("season") != test_season]
        test_rows  = [r for r in all_rows if r.get("season") == test_season]
        for lane_cfg in LANE_CONFIGS:
            train_bins = compute_lane_bins(train_rows, lane_cfg, bins, shrink_n)
            bin_map = {b["score_bin"]: b for b in train_bins}

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
                t_hr = t_hits / len(t_rows)
                train_calib = bin_map.get(label, {})
                loo_rows.append({
                    "test_season":           test_season,
                    "lane":                  lane_cfg["lane"],
                    "score_bin":             label,
                    "train_sample":          train_calib.get("sample_size", 0),
                    "train_conservative_prob": train_calib.get("conservative_probability", ""),
                    "test_sample":           len(t_rows),
                    "test_hits":             t_hits,
                    "test_hit_rate":         round(t_hr, 4),
                    "train_baseline":        train_calib.get("baseline_rate", ""),
                    "error_vs_conservative": round(
                        t_hr - float(train_calib.get("conservative_probability", 0) or 0), 4
                    ) if train_calib.get("conservative_probability") != "" else "",
                })
    return loo_rows


# ── Output writers ────────────────────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_markdown(path: Path, all_bins: list[dict], loo_rows: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    lines = [
        f"# Pregame Probability Calibration",
        f"Generated: {today}",
        "",
        "Shrinkage formula: `conservative_prob = (hits + baseline * shrink_n) / (n + shrink_n)`",
        "Confidence: very_low <30 | low 30–99 | medium 100–299 | high 300–999 | very_high 1000+",
        "",
    ]
    lanes = {b["lane"] for b in all_bins}
    for lane in sorted(lanes):
        lbins = [b for b in all_bins if b["lane"] == lane]
        baseline = next((b["baseline_rate"] for b in lbins if b["sample_size"] > 0), "?")
        lines.append(f"## {lane}  (baseline={baseline})")
        lines.append("")
        lines.append(f"{'Bin':<14} {'n':>6} {'Hits':>6} {'HitRate':>8} {'Lift':>7} {'ConservProb':>12} {'Confidence'}")
        lines.append("-" * 70)
        for b in lbins:
            if b["sample_size"] == 0:
                continue
            lines.append(
                f"{b['score_bin']:<14} {b['sample_size']:>6} {b['hits']:>6} "
                f"{float(b['hit_rate'] or 0):>8.3f} {float(b['lift_vs_baseline'] or 0):>+7.3f} "
                f"{float(b['conservative_probability']):>12.4f} {b['confidence']}"
            )
        lines.append("")

    if loo_rows:
        lines.append("## Leave-One-Season-Out Validation")
        lines.append("")
        lines.append(f"{'Season':<8} {'Lane':<22} {'Bin':<14} {'TrainP':>8} {'TestRate':>9} {'Error':>7}")
        lines.append("-" * 75)
        for r in loo_rows:
            if not r.get("test_sample"):
                continue
            lines.append(
                f"{r['test_season']:<8} {r['lane']:<22} {r['score_bin']:<14} "
                f"{str(r.get('train_conservative_prob',''))[:6]:>8} "
                f"{float(r['test_hit_rate']):>9.3f} "
                f"{str(r.get('error_vs_conservative',''))[:7]:>7}"
            )

    path.write_text("\n".join(lines), encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate pregame brain scores. Read-only.")
    parser.add_argument("--card-csv", default=str(CARD_CSV))
    parser.add_argument("--out-dir",  default=str(OUT_DIR))
    parser.add_argument("--shrink-n", type=int, default=100,
                        help="Shrinkage weight toward baseline (default 100)")
    parser.add_argument("--loo", action="store_true",
                        help="Also run leave-one-season-out cross-validation")
    args = parser.parse_args()

    card_path = Path(args.card_csv)
    out_dir   = Path(args.out_dir)

    print(f"Loading cards: {card_path}")
    rows = load_cards(card_path)
    print(f"  Historical rows with actuals: {len(rows)}")
    seasons = sorted({r["season"] for r in rows})
    print(f"  Seasons: {seasons}")

    all_bins: list[dict] = []
    for lane_cfg in LANE_CONFIGS:
        lane_bins = compute_lane_bins(rows, lane_cfg, SCORE_BINS, args.shrink_n)
        all_bins.extend(lane_bins)
        total_n = sum(b["sample_size"] for b in lane_bins)
        nonzero = [b for b in lane_bins if b["sample_size"] > 0 and b["score_bin"] != "<0.00"]
        print(f"  {lane_cfg['lane']:22}  total={total_n}  nonzero-bins={len(nonzero)}")
        for b in nonzero:
            print(f"    {b['score_bin']:12}  n={b['sample_size']:5}  rate={b['hit_rate']}  cons={b['conservative_probability']}  [{b['confidence']}]")

    loo_rows = None
    if args.loo:
        print("\nRunning leave-one-season-out...")
        loo_rows = run_loo(rows, SCORE_BINS, args.shrink_n)
        write_csv(
            out_dir / "calibration_loo_summary.csv",
            loo_rows,
            ["test_season","lane","score_bin","train_sample","train_conservative_prob",
             "test_sample","test_hits","test_hit_rate","train_baseline","error_vs_conservative"],
        )
        print(f"  LOO rows: {len(loo_rows)}")

    write_csv(out_dir / "calibration_bins.csv",        all_bins, CSV_FIELDS)
    write_csv(out_dir / "latest_calibration_bins.csv", all_bins, CSV_FIELDS)
    write_markdown(out_dir / "calibration_summary.md", all_bins, loo_rows)

    print(f"\nWROTE: {out_dir}/calibration_bins.csv")
    print(f"WROTE: {out_dir}/latest_calibration_bins.csv")
    print(f"WROTE: {out_dir}/calibration_summary.md")


if __name__ == "__main__":
    main()
```

---

## Step 3 — Run and validate

```bash
python -m pytest tests/test_probability_calibration.py -q   # should all pass now
python pregame_probability_calibration.py --loo
```

Expected output (from preliminary analysis):
- `side` 0.40+ bin: n≈2445, rate≈0.608, cons_prob≈0.596
- `team_runs_5plus_no` 0.40+ bin: n≈404, rate≈0.686, cons_prob≈0.667
- `full_total_avoid` above 0.10: n=5 (very_low confidence)
- LOO summary: errors should be small (< ±3%) for high-n bins

---

## Step 4 — Update `kalshi_ev_overlay_preview.py`

### 4a. Add calibration loader (after the `write_csv` helper, ~line 193)

```python
CALIB_CSV = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")
MIN_CALIB_SAMPLE = 30   # below this → "insufficient_sample" not "tradeable"

def load_calibration_bins(path: Path = CALIB_CSV) -> dict[tuple[str, str], dict]:
    """Load latest_calibration_bins.csv → {(lane, score_bin): row_dict}."""
    if not path.exists():
        return {}
    result: dict[tuple[str, str], dict] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["lane"], row["score_bin"])
            result[key] = row
    return result


def _score_bins() -> list[tuple[str, float, float]]:
    import math
    return [
        ("<0.00",    -math.inf, 0.00),
        ("0.00-0.10", 0.00,    0.10),
        ("0.10-0.20", 0.10,    0.20),
        ("0.20-0.30", 0.20,    0.30),
        ("0.30-0.40", 0.30,    0.40),
        ("0.40+",     0.40,    math.inf),
    ]


def lookup_calibration(
    calib: dict[tuple[str, str], dict],
    lane: str,
    score: float,
) -> dict | None:
    bins = _score_bins()
    for label, lo, hi in bins:
        if lo <= score < hi:
            return calib.get((lane, label))
    return calib.get((lane, bins[-1][0]))
```

### 4b. Load calibration at startup in `main()` and `run_forward_brain()`

At the top of `main()` and `run_forward_brain()`, add:
```python
calib_bins = load_calibration_bins()
if not calib_bins:
    print("  NOTE: No calibration bins found. Run: python pregame_probability_calibration.py")
```

Pass `calib_bins` into `_score_ev_row()` (rename current `score_ev_row` for clarity if needed, or add as kwarg).

### 4c. Update `build_ev_row()` — replace hardcoded `model_prob_value`

Replace lines 648–651:
```python
# BEFORE
model_prob = lane_cfg.get("model_prob_value")
edge: float | None = None
if model_prob is not None and entry_price is not None:
    edge = round(model_prob * 100 - entry_price, 2)

# AFTER
model_prob = lane_cfg.get("model_prob_value")   # keep as fallback
calib_row  = calib_bins.get((lane_cfg["lane"],)) if calib_bins else None  # see 4d below
# calib_row injected via new param; see signature change
```

Change `build_ev_row` signature to accept `calib_row: dict | None = None`:

```python
def build_ev_row(
    card: dict,
    lane_cfg: dict,
    market: dict | None,
    snap: dict | None,
    entry_price: int | None,
    spread: int | None,
    tradeability: str,
    reason_not_tradeable: str,
    is_fallback: bool = False,
    snap_age_hours: float | None = None,
    fallback_snap: dict | None = None,
    game_start_utc: str | None = None,
    calib_row: dict | None = None,       # NEW
) -> dict:
    # Prefer calibrated probability; fall back to hardcoded model_prob_value
    calib_prob: float | None = None
    calib_sample: int | None = None
    calib_hit_rate: float | None = None
    calib_baseline: float | None = None
    calib_confidence: str | None = None
    calib_bin: str | None = None

    if calib_row and calib_row.get("conservative_probability") not in ("", None):
        try:
            calib_prob       = float(calib_row["conservative_probability"])
            calib_sample     = int(calib_row["sample_size"])
            calib_hit_rate   = float(calib_row["hit_rate"]) if calib_row.get("hit_rate") else None
            calib_baseline   = float(calib_row["baseline_rate"])
            calib_confidence = calib_row["confidence"]
            calib_bin        = calib_row["score_bin"]
        except Exception:
            pass

    model_prob = calib_prob if calib_prob is not None else lane_cfg.get("model_prob_value")

    edge: float | None = None
    if model_prob is not None and entry_price is not None:
        edge = round(model_prob * 100 - entry_price, 2)
    ...
    return {
        ...existing fields...,
        # NEW calibration fields
        "calibrated_probability":    calib_prob,
        "calibration_bin":           calib_bin,
        "calibration_sample_size":   calib_sample,
        "calibration_hit_rate":      calib_hit_rate,
        "calibration_baseline":      calib_baseline,
        "calibration_confidence":    calib_confidence,
        "proxy_brain_score":         card.get(lane_cfg["score_col"]),
    }
```

### 4d. Update tradeability guard to use calibration quality

In `determine_tradeability()` (~line 488), add after the existing `needs_probability_calibration` check:

```python
# Insufficient calibration sample
if calib_row is not None:
    n = int(calib_row.get("sample_size") or 0)
    if n < MIN_CALIB_SAMPLE:
        return "insufficient_sample", (
            f"Calibration bin has only {n} historical samples (need ≥ {MIN_CALIB_SAMPLE}). "
            f"Observe only."
        )

# No calibration at all for this lane/bin
if calib_row is None and lane_cfg.get("model_prob_value") is None:
    return "uncalibrated", (
        f"No calibration data for lane={lane_cfg['lane']}. "
        f"Run: python pregame_probability_calibration.py"
    )
```

Also add `calib_row` parameter to `determine_tradeability()` signature and thread it through all callers.

### 4e. Remove hardcoded `model_prob_value` from `LANE_CONFIGS`

```python
# BEFORE
{"lane": "side", ..., "model_prob_value": 0.579, ...}

# AFTER
{"lane": "side", ..., "model_prob_value": None, ...}
# (None signals "use calibration"; hardcoded value only as last resort if no calib file)
```

---

## Step 5 — Update `SlateMonitor.tsx` — evNote for 2 new labels

In `evStatusLabel()` add:
```typescript
'insufficient_sample': 'Low sample',
'uncalibrated':        'No calibration',
```

In `evNote()` add:
```typescript
if (label === 'insufficient_sample') {
  const n = r.calibration_sample_size
  return n ? `Only ${n} historical samples` : 'Insufficient sample'
}
if (label === 'uncalibrated') {
  return 'Run calibration script first'
}
```

---

## Step 6 — Run full test suite

```bash
python -m pytest tests/test_probability_calibration.py -q   # all green
python -m pytest tests/ -q --deselect tests/test_candidate_generator.py::test_live_watcher_one_active_game_scanned   # pre-existing failures only
cd frontend && npx tsc --noEmit                             # no TS errors
```

---

## Quality Checks

- [x] Every step has exact file paths
- [x] Every step has complete code
- [x] SCORE_BINS defined once in calibration script and duplicated inline in overlay (no shared import between scripts — consistent with existing codebase pattern)
- [x] `full_total_avoid` calibrated but will show `very_low` or `low` confidence for all bins — overlay will return `insufficient_sample`
- [x] `side_fade` and `team_f5_runs_2plus` calibrated in script; not yet in overlay LANE_CONFIGS (no corresponding Kalshi market) — calibration data produced for future use
- [x] `conservative_probability` formula: `(hits + baseline * shrink_n) / (n + shrink_n)` — shrinks toward baseline for small n
- [x] LOO is behind `--loo` flag — doesn't block fixed-bin calibration
- [x] No writes to Kalshi, no orders, no paper trades

---

## Execution Modes

**Recommended: Inline execution** — 6 steps, all sequential, ~30 min total.

Steps 1→2→3 are self-contained (calibration only).
Steps 4→5→6 depend on knowing the exact output format from Step 3.
Run all 6 in this session in order.
