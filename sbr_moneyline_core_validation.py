"""
sbr_moneyline_core_validation.py -- Join SBR consensus moneyline odds to
Moneyline Core v1 pregame card rows and validate whether the lane beats
market-implied probability.

Read-only research. No trades. No paper entries. No model changes.

Usage:
    python sbr_moneyline_core_validation.py
    python sbr_moneyline_core_validation.py --years 2023,2024,2025
"""
import argparse
import csv
import math
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CARDS_CSV     = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
CONSENSUS_CSV = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")
CALIB_CSV     = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")
OUT_DIR       = Path("outputs/sbr_moneyline_core_validation")

_ML_CORE_THRESHOLD = 0.40
_REASON_RE = re.compile(r"\[.*?\]\s*([\w+_]+)=([\w._+-]+)")

_ROW_FIELDS = [
    "game_date", "season", "game_id", "team", "opponent", "home_away",
    "side_score", "ml_core_lane",
    "tag_weak_leader", "tag_live_rebound", "opponent_strength_bucket",
    "actual_team_won",
    "brain_calibrated_prob", "lane_hist_prob",
    "sbr_home_no_vig_avg", "sbr_away_no_vig_avg",
    "sbr_home_no_vig_open_avg", "sbr_away_no_vig_open_avg",
    "sbr_book_count",
    "team_no_vig_avg", "team_no_vig_open_avg",
    "market_edge_pp",
    "actual_minus_market",
    "implied_roi_pct",
]


# ── ML Core v1 lane classifier ────────────────────────────────────────────────

def _parse_reasons(reasons_str: str) -> dict[str, str]:
    if not reasons_str or str(reasons_str).strip().lower() in {"", "nan", "none"}:
        return {}
    return {m.group(1): m.group(2).strip() for m in _REASON_RE.finditer(reasons_str)}


def classify_ml_core_lane(card: dict) -> str | None:
    try:
        side_score = float(card.get("side_score") or 0)
    except (ValueError, TypeError):
        side_score = 0.0

    if side_score < _ML_CORE_THRESHOLD:
        return None
    if card.get("home_away") != "home":
        return None

    parsed = _parse_reasons(card.get("top_positive_reasons", ""))
    if (parsed.get("tag_weak_leader_fade_watch") == "yes"
            or parsed.get("tag_live_rebound_watch") == "yes"):
        return "suppressed"

    opp_bucket = (
        card.get("opponent_strength_bucket")
        or parsed.get("opponent_strength_bucket")
        or ""
    )
    if opp_bucket == "lt_40":
        return "core_home_opp_weak"
    return "core_home_standard"


# ── Calibration ───────────────────────────────────────────────────────────────

def load_calibration(path: Path) -> dict[str, float]:
    calib: dict[str, float] = {}
    if not path.exists():
        return calib
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lane = row.get("lane", "")
            bin_ = row.get("score_bin", "")
            prob = row.get("conservative_probability")
            if lane and bin_ and prob:
                try:
                    calib[f"{lane}:{bin_}"] = float(prob)
                except ValueError:
                    pass
    return calib


def lookup_calib(calib: dict[str, float], lane: str, side_score: float) -> float | None:
    bins = [
        ("<0.00",     float("-inf"), 0.0),
        ("0.00-0.10", 0.0,          0.10),
        ("0.10-0.20", 0.10,         0.20),
        ("0.20-0.30", 0.20,         0.30),
        ("0.30-0.40", 0.30,         0.40),
        ("0.40+",     0.40,         float("inf")),
    ]
    for label, lo, hi in bins:
        if lo <= side_score < hi or (hi == float("inf") and side_score >= lo):
            return calib.get(f"{lane}:{label}")
    return None


# ── SBR consensus loader ──────────────────────────────────────────────────────

def load_sbr_consensus(path: Path) -> dict[tuple, dict]:
    idx: dict[tuple, dict] = {}
    if not path.exists():
        print(f"WARNING: {path} not found. Run sbr_mlb_odds_fetcher.py first.")
        return idx
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("game_date", ""), row.get("home_abbr", ""), row.get("away_abbr", ""))
            if all(key):
                idx[key] = row
    return idx


def _as_float(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ── Join and validate ─────────────────────────────────────────────────────────

def build_validation_rows(
    cards: list[dict],
    sbr: dict[tuple, dict],
    calib: dict[str, float],
    years: list[int] | None = None,
) -> tuple[list[dict], list[dict]]:
    matched: list[dict] = []
    unmatched: list[dict] = []

    for card in cards:
        gdate = card.get("game_date", "")
        if not gdate or len(gdate) < 4:
            continue
        if years and int(gdate[:4]) not in years:
            continue

        lane = classify_ml_core_lane(card)
        if lane is None or lane == "suppressed":
            continue

        team_abbr     = card.get("team", "")
        opponent_abbr = card.get("opponent", "")
        game_id       = card.get("game_id", "")

        sbr_key = (gdate, team_abbr, opponent_abbr)
        sbr_row = sbr.get(sbr_key)

        parsed = _parse_reasons(card.get("top_positive_reasons", ""))

        side_score    = _as_float(card.get("side_score")) or 0.0
        actual_won    = card.get("actual_team_won")
        actual_won_int = (
            1 if str(actual_won).strip() == "1"
            else 0 if str(actual_won).strip() == "0"
            else None
        )

        calib_prob = lookup_calib(calib, "side", side_score)
        lane_hist  = 0.685 if lane == "core_home_opp_weak" else 0.617

        home_nv_avg  = _as_float(sbr_row.get("home_no_vig_avg"))  if sbr_row else None
        home_nv_open = _as_float(sbr_row.get("home_no_vig_open_avg")) if sbr_row else None
        away_nv_avg  = _as_float(sbr_row.get("away_no_vig_avg"))  if sbr_row else None
        away_nv_open = _as_float(sbr_row.get("away_no_vig_open_avg")) if sbr_row else None
        book_count   = int(sbr_row.get("book_count", 0)) if sbr_row else 0

        team_nv      = home_nv_avg
        team_nv_open = home_nv_open

        market_edge = None
        if calib_prob is not None and team_nv is not None:
            market_edge = round((calib_prob - team_nv) * 100, 2)

        actual_minus_market = None
        if actual_won_int is not None and team_nv is not None:
            actual_minus_market = round(actual_won_int - team_nv, 4)

        implied_roi = None
        if actual_won_int is not None and team_nv is not None and team_nv > 0:
            implied_roi = round((actual_won_int - team_nv) / team_nv * 100, 2)

        row = {
            "game_date":             gdate,
            "season":                gdate[:4],
            "game_id":               game_id,
            "team":                  team_abbr,
            "opponent":              opponent_abbr,
            "home_away":             card.get("home_away", ""),
            "side_score":            round(side_score, 4),
            "ml_core_lane":          lane,
            "tag_weak_leader":       parsed.get("tag_weak_leader_fade_watch", ""),
            "tag_live_rebound":      parsed.get("tag_live_rebound_watch", ""),
            "opponent_strength_bucket": (
                card.get("opponent_strength_bucket", "")
                or parsed.get("opponent_strength_bucket", "")
            ),
            "actual_team_won":       actual_won_int,
            "brain_calibrated_prob": round(calib_prob, 4) if calib_prob is not None else None,
            "lane_hist_prob":        lane_hist,
            "sbr_home_no_vig_avg":   round(home_nv_avg, 4)  if home_nv_avg  is not None else None,
            "sbr_away_no_vig_avg":   round(away_nv_avg, 4)  if away_nv_avg  is not None else None,
            "sbr_home_no_vig_open_avg": round(home_nv_open, 4) if home_nv_open is not None else None,
            "sbr_away_no_vig_open_avg": round(away_nv_open, 4) if away_nv_open is not None else None,
            "sbr_book_count":        book_count,
            "team_no_vig_avg":       round(team_nv, 4)      if team_nv      is not None else None,
            "team_no_vig_open_avg":  round(team_nv_open, 4) if team_nv_open is not None else None,
            "market_edge_pp":        market_edge,
            "actual_minus_market":   actual_minus_market,
            "implied_roi_pct":       implied_roi,
        }

        if sbr_row:
            matched.append(row)
        else:
            unmatched.append(row)

    return matched, unmatched


# ── Report generation ─────────────────────────────────────────────────────────

def _rate(rows, field="actual_team_won") -> float | None:
    vals = [r[field] for r in rows if r.get(field) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _avg(rows, field) -> float | None:
    vals = [r[field] for r in rows if r.get(field) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _section(lines: list, rows: list, label: str, min_n: int = 10) -> None:
    n       = len(rows)
    graded  = [r for r in rows if r.get("actual_team_won") is not None]
    sbr_matched = [r for r in rows if r.get("team_no_vig_avg") is not None]
    hit     = _rate(graded) or 0
    mkt     = _avg(sbr_matched, "team_no_vig_avg") or 0
    edge    = round((hit - mkt) * 100, 2) if graded and sbr_matched else None
    mkt_open = _avg([r for r in sbr_matched if r.get("team_no_vig_open_avg") is not None], "team_no_vig_open_avg")
    lines.append(f"### {label}")
    lines.append(f"n={n}  graded={len(graded)}  sbr_matched={len(sbr_matched)}")
    if len(graded) >= min_n:
        edge_str = f"{edge:+.2f}pp" if edge is not None else "n/a"
        lines.append(f"hit_rate={hit:.3f}  sbr_no_vig={mkt:.3f}  **actual_minus_mkt={edge_str}**")
        if mkt_open:
            open_edge = round((hit - mkt_open) * 100, 2)
            lines.append(f"sbr_open_no_vig={mkt_open:.3f}  actual_minus_open={open_edge:+.2f}pp")
    else:
        lines.append(f"LOW SAMPLE (n<{min_n}) -- do not interpret")
    lines.append("")


def generate_report(matched: list[dict], unmatched: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    total_core  = len(matched) + len(unmatched)
    graded      = [r for r in matched if r.get("actual_team_won") is not None]
    sbr_graded  = [r for r in graded  if r.get("team_no_vig_avg") is not None]

    lines = [
        "# Moneyline Core v1 Market Validation",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "Read-only research. No trades. No paper entries. Do not change ML Core v1.",
        "",
        "---",
        "",
        "## 1. Coverage",
        "",
        f"- Total ML Core v1 rows (home, side>=0.40, NOT suppressed): {total_core}",
        f"- Matched to SBR consensus odds: {len(matched)} ({len(matched)/max(total_core,1):.0%})",
        f"- Unmatched (no SBR odds found): {len(unmatched)}",
        f"- Graded rows (actual_team_won known): {len(graded)}",
        f"- Graded + SBR matched: {len(sbr_graded)}",
        "",
    ]

    lines += ["## 2. Overall Moneyline Core v1 vs Market", ""]
    _section(lines, matched, "All ML Core v1 (home, side>=0.40, not suppressed)")

    lines += ["## 3. Sub-Lane Split", ""]
    for lane_label in ("core_home_opp_weak", "core_home_standard"):
        subset = [r for r in matched if r.get("ml_core_lane") == lane_label]
        _section(lines, subset, lane_label)

    lines += ["## 4. Brain Edge Bucket Split (brain_calib_prob - sbr_no_vig)", ""]
    edge_buckets = [
        ("edge_leq_0",    lambda r: (r.get("market_edge_pp") or 0) <= 0),
        ("edge_0_to_2pp", lambda r: 0 < (r.get("market_edge_pp") or 0) <= 2),
        ("edge_2_to_5pp", lambda r: 2 < (r.get("market_edge_pp") or 0) <= 5),
        ("edge_5pp_plus", lambda r: (r.get("market_edge_pp") or 0) > 5),
    ]
    for label, fn in edge_buckets:
        subset = [r for r in matched if r.get("market_edge_pp") is not None and fn(r)]
        _section(lines, subset, label)

    lines += ["## 5. Market Implied Probability Buckets (SBR no-vig)", ""]
    price_buckets = [
        ("<50%",   lambda r: (r.get("team_no_vig_avg") or 0) < 0.50),
        ("50-55%", lambda r: 0.50 <= (r.get("team_no_vig_avg") or 0) < 0.55),
        ("55-60%", lambda r: 0.55 <= (r.get("team_no_vig_avg") or 0) < 0.60),
        ("60-65%", lambda r: 0.60 <= (r.get("team_no_vig_avg") or 0) < 0.65),
        ("65-70%", lambda r: 0.65 <= (r.get("team_no_vig_avg") or 0) < 0.70),
        ("70%+",   lambda r: (r.get("team_no_vig_avg") or 0) >= 0.70),
    ]
    for label, fn in price_buckets:
        subset = [r for r in matched if r.get("team_no_vig_avg") is not None and fn(r)]
        _section(lines, subset, label)

    lines += ["## 6. Opening vs Current Line Movement", ""]
    both = [r for r in matched
            if r.get("team_no_vig_avg") is not None and r.get("team_no_vig_open_avg") is not None]
    if both:
        moved_toward = [r for r in both if r["team_no_vig_avg"] > r["team_no_vig_open_avg"]]
        moved_away   = [r for r in both if r["team_no_vig_avg"] < r["team_no_vig_open_avg"]]
        stable       = [r for r in both if r["team_no_vig_avg"] == r["team_no_vig_open_avg"]]
        lines.append(f"Games with both open and current: n={len(both)}")
        lines.append(f"Market shortened (team implied rose): n={len(moved_toward)}")
        lines.append(f"Market lengthened (team implied fell): n={len(moved_away)}")
        lines.append(f"No movement: n={len(stable)}")
        lines.append("")
        if moved_toward:
            _section(lines, moved_toward, "Market moved TOWARD team (team shortened)")
        if moved_away:
            _section(lines, moved_away, "Market moved AWAY from team (team lengthened)")
    else:
        lines.append("Insufficient data for opening vs current comparison.")
        lines.append("")

    lines += ["## 7. Season Splits", ""]
    for yr in ("2023", "2024", "2025"):
        subset = [r for r in matched if r.get("season") == yr]
        _section(lines, subset, f"Season {yr}")

    lines += ["## 8. Plain-English Verdict", ""]
    overall_hit = _rate(graded)
    overall_mkt = _avg(sbr_graded, "team_no_vig_avg")
    if overall_hit is not None and overall_mkt is not None and len(sbr_graded) >= 20:
        diff = round((overall_hit - overall_mkt) * 100, 2)
        if diff >= 3:
            verdict = (
                f"ENCOURAGING (observe only): ML Core v1 shows {overall_hit:.1%} actual hit rate "
                f"vs {overall_mkt:.1%} market-implied ({diff:+.2f}pp above market). "
                f"This warrants further investigation but does NOT authorize trading "
                f"until sample is larger and price data is verified."
            )
        elif diff >= 0:
            verdict = (
                f"INCONCLUSIVE: ML Core v1 hit rate ({overall_hit:.1%}) is marginally above "
                f"market implied ({overall_mkt:.1%}) by only {diff:+.2f}pp. "
                f"Insufficient to distinguish from noise. Observe only."
            )
        else:
            verdict = (
                f"NOT PROMISING: ML Core v1 hit rate ({overall_hit:.1%}) is BELOW "
                f"market implied ({overall_mkt:.1%}) by {abs(diff):.2f}pp. "
                f"The market was pricing these teams correctly or higher. Do not trade."
            )
        lines.append(verdict)
    else:
        lines.append("Insufficient matched+graded rows for a reliable verdict. Run full backfill first.")

    lines += [
        "",
        "**Interpretation rules:**",
        "- Hit rate alone means nothing. The question is: did we beat the market-implied probability?",
        "- A 63% hit rate is good if market implied 58%. It is not good if market implied 66%.",
        "- This report is observe-only. No model changes based on this alone.",
        "- Do not change Moneyline Core v1 thresholds without consistent multi-season market edge evidence.",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "moneyline_core_market_validation.md").write_text("\n".join(lines), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate ML Core v1 against SBR market odds. Read-only research."
    )
    parser.add_argument("--years", default="2023,2024,2025")
    args = parser.parse_args()
    years = [int(y.strip()) for y in args.years.split(",") if y.strip().isdigit()]

    calib = load_calibration(CALIB_CSV)
    sbr   = load_sbr_consensus(CONSENSUS_CSV)

    if not CARDS_CSV.exists():
        print(f"ERROR: {CARDS_CSV} not found")
        return

    with open(CARDS_CSV, encoding="utf-8") as f:
        cards = list(csv.DictReader(f))

    print(f"\nML Core v1 Market Validation")
    print(f"  Cards: {len(cards)}  |  SBR consensus rows: {len(sbr)}  |  Years: {years}")

    matched, unmatched = build_validation_rows(cards, sbr, calib, years)

    write_csv(
        OUT_DIR / "moneyline_core_market_validation_rows.csv",
        matched + unmatched, _ROW_FIELDS,
    )
    generate_report(matched, unmatched, OUT_DIR)

    print(f"  ML Core rows: {len(matched)+len(unmatched)}")
    print(f"  SBR matched: {len(matched)}  unmatched: {len(unmatched)}")
    print(f"\nOutputs -> {OUT_DIR}/")
    print("  moneyline_core_market_validation.md")
    print("  moneyline_core_market_validation_rows.csv")


if __name__ == "__main__":
    main()
