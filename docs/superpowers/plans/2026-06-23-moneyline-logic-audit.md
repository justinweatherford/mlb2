## Goal
Determine whether the pregame brain has a repeatable moneyline sub-lane with genuine edge — not just a favorite detector — by auditing 2023-2025 historical card rows across all available dimensions.

## Architecture
Single read-only script (`pregame_moneyline_logic_audit.py`) reads card CSV and calibration CSV, computes all analysis in memory, and writes markdown + CSV outputs. No DB access. No model changes. No trading behavior.

## Tech Stack
- Python stdlib only: csv, re, math, pathlib, collections, argparse
- Input: `outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv`
- Input: `outputs/pregame_probability_calibration/latest_calibration_bins.csv`
- Output dir: `outputs/pregame_moneyline_logic_audit/`

---

## Pre-Research Findings

These facts are confirmed from data inspection. Plan steps must not re-derive them.

**Available sub-lane dimensions:**
- `home_away` — directly in CSV column, fully populated (9981 home / 9981 away)
- `bo_bucket`, `bd_bucket` — populated except 122 "missing" rows
- `bo_plus_weak_bd_tag`, `avoid_low_bo_strong_bd_tag` — populated (yes/no)
- ALL other bucket/strength columns are **blank in the historical CSV** — must be extracted by parsing `top_positive_reasons`

**Reason string format:**
```
[team_won] opponent_strength_bucket=lt_40(+0.115)|[team_runs_4plus] tag_weak_leader_fade_watch=yes(+0.081)
```
Parse with regex: `\[.*?\]\s*([\w+_]+)=([\w._+-]+)`
Keys available in reasons: `team_strength_gap_bucket`, `opponent_strength_bucket`, `team_strength_bucket`, `tag_weak_leader_fade_watch`, `tag_live_rebound_watch`, `opponent_starter_xfip_bucket`, `l10_rpg_bucket`, `offense_form_bucket`, `tag_strong_offense_vs_vulnerable_starter`, `tag_home_scoring_spot`, `opponent_run_prevention_bucket`, etc.

**No odds data exists** — favorite detection must use `opponent_strength_bucket=lt_40` and `team_strength_gap_bucket=plus_10_plus` as proxies (clearly labeled "approximate").

**Key numbers already confirmed:**
- Historical baseline: home 53.1%, away 46.8%, all 49.9%
- side>=0.40: n=2445, hit=60.8%
- side>=0.40 + HOME: n=1510, hit=63.4% (consistent: 61.9%/61.2%/65.6%)
- side>=0.40 + AWAY: n=935, hit=56.6% (degrading: 59.6%/61.9%/**52.2%** in 2025)
- side>=0.40 + HOME + opp_lt40: n=390, hit=68.5% (consistent: 64.9%/66.3%/78.3%)
- side>=0.40 + HOME + NOT opp_lt40: n=1120, hit=61.7% (consistent: 59.9%/58.8%/63.7%)
- tag_weak_leader_fade_watch at side>=0.40: hit=59.7% (BELOW baseline)
- tag_live_rebound_watch at side>=0.40: hit=55.4% (well BELOW baseline)
- side_fade>=0.40: n=1235, hit=61.0% (fade = team lost)

---

## Files Created/Modified

| File | Action | Responsibility |
|------|--------|----------------|
| `pregame_moneyline_logic_audit.py` | CREATE | Full analysis script |
| `tests/test_moneyline_audit.py` | CREATE | Tests for parse_reason_conditions() |
| `outputs/pregame_moneyline_logic_audit/moneyline_logic_summary.md` | GENERATED | Human-readable findings |
| `outputs/pregame_moneyline_logic_audit/latest_moneyline_logic_summary.md` | GENERATED | Always-current copy |
| `outputs/pregame_moneyline_logic_audit/moneyline_sublanes.csv` | GENERATED | One row per sub-lane condition |
| `outputs/pregame_moneyline_logic_audit/latest_moneyline_sublanes.csv` | GENERATED | Always-current copy |
| `outputs/pregame_moneyline_logic_audit/moneyline_season_splits.csv` | GENERATED | Per-bin per-season breakdown |

---

## Step 1 — Write tests for `parse_reason_conditions()`

**File:** `tests/test_moneyline_audit.py`

Tests cover the only novel pure function in the script.

```python
"""tests/test_moneyline_audit.py"""
import importlib.util
from pathlib import Path
import pytest

_SCRIPT = Path("pregame_moneyline_logic_audit.py")

def _load():
    if not _SCRIPT.exists():
        pytest.skip("pregame_moneyline_logic_audit.py not yet implemented")
    spec = importlib.util.spec_from_file_location("ml_audit", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParseReasonConditions:
    def test_single_condition(self):
        m = _load()
        r = "[team_won] opponent_strength_bucket=lt_40(+0.115)"
        assert m.parse_reason_conditions(r) == {"opponent_strength_bucket": "lt_40"}

    def test_multiple_conditions(self):
        m = _load()
        r = "[team_won] opponent_strength_bucket=lt_40(+0.115)|[team_runs_4plus] tag_weak_leader_fade_watch=yes(+0.081)"
        result = m.parse_reason_conditions(r)
        assert result.get("opponent_strength_bucket") == "lt_40"
        assert result.get("tag_weak_leader_fade_watch") == "yes"

    def test_compound_key_value(self):
        m = _load()
        r = "[team_won] team_strength_bucket+opponent_strength_bucket=50_55__lt_40(+0.123)"
        result = m.parse_reason_conditions(r)
        # compound keys use + in key name; value contains __
        assert "team_strength_bucket+opponent_strength_bucket" in result

    def test_empty_string_returns_empty(self):
        m = _load()
        assert m.parse_reason_conditions("") == {}

    def test_nan_string_returns_empty(self):
        m = _load()
        assert m.parse_reason_conditions("nan") == {}

    def test_deduplicates_same_key(self):
        m = _load()
        # Same key appears twice with different outcomes — last wins or first wins, must not crash
        r = "[team_won] opponent_strength_bucket=lt_40(+0.1)|[team_runs_4plus] opponent_strength_bucket=lt_40(+0.09)"
        result = m.parse_reason_conditions(r)
        assert result.get("opponent_strength_bucket") == "lt_40"


class TestConsistencyLabel:
    def test_consistent_positive_all_above(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": 0.60, "2024": 0.62, "2025": 0.61},
            baseline=0.50,
            min_lift=0.03,
        )
        assert label == "consistent_positive"

    def test_mixed_when_one_season_below_baseline(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": 0.60, "2024": 0.62, "2025": 0.47},
            baseline=0.50,
            min_lift=0.03,
        )
        assert label == "mixed"

    def test_negative_all_below(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": 0.47, "2024": 0.48, "2025": 0.46},
            baseline=0.50,
            min_lift=0.03,
        )
        assert label == "negative"

    def test_insufficient_sample(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": None, "2024": None, "2025": 0.65},
            baseline=0.50,
            min_lift=0.03,
        )
        assert label == "insufficient_sample"
```

**Shell:** `python -m pytest tests/test_moneyline_audit.py -v`
Expected: all 10 skip (script not yet created).

---

## Step 2 — Implement `pregame_moneyline_logic_audit.py`

**File:** `pregame_moneyline_logic_audit.py`

```python
"""
pregame_moneyline_logic_audit.py

Read-only audit of the pregame brain's moneyline sub-lanes.
No model changes. No trades. No paper entries.

Usage:
    python pregame_moneyline_logic_audit.py
    python pregame_moneyline_logic_audit.py --seasons 2023 2024 2025
"""
import argparse
import csv
import math
import re
from collections import defaultdict
from pathlib import Path

CARD_CSV  = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
CALIB_CSV = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")
OUT_DIR   = Path("outputs/pregame_moneyline_logic_audit")

HISTORICAL_SEASONS = {"2023", "2024", "2025"}
SCORE_BINS = [
    ("<0.00",     -math.inf, 0.00),
    ("0.00-0.10",  0.00,     0.10),
    ("0.10-0.20",  0.10,     0.20),
    ("0.20-0.30",  0.20,     0.30),
    ("0.30-0.40",  0.30,     0.40),
    ("0.40+",      0.40,     math.inf),
]
MIN_PROMISING_TOTAL   = 100
MIN_PROMISING_SEASON  = 25
MIN_LIFT_PP           = 3.0  # percentage points

# Regex for parsing reason strings
_REASON_RE = re.compile(r"\[.*?\]\s*([\w+_]+)=([\w._+-]+)")

# Sub-lane conditions to test — (key, value, display_label)
# Sourced from top reason keys observed in data
SUBLANE_CONDITIONS = [
    # Home/away
    ("home_away", "home",  "home_game"),
    ("home_away", "away",  "away_game"),
    # Bullpen context (direct CSV columns, not parsed from reasons)
    ("bo_bucket_direct", "very_high_115_plus", "BO_very_high"),
    ("bo_bucket_direct", "very_low_lt_85",     "BO_very_low"),
    ("bd_bucket_direct", "very_high_115_plus", "BD_very_high"),
    ("bd_bucket_direct", "very_low_lt_85",     "BD_very_low"),
    ("bo_plus_weak_bd_tag_direct", "yes",      "BO_plus_weak_BD"),
    # Parsed from reasons
    ("opponent_strength_bucket",    "lt_40",        "opp_weak_lt40"),
    ("team_strength_gap_bucket",    "plus_10_plus",  "strength_gap_plus10"),
    ("tag_weak_leader_fade_watch",  "yes",           "tag_weak_leader"),
    ("tag_live_rebound_watch",      "yes",           "tag_live_rebound"),
    ("tag_strong_offense_vs_vulnerable_starter", "yes", "tag_strong_vs_vuln_starter"),
    ("tag_home_scoring_spot",       "yes",           "tag_home_scoring"),
    ("opponent_starter_xfip_bucket","excellent_lt_3_75", "opp_starter_excellent"),
    ("opponent_starter_xfip_bucket","very_bad_5_25_plus","opp_starter_very_bad"),
]
```

The script body (pure functions section):

```python
# ── Pure functions ──────────────────────────────────────────────────────────

def parse_reason_conditions(reasons: str) -> dict[str, str]:
    """Extract {key: value} dict from a reasons string. Last value wins for duplicate keys."""
    if not reasons or reasons.strip().lower() in {"", "nan", "none"}:
        return {}
    result = {}
    for m in _REASON_RE.finditer(reasons):
        result[m.group(1)] = m.group(2).strip()
    return result


def consistency_label(
    season_rates: dict[str, float | None],
    baseline: float,
    min_lift: float = 0.03,
) -> str:
    """
    Classify season-by-season hit rate pattern.
    Requires all seasons to have a rate (not None) to be labeled consistent/negative.
    """
    valid = {s: r for s, r in season_rates.items() if r is not None}
    if len(valid) < 2:
        return "insufficient_sample"
    if all(r >= baseline + min_lift for r in valid.values()):
        return "consistent_positive"
    if all(r < baseline for r in valid.values()):
        return "negative"
    return "mixed"


def shrink_prob(hits: int, n: int, baseline: float, shrink_n: int = 100) -> float:
    """Conservative probability using shrinkage toward baseline."""
    return (hits + baseline * shrink_n) / (n + shrink_n)


def _f(v) -> float | None:
    try:
        s = str(v).strip()
        return None if not s or s.lower() in {"", "nan", "none"} else float(s)
    except Exception:
        return None


def _i(v) -> int | None:
    f = _f(v)
    return None if f is None else int(round(f))
```

The `get_condition_value(row, cond_key, parsed_conditions)` function:

```python
def get_condition_value(
    row: dict,
    cond_key: str,
    parsed: dict[str, str],
) -> str | None:
    """Return the value for a condition from the row, handling direct vs parsed sources."""
    if cond_key == "home_away":
        return row.get("home_away")
    if cond_key == "bo_bucket_direct":
        return row.get("bo_bucket")
    if cond_key == "bd_bucket_direct":
        return row.get("bd_bucket")
    if cond_key == "bo_plus_weak_bd_tag_direct":
        return row.get("bo_plus_weak_bd_tag")
    return parsed.get(cond_key)
```

**Section 1 — Broad moneyline sanity:**
For each of side and side_fade, compute over all historical rows:
- total rows, hit rate, baseline (home 53.1%, away 46.8%, all 49.9%)
- hit rate by season (2023/2024/2025)
- lift vs baseline

**Section 2 — Score-bin validation:**
For each bin in SCORE_BINS, for side and side_fade:
- n, hits, hit_rate, baseline, lift
- season splits (hit rate for each of 2023/2024/2025)
- conservative_probability (shrinkage toward baseline)
- consistency_label()

**Section 3 — Sub-lane breakdowns:**
For side score >= 0.20 threshold:
For each condition in SUBLANE_CONDITIONS:
1. Split rows into has_condition / no_condition
2. Compute hit rate for has_condition vs no_condition
3. Compute season splits for has_condition
4. Compute consistency_label
5. Compute lift vs lane baseline (not overall baseline — compare to all side>=0.20 rows)

Also do the key combinations:
- HOME + side>=0.40
- AWAY + side>=0.40
- HOME + opp_weak_lt40 + side>=0.40
- HOME + NOT opp_weak_lt40 + side>=0.40

**Section 4 — Favorite detector check:**
For side>=0.40, split into:
- "obvious_favorite": opp_strength_bucket=lt_40 (proxy, labeled as approximate)
- "not_obvious_favorite": everything else
- "strong_gap": team_strength_gap_bucket=plus_10_plus
Show hit rate for each, with season splits, and plain-English answer to each of the 5 questions.

**Section 5 — Core lane recommendation:**
Algorithmic: apply the thresholds from spec:
- MIN_PROMISING_TOTAL = 100
- MIN_PROMISING_SEASON = 25 (where available)
- MIN_LIFT_PP = +3.0pp over relevant baseline
- consistency_label == "consistent_positive"
Output top-3 promising, top-3 suppress, whether moneyline is main training lane, and what conditions should define Moneyline Disagreement v1.

**Section 6 — Future market prep:**
A note section in the markdown listing what fields will be added when odds data arrives:
```
brain_probability   (from calibrated_probability field in EV overlay)
kalshi_ask          (from orderbook snapshot)
implied_edge        (brain_probability * 100 - kalshi_ask)
market_disagreement (brain > kalshi + threshold)
```

**Output generation:**

`moneyline_sublanes.csv` — columns:
```
lane, condition, condition_value, score_threshold, n, hit_rate, baseline, lift_pp,
hit_rate_2023, n_2023, hit_rate_2024, n_2024, hit_rate_2025, n_2025,
worst_season_rate, consistency_label, conservative_prob, notes
```

`moneyline_season_splits.csv` — columns:
```
lane, score_bin, season, n, hit_rate, baseline, lift_pp, conservative_prob
```

`moneyline_logic_summary.md` — full markdown report with:
- Plain-English answers to all 5 spec questions
- Section 1 table
- Section 2 table
- Section 3 table
- Section 4 table
- Section 5 recommendations
- Section 6 future prep note

---

## Step 3 — Run tests and verify

```bash
python -m pytest tests/test_moneyline_audit.py -v
```
All 10 tests must pass before continuing.

```bash
python -m pytest tests/test_probability_calibration.py tests/test_actuals_enrichment.py -v
```
All 48 existing tests must still pass.

```bash
python pregame_moneyline_logic_audit.py
```
Verify outputs exist:
- `outputs/pregame_moneyline_logic_audit/moneyline_logic_summary.md`
- `outputs/pregame_moneyline_logic_audit/moneyline_sublanes.csv`
- `outputs/pregame_moneyline_logic_audit/moneyline_season_splits.csv`
- `outputs/pregame_moneyline_logic_audit/latest_moneyline_logic_summary.md`
- `outputs/pregame_moneyline_logic_audit/latest_moneyline_sublanes.csv`

---

## Quality Gates

1. No calls to external APIs or DB
2. No writes to card CSV or calibration CSV
3. No model changes
4. All hit rates expressed with n shown — never standalone percentages
5. Any sub-lane with n < MIN_PROMISING_TOTAL must be labeled "insufficient_sample" in the recommendation section
6. The word "guaranteed" must not appear in any output
7. The word "tradeable" must not appear in any output

---

## Expected Key Outputs (from pre-research)

These are the expected findings the script should confirm (not hard-coded — the script re-derives them):

| Sub-lane | n | Hit rate | Baseline | Lift | Verdict |
|----------|---|----------|----------|------|---------|
| side>=0.40 all | 2445 | 60.8% | 49.9% | +10.9pp | strong (but test sub-lanes) |
| side>=0.40 HOME | 1510 | 63.4% | 53.1% | +10.3pp | promising, consistent |
| side>=0.40 AWAY | 935 | 56.6% | 46.8% | +9.8pp | WARNING: 2025 dropped to 52.2% |
| side>=0.40 HOME+opp_lt40 | 390 | 68.5% | 53.1% | +15.4pp | top lane, consistent |
| side>=0.40 HOME+NOT_opp_lt40 | 1120 | 61.7% | 53.1% | +8.6pp | promising, consistent |
| tag_weak_leader at side>=0.40 | 1268 | 59.7% | 60.8% | -1.1pp | BELOW sub-baseline — suppress |
| tag_live_rebound at side>=0.40 | 287 | 55.4% | 60.8% | -5.4pp | well below — suppress |

Favorite detector answer: The brain has edge WITHOUT weak opponents (HOME+NOT_opp_lt40 at 61.7% vs 53.1% home baseline = +8.6pp). It is NOT purely a favorite detector.

---

## Execution Mode

**Inline Execution** — 3 steps in the current session. The script is a single file with no external deps; no subagent needed.
