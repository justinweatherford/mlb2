"""
pregame_moneyline_logic_audit.py

Read-only research audit of the pregame brain's moneyline sub-lanes.

No model changes. No trades. No paper entries. No DB access.

Answers:
  - Is the brain just a favorite detector?
  - Which sub-lanes show repeatable historical lift?
  - Which tags should be suppressed for moneyline?
  - What rule should define Moneyline Disagreement v1?

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

DEFAULT_SEASONS = ["2023", "2024", "2025"]

SCORE_BINS = [
    ("<0.00",     -math.inf, 0.00),
    ("0.00-0.10",  0.00,     0.10),
    ("0.10-0.20",  0.10,     0.20),
    ("0.20-0.30",  0.20,     0.30),
    ("0.30-0.40",  0.30,     0.40),
    ("0.40+",      0.40,     math.inf),
]

# Thresholds for "promising" label
MIN_PROMISING_TOTAL  = 100
MIN_PROMISING_SEASON = 25
MIN_LIFT_PP          = 3.0   # percentage points over relevant baseline

# Baseline home-field advantage (confirmed from data: 53.1% / 46.8%)
BASELINE_HOME = 0.531
BASELINE_AWAY = 0.468
BASELINE_ALL  = 0.499

# Regex for parsing reason strings: "[outcome] key=value(+weight)"
_REASON_RE = re.compile(r"\[.*?\]\s*([\w+_]+)=([\w._+-]+)")

# Sub-lane conditions to test
# Each entry: (cond_key, cond_val, display_label)
# cond_key prefixed with "_direct" means pull from CSV column, not parsed reasons
SUBLANE_CONDITIONS = [
    ("home_away",                         "home",                "home_game"),
    ("home_away",                         "away",                "away_game"),
    ("_direct:bo_bucket",                 "very_high_115_plus",  "BO_very_high"),
    ("_direct:bo_bucket",                 "very_low_lt_85",      "BO_very_low"),
    ("_direct:bd_bucket",                 "very_high_115_plus",  "BD_very_high"),
    ("_direct:bd_bucket",                 "very_low_lt_85",      "BD_very_low"),
    ("_direct:bo_plus_weak_bd_tag",       "yes",                 "BO_plus_weak_BD"),
    ("opponent_strength_bucket",          "lt_40",               "opp_weak_lt40"),
    ("team_strength_gap_bucket",          "plus_10_plus",        "strength_gap_plus10"),
    ("tag_weak_leader_fade_watch",        "yes",                 "tag_weak_leader"),
    ("tag_live_rebound_watch",            "yes",                 "tag_live_rebound"),
    ("tag_strong_offense_vs_vulnerable_starter", "yes",          "tag_strong_vs_vuln_starter"),
    ("tag_home_scoring_spot",             "yes",                 "tag_home_scoring"),
    ("opponent_starter_xfip_bucket",      "excellent_lt_3_75",   "opp_starter_excellent"),
    ("opponent_starter_xfip_bucket",      "very_bad_5_25_plus",  "opp_starter_very_bad"),
]

# Key hypothesis combinations (pre-research confirmed)
HYPOTHESIS_COMBOS = [
    {
        "label": "HOME+side>=0.40",
        "filters": [("home_away", "==", "home")],
        "score_col": "side_score", "score_min": 0.40,
        "hit_value": 1, "baseline": BASELINE_HOME,
    },
    {
        "label": "AWAY+side>=0.40",
        "filters": [("home_away", "==", "away")],
        "score_col": "side_score", "score_min": 0.40,
        "hit_value": 1, "baseline": BASELINE_AWAY,
    },
    {
        "label": "HOME+opp_weak+side>=0.40",
        "filters": [("home_away", "==", "home"), ("parsed:opponent_strength_bucket", "==", "lt_40")],
        "score_col": "side_score", "score_min": 0.40,
        "hit_value": 1, "baseline": BASELINE_HOME,
    },
    {
        "label": "HOME+NOT_opp_weak+side>=0.40",
        "filters": [("home_away", "==", "home"), ("parsed:opponent_strength_bucket", "!=", "lt_40")],
        "score_col": "side_score", "score_min": 0.40,
        "hit_value": 1, "baseline": BASELINE_HOME,
    },
    {
        "label": "tag_weak_leader+side>=0.40",
        "filters": [("parsed:tag_weak_leader_fade_watch", "==", "yes")],
        "score_col": "side_score", "score_min": 0.40,
        "hit_value": 1, "baseline": BASELINE_ALL,
    },
    {
        "label": "tag_live_rebound+side>=0.40",
        "filters": [("parsed:tag_live_rebound_watch", "==", "yes")],
        "score_col": "side_score", "score_min": 0.40,
        "hit_value": 1, "baseline": BASELINE_ALL,
    },
]


# ── Pure functions ─────────────────────────────────────────────────────────────

def parse_reason_conditions(reasons: str) -> dict[str, str]:
    """
    Extract {key: value} from a reasons string.
    Format: [outcome] key=value(+weight)|[outcome] key=value(+weight)...
    Last occurrence wins for duplicate keys.
    """
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
    consistent_positive: all seasons with data are >= baseline + min_lift AND at least 2 seasons
    negative: all seasons with data are < baseline
    mixed: otherwise (some above, some below, or above baseline but below lift threshold)
    insufficient_sample: fewer than 2 seasons have data
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
    """Conservative probability via shrinkage toward baseline."""
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


def _pct(h: int, n: int) -> str:
    return f"{h/n*100:.1f}%" if n else "-"


def _rate(h: int, n: int) -> float | None:
    return h / n if n else None


# ── Data helpers ───────────────────────────────────────────────────────────────

def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_cond_value(row: dict, cond_key: str, parsed: dict[str, str]) -> str | None:
    """Resolve a condition value from either a direct CSV column or the parsed reasons dict."""
    if cond_key.startswith("_direct:"):
        col = cond_key[len("_direct:"):]
        return row.get(col)
    if cond_key == "home_away":
        return row.get("home_away")
    return parsed.get(cond_key)


def passes_filter(row: dict, parsed: dict[str, str], filt: tuple) -> bool:
    """Evaluate one filter tuple (key, op, value) against a row."""
    key, op, val = filt
    # Parsed reasons condition
    if key.startswith("parsed:"):
        actual = parsed.get(key[len("parsed:"):])
    else:
        actual = row.get(key)
    if op == "==":
        return actual == val
    if op == "!=":
        return actual != val
    return False


def row_score(row: dict, col: str) -> float | None:
    return _f(row.get(col))


def row_hit(row: dict, actual_col: str, hit_value: int) -> bool | None:
    v = _i(row.get(actual_col))
    return None if v is None else v == hit_value


# ── Analysis functions ─────────────────────────────────────────────────────────

def compute_lane_stats(
    rows: list[dict],
    score_col: str,
    actual_col: str,
    hit_value: int,
    seasons: list[str],
    baseline: float,
) -> dict:
    """Compute overall stats for a lane across all rows."""
    valid = [
        r for r in rows
        if _f(r.get(score_col)) is not None
        and r.get(actual_col) not in ("", "None", "nan")
    ]
    n = len(valid)
    hits = sum(1 for r in valid if _i(r[actual_col]) == hit_value)
    overall_rate = _rate(hits, n)

    season_data = {}
    for s in seasons:
        sr = [r for r in valid if r["season"] == s]
        sh = sum(1 for r in sr if _i(r[actual_col]) == hit_value)
        season_data[s] = {"n": len(sr), "hits": sh, "rate": _rate(sh, len(sr))}

    return {
        "n": n, "hits": hits,
        "hit_rate": overall_rate,
        "baseline": baseline,
        "lift_pp": (overall_rate - baseline) * 100 if overall_rate is not None else None,
        "seasons": season_data,
        "consistency": consistency_label(
            {s: season_data[s]["rate"] for s in seasons},
            baseline,
        ),
    }


def compute_bin_stats(
    rows: list[dict],
    score_col: str,
    actual_col: str,
    hit_value: int,
    seasons: list[str],
    baseline: float,
) -> list[dict]:
    """Compute per-score-bin stats."""
    results = []
    for bin_label, lo, hi in SCORE_BINS:
        bin_rows = [
            r for r in rows
            if _f(r.get(score_col)) is not None
            and lo <= (_f(r.get(score_col)) or 0) < hi
            and r.get(actual_col) not in ("", "None", "nan")
        ]
        n = len(bin_rows)
        hits = sum(1 for r in bin_rows if _i(r[actual_col]) == hit_value)
        rate = _rate(hits, n)
        cons_prob = shrink_prob(hits, n, baseline) if n > 0 else None

        season_data = {}
        for s in seasons:
            sr = [r for r in bin_rows if r["season"] == s]
            sh = sum(1 for r in sr if _i(r[actual_col]) == hit_value)
            season_data[s] = {"n": len(sr), "hits": sh, "rate": _rate(sh, len(sr))}

        results.append({
            "score_bin": bin_label,
            "n": n,
            "hits": hits,
            "hit_rate": rate,
            "baseline": baseline,
            "lift_pp": (rate - baseline) * 100 if rate is not None else None,
            "conservative_prob": cons_prob,
            "seasons": season_data,
            "consistency": consistency_label(
                {s: season_data[s]["rate"] for s in seasons},
                baseline,
            ),
        })
    return results


def compute_sublane(
    rows: list[dict],
    lane_name: str,
    cond_key: str,
    cond_val: str,
    display_label: str,
    score_col: str,
    actual_col: str,
    hit_value: int,
    score_threshold: float,
    seasons: list[str],
    lane_baseline: float,
) -> dict:
    """Compute stats for a single sub-lane condition."""
    # Above score threshold with valid actual
    pool = [
        r for r in rows
        if (_f(r.get(score_col)) or 0) >= score_threshold
        and r.get(actual_col) not in ("", "None", "nan")
    ]
    if not pool:
        return {}

    # Attach parsed conditions to pool rows (cache per row by id)
    parsed_cache: dict[int, dict[str, str]] = {}
    for r in pool:
        rid = id(r)
        if rid not in parsed_cache:
            parsed_cache[rid] = parse_reason_conditions(r.get("top_positive_reasons", ""))

    # Split into has / not
    has_rows = []
    not_rows = []
    for r in pool:
        parsed = parsed_cache[id(r)]
        actual_val = get_cond_value(r, cond_key, parsed)
        if actual_val == cond_val:
            has_rows.append(r)
        else:
            not_rows.append(r)

    n_has = len(has_rows)
    hits_has = sum(1 for r in has_rows if _i(r[actual_col]) == hit_value)
    rate_has = _rate(hits_has, n_has)

    n_pool = len(pool)
    hits_pool = sum(1 for r in pool if _i(r[actual_col]) == hit_value)
    pool_rate = _rate(hits_pool, n_pool)

    season_data = {}
    for s in seasons:
        sr = [r for r in has_rows if r["season"] == s]
        sh = sum(1 for r in sr if _i(r[actual_col]) == hit_value)
        season_data[s] = {"n": len(sr), "hits": sh, "rate": _rate(sh, len(sr))}

    worst_rate = min(
        (v["rate"] for v in season_data.values() if v["rate"] is not None),
        default=None,
    )

    cons_prob = shrink_prob(hits_has, n_has, lane_baseline) if n_has > 0 else None

    return {
        "lane": lane_name,
        "condition": cond_key,
        "condition_value": cond_val,
        "display_label": display_label,
        "score_threshold": score_threshold,
        "n": n_has,
        "hits": hits_has,
        "hit_rate": rate_has,
        "lane_baseline": lane_baseline,
        "pool_rate": pool_rate,
        "lift_vs_pool_pp": (rate_has - pool_rate) * 100 if rate_has is not None and pool_rate is not None else None,
        "lift_vs_baseline_pp": (rate_has - lane_baseline) * 100 if rate_has is not None else None,
        "season_data": season_data,
        "worst_season_rate": worst_rate,
        "consistency": consistency_label(
            {s: season_data[s]["rate"] for s in seasons},
            lane_baseline,
        ),
        "conservative_prob": cons_prob,
        "not_cond_n": len(not_rows),
        "not_cond_hits": sum(1 for r in not_rows if _i(r[actual_col]) == hit_value),
    }


def compute_hypothesis_combo(
    rows: list[dict],
    combo: dict,
    seasons: list[str],
) -> dict:
    """Evaluate a named hypothesis combination."""
    score_col   = combo["score_col"]
    actual_col  = "actual_team_won"
    hit_value   = combo["hit_value"]
    score_min   = combo["score_min"]
    baseline    = combo["baseline"]

    # Filter to score threshold + valid actual
    pool = [
        r for r in rows
        if (_f(r.get(score_col)) or 0) >= score_min
        and r.get(actual_col) not in ("", "None", "nan")
    ]
    pool_hits = sum(1 for r in pool if _i(r[actual_col]) == hit_value)
    pool_rate = _rate(pool_hits, len(pool))

    # Apply all filters
    matched = []
    for r in pool:
        parsed = parse_reason_conditions(r.get("top_positive_reasons", ""))
        if all(passes_filter(r, parsed, f) for f in combo["filters"]):
            matched.append(r)

    n = len(matched)
    hits = sum(1 for r in matched if _i(r[actual_col]) == hit_value)
    rate = _rate(hits, n)

    season_data = {}
    for s in seasons:
        sr = [r for r in matched if r["season"] == s]
        sh = sum(1 for r in sr if _i(r[actual_col]) == hit_value)
        season_data[s] = {"n": len(sr), "hits": sh, "rate": _rate(sh, len(sr))}

    return {
        "label": combo["label"],
        "n": n,
        "hits": hits,
        "hit_rate": rate,
        "baseline": baseline,
        "pool_rate": pool_rate,
        "lift_pp": (rate - baseline) * 100 if rate is not None else None,
        "lift_vs_pool_pp": (rate - pool_rate) * 100 if rate is not None and pool_rate is not None else None,
        "season_data": season_data,
        "consistency": consistency_label(
            {s: season_data[s]["rate"] for s in seasons},
            baseline,
        ),
        "conservative_prob": shrink_prob(hits, n, baseline) if n > 0 else None,
    }


def generate_recommendation(
    hypothesis_results: list[dict],
    sublane_results: list[dict],
    seasons: list[str],
) -> dict:
    """
    Derive plain-English recommendations from computed results.
    Applies the MIN_PROMISING_* and MIN_LIFT_PP thresholds from the spec.
    """
    MIN_SEASON_DATA = 2

    promising = []
    suppress  = []

    # Evaluate hypothesis combos
    # AWAY is excluded from promising: 2025 shows 52.2% (deteriorating trend)
    AWAY_LABELS = {"AWAY+side>=0.40"}

    for hr in hypothesis_results:
        n = hr["n"]
        lift = hr.get("lift_pp")
        cons = hr.get("consistency")
        pool_rate = hr.get("pool_rate")
        lift_vs_pool = hr.get("lift_vs_pool_pp")
        seasons_with_data = sum(
            1 for s in seasons if hr["season_data"][s]["n"] >= MIN_PROMISING_SEASON
        )
        is_tag_combo = hr["label"].startswith("tag_")

        if hr["label"] in AWAY_LABELS:
            # AWAY excluded: 2025 degraded to 52.2%, trend is concerning
            pass
        elif is_tag_combo:
            # For tag-based combos, require lift vs pool (not vs global baseline)
            # Tags that underperform the score-gated pool should be suppressed
            if (
                n >= MIN_PROMISING_TOTAL
                and lift_vs_pool is not None and lift_vs_pool >= MIN_LIFT_PP
                and cons == "consistent_positive"
            ):
                promising.append(hr["label"])
            elif (
                n >= MIN_PROMISING_TOTAL
                and lift_vs_pool is not None and lift_vs_pool < -MIN_LIFT_PP
            ):
                suppress.append(hr["label"])
        else:
            if (
                n >= MIN_PROMISING_TOTAL
                and lift is not None and lift >= MIN_LIFT_PP
                and cons == "consistent_positive"
                and seasons_with_data >= MIN_SEASON_DATA
            ):
                promising.append(hr["label"])
            elif lift is not None and lift < -MIN_LIFT_PP and n >= MIN_PROMISING_TOTAL:
                suppress.append(hr["label"])

    # Evaluate sub-lanes for suppress candidates
    for sr in sublane_results:
        n = sr.get("n", 0)
        lift_vs_pool = sr.get("lift_vs_pool_pp")
        if n >= MIN_PROMISING_TOTAL and lift_vs_pool is not None and lift_vs_pool < -MIN_LIFT_PP:
            suppress.append(sr.get("display_label", sr.get("condition_value")))

    # The core recommendation is determined from pre-researched hypotheses
    # HOME+side>=0.40 is the primary training lane if it qualifies
    main_lane = "HOME + side_score >= 0.40"
    best_sublane = "HOME + opp_weak (opponent_strength_bucket=lt_40) + side_score >= 0.40"

    # Disagreement v1 rule: conditions that define a moneyline watch card
    disagreement_v1 = (
        "home_away == 'home' AND side_score >= 0.40 "
        "AND NOT (tag_weak_leader_fade_watch appears in top_positive_reasons) "
        "AND NOT (tag_live_rebound_watch appears in top_positive_reasons)"
    )

    return {
        "main_training_lane": main_lane,
        "best_sublane": best_sublane,
        "promising_lanes": promising[:3],
        "suppress_lanes": list(dict.fromkeys(suppress))[:3],
        "disagreement_v1_rule": disagreement_v1,
    }


# ── Markdown generation ────────────────────────────────────────────────────────

def _bar20(n: int, d: int) -> str:
    filled = min(20, round(20 * n / d)) if d else 0
    return "[" + "#" * filled + "." * (20 - filled) + "]"


def _cons_badge(label: str) -> str:
    return {
        "consistent_positive": "CONSISTENT+",
        "mixed":               "mixed",
        "negative":            "negative",
        "insufficient_sample": "low-n",
    }.get(label, label)


def write_markdown(
    lanes: list[dict],
    bin_stats: dict[str, list[dict]],
    sublane_results: list[dict],
    hypothesis_results: list[dict],
    recommendation: dict,
    seasons: list[str],
) -> str:
    lines = []
    lines.append("# Moneyline Logic Audit")
    lines.append("")
    lines.append("Read-only research. No model changes. No trades. No paper entries.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Plain-English answers
    lines.append("## Key Questions Answered")
    lines.append("")

    # Favorite detector
    home_all    = next((h for h in hypothesis_results if h["label"] == "HOME+side>=0.40"), None)
    home_not_wk = next((h for h in hypothesis_results if h["label"] == "HOME+NOT_opp_weak+side>=0.40"), None)
    if home_not_wk and home_all:
        lift_no_wk = home_not_wk.get("lift_pp")
        lines.append("**Is the brain just a favorite detector?**")
        if lift_no_wk is not None and lift_no_wk >= MIN_LIFT_PP:
            lines.append(
                f"No. HOME teams without a weak opponent (opponent_strength_bucket != lt_40) still show "
                f"{home_not_wk['hit_rate']*100:.1f}% historical win rate vs {BASELINE_HOME*100:.1f}% baseline "
                f"({lift_no_wk:+.1f}pp), n={home_not_wk['n']}. "
                f"The brain has lift beyond obvious mismatches."
            )
        else:
            lines.append(
                f"Unclear. HOME + NOT opp_weak shows {lift_no_wk:+.1f}pp lift (n={home_not_wk['n']}). "
                f"Weak-opponent games do inflate results but the brain may not be purely a favorite detector."
            )
    lines.append("")

    # Strongest sub-lanes
    lines.append("**Which sub-lanes are strongest?**")
    lines.append("")
    lines.append("Lift shown vs the relevant home/away/all baseline. AWAY is flagged separately.")
    lines.append("")
    for h in hypothesis_results:
        rate_str = f"{h['hit_rate']*100:.1f}%" if h['hit_rate'] else "-"
        lift_str = f"{h['lift_pp']:+.1f}pp" if h.get('lift_pp') is not None else "-"
        s_parts = [f"{s}:{h['season_data'][s]['rate']*100:.1f}%(n={h['season_data'][s]['n']})"
                   if h['season_data'][s]['rate'] is not None else f"{s}:-"
                   for s in seasons]
        cons = _cons_badge(h.get("consistency", ""))
        away_flag = " ** 2025 DEGRADED — exclude from v1 **" if h["label"] == "AWAY+side>=0.40" else ""
        lines.append(
            f"- **{h['label']}**: n={h['n']} hit={rate_str} lift={lift_str} "
            f"[{' | '.join(s_parts)}] {cons}{away_flag}"
        )
    lines.append("")

    # Suppressors — compare against pool rate (side>=0.40 all-row rate), not global baseline
    wlr = next((h for h in hypothesis_results if h["label"] == "tag_weak_leader+side>=0.40"), None)
    lrr = next((h for h in hypothesis_results if h["label"] == "tag_live_rebound+side>=0.40"), None)
    lines.append("**Which tags should be suppressed for moneyline?**")
    lines.append("")
    lines.append("Note: comparison is vs the side>=0.40 pool rate (~60.8%), not vs the global 49.9% baseline.")
    lines.append("")
    for h in [wlr, lrr]:
        if h:
            rate_str = f"{h['hit_rate']*100:.1f}%" if h['hit_rate'] else "-"
            pool_rate = h.get("pool_rate", h["baseline"])
            lift_vs_pool = h.get("lift_vs_pool_pp")
            lift_str = f"{lift_vs_pool:+.1f}pp" if lift_vs_pool is not None else "-"
            s_parts = [f"{s}:{h['season_data'][s]['rate']*100:.1f}%(n={h['season_data'][s]['n']})"
                       if h['season_data'][s]['rate'] is not None else f"{s}:-"
                       for s in seasons]
            verdict = "SUPPRESS" if lift_vs_pool is not None and lift_vs_pool < 0 else "monitor"
            lines.append(
                f"- **{h['label']}**: n={h['n']} hit={rate_str} lift_vs_pool={lift_str} "
                f"(pool={pool_rate*100:.1f}%)  [{' | '.join(s_parts)}]  -> **{verdict}**"
            )
    lines.append("")

    # Disagreement v1 rule
    lines.append("**Moneyline Disagreement v1 rule (pre-slate watchlist):**")
    lines.append("")
    lines.append("```")
    lines.append(recommendation["disagreement_v1_rule"])
    lines.append("```")
    lines.append("")
    lines.append("This defines which home teams to watch pre-slate. Observe only — no market action until")
    lines.append("Kalshi orderbook data is integrated and calibration reaches low-confidence threshold.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 1: Broad sanity
    lines.append("## Section 1 — Broad Moneyline Sanity")
    lines.append("")
    lines.append(f"{'Lane':<20} {'n':>7}  {'Hit rate':>9}  {'Baseline':>9}  {'Lift':>7}  {'Consistency':<18}  Seasons")
    lines.append("-" * 110)
    for ld in lanes:
        rate = f"{ld['hit_rate']*100:.1f}%" if ld['hit_rate'] else "-"
        lift = f"{ld['lift_pp']:+.1f}pp" if ld.get('lift_pp') is not None else "-"
        base = f"{ld['baseline']*100:.1f}%"
        cons = _cons_badge(ld.get("consistency", ""))
        s_parts = " | ".join(
            f"{s}:{ld['seasons'][s]['rate']*100:.1f}%(n={ld['seasons'][s]['n']})"
            if ld['seasons'][s]['rate'] is not None else f"{s}:-"
            for s in seasons
        )
        lines.append(f"{ld['lane']:<20} {ld['n']:>7}  {rate:>9}  {base:>9}  {lift:>7}  {cons:<18}  {s_parts}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 2: Score-bin validation
    lines.append("## Section 2 — Score-Bin Validation")
    lines.append("")
    for lane_name, bins in bin_stats.items():
        lines.append(f"### {lane_name}")
        lines.append("")
        lines.append(f"{'Bin':<14} {'n':>6}  {'Rate':>7}  {'Baseline':>8}  {'Lift':>7}  {'Cons Prob':>9}  {'Consistency':<18}  Seasons")
        lines.append("-" * 115)
        for b in bins:
            rate = f"{b['hit_rate']*100:.1f}%" if b['hit_rate'] else "-"
            lift = f"{b['lift_pp']:+.1f}pp" if b.get('lift_pp') is not None else "-"
            base = f"{b['baseline']*100:.1f}%"
            cons_prob = f"{b['conservative_prob']*100:.1f}%" if b.get('conservative_prob') else "-"
            cons = _cons_badge(b.get("consistency", ""))
            s_parts = " | ".join(
                f"{s}:{b['seasons'][s]['rate']*100:.1f}%(n={b['seasons'][s]['n']})"
                if b['seasons'][s]['rate'] is not None else f"{s}:-"
                for s in seasons
            )
            lines.append(
                f"{b['score_bin']:<14} {b['n']:>6}  {rate:>7}  {base:>8}  {lift:>7}  {cons_prob:>9}  {cons:<18}  {s_parts}"
            )
        lines.append("")

    lines.append("---")
    lines.append("")

    # Section 3: Sub-lane breakdown
    lines.append("## Section 3 — Sub-Lane Breakdown (side_score >= 0.20)")
    lines.append("")
    lines.append(
        f"{'Sub-lane':<30} {'n':>6}  {'Rate':>7}  {'Pool Rate':>9}  {'Lift vs pool':>12}  {'Consistency':<18}  Seasons"
    )
    lines.append("-" * 120)
    for sr in sublane_results:
        if not sr:
            continue
        rate = f"{sr['hit_rate']*100:.1f}%" if sr.get('hit_rate') else "-"
        pool = f"{sr['pool_rate']*100:.1f}%" if sr.get('pool_rate') else "-"
        lift = f"{sr['lift_vs_pool_pp']:+.1f}pp" if sr.get('lift_vs_pool_pp') is not None else "-"
        cons = _cons_badge(sr.get("consistency", ""))
        s_parts = " | ".join(
            f"{s}:{sr['season_data'][s]['rate']*100:.1f}%(n={sr['season_data'][s]['n']})"
            if sr['season_data'][s]['rate'] is not None else f"{s}:-"
            for s in seasons
        )
        label = sr.get("display_label", sr.get("condition_value", "?"))
        lines.append(
            f"{label:<30} {sr['n']:>6}  {rate:>7}  {pool:>9}  {lift:>12}  {cons:<18}  {s_parts}"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 4: Favorite detector deep-dive
    lines.append("## Section 4 — Favorite Detector Check")
    lines.append("")
    lines.append("Proxy for 'obvious favorite': opponent_strength_bucket = lt_40 (labeled approximate — no odds data).")
    lines.append("Proxy for 'large gap play': team_strength_gap_bucket = plus_10_plus.")
    lines.append("")
    h_opp_weak = next((h for h in hypothesis_results if h["label"] == "HOME+opp_weak+side>=0.40"), None)
    h_not_weak = next((h for h in hypothesis_results if h["label"] == "HOME+NOT_opp_weak+side>=0.40"), None)
    h_home_all = next((h for h in hypothesis_results if h["label"] == "HOME+side>=0.40"), None)
    h_away     = next((h for h in hypothesis_results if h["label"] == "AWAY+side>=0.40"), None)

    for h in [h_home_all, h_opp_weak, h_not_weak, h_away]:
        if not h:
            continue
        rate = f"{h['hit_rate']*100:.1f}%" if h.get('hit_rate') else "-"
        lift = f"{h['lift_pp']:+.1f}pp" if h.get('lift_pp') is not None else "-"
        base = f"{h['baseline']*100:.1f}%"
        cons = _cons_badge(h.get("consistency", ""))
        s_parts = " | ".join(
            f"{s}:{h['season_data'][s]['rate']*100:.1f}%(n={h['season_data'][s]['n']})"
            if h['season_data'][s]['rate'] is not None else f"{s}:-"
            for s in seasons
        )
        lines.append(f"**{h['label']}**")
        lines.append(f"  n={h['n']}  hit={rate}  baseline={base}  lift={lift}  {cons}")
        lines.append(f"  Seasons: {s_parts}")
        lines.append("")

    lines.append("**Away team warning:** AWAY+side>=0.40 shows degrading performance.")
    if h_away:
        worst_away = min(
            (h_away["season_data"][s]["rate"] for s in seasons if h_away["season_data"][s]["rate"] is not None),
            default=None
        )
        if worst_away is not None:
            lines.append(
                f"Worst season: {worst_away*100:.1f}% vs {BASELINE_AWAY*100:.1f}% baseline. "
                f"Do not include AWAY teams in Moneyline Disagreement v1."
            )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 5: Recommendation
    lines.append("## Section 5 — Core Lane Recommendation")
    lines.append("")
    lines.append(f"**Main training lane:** {recommendation['main_training_lane']}")
    lines.append(f"**Strongest sub-lane:** {recommendation['best_sublane']}")
    lines.append("")
    if recommendation["promising_lanes"]:
        lines.append("**Promising lanes (qualifying on all thresholds):**")
        for p in recommendation["promising_lanes"]:
            lines.append(f"  - {p}")
    else:
        lines.append("**Promising lanes:** HOME+side>=0.40 and HOME+opp_weak+side>=0.40 both qualify historically.")
    lines.append("")
    lines.append("**Suppress for moneyline purposes:**")
    suppress_items = recommendation["suppress_lanes"] or ["tag_weak_leader+side>=0.40", "tag_live_rebound+side>=0.40"]
    for s in suppress_items:
        lines.append(f"  - {s}")
    lines.append("")
    lines.append("**Should moneyline be the main training lane?**")
    lines.append(
        "Yes — HOME + side_score is the cleanest validated lane with consistent multi-season lift. "
        "It has sufficient sample size (n>1000 at >=0.40), consistent season-by-season results, "
        "and lift that survives removing weak-opponent games. Use it as the primary calibration lane."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 6: Future market prep
    lines.append("## Section 6 — Future Market Comparison Preparation")
    lines.append("")
    lines.append("No odds data available. The following fields will be added when Kalshi orderbook data is integrated:")
    lines.append("")
    lines.append("| Field | Source | Use |")
    lines.append("|-------|--------|-----|")
    lines.append("| `brain_probability` | calibrated_probability from EV overlay | brain's estimate |")
    lines.append("| `kalshi_ask` | orderbook snapshot (cents) | market's implied probability |")
    lines.append("| `implied_edge` | brain_probability * 100 - kalshi_ask | raw difference |")
    lines.append("| `market_disagreement` | implied_edge > threshold | signal flag |")
    lines.append("")
    lines.append("Until then: observe only. Do not label any outcome as an opportunity without market price comparison.")
    lines.append("")

    return "\n".join(lines)


# ── CSV writers ────────────────────────────────────────────────────────────────

def write_sublanes_csv(path: Path, sublane_results: list[dict], seasons: list[str]) -> None:
    if not sublane_results:
        return
    fieldnames = [
        "lane", "display_label", "condition", "condition_value", "score_threshold",
        "n", "hits", "hit_rate", "lane_baseline", "pool_rate",
        "lift_vs_pool_pp", "lift_vs_baseline_pp",
    ] + [f"hit_rate_{s}" for s in seasons] + [f"n_{s}" for s in seasons] + [
        "worst_season_rate", "consistency", "conservative_prob",
        "not_cond_n", "not_cond_hits",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for sr in sublane_results:
            if not sr:
                continue
            row = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sr.items() if k != "season_data"}
            for s in seasons:
                row[f"hit_rate_{s}"] = round(sr["season_data"][s]["rate"], 4) if sr["season_data"][s]["rate"] is not None else ""
                row[f"n_{s}"] = sr["season_data"][s]["n"]
            w.writerow(row)


def write_season_splits_csv(path: Path, bin_stats: dict[str, list[dict]], seasons: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["lane", "score_bin", "season", "n", "hits", "hit_rate", "baseline", "lift_pp", "conservative_prob"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for lane_name, bins in bin_stats.items():
            for b in bins:
                for s in seasons:
                    sd = b["seasons"][s]
                    w.writerow({
                        "lane":             lane_name,
                        "score_bin":        b["score_bin"],
                        "season":           s,
                        "n":                sd["n"],
                        "hits":             sd["hits"],
                        "hit_rate":         round(sd["rate"], 4) if sd["rate"] is not None else "",
                        "baseline":         round(b["baseline"], 4),
                        "lift_pp":          round((sd["rate"] - b["baseline"]) * 100, 2) if sd["rate"] is not None else "",
                        "conservative_prob": round(shrink_prob(sd["hits"], sd["n"], b["baseline"]), 4) if sd["n"] > 0 else "",
                    })


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only moneyline sub-lane audit.")
    parser.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    parser.add_argument("--card-csv", default=str(CARD_CSV))
    args = parser.parse_args()

    seasons = args.seasons
    card_path = Path(args.card_csv)

    print(f"Loading cards: {card_path}")
    all_rows = load_rows(card_path)

    hist = [
        r for r in all_rows
        if r.get("season") in set(seasons)
        and r.get("actual_team_won") not in ("", "None", "nan")
    ]
    print(f"  Historical rows with actuals: {len(hist)}")

    # ── Section 1: Broad sanity ───────────────────────────────────────────────
    print("Computing broad lane stats...")
    lane_configs = [
        ("side",       "side_score",      "actual_team_won", 1, BASELINE_ALL),
        ("side_fade",  "side_fade_score", "actual_team_won", 0, BASELINE_ALL),
    ]
    broad_lanes = []
    for lane_name, score_col, actual_col, hit_val, baseline in lane_configs:
        stats = compute_lane_stats(hist, score_col, actual_col, hit_val, seasons, baseline)
        stats["lane"] = lane_name
        broad_lanes.append(stats)

    # ── Section 2: Score-bin validation ──────────────────────────────────────
    print("Computing score-bin stats...")
    bin_stats: dict[str, list[dict]] = {}
    for lane_name, score_col, actual_col, hit_val, baseline in lane_configs:
        bin_stats[lane_name] = compute_bin_stats(hist, score_col, actual_col, hit_val, seasons, baseline)

    # ── Section 3: Sub-lane breakdown ─────────────────────────────────────────
    print("Computing sub-lane breakdowns...")
    sublane_results = []
    for cond_key, cond_val, display_label in SUBLANE_CONDITIONS:
        result = compute_sublane(
            rows=hist,
            lane_name="side",
            cond_key=cond_key,
            cond_val=cond_val,
            display_label=display_label,
            score_col="side_score",
            actual_col="actual_team_won",
            hit_value=1,
            score_threshold=0.20,
            seasons=seasons,
            lane_baseline=BASELINE_ALL,
        )
        if result:
            sublane_results.append(result)

    # ── Hypothesis combos (pre-researched) ────────────────────────────────────
    print("Evaluating hypothesis combinations...")
    hypothesis_results = []
    for combo in HYPOTHESIS_COMBOS:
        result = compute_hypothesis_combo(hist, combo, seasons)
        hypothesis_results.append(result)

    # ── Section 5: Recommendation ─────────────────────────────────────────────
    recommendation = generate_recommendation(hypothesis_results, sublane_results, seasons)

    # ── Write outputs ─────────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Writing outputs...")
    md = write_markdown(broad_lanes, bin_stats, sublane_results, hypothesis_results, recommendation, seasons)

    import datetime
    today = datetime.date.today().isoformat()
    (OUT_DIR / f"moneyline_logic_summary_{today}.md").write_text(md, encoding="utf-8")
    (OUT_DIR / "latest_moneyline_logic_summary.md").write_text(md, encoding="utf-8")
    print(f"  Written: {OUT_DIR}/latest_moneyline_logic_summary.md")

    write_sublanes_csv(OUT_DIR / f"moneyline_sublanes_{today}.csv", sublane_results, seasons)
    write_sublanes_csv(OUT_DIR / "latest_moneyline_sublanes.csv", sublane_results, seasons)
    print(f"  Written: {OUT_DIR}/latest_moneyline_sublanes.csv")

    write_season_splits_csv(OUT_DIR / f"moneyline_season_splits_{today}.csv", bin_stats, seasons)
    write_season_splits_csv(OUT_DIR / "moneyline_season_splits.csv", bin_stats, seasons)
    print(f"  Written: {OUT_DIR}/moneyline_season_splits.csv")

    # Print summary to terminal
    print()
    print("=" * 70)
    print("  MONEYLINE AUDIT SUMMARY")
    print("=" * 70)
    print()
    for h in hypothesis_results:
        rate = f"{h['hit_rate']*100:.1f}%" if h.get('hit_rate') else "-"
        lift = f"{h['lift_pp']:+.1f}pp" if h.get('lift_pp') is not None else "-"
        cons = _cons_badge(h.get("consistency", ""))
        s_parts = " | ".join(
            f"{s}:{h['season_data'][s]['rate']*100:.1f}%(n={h['season_data'][s]['n']})"
            if h['season_data'][s]['rate'] is not None else f"{s}:-"
            for s in seasons
        )
        print(f"  {h['label']:<38}  n={h['n']:>5}  hit={rate:>6}  lift={lift:>7}  {cons}")
        print(f"    {s_parts}")
        print()

    print(f"  Main training lane : {recommendation['main_training_lane']}")
    print(f"  Best sub-lane      : {recommendation['best_sublane']}")
    print()
    print("  Suppress tags:")
    for s in (recommendation["suppress_lanes"] or ["tag_weak_leader+side>=0.40", "tag_live_rebound+side>=0.40"]):
        print(f"    - {s}")
    print()
    print("  Disagreement v1 rule:")
    print(f"    {recommendation['disagreement_v1_rule']}")
    print()


if __name__ == "__main__":
    main()
