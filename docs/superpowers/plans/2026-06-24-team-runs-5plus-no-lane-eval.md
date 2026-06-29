## Goal
Evaluate the `team_runs_5plus_no` lane as a second observe-only research lane: historical baseball audit, Kalshi market price survey, SBR context split, and shadow tracking recommendation.

## Architecture
- `team_runs_5plus_no_logic_audit.py` — Part 1: historical baseball signal analysis (2023–2025 identifier cards)
- `team_runs_5plus_no_kalshi_validation.py` — Part 2: Kalshi [TEAM]5 pregame price survey (June 15–24, 2026)
- `tests/test_team_runs_5plus_no_audit.py` — tests for Part 1
- `tests/test_team_runs_5plus_no_kalshi.py` — tests for Part 2

**No changes to**: ev_shadow_review_log.py, ev_fill_reconciler.py, Moneyline Core v1, any model scoring, any live pipeline.

## Tech Stack
- Python stdlib only: csv, re, sqlite3, pathlib, datetime, statistics, collections
- Input: `outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv` (20,018 rows, 2023–2026)
- Input: `outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv` (7,341 games, moneyline only — no game totals)
- Input: `kalshi_mlb.db` → `kalshi_orderbook_snapshots` (team_total [TEAM]5 coverage: June 15–24, 2026)
- Output dirs: `outputs/team_runs_5plus_no_logic_audit/`, `outputs/team_runs_5plus_no_kalshi_validation/`

## Data Reality (confirmed pre-plan)
- Identifier cards: 404 candidates with score >= 0.40 across 2023–2025; 277 hits = 68.6% hit rate; baseline 57.2%. Hit rate consistent: 2023=68.8%, 2024=69.1%, 2025=67.9%.
- Kalshi [TEAM]5 snapshots: 8 days (June 15–24, 2026). **Zero identifier card candidates fall in this window** — no graded overlap exists yet. Part 2 is a price survey, not an outcome validation.
- Average [TEAM]5 NO ask (all good books): 76.9c. Edge at 68.6% calibrated probability: 68.6 − no_ask − 1.5. At 76.9c average, edge = −9.9c. Only rows with no_ask < 67.1c have positive net edge.
- SBR data: moneyline (home/away no-vig open + close) only. No game totals. Split candidates by moneyline-implied win probability of the candidate team (context only, not price validation).
- `bo_bucket` is the only structural dimension populated for all 404 candidates. `offense_form_bucket`, `team_strength_bucket`, `opponent_strength_bucket`, `opponent_starter_ra9_bucket` are all blank. Use `bo_bucket`, `bd_bucket`, `home_away`, `season`, and parsed `top_positive_reasons` (196/404 rows populated).
- Ticker format: `KXMLBTEAMTOTAL-{YY}{MON}{DD}{HHMM}{AWAY}{HOME}-{TEAM}5`
- `no_ask` is the NO fill price. Never use midpoint or bid.
- CONTAMINATED_FIELDS = never used: team_no_vig_avg, sbr_home_no_vig_avg, market_edge_pp, actual_minus_market, implied_roi_pct.

---

## Files Created/Modified

| File | Action | Responsibility |
|---|---|---|
| `team_runs_5plus_no_logic_audit.py` | Create | Historical baseball audit, SBR context, sublane CSV, reason drivers CSV, summary MD |
| `team_runs_5plus_no_kalshi_validation.py` | Create | Kalshi price survey, pregame NO ask distribution, hypothetical edge, fill quality |
| `tests/test_team_runs_5plus_no_audit.py` | Create | Tests for Part 1 pure functions |
| `tests/test_team_runs_5plus_no_kalshi.py` | Create | Tests for Part 2 pure functions |
| `outputs/team_runs_5plus_no_logic_audit/latest_summary.md` | Generated | Part 1 report |
| `outputs/team_runs_5plus_no_logic_audit/latest_sublanes.csv` | Generated | Part 1 sublane breakdown |
| `outputs/team_runs_5plus_no_logic_audit/latest_reason_drivers.csv` | Generated | Part 1 reason feature analysis |
| `outputs/team_runs_5plus_no_kalshi_validation/latest_summary.md` | Generated | Part 2 price survey report |
| `outputs/team_runs_5plus_no_kalshi_validation/latest_rows.csv` | Generated | Part 2 per-ticker price rows |

---

## Task 1 — Write failing tests for Part 1 logic

**File:** `tests/test_team_runs_5plus_no_audit.py`

```python
"""Unit tests for team_runs_5plus_no_logic_audit.py"""
import csv
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from team_runs_5plus_no_logic_audit import (
    _safe_float,
    _score_bin,
    _is_hit,
    _confidence_label,
    _parse_reasons,
    _sbr_strength_bucket,
    _sublane_stats,
    THRESHOLD,
    FEE_BUFFER_CENTS,
)


class TestSafeFloat(unittest.TestCase):
    def test_parses_valid(self):
        self.assertAlmostEqual(_safe_float("0.686"), 0.686)

    def test_none_for_blank(self):
        self.assertIsNone(_safe_float(""))

    def test_none_for_non_numeric(self):
        self.assertIsNone(_safe_float("n/a"))


class TestScoreBin(unittest.TestCase):
    def test_below_threshold(self):
        self.assertEqual(_score_bin(0.05), "0.00-0.10")

    def test_near_miss_band(self):
        self.assertEqual(_score_bin(0.25), "0.20-0.30")
        self.assertEqual(_score_bin(0.35), "0.30-0.40")

    def test_at_threshold(self):
        self.assertEqual(_score_bin(0.40), "0.40-0.50")
        self.assertEqual(_score_bin(0.45), "0.40-0.50")

    def test_high(self):
        self.assertEqual(_score_bin(0.55), "0.50+")
        self.assertEqual(_score_bin(0.72), "0.50+")


class TestIsHit(unittest.TestCase):
    def test_no_wins_when_team_scores_under_5(self):
        # actual_team_runs_5plus == '0' means team did NOT score 5+, so NO wins
        self.assertTrue(_is_hit({"actual_team_runs_5plus": "0"}))

    def test_no_loses_when_team_scores_5plus(self):
        self.assertFalse(_is_hit({"actual_team_runs_5plus": "1"}))

    def test_none_for_ungraded(self):
        self.assertIsNone(_is_hit({"actual_team_runs_5plus": ""}))
        self.assertIsNone(_is_hit({}))


class TestConfidenceLabel(unittest.TestCase):
    def test_very_low(self):
        self.assertEqual(_confidence_label(29), "very_low")

    def test_low(self):
        self.assertEqual(_confidence_label(75), "low")

    def test_medium(self):
        self.assertEqual(_confidence_label(200), "medium")

    def test_high(self):
        self.assertEqual(_confidence_label(500), "high")

    def test_very_high(self):
        self.assertEqual(_confidence_label(1001), "very_high")

    def test_boundary(self):
        self.assertEqual(_confidence_label(1000), "very_high")
        self.assertEqual(_confidence_label(300), "high")
        self.assertEqual(_confidence_label(100), "medium")
        self.assertEqual(_confidence_label(30), "low")


class TestParseReasons(unittest.TestCase):
    SAMPLE = (
        "[team_early_deficit_tied_or_led_later] home_away+l10_rpg_bucket=home__low_lt_3_5(+0.043) | "
        "[team_early_deficit_scored_next2] home_away+l10_rpg_bucket=home__low_lt_3_5(+0.059)"
    )

    def test_parses_multiple_reasons(self):
        result = _parse_reasons(self.SAMPLE)
        self.assertEqual(len(result), 2)

    def test_extracts_fields(self):
        result = _parse_reasons(self.SAMPLE)
        r = result[0]
        self.assertEqual(r["outcome"], "team_early_deficit_tied_or_led_later")
        self.assertIn("l10_rpg_bucket", r["feature"])
        self.assertAlmostEqual(r["weight"], 0.043)

    def test_blank_returns_empty(self):
        self.assertEqual(_parse_reasons(""), [])

    def test_none_returns_empty(self):
        self.assertEqual(_parse_reasons(None), [])


class TestSbrStrengthBucket(unittest.TestCase):
    def test_heavy_favorite(self):
        self.assertEqual(_sbr_strength_bucket(0.70), "heavy_favorite")

    def test_favorite(self):
        self.assertEqual(_sbr_strength_bucket(0.58), "favorite")

    def test_coin_flip(self):
        self.assertEqual(_sbr_strength_bucket(0.50), "coin_flip")

    def test_underdog(self):
        self.assertEqual(_sbr_strength_bucket(0.38), "underdog")

    def test_none_for_missing(self):
        self.assertIsNone(_sbr_strength_bucket(None))


class TestSublaneStats(unittest.TestCase):
    def _make_rows(self, hit_pattern):
        # hit_pattern: list of True/False/None
        result = []
        for h in hit_pattern:
            if h is None:
                result.append({"actual_team_runs_5plus": ""})
            elif h:
                result.append({"actual_team_runs_5plus": "0"})
            else:
                result.append({"actual_team_runs_5plus": "1"})
        return result

    def test_hit_rate_calculation(self):
        rows = self._make_rows([True, True, False, True])  # 3/4 = 75%
        stats = _sublane_stats(rows, baseline_rate=0.57)
        self.assertEqual(stats["n"], 4)
        self.assertEqual(stats["hits"], 3)
        self.assertAlmostEqual(stats["hit_rate"], 0.75)
        self.assertAlmostEqual(stats["lift"], 0.75 - 0.57)

    def test_excludes_ungraded(self):
        rows = self._make_rows([True, None, False])  # 1/2 graded
        stats = _sublane_stats(rows, baseline_rate=0.57)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["hits"], 1)

    def test_zero_rows(self):
        stats = _sublane_stats([], baseline_rate=0.57)
        self.assertEqual(stats["n"], 0)
        self.assertIsNone(stats["hit_rate"])


if __name__ == "__main__":
    unittest.main()
```

**Shell:** `python -m pytest tests/test_team_runs_5plus_no_audit.py -v`
Expected: All tests fail with ImportError (module doesn't exist yet).

---

## Task 2 — Implement `team_runs_5plus_no_logic_audit.py`

**File:** `team_runs_5plus_no_logic_audit.py`

```python
#!/usr/bin/env python3
"""
team_runs_5plus_no_logic_audit.py — Historical baseball audit for team_runs_5plus_no lane.

Lane rule: team_runs_5plus_no_score >= 0.40
Hit: team scores fewer than 5 runs (actual_team_runs_5plus == '0')
Direction: NO on Kalshi [TEAM]5 contracts

Observe-only research. Does not trade, call APIs, or change model scoring.
"""
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

CARDS_PATH = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
SBR_PATH   = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")
OUT_DIR    = Path("outputs/team_runs_5plus_no_logic_audit")

THRESHOLD        = 0.40
FEE_BUFFER_CENTS = 1.5

# Calibrated probability from calibration bins (score 0.40+, historical)
CALIBRATED_PROB = 0.686

REASONS_PATTERN = re.compile(
    r'\[([^\]]+)\]\s+([^=|]+)=([^(|]+)\(\+([0-9.]+)\)'
)

SUBLANE_CSV_FIELDS = [
    "dimension", "label", "n", "hits", "hit_rate",
    "baseline_hit_rate", "lift", "confidence",
]

REASON_CSV_FIELDS = [
    "outcome", "feature", "feature_value", "count_in_qualified",
    "total_qualified_with_reasons", "rate_in_qualified", "avg_weight",
]


# ── Pure utility functions ─────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _score_bin(score: float) -> str:
    if score >= 0.50:
        return "0.50+"
    if score >= 0.40:
        return "0.40-0.50"
    if score >= 0.30:
        return "0.30-0.40"
    if score >= 0.20:
        return "0.20-0.30"
    if score >= 0.10:
        return "0.10-0.20"
    return "0.00-0.10"


def _is_hit(row: dict) -> bool | None:
    v = row.get("actual_team_runs_5plus", "")
    if v == "0":
        return True
    if v == "1":
        return False
    return None


def _confidence_label(n: int) -> str:
    if n >= 1000:
        return "very_high"
    if n >= 300:
        return "high"
    if n >= 100:
        return "medium"
    if n >= 30:
        return "low"
    return "very_low"


def _parse_reasons(text) -> list[dict]:
    if not text:
        return []
    results = []
    for m in REASONS_PATTERN.finditer(str(text)):
        outcome, feature, value, weight = m.groups()
        results.append({
            "outcome":       outcome.strip(),
            "feature":       feature.strip(),
            "feature_value": value.strip(),
            "weight":        float(weight),
        })
    return results


def _sbr_strength_bucket(win_prob: float | None) -> str | None:
    if win_prob is None:
        return None
    if win_prob >= 0.65:
        return "heavy_favorite"
    if win_prob >= 0.55:
        return "favorite"
    if win_prob >= 0.45:
        return "coin_flip"
    return "underdog"


def _sublane_stats(rows: list[dict], baseline_rate: float) -> dict:
    graded = [(r, _is_hit(r)) for r in rows if _is_hit(r) is not None]
    n     = len(graded)
    hits  = sum(1 for _, h in graded if h)
    if n == 0:
        return {"n": 0, "hits": 0, "hit_rate": None, "lift": None}
    hit_rate = hits / n
    return {
        "n":        n,
        "hits":     hits,
        "hit_rate": hit_rate,
        "lift":     hit_rate - baseline_rate,
    }


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_cards() -> list[dict]:
    if not CARDS_PATH.exists():
        print(f"[audit] ERROR: {CARDS_PATH} not found", file=sys.stderr)
        sys.exit(1)
    with open(CARDS_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_sbr_index() -> dict[str, list[dict]]:
    if not SBR_PATH.exists():
        return {}
    index: dict[str, list[dict]] = defaultdict(list)
    with open(SBR_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            index[r["game_date"]].append(r)
    return dict(index)


def _sbr_team_win_prob(row: dict, sbr_index: dict) -> float | None:
    date_rows = sbr_index.get(row.get("game_date", ""), [])
    team      = row.get("team", "")
    side      = row.get("home_away", "")
    for sr in date_rows:
        if side == "home" and sr.get("home_abbr") == team:
            return _safe_float(sr.get("home_no_vig_avg"))
        if side == "away" and sr.get("away_abbr") == team:
            return _safe_float(sr.get("away_no_vig_avg"))
    return None


# ── Analysis ───────────────────────────────────────────────────────────────────

def _build_sublane_rows(
    qualified: list[dict],
    all_graded: list[dict],
    sbr_index: dict,
) -> list[dict]:
    # Baseline from all graded rows in identifier cards
    baseline_stats = _sublane_stats(all_graded, baseline_rate=0.0)
    baseline_rate = (
        baseline_stats["hit_rate"]
        if baseline_stats["hit_rate"] is not None
        else 0.572
    )

    sublane_rows: list[dict] = []

    def _add(dimension: str, label: str, subset: list[dict]) -> None:
        stats = _sublane_stats(subset, baseline_rate)
        if stats["n"] == 0:
            return
        sublane_rows.append({
            "dimension":        dimension,
            "label":            label,
            "n":                stats["n"],
            "hits":             stats["hits"],
            "hit_rate":         f"{stats['hit_rate']:.3f}" if stats["hit_rate"] is not None else "",
            "baseline_hit_rate": f"{baseline_rate:.3f}",
            "lift":             f"{stats['lift']:+.3f}" if stats["lift"] is not None else "",
            "confidence":       _confidence_label(stats["n"]),
        })

    # Overall qualified
    _add("overall", f"score_gte_{THRESHOLD}", qualified)

    # Score bins (including near-miss bands)
    for bin_label in ["0.20-0.30", "0.30-0.40", "0.40-0.50", "0.50+"]:
        subset = [r for r in all_graded if _score_bin(_safe_float(r.get("team_runs_5plus_no_score")) or 0.0) == bin_label]
        _add("score_bin", bin_label, subset)

    # Season
    for season in ["2023", "2024", "2025", "2026"]:
        subset = [r for r in qualified if r.get("season") == season]
        _add("season", season, subset)

    # Home / Away
    for side in ["home", "away"]:
        subset = [r for r in qualified if r.get("home_away") == side]
        _add("home_away", side, subset)

    # BO bucket
    bo_vals = sorted(set(r.get("bo_bucket", "") for r in qualified if r.get("bo_bucket")))
    for val in bo_vals:
        subset = [r for r in qualified if r.get("bo_bucket") == val]
        _add("bo_bucket", val, subset)

    # BD bucket
    bd_vals = sorted(set(r.get("bd_bucket", "") for r in qualified if r.get("bd_bucket")))
    for val in bd_vals:
        subset = [r for r in qualified if r.get("bd_bucket") == val]
        _add("bd_bucket", val, subset)

    # SBR-implied strength (context only — moneyline win probability, not run scoring)
    sbr_buckets: dict[str, list[dict]] = defaultdict(list)
    for r in qualified:
        win_prob = _sbr_team_win_prob(r, sbr_index)
        bucket   = _sbr_strength_bucket(win_prob)
        if bucket:
            sbr_buckets[bucket].append(r)
    for bucket, subset in sorted(sbr_buckets.items()):
        _add("sbr_ml_strength", bucket, subset)

    return sublane_rows


def _build_reason_drivers(qualified: list[dict]) -> list[dict]:
    # Count feature occurrences in qualified rows with reasons
    rows_with_reasons = [r for r in qualified if r.get("top_positive_reasons", "").strip()]
    total = len(rows_with_reasons)
    if total == 0:
        return []

    from collections import defaultdict, Counter
    feature_counts: dict[str, Counter] = defaultdict(Counter)
    feature_weights: dict[str, list[float]] = defaultdict(list)

    for r in rows_with_reasons:
        for item in _parse_reasons(r.get("top_positive_reasons", "")):
            key = item["outcome"] + " | " + item["feature"] + "=" + item["feature_value"]
            feature_counts[key]["count"] += 1
            feature_weights[key].append(item["weight"])

    driver_rows = []
    for key, cnt in feature_counts.items():
        outcome_feature, _, fval = key.partition(" | ")
        outcome, _, feature = outcome_feature.partition(" | ")
        # re-split correctly
        parts = key.split(" | ", 1)
        outcome_label = parts[0]
        feature_kv = parts[1] if len(parts) > 1 else ""
        count = cnt["count"]
        avg_w = sum(feature_weights[key]) / len(feature_weights[key])
        driver_rows.append({
            "outcome":                     outcome_label,
            "feature":                     feature_kv.split("=")[0] if "=" in feature_kv else feature_kv,
            "feature_value":               feature_kv.split("=")[1] if "=" in feature_kv else "",
            "count_in_qualified":          count,
            "total_qualified_with_reasons": total,
            "rate_in_qualified":           f"{count / total:.3f}",
            "avg_weight":                  f"{avg_w:.4f}",
        })

    driver_rows.sort(key=lambda r: -int(r["count_in_qualified"]))
    return driver_rows[:50]


# ── Output writers ─────────────────────────────────────────────────────────────

def _write_sublanes_csv(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest_sublanes.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUBLANE_CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[audit] Sublanes: {path}")


def _write_reasons_csv(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest_reason_drivers.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REASON_CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[audit] Reason drivers: {path}")


def _write_summary(
    qualified: list[dict],
    sublane_rows: list[dict],
    baseline_rate: float,
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest_summary.md"

    graded = [r for r in qualified if _is_hit(r) is not None]
    hits   = sum(1 for r in graded if _is_hit(r))
    n      = len(graded)
    hit_rate = hits / n if n else 0.0
    lift     = hit_rate - baseline_rate
    edge_at_avg_no_ask = CALIBRATED_PROB * 100 - 76.9 - FEE_BUFFER_CENTS

    lines = [
        "# Team Runs 5+ NO — Historical Logic Audit",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## Lane Rule",
        f"- Score field: `team_runs_5plus_no_score >= {THRESHOLD}`",
        "- Direction: NO on Kalshi `[TEAM]5` contracts",
        "- Hit definition: team scores fewer than 5 runs (`actual_team_runs_5plus == 0`)",
        "",
        "## Overall Historical Performance",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Qualified candidates (score >= {THRESHOLD}) | {n:,} |",
        f"| Hit rate (team scores <5) | {hit_rate:.1%} |",
        f"| Baseline hit rate (all teams) | {baseline_rate:.1%} |",
        f"| Lift vs baseline | {lift:+.1%} |",
        f"| Calibrated probability (bin 0.40+) | {CALIBRATED_PROB:.1%} |",
        f"| Confidence | {_confidence_label(n)} |",
        "",
        "## Market Edge Context (Kalshi price survey — June 15–24 2026)",
        "| Metric | Value |",
        "|---|---|",
        "| Average [TEAM]5 NO ask (all books, all states) | 76.9c |",
        f"| Net edge at 68.6% prob, 76.9c ask | {edge_at_avg_no_ask:+.1f}c |",
        "| Interpretation | Market prices NO at ~77c; brain only has 68.6% → **no edge at average price** |",
        "| Required max NO ask for breakeven | ~67c (after 1.5c fee buffer) |",
        "| Coverage (good books, pregame) | See Kalshi validation report |",
        "",
        "## Season Splits",
        "| Season | N | Hit Rate | Lift |",
        "|---|---|---|---|",
    ]

    for sl in sublane_rows:
        if sl["dimension"] == "season":
            lines.append(
                f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} |"
            )

    lines += [
        "",
        "## Home vs Away",
        "| Side | N | Hit Rate | Lift |",
        "|---|---|---|---|",
    ]
    for sl in sublane_rows:
        if sl["dimension"] == "home_away":
            lines.append(
                f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} |"
            )

    lines += [
        "",
        "## BO Bucket (Bullpen Overuse)",
        "| BO Bucket | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in sublane_rows:
        if sl["dimension"] == "bo_bucket":
            lines.append(
                f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |"
            )

    lines += [
        "",
        "## BD Bucket (Bullpen Depth)",
        "| BD Bucket | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in sublane_rows:
        if sl["dimension"] == "bd_bucket":
            lines.append(
                f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |"
            )

    lines += [
        "",
        "## SBR Moneyline Strength Split (context only — win probability, not runs)",
        "Note: SBR has moneyline data only. Game totals not available. This split shows whether the lane fires on favorites vs underdogs.",
        "| ML Strength | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in sublane_rows:
        if sl["dimension"] == "sbr_ml_strength":
            lines.append(
                f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |"
            )

    lines += [
        "",
        "## Score Bands (Near-Miss and Qualified)",
        "| Score Band | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in sublane_rows:
        if sl["dimension"] == "score_bin":
            lines.append(
                f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |"
            )

    lines += [
        "",
        "## Plain-English Verdict",
        "",
        "Populate after running the audit.",
        "",
        "---",
        f"_Inputs: {CARDS_PATH}, {SBR_PATH}_",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[audit] Summary: {path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("[audit] Loading identifier cards...")
    cards = _load_cards()
    print(f"[audit] Loaded {len(cards):,} rows")

    # Baseline: all graded rows (any score)
    all_graded = [r for r in cards if _is_hit(r) is not None]
    baseline_stats = _sublane_stats(all_graded, 0.0)
    baseline_rate  = baseline_stats["hit_rate"] or 0.572
    print(f"[audit] Baseline hit rate: {baseline_rate:.3f} ({len(all_graded):,} graded rows)")

    # Qualified: score >= 0.40, graded
    qualified = [
        r for r in cards
        if (_safe_float(r.get("team_runs_5plus_no_score")) or 0.0) >= THRESHOLD
    ]
    print(f"[audit] Qualified (score >= {THRESHOLD}): {len(qualified):,}")

    sbr_index = _load_sbr_index()
    print(f"[audit] SBR index: {len(sbr_index)} dates")

    sublane_rows = _build_sublane_rows(qualified, all_graded, sbr_index)
    reason_rows  = _build_reason_drivers(qualified)

    _write_sublanes_csv(sublane_rows)
    _write_reasons_csv(reason_rows)
    _write_summary(qualified, sublane_rows, baseline_rate)

    # Print key stats
    graded_q = [r for r in qualified if _is_hit(r) is not None]
    hits_q   = sum(1 for r in graded_q if _is_hit(r))
    print(f"\n[audit] RESULT: {hits_q}/{len(graded_q)} = {hits_q/len(graded_q):.1%} hit rate")
    print(f"[audit] Baseline: {baseline_rate:.1%} | Lift: {hits_q/len(graded_q)-baseline_rate:+.1%}")
    print(f"[audit] Calibrated probability: {CALIBRATED_PROB:.1%}")
    print(f"[audit] Outputs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
```

**Shell:** `python -m pytest tests/test_team_runs_5plus_no_audit.py -v`
Expected: All tests pass.

Then run: `python team_runs_5plus_no_logic_audit.py`
Expected: Outputs written to `outputs/team_runs_5plus_no_logic_audit/`.

---

## Task 3 — Write failing tests for Part 2 Kalshi validation

**File:** `tests/test_team_runs_5plus_no_kalshi.py`

```python
"""Unit tests for team_runs_5plus_no_kalshi_validation.py"""
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from team_runs_5plus_no_kalshi_validation import (
    _parse_team5_ticker,
    _no_fill_price,
    _no_spread_cents,
    _assess_fill_quality_no,
    _pnl_no,
    _net_edge_no,
    FEE_BUFFER_CENTS,
    WIDE_SPREAD_THRESHOLD,
    ABSURD_BID_MAX,
    ABSURD_ASK_MIN,
)


class TestParseTeam5Ticker(unittest.TestCase):
    def test_parses_away_team(self):
        result = _parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH5")
        self.assertIsNotNone(result)
        self.assertEqual(result["team_code"], "ATH")
        self.assertEqual(result["away_team"], "ATH")
        self.assertEqual(result["home_team"], "SF")

    def test_parses_home_team(self):
        result = _parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-SF5")
        self.assertIsNotNone(result)
        self.assertEqual(result["team_code"], "SF")
        self.assertEqual(result["away_team"], "ATH")
        self.assertEqual(result["home_team"], "SF")

    def test_parses_game_start_utc(self):
        result = _parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH5")
        self.assertIsNotNone(result)
        self.assertEqual(result["game_start_utc"], datetime(2026, 6, 23, 21, 45, tzinfo=timezone.utc))

    def test_returns_none_for_team4(self):
        # [TEAM]4 is a different line; only [TEAM]5 is our target
        self.assertIsNone(_parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH4"))

    def test_returns_none_for_moneyline(self):
        self.assertIsNone(_parse_team5_ticker("KXMLBGAME-26JUN232145ATHSF-ATH"))

    def test_returns_none_for_bad_ticker(self):
        self.assertIsNone(_parse_team5_ticker("GARBAGE"))

    def test_parses_date_correctly(self):
        result = _parse_team5_ticker("KXMLBTEAMTOTAL-26JUN161915SFATL-SF5")
        self.assertIsNotNone(result)
        self.assertEqual(result["game_start_utc"].day, 16)
        self.assertEqual(result["game_start_utc"].month, 6)


class TestNoFillPrice(unittest.TestCase):
    def test_returns_no_ask(self):
        snap = {"no_ask": 58, "no_bid": 42, "yes_ask": 43, "yes_bid": 57}
        self.assertEqual(_no_fill_price(snap), 58)

    def test_returns_none_when_no_ask_null(self):
        snap = {"no_ask": None, "no_bid": None, "yes_ask": 43, "yes_bid": 57}
        self.assertIsNone(_no_fill_price(snap))

    def test_returns_none_when_no_ask_zero(self):
        snap = {"no_ask": 0}
        self.assertIsNone(_no_fill_price(snap))


class TestNoSpreadCents(unittest.TestCase):
    def test_computes_spread(self):
        snap = {"no_ask": 58, "no_bid": 42}
        self.assertEqual(_no_spread_cents(snap), 16)

    def test_none_when_bid_null(self):
        snap = {"no_ask": 58, "no_bid": None}
        self.assertIsNone(_no_spread_cents(snap))


class TestAssessFillQualityNo(unittest.TestCase):
    GAME_START = datetime(2026, 6, 23, 21, 45, tzinfo=timezone.utc)

    def _snap(self, **kwargs):
        defaults = {
            "no_ask": 58, "no_bid": 42, "yes_ask": 43, "yes_bid": 57,
            "snapped_at": "2026-06-23T21:30:00+00:00",
        }
        defaults.update(kwargs)
        return defaults

    def test_usable_good_book(self):
        quality, reason = _assess_fill_quality_no(self._snap(), self.GAME_START)
        self.assertEqual(quality, "usable")

    def test_no_ask_missing(self):
        quality, reason = _assess_fill_quality_no(self._snap(no_ask=None), self.GAME_START)
        self.assertEqual(quality, "no_ask")

    def test_wide_spread(self):
        # spread = no_ask - no_bid = 58 - 42 = 16 >= WIDE_SPREAD_THRESHOLD (10)
        snap = self._snap(no_ask=58, no_bid=42)
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "wide_spread")

    def test_tight_spread_usable(self):
        # spread = 58 - 53 = 5 < 10
        snap = self._snap(no_ask=58, no_bid=53)
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "usable")

    def test_stale_snapshot(self):
        # snapshot > 120s before game start → stale
        snap = self._snap(snapped_at="2026-06-23T19:00:00+00:00")  # 2h45m before game
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "stale_snapshot")

    def test_invalid_book(self):
        # yes_bid <= ABSURD_BID_MAX and no_ask >= ABSURD_ASK_MIN
        snap = self._snap(yes_bid=1, no_ask=97)
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "invalid_book")


class TestPnlNo(unittest.TestCase):
    def test_win(self):
        # NO wins: team scored < 5. Profit = 100 - no_ask - fee
        result = _pnl_no(no_ask=40, won=True)
        self.assertAlmostEqual(result, 100 - 40 - FEE_BUFFER_CENTS)

    def test_loss(self):
        # NO loses: team scored 5+. Loss = -no_ask
        result = _pnl_no(no_ask=40, won=False)
        self.assertAlmostEqual(result, -40.0)


class TestNetEdgeNo(unittest.TestCase):
    def test_positive_edge(self):
        # calib_prob=0.686, no_ask=55 → 68.6 - 55 - 1.5 = 12.1
        result = _net_edge_no(calib_prob=0.686, no_ask=55)
        self.assertAlmostEqual(result, 12.1)

    def test_negative_edge_at_high_ask(self):
        # calib_prob=0.686, no_ask=77 → 68.6 - 77 - 1.5 = -9.9
        result = _net_edge_no(calib_prob=0.686, no_ask=77)
        self.assertAlmostEqual(result, -9.9)

    def test_zero_edge(self):
        # calib_prob=0.686, no_ask=67.1 → ~0
        result = _net_edge_no(calib_prob=0.686, no_ask=67.1)
        self.assertAlmostEqual(result, 0.0, places=0)


if __name__ == "__main__":
    unittest.main()
```

**Shell:** `python -m pytest tests/test_team_runs_5plus_no_kalshi.py -v`
Expected: All tests fail with ImportError.

---

## Task 4 — Implement `team_runs_5plus_no_kalshi_validation.py`

**File:** `team_runs_5plus_no_kalshi_validation.py`

```python
#!/usr/bin/env python3
"""
team_runs_5plus_no_kalshi_validation.py — Kalshi [TEAM]5 pregame price survey.

Validates market liquidity and pricing for the team_runs_5plus_no lane.
NOTE: Historical brain candidates (2023-2025) do not overlap with Kalshi
snapshot coverage (June 15-24, 2026). This script is a price survey that
asks: given our calibrated probability (68.6%), what NO ask prices would
be needed, and how often does the actual market hit that threshold?

Does NOT trade, call APIs, change model scoring, or touch Moneyline Core v1.
"""
import csv
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

KALSHI_DB = Path("kalshi_mlb.db")
OUT_DIR   = Path("outputs/team_runs_5plus_no_kalshi_validation")

FEE_BUFFER_CENTS    = 1.5
CALIBRATED_PROB     = 0.686        # from calibration bins: score >= 0.40, hist
WIDE_SPREAD_THRESHOLD = 10         # NO spread cents >= 10 → wide_spread
ABSURD_BID_MAX      = 2            # yes_bid <= 2 AND no_ask >= ABSURD_ASK_MIN → invalid
ABSURD_ASK_MIN      = 95
PREGAME_WINDOW_SECS = 7200         # 2 hours before game start
STALE_THRESHOLD_SECS = 120         # snapshot must be within 2 min of game start

MONTH_MAP = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
)}

TEAM5_PATTERN = re.compile(
    r'^KXMLBTEAMTOTAL-(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)([A-Z]+)-([A-Z]+)5$'
)

ROWS_CSV_FIELDS = [
    "market_ticker", "team_code", "away_team", "home_team",
    "game_start_utc", "game_date",
    "snap_at", "secs_before_game",
    "no_ask", "no_bid", "yes_ask", "yes_bid", "spread_cents_no",
    "fill_quality", "fill_quality_reason",
    "net_edge_at_calib", "breakeven_max_no_ask",
    "would_be_positive_edge",
]


# ── Pure utility functions ─────────────────────────────────────────────────────

def _parse_team5_ticker(ticker: str) -> dict | None:
    m = TEAM5_PATTERN.match(ticker)
    if not m:
        return None
    yr, mon, day, time4, away, home, team_code = m.groups()
    month = MONTH_MAP.get(mon)
    if not month:
        return None
    try:
        game_start = datetime(
            2000 + int(yr), month, int(day),
            int(time4[:2]), int(time4[2:]),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return {
        "team_code":      team_code,
        "away_team":      away,
        "home_team":      home,
        "game_start_utc": game_start,
    }


def _no_fill_price(snap: dict) -> int | None:
    v = snap.get("no_ask")
    return v if (v is not None and v > 0) else None


def _no_spread_cents(snap: dict) -> int | None:
    ask = snap.get("no_ask")
    bid = snap.get("no_bid")
    if ask is None or bid is None:
        return None
    return ask - bid


def _assess_fill_quality_no(
    snap: dict, game_start_utc: datetime
) -> tuple[str, str]:
    """Return (quality, reason) for a NO-side fill on a [TEAM]5 market."""
    yes_bid = snap.get("yes_bid")
    no_ask  = snap.get("no_ask")
    no_bid  = snap.get("no_bid")

    # Parse snapshot time
    snap_str = snap.get("snapped_at", "")
    try:
        snap_dt = datetime.fromisoformat(snap_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "invalid_book", "unparseable_timestamp"

    # Stale check: snapshot must be within STALE_THRESHOLD_SECS of game start
    secs_before = (game_start_utc - snap_dt).total_seconds()
    if secs_before > STALE_THRESHOLD_SECS:
        return "stale_snapshot", f"snapshot_{int(secs_before)}s_before_game"

    # No ask missing
    if no_ask is None or no_ask <= 0 or no_ask >= 100:
        return "no_ask", "no_ask_missing_or_invalid"

    # Absurd book guard
    if yes_bid is not None and yes_bid <= ABSURD_BID_MAX and no_ask >= ABSURD_ASK_MIN:
        return "invalid_book", f"yes_bid_{yes_bid}_no_ask_{no_ask}"

    # Wide spread
    if no_bid is not None:
        spread = no_ask - no_bid
        if spread >= WIDE_SPREAD_THRESHOLD:
            return "wide_spread", f"no_spread_{spread}c"

    return "usable", ""


def _pnl_no(no_ask: float, won: bool) -> float:
    if won:
        return 100.0 - no_ask - FEE_BUFFER_CENTS
    return -float(no_ask)


def _net_edge_no(calib_prob: float, no_ask: float) -> float:
    return calib_prob * 100.0 - no_ask - FEE_BUFFER_CENTS


# ── Database queries ───────────────────────────────────────────────────────────

def _get_all_team5_tickers(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT DISTINCT market_ticker FROM kalshi_orderbook_snapshots "
        "WHERE market_ticker LIKE 'KXMLBTEAMTOTAL%5' AND market_type = 'team_total'"
    )
    return [row[0] for row in cur.fetchall()]


def _get_best_pregame_snapshot(
    conn: sqlite3.Connection,
    market_ticker: str,
    game_start_utc: datetime,
) -> dict | None:
    """
    Find the last snapshot within PREGAME_WINDOW_SECS before game start,
    with a valid no_ask.
    """
    cutoff   = game_start_utc.isoformat()
    earliest = (game_start_utc - timedelta(seconds=PREGAME_WINDOW_SECS)).isoformat()

    cur = conn.execute(
        """
        SELECT market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask,
               spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at <= ?
          AND snapped_at >= ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (market_ticker, cutoff, earliest),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = ["market_ticker", "snapped_at", "yes_bid", "yes_ask", "no_bid", "no_ask", "spread_cents"]
    return dict(zip(cols, row))


# ── Survey logic ───────────────────────────────────────────────────────────────

def _survey_tickers(conn: sqlite3.Connection) -> list[dict]:
    """
    For every [TEAM]5 ticker, get the best pregame snapshot and assess quality.
    """
    tickers = _get_all_team5_tickers(conn)
    print(f"[kalshi] Found {len(tickers):,} distinct [TEAM]5 tickers")

    rows_out = []
    skipped_parse  = 0
    skipped_nosnap = 0

    for ticker in tickers:
        parsed = _parse_team5_ticker(ticker)
        if not parsed:
            skipped_parse += 1
            continue

        game_start = parsed["game_start_utc"]
        snap = _get_best_pregame_snapshot(conn, ticker, game_start)

        if snap is None:
            skipped_nosnap += 1
            quality, reason = "no_snapshot", "no_pregame_snapshot_in_window"
            no_ask = None
            no_bid = None
            yes_ask = None
            yes_bid = None
            spread_no = None
            snap_at   = ""
            secs_before = None
        else:
            quality, reason = _assess_fill_quality_no(snap, game_start)
            no_ask    = _no_fill_price(snap)
            no_bid    = snap.get("no_bid")
            yes_ask   = snap.get("yes_ask")
            yes_bid   = snap.get("yes_bid")
            spread_no = _no_spread_cents(snap)
            snap_at   = snap.get("snapped_at", "")
            try:
                snap_dt     = datetime.fromisoformat(snap_at.replace("Z", "+00:00"))
                secs_before = int((game_start - snap_dt).total_seconds())
            except (ValueError, AttributeError):
                secs_before = None

        net_edge = _net_edge_no(CALIBRATED_PROB, no_ask) if no_ask else None
        breakeven_max = CALIBRATED_PROB * 100 - FEE_BUFFER_CENTS

        rows_out.append({
            "market_ticker":         ticker,
            "team_code":             parsed["team_code"],
            "away_team":             parsed["away_team"],
            "home_team":             parsed["home_team"],
            "game_start_utc":        game_start.isoformat(),
            "game_date":             game_start.strftime("%Y-%m-%d"),
            "snap_at":               snap_at,
            "secs_before_game":      secs_before if secs_before is not None else "",
            "no_ask":                no_ask if no_ask is not None else "",
            "no_bid":                no_bid if no_bid is not None else "",
            "yes_ask":               yes_ask if yes_ask is not None else "",
            "yes_bid":               yes_bid if yes_bid is not None else "",
            "spread_cents_no":       spread_no if spread_no is not None else "",
            "fill_quality":          quality,
            "fill_quality_reason":   reason,
            "net_edge_at_calib":     f"{net_edge:.2f}" if net_edge is not None else "",
            "breakeven_max_no_ask":  f"{breakeven_max:.1f}",
            "would_be_positive_edge": "yes" if (net_edge is not None and net_edge > 0) else ("no" if net_edge is not None else ""),
        })

    print(f"[kalshi] Skipped (parse fail): {skipped_parse}, (no snapshot): {skipped_nosnap}")
    return rows_out


# ── Output writers ─────────────────────────────────────────────────────────────

def _write_rows_csv(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest_rows.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ROWS_CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[kalshi] Rows CSV: {path}")


def _write_summary(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest_summary.md"

    usable    = [r for r in rows if r["fill_quality"] == "usable"]
    has_ask   = [r for r in rows if r["no_ask"] not in ("", None)]
    pos_edge  = [r for r in rows if r["would_be_positive_edge"] == "yes"]

    no_asks   = [int(r["no_ask"]) for r in has_ask if str(r["no_ask"]).isdigit()]
    net_edges = [float(r["net_edge_at_calib"]) for r in has_ask if r["net_edge_at_calib"]]

    quality_counts: dict[str, int] = {}
    for r in rows:
        q = r["fill_quality"]
        quality_counts[q] = quality_counts.get(q, 0) + 1

    breakeven = CALIBRATED_PROB * 100 - FEE_BUFFER_CENTS

    lines = [
        "# Team Runs 5+ NO — Kalshi Market Price Survey",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## Coverage Window",
        "Kalshi [TEAM]5 snapshots available: June 15–24, 2026 (8 dates).",
        "Historical brain candidates (2023-2025) do not yet overlap with this window.",
        "This is a **price survey only** — no graded outcomes available.",
        "",
        "## Market Coverage",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Distinct [TEAM]5 tickers | {len(rows):,} |",
        f"| With valid NO ask | {len(has_ask):,} |",
        f"| Fill quality: usable | {quality_counts.get('usable', 0):,} |",
        f"| Fill quality: stale_snapshot | {quality_counts.get('stale_snapshot', 0):,} |",
        f"| Fill quality: wide_spread | {quality_counts.get('wide_spread', 0):,} |",
        f"| Fill quality: no_ask | {quality_counts.get('no_ask', 0):,} |",
        f"| Fill quality: invalid_book | {quality_counts.get('invalid_book', 0):,} |",
        f"| Fill quality: no_snapshot | {quality_counts.get('no_snapshot', 0):,} |",
        "",
        "## NO Ask Distribution (pregame, valid books)",
    ]
    if no_asks:
        lines += [
            f"| Stat | Value |",
            f"|---|---|",
            f"| Mean NO ask | {mean(no_asks):.1f}c |",
            f"| Median NO ask | {median(no_asks):.1f}c |",
            f"| Min | {min(no_asks)}c |",
            f"| Max | {max(no_asks)}c |",
            f"| N | {len(no_asks):,} |",
        ]
    else:
        lines.append("No valid NO ask data found.")

    lines += [
        "",
        "## Edge Analysis (at calibrated probability = 68.6%)",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Calibrated probability (score >= 0.40) | 68.6% |",
        f"| Fee buffer | {FEE_BUFFER_CENTS}c |",
        f"| Breakeven max NO ask | {breakeven:.1f}c |",
        f"| Tickers where NO ask < breakeven | {len(pos_edge):,} / {len(has_ask):,} |",
    ]
    if net_edges:
        lines += [
            f"| Mean net edge (has_ask rows) | {mean(net_edges):+.1f}c |",
            f"| Median net edge | {median(net_edges):+.1f}c |",
        ]

    lines += [
        "",
        "## Plain-English Verdict",
        "",
        "Populate after running the survey.",
        "",
        "---",
        f"_Input: {KALSHI_DB}_",
        f"_Calibrated probability: {CALIBRATED_PROB:.1%} (historical 2023-2025, 404 games)_",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[kalshi] Summary: {path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if not KALSHI_DB.exists():
        print(f"[kalshi] ERROR: {KALSHI_DB} not found", file=sys.stderr)
        sys.exit(1)

    print(f"[kalshi] Connecting to {KALSHI_DB}...")
    conn = sqlite3.connect(KALSHI_DB)

    rows = _survey_tickers(conn)
    conn.close()

    _write_rows_csv(rows)
    _write_summary(rows)

    usable   = sum(1 for r in rows if r["fill_quality"] == "usable")
    pos_edge = sum(1 for r in rows if r["would_be_positive_edge"] == "yes")
    total    = len(rows)
    print(f"\n[kalshi] {total} tickers | {usable} usable books | {pos_edge} with positive edge at 68.6%")
    print(f"[kalshi] Outputs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
```

**Shell:** `python -m pytest tests/test_team_runs_5plus_no_kalshi.py -v`
Expected: All tests pass.

Then run: `python team_runs_5plus_no_kalshi_validation.py`
Expected: Outputs written to `outputs/team_runs_5plus_no_kalshi_validation/`.

---

## Task 5 — Run full test suite

**Shell:**
```
python -m pytest tests/ -v --tb=short
```

Expected: All 152+ existing tests pass, plus all new tests. Zero failures.

---

## Quality Checks

- [ ] Every step has exact file paths
- [x] Every step has complete code (no "..." or "etc.")
- [x] Type/method names are consistent across all steps
- [x] No step references a function not yet defined
- [x] Plan fully covers the spec

## Execution Handoff

Two modes:

1. **Inline** — run all 5 tasks in the current session (recommended — 4 files, clear order)
2. **Subagent** — spawn a fresh subagent with this plan as context

---

## Part 4 — Shadow Tracking Recommendation (written after audit runs)

After running Parts 1 and 2, evaluate against these criteria:

| Criterion | Target | Status |
|---|---|---|
| Historical hit rate | > 65% | TBD (run audit) |
| Season consistency | All 3 years > 63% | TBD |
| Calibrated lift | > +8pp | TBD |
| Pregame NO ask below breakeven (67.1c) | > 20% of usable books | TBD |
| Usable book rate | > 50% of tickers | TBD |

If all criteria pass → recommend `team_total_suppression_v1` shadow rule:
- `team_runs_5plus_no_score >= 0.40`
- target `[TEAM]5 NO`
- require `fill_quality == usable`
- require `no_ask <= 65c` (ensures positive edge at 68.6%)
- require `spread_cents_no <= 8`
- log only through shadow review log (observe_only=true)
- no real trades, no Discord

If criteria fail → document which criterion failed and why.
