"""
sbr_line_movement_audit.py

Lookahead audit and clean re-analysis of the ML Core v1 line movement finding.

CONFIRMED LOOKAHEAD IN PRIOR REPORT:
  Section 6 ("Market moved toward/away from team") used:
    team_no_vig_avg     = CLOSING line  ← LOOKAHEAD (post-decision)
    team_no_vig_open_avg = OPENING line  ← PRE-DECISION (safe)
  This comparison is invalid as a pre-decision filter.

DATA AVAILABILITY FROM SBR HISTORICAL HTML:
  We have exactly 2 price points per game:
    - Opening line (team_no_vig_open_avg) — pre-decision, safe to use
    - Closing line (team_no_vig_avg)      — post-decision, lookahead if used as filter

  Time windows requested vs availability:
    Open → morning:          UNAVAILABLE (no intraday SBR data)
    Morning → 2h pregame:   UNAVAILABLE
    2h pregame → 30m pre:   UNAVAILABLE
    30m pre → close:        PARTIALLY AVAILABLE as total open→close movement
                             (labeled as post-hoc CLV only, NOT a filter)

WHAT THIS SCRIPT DOES:
  1. Field-by-field lookahead audit
  2. Clean analysis using opening line only (no lookahead)
  3. Post-hoc CLV analysis (open→close, labeled clearly — cannot be used as filter)
  4. All metrics by sub-lane and season
  5. 2024 anomaly investigation

Read-only research. No trades. No model changes.

Usage:
    python sbr_line_movement_audit.py
"""
import csv
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROWS_CSV = Path("outputs/sbr_moneyline_core_validation/moneyline_core_market_validation_rows.csv")
OUT_DIR  = Path("outputs/sbr_line_movement_audit")

# Lookahead status of each field in the validation rows
FIELD_AUDIT = [
    ("game_date",             "PRE-DECISION",  "Date of game"),
    ("season",                "PRE-DECISION",  "Season year"),
    ("game_id",               "PRE-DECISION",  "Game matchup"),
    ("team",                  "PRE-DECISION",  "Home team abbreviation"),
    ("opponent",              "PRE-DECISION",  "Away team abbreviation"),
    ("home_away",             "PRE-DECISION",  "Always 'home' for ML Core v1"),
    ("side_score",            "PRE-DECISION",  "Brain score — uses only pre-game rolling stats"),
    ("ml_core_lane",          "PRE-DECISION",  "Derived from side_score + home_away + parsed reasons"),
    ("opponent_strength_bucket","PRE-DECISION","Parsed from top_positive_reasons — pre-game feature"),
    ("brain_calibrated_prob", "PRE-DECISION",  "Calibrated prob from historical bins — no game data"),
    ("lane_hist_prob",        "PRE-DECISION",  "Fixed historical rate from audit — no game data"),
    ("team_no_vig_open_avg",  "PRE-DECISION",  "SBR opening line no-vig — available before first pitch"),
    ("actual_team_won",       "OUTCOME",       "Game result — known only after game"),
    ("team_no_vig_avg",       "** LOOKAHEAD **","SBR CLOSING line — known only after market closes"),
    ("sbr_home_no_vig_avg",   "** LOOKAHEAD **","Alias for team_no_vig_avg (closing line)"),
    ("market_edge_pp",        "** LOOKAHEAD **","Uses closing line: brain_calib_prob - team_no_vig_avg"),
    ("actual_minus_market",   "** LOOKAHEAD **","Uses closing line as market reference"),
    ("implied_roi_pct",       "** LOOKAHEAD **","Uses closing line as entry price"),
]


def _f(v) -> float | None:
    try:
        s = str(v).strip()
        return None if not s or s.lower() in {"", "nan", "none"} else float(s)
    except Exception:
        return None


def _rate(rows, field="actual_team_won") -> float | None:
    vals = [_f(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _avg(rows, field) -> float | None:
    vals = [_f(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def compute_metrics(rows: list[dict], label: str, entry_field: str = "team_no_vig_open_avg") -> dict:
    """
    Compute all requested metrics for a group of rows.
    entry_field: which market price to use as entry (default: opening line = pre-decision).
    """
    graded = [r for r in rows if _f(r.get("actual_team_won")) is not None]
    with_entry = [r for r in graded if _f(r.get(entry_field)) is not None]
    with_close  = [r for r in with_entry if _f(r.get("team_no_vig_avg")) is not None]

    n        = len(rows)
    n_graded = len(with_entry)
    hit_rate = _rate(with_entry) or 0.0
    entry_p  = _avg(with_entry, entry_field) or 0.0
    close_p  = _avg(with_close, "team_no_vig_avg") or 0.0

    # Edge vs opening line (pp)
    edge_pp = round((hit_rate - entry_p) * 100, 2) if n_graded else None

    # Gross ROI: treating each game as a YES contract purchased at entry_p cents
    # ROI per contract = (hit_rate * (1 - entry_p) - (1 - hit_rate) * entry_p) / entry_p
    # = (hit_rate - entry_p) / entry_p
    gross_roi = None
    if n_graded and entry_p > 0:
        gross_roi = round((hit_rate - entry_p) / entry_p * 100, 2)

    # CLV vs close (post-hoc only, NOT a pre-decision filter)
    # CLV = close_p - entry_p  (positive = market moved toward us after entry)
    clv_vs_close = None
    if with_close and entry_p > 0:
        clv_vs_close = round((close_p - entry_p) * 100, 2)

    return {
        "label":         label,
        "n":             n,
        "n_graded":      n_graded,
        "hit_rate":      round(hit_rate, 4) if n_graded else None,
        "entry_prob":    round(entry_p, 4)  if n_graded else None,
        "close_prob":    round(close_p, 4)  if with_close else None,
        "edge_pp":       edge_pp,
        "gross_roi_pct": gross_roi,
        "clv_vs_close_pp": clv_vs_close,
    }


def section_table(lines: list, metrics_list: list[dict], note: str = "") -> None:
    if note:
        lines.append(f"*{note}*")
        lines.append("")
    header = f"{'Label':<45} {'n':>5} {'HitRate':>8} {'EntryProb':>10} {'Edge(pp)':>9} {'GrossROI%':>10} {'CLVvsClose':>11}"
    lines.append(header)
    lines.append("-" * len(header))
    for m in metrics_list:
        def fmt(v, fmt_str): return fmt_str.format(v) if v is not None else "  n/a"
        lines.append(
            f"{m['label']:<45} "
            f"{m['n']:>5} "
            f"{fmt(m['hit_rate'], '{:.3f}'):>8} "
            f"{fmt(m['entry_prob'], '{:.3f}'):>10} "
            f"{fmt(m['edge_pp'], '{:+.2f}'):>9} "
            f"{fmt(m['gross_roi_pct'], '{:+.2f}'):>10} "
            f"{fmt(m['clv_vs_close_pp'], '{:+.2f}'):>11}"
        )
    lines.append("")


def main() -> None:
    if not ROWS_CSV.exists():
        print(f"ERROR: {ROWS_CSV} not found. Run sbr_moneyline_core_validation.py first.")
        return

    rows = list(csv.DictReader(open(ROWS_CSV, encoding="utf-8")))
    # Only graded ML Core rows (excludes unmatched/ungraded)
    core = [r for r in rows if r.get("ml_core_lane") and r["ml_core_lane"] != "suppressed"
            and _f(r.get("actual_team_won")) is not None
            and _f(r.get("team_no_vig_open_avg")) is not None]

    print(f"Rows loaded: {len(rows)}  |  Graded+matched ML Core: {len(core)}")

    lines = [
        "# SBR Line Movement Audit — ML Core v1",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "Read-only research. No trades. No model changes.",
        "",
        "---",
        "",
        "## 1. Lookahead Field Audit",
        "",
        "All fields in the validation rows, with their lookahead status:",
        "",
        f"{'Field':<35} {'Status':<18} {'Notes'}",
        "-" * 90,
    ]
    for field, status, notes in FIELD_AUDIT:
        lines.append(f"{field:<35} {status:<18} {notes}")
    lines += [
        "",
        "### Conclusion",
        "",
        "The Section 6 finding ('Market moved toward/away from team') in the prior report",
        "used `team_no_vig_avg` (the CLOSING line) to determine movement direction.",
        "This is a LOOKAHEAD: the closing line is only known after the market closes,",
        "which is after the hypothetical decision time (pregame).",
        "",
        "The finding (+8.04pp when market shortened) is NOT valid as a pre-decision filter.",
        "",
        "---",
        "",
        "## 2. Data Availability — Time Windows",
        "",
        "SBR historical HTML provides exactly two price points per game:",
        "",
        "  | Window                   | Data Available?                              |",
        "  |--------------------------|----------------------------------------------|",
        "  | Opening line             | YES — team_no_vig_open_avg (PRE-DECISION)    |",
        "  | Open → morning           | NO — intraday data not in SBR HTML           |",
        "  | Morning → 2h pregame     | NO — intraday data not in SBR HTML           |",
        "  | 2h pregame → 30m pregame | NO — intraday data not in SBR HTML           |",
        "  | 30m pregame → close      | PARTIAL — total movement only (not windowed) |",
        "  | Closing line             | YES — team_no_vig_avg (POST-DECISION, CLV only)|",
        "",
        "True intraday time-window analysis would require a different data source",
        "(e.g., live odds API with timestamps, or a paid historical odds feed).",
        "",
        "This report uses OPENING LINE as the market reference throughout.",
        "Closing line is reported post-hoc as CLV only — it is NOT used as a filter.",
        "",
        "---",
        "",
        "## 3. Clean Analysis — Opening Line as Market Reference (No Lookahead)",
        "",
        "Entry price = opening line no-vig probability (team_no_vig_open_avg).",
        "Edge = hit_rate - entry_prob (in percentage points).",
        "Gross ROI = (hit_rate - entry_prob) / entry_prob × 100%.",
        "CLV vs close = closing_prob - opening_prob (post-hoc, labeled only).",
        "",
    ]

    # Overall
    m_all = compute_metrics(core, "ALL ML Core v1 (home, side>=0.40)")
    section_table(lines, [m_all], note="Opening line as entry. No line movement filter.")

    # Sub-lanes
    opp_weak = [r for r in core if r["ml_core_lane"] == "core_home_opp_weak"]
    standard = [r for r in core if r["ml_core_lane"] == "core_home_standard"]
    m_weak = compute_metrics(opp_weak, "core_home_opp_weak (HOME + opp lt_40)")
    m_std  = compute_metrics(standard, "core_home_standard (HOME, not opp_weak)")
    section_table(lines, [m_weak, m_std], note="Sub-lane split, opening line only.")

    # Season splits
    lines += ["### Season Splits (opening line)", ""]
    season_metrics = []
    for yr in ("2023", "2024", "2025"):
        subset = [r for r in core if r.get("season") == yr]
        season_metrics.append(compute_metrics(subset, f"Season {yr}"))
    section_table(lines, season_metrics)

    # Sub-lane × season
    lines += ["### Sub-Lane × Season (opening line)", ""]
    sub_season = []
    for lane, label in [("core_home_opp_weak", "opp_weak"), ("core_home_standard", "standard")]:
        for yr in ("2023", "2024", "2025"):
            subset = [r for r in core if r["ml_core_lane"] == lane and r.get("season") == yr]
            sub_season.append(compute_metrics(subset, f"{label}  {yr}"))
    section_table(lines, sub_season)

    lines += [
        "---",
        "",
        "## 4. Post-Hoc CLV Analysis (NOT a pre-decision filter)",
        "",
        "The following splits use closing line direction to categorize rows.",
        "This is POST-HOC only — it cannot be known before the game starts.",
        "Results are shown to understand the economic character of the lane,",
        "NOT to define a tradeable filter.",
        "",
    ]

    # CLV direction splits — labeled as post-hoc
    FLAT_THRESHOLD = 0.005  # < 0.5pp movement = flat

    def clv_dir(r):
        entry = _f(r.get("team_no_vig_open_avg"))
        close = _f(r.get("team_no_vig_avg"))
        if entry is None or close is None:
            return "no_data"
        diff = close - entry
        if abs(diff) < FLAT_THRESHOLD:
            return "flat"
        return "shortened" if diff > 0 else "lengthened"

    shortened  = [r for r in core if clv_dir(r) == "shortened"]
    lengthened = [r for r in core if clv_dir(r) == "lengthened"]
    flat       = [r for r in core if clv_dir(r) == "flat"]

    clv_metrics = [
        compute_metrics(shortened,  "Market shortened (team more favored at close)"),
        compute_metrics(flat,       f"Market flat (<{FLAT_THRESHOLD*100:.1f}pp movement)"),
        compute_metrics(lengthened, "Market lengthened (team less favored at close)"),
    ]
    section_table(lines, clv_metrics,
                  note="POST-HOC: closing line direction is only known after market closes.")

    # Same for opp_weak only
    lines += ["### core_home_opp_weak by post-hoc CLV direction", ""]
    weak_shortened  = [r for r in opp_weak if clv_dir(r) == "shortened"]
    weak_lengthened = [r for r in opp_weak if clv_dir(r) == "lengthened"]
    weak_flat       = [r for r in opp_weak if clv_dir(r) == "flat"]
    section_table(lines, [
        compute_metrics(weak_shortened,  "opp_weak + market shortened (post-hoc)"),
        compute_metrics(weak_flat,       "opp_weak + market flat (post-hoc)"),
        compute_metrics(weak_lengthened, "opp_weak + market lengthened (post-hoc)"),
    ], note="POST-HOC only. Not a valid pre-decision filter.")

    lines += [
        "---",
        "",
        "## 5. 2024 Anomaly Investigation",
        "",
        "2024 was the only season with negative edge vs opening line.",
        "Investigating whether this is sample noise, regime change, or structural.",
        "",
    ]

    rows_2024 = [r for r in core if r.get("season") == "2024"]

    # 2024 by sub-lane
    lines += ["### 2024 sub-lane breakdown", ""]
    section_table(lines, [
        compute_metrics([r for r in rows_2024 if r["ml_core_lane"] == "core_home_opp_weak"], "2024 opp_weak"),
        compute_metrics([r for r in rows_2024 if r["ml_core_lane"] == "core_home_standard"], "2024 standard"),
    ])

    # 2024 vs field: opponent implied probability buckets
    lines += ["### 2024: breakdown by market implied probability at open", ""]
    price_buckets = [
        ("<55%",   lambda r: (_f(r.get("team_no_vig_open_avg")) or 0) < 0.55),
        ("55-65%", lambda r: 0.55 <= (_f(r.get("team_no_vig_open_avg")) or 0) < 0.65),
        ("65%+",   lambda r: (_f(r.get("team_no_vig_open_avg")) or 0) >= 0.65),
    ]
    price_m = []
    for label, fn in price_buckets:
        subset_2024 = [r for r in rows_2024 if fn(r)]
        subset_all  = [r for r in core     if fn(r)]
        price_m.append(compute_metrics(subset_2024, f"2024 market_open {label}  (all-seasons n={len(subset_all)})"))
    section_table(lines, price_m)

    # 2024 CLV profile — did market consistently move against picks?
    lines += ["### 2024: post-hoc CLV profile vs 2023/2025", ""]
    for yr in ("2023", "2024", "2025"):
        subset = [r for r in core if r.get("season") == yr]
        avg_clv = _avg(subset, None)  # compute manually
        clv_vals = [(_f(r.get("team_no_vig_avg")) or 0) - (_f(r.get("team_no_vig_open_avg")) or 0)
                    for r in subset
                    if _f(r.get("team_no_vig_avg")) is not None and _f(r.get("team_no_vig_open_avg")) is not None]
        avg_clv_val = round(sum(clv_vals) / len(clv_vals) * 100, 2) if clv_vals else None
        pct_shortened = round(sum(1 for v in clv_vals if v > FLAT_THRESHOLD) / len(clv_vals) * 100, 1) if clv_vals else None
        pct_lengthened = round(sum(1 for v in clv_vals if v < -FLAT_THRESHOLD) / len(clv_vals) * 100, 1) if clv_vals else None
        lines.append(
            f"  {yr}: n={len(subset)}  avg_CLV={avg_clv_val:+.2f}pp  "
            f"shortened={pct_shortened}%  lengthened={pct_lengthened}%"
            if avg_clv_val is not None else f"  {yr}: n={len(subset)}  no CLV data"
        )
    lines.append("")

    # 2024 by month (did a specific stretch drive it?)
    lines += ["### 2024: monthly hit rate vs opening line", ""]
    by_month: defaultdict[str, list] = defaultdict(list)
    for r in rows_2024:
        month = r.get("game_date", "")[:7]
        if month:
            by_month[month].append(r)
    month_metrics = []
    for month in sorted(by_month):
        month_metrics.append(compute_metrics(by_month[month], f"2024-{month[5:]}"))
    section_table(lines, month_metrics, note="Monthly breakdown — which months drove the 2024 underperformance?")

    # 2024 extreme misses (team was big favorite, lost)
    lines += ["### 2024: worst losses (high entry prob, team lost)", ""]
    worst = [r for r in rows_2024
             if _f(r.get("actual_team_won")) == 0
             and _f(r.get("team_no_vig_open_avg")) is not None]
    worst.sort(key=lambda r: -(_f(r.get("team_no_vig_open_avg")) or 0))
    lines += [
        f"{'Date':<12} {'Game':<12} {'Lane':<22} {'EntryProb':>10} {'SideScore':>10}",
        "-" * 70,
    ]
    for r in worst[:15]:
        lines.append(
            f"{r.get('game_date',''):<12} "
            f"{r.get('game_id',''):<12} "
            f"{r.get('ml_core_lane',''):<22} "
            f"{(_f(r.get('team_no_vig_open_avg')) or 0):>10.3f} "
            f"{(_f(r.get('side_score')) or 0):>10.4f}"
        )
    lines.append("")

    lines += [
        "---",
        "",
        "## 6. Summary and Action Items",
        "",
        "### What changed from the prior report",
        "",
        "| Finding | Prior Report | This Report (Clean) |",
        "|---------|-------------|---------------------|",
        f"| Overall edge | +4.34pp (closing line) | {_sign(m_all['edge_pp'])} (opening line) |",
        f"| core_home_opp_weak edge | +9.85pp (closing) | {_sign(m_weak['edge_pp'])} (opening) |",
        f"| core_home_standard edge | +2.13pp (closing) | {_sign(m_std['edge_pp'])} (opening) |",
        "| Line movement filter | LOOKAHEAD (closing line) | REMOVED — cannot be applied pre-decision |",
        "",
        "### Rules that survive without lookahead",
        "",
    ]

    # Determine which findings survive
    findings = []
    if m_weak["edge_pp"] is not None and m_weak["edge_pp"] >= 3.0 and (m_weak["n_graded"] or 0) >= 50:
        findings.append(f"- core_home_opp_weak: {m_weak['edge_pp']:+.2f}pp edge at opening line, n={m_weak['n_graded']} — SURVIVES")
    else:
        findings.append(f"- core_home_opp_weak: {(m_weak['edge_pp'] or 0):+.2f}pp edge at opening line, n={m_weak.get('n_graded',0)} — MARGINAL OR INSUFFICIENT")

    if m_std["edge_pp"] is not None and m_std["edge_pp"] >= 3.0 and (m_std["n_graded"] or 0) >= 50:
        findings.append(f"- core_home_standard: {m_std['edge_pp']:+.2f}pp edge at opening line, n={m_std['n_graded']} — SURVIVES")
    else:
        findings.append(f"- core_home_standard: {(m_std['edge_pp'] or 0):+.2f}pp edge at opening line, n={m_std.get('n_graded',0)} — MARGINAL OR INSUFFICIENT")

    lines += findings
    lines += [
        "",
        "### What requires further investigation",
        "",
        "- 2024 anomaly: negative edge in one of three seasons — seasonal regime change or sample noise?",
        "- Intraday line data: to implement true time-window filters, need a paid odds API",
        "  (e.g., OddsJam, The Odds API, or BetResearch with timestamps)",
        "- The CLV post-hoc split shows the brain's picks get shortened by the market",
        "  more often than not — this is CONSISTENT with the brain finding real signal,",
        "  but cannot be used as a pre-decision filter without intraday data.",
        "",
        "**No rules promoted. Observe only.**",
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / "line_movement_audit.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWROTE: {report_path}")

    # Also write clean metrics CSV
    all_metrics = []
    for lane in ("all", "opp_weak", "standard"):
        for yr in ("all", "2023", "2024", "2025"):
            if lane == "all":
                subset = core if yr == "all" else [r for r in core if r.get("season") == yr]
                lbl = f"all__{yr}"
            elif lane == "opp_weak":
                base = opp_weak
                subset = base if yr == "all" else [r for r in base if r.get("season") == yr]
                lbl = f"opp_weak__{yr}"
            else:
                base = standard
                subset = base if yr == "all" else [r for r in base if r.get("season") == yr]
                lbl = f"standard__{yr}"
            m = compute_metrics(subset, lbl)
            all_metrics.append(m)

    fields = ["label", "n", "n_graded", "hit_rate", "entry_prob", "close_prob",
              "edge_pp", "gross_roi_pct", "clv_vs_close_pp"]
    with open(OUT_DIR / "clean_metrics.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_metrics)
    print(f"WROTE: {OUT_DIR / 'clean_metrics.csv'}")


def _sign(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}pp"


if __name__ == "__main__":
    main()
