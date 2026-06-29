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
    n    = len(graded)
    hits = sum(1 for _, h in graded if h)
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
    baseline_stats = _sublane_stats(all_graded, 0.0)
    baseline_rate  = baseline_stats["hit_rate"] if baseline_stats["hit_rate"] is not None else 0.572

    sublane_rows: list[dict] = []

    def _add(dimension: str, label: str, subset: list[dict]) -> None:
        stats = _sublane_stats(subset, baseline_rate)
        if stats["n"] == 0:
            return
        sublane_rows.append({
            "dimension":         dimension,
            "label":             label,
            "n":                 stats["n"],
            "hits":              stats["hits"],
            "hit_rate":          f"{stats['hit_rate']:.3f}" if stats["hit_rate"] is not None else "",
            "baseline_hit_rate": f"{baseline_rate:.3f}",
            "lift":              f"{stats['lift']:+.3f}" if stats["lift"] is not None else "",
            "confidence":        _confidence_label(stats["n"]),
        })

    # Overall
    _add("overall", f"score_gte_{THRESHOLD}", qualified)

    # Score bins
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

    # SBR-implied moneyline strength (context only)
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
    rows_with_reasons = [r for r in qualified if r.get("top_positive_reasons", "").strip()]
    total = len(rows_with_reasons)
    if total == 0:
        return []

    feature_counts: dict[str, int]         = defaultdict(int)
    feature_weights: dict[str, list[float]] = defaultdict(list)
    feature_meta: dict[str, dict]           = {}

    for r in rows_with_reasons:
        for item in _parse_reasons(r.get("top_positive_reasons", "")):
            key = f"{item['outcome']}||{item['feature']}||{item['feature_value']}"
            feature_counts[key] += 1
            feature_weights[key].append(item["weight"])
            if key not in feature_meta:
                feature_meta[key] = {
                    "outcome":       item["outcome"],
                    "feature":       item["feature"],
                    "feature_value": item["feature_value"],
                }

    driver_rows = []
    for key, count in feature_counts.items():
        meta  = feature_meta[key]
        avg_w = sum(feature_weights[key]) / len(feature_weights[key])
        driver_rows.append({
            "outcome":                      meta["outcome"],
            "feature":                      meta["feature"],
            "feature_value":                meta["feature_value"],
            "count_in_qualified":           count,
            "total_qualified_with_reasons": total,
            "rate_in_qualified":            f"{count / total:.3f}",
            "avg_weight":                   f"{avg_w:.4f}",
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

    graded   = [r for r in qualified if _is_hit(r) is not None]
    hits     = sum(1 for r in graded if _is_hit(r))
    n        = len(graded)
    hit_rate = hits / n if n else 0.0
    lift     = hit_rate - baseline_rate

    edge_at_avg = CALIBRATED_PROB * 100 - 76.9 - FEE_BUFFER_CENTS

    def _section(dimension: str) -> list[dict]:
        return [sl for sl in sublane_rows if sl["dimension"] == dimension]

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
        "| Metric | Value |",
        "|---|---|",
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
        f"| Net edge at 68.6% prob, 76.9c NO ask | {edge_at_avg:+.1f}c |",
        "| Interpretation | Market prices NO at ~77c on average; brain has 68.6% → **no edge at average price** |",
        "| Required max NO ask for breakeven | ~67.1c (after 1.5c fee buffer) |",
        "| Coverage | See Kalshi validation report |",
        "",
        "## Season Splits",
        "| Season | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in _section("season"):
        lines.append(f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |")

    lines += [
        "",
        "## Home vs Away",
        "| Side | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in _section("home_away"):
        lines.append(f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |")

    lines += [
        "",
        "## BO Bucket (Bullpen Overuse Index)",
        "| BO Bucket | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in _section("bo_bucket"):
        lines.append(f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |")

    lines += [
        "",
        "## BD Bucket (Bullpen Depth Index)",
        "| BD Bucket | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in _section("bd_bucket"):
        lines.append(f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |")

    lines += [
        "",
        "## SBR Moneyline Strength Split (context only — win probability, not run scoring)",
        "_Note: SBR has moneyline data only. No game totals available. "
        "This split shows whether the lane fires on favorites vs underdogs._",
        "| ML Strength | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in _section("sbr_ml_strength"):
        lines.append(f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |")

    lines += [
        "",
        "## Score Bands (Near-Miss and Qualified)",
        "| Score Band | N | Hit Rate | Lift | Confidence |",
        "|---|---|---|---|---|",
    ]
    for sl in _section("score_bin"):
        lines.append(f"| {sl['label']} | {sl['n']} | {sl['hit_rate']} | {sl['lift']} | {sl['confidence']} |")

    lines += [
        "",
        "## Plain-English Verdict",
        "",
        "_Populate after reviewing the above tables._",
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

    all_graded  = [r for r in cards if _is_hit(r) is not None]
    baseline_st = _sublane_stats(all_graded, 0.0)
    baseline_rate = baseline_st["hit_rate"] or 0.572
    print(f"[audit] Baseline hit rate: {baseline_rate:.3f} ({len(all_graded):,} graded rows)")

    qualified = [
        r for r in cards
        if (_safe_float(r.get("team_runs_5plus_no_score")) or 0.0) >= THRESHOLD
    ]
    print(f"[audit] Qualified (score >= {THRESHOLD}): {len(qualified):,}")

    sbr_index = _load_sbr_index()
    print(f"[audit] SBR index: {len(sbr_index):,} dates")

    sublane_rows = _build_sublane_rows(qualified, all_graded, sbr_index)
    reason_rows  = _build_reason_drivers(qualified)

    _write_sublanes_csv(sublane_rows)
    _write_reasons_csv(reason_rows)
    _write_summary(qualified, sublane_rows, baseline_rate)

    graded_q = [r for r in qualified if _is_hit(r) is not None]
    hits_q   = sum(1 for r in graded_q if _is_hit(r))
    rate_q   = hits_q / len(graded_q) if graded_q else 0.0
    print(f"\n[audit] RESULT: {hits_q}/{len(graded_q)} = {rate_q:.1%} hit rate")
    print(f"[audit] Baseline: {baseline_rate:.1%} | Lift: {rate_q - baseline_rate:+.1%}")
    print(f"[audit] Outputs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
