"""
api/routers/slate_monitor.py — GET /api/mlb/slate-monitor?date=YYYY-MM-DD

Read-only. Reads pre-generated output CSVs and returns them as JSON for the
Slate Monitor UI.  No DB writes. No candidate generation. No paper entries.
"""
import csv
import re
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

# Same regex as kalshi/orderbook_recorder.py — game date encoded in ticker
_TICKER_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})\d{4}")
_TICKER_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

router = APIRouter()

_OUTPUTS = Path("outputs")
_HEALTH_CSV = _OUTPUTS / "kalshi_snapshot_collection_health" / "latest_collection_health.csv"
_BRAIN_DIR = _OUTPUTS / "pregame_identifier_card_preview"
_EV_CSV = _OUTPUTS / "kalshi_ev_overlay_preview" / "ev_overlay_rows.csv"

_BRAIN_FILES = {
    "side_leans":             _BRAIN_DIR / "pregame_side_leans.csv",
    "side_fades":             _BRAIN_DIR / "pregame_side_fades.csv",
    "team_scoring_watchlist": _BRAIN_DIR / "team_scoring_watchlist.csv",
    "team_5plus_avoid":       _BRAIN_DIR / "team_5plus_avoid_list.csv",
    "team_f5_scoring_watchlist": _BRAIN_DIR / "team_f5_scoring_watchlist.csv",
    "live_watchlist":         _BRAIN_DIR / "live_watchlist.csv",
    "full_avoid_list":        _BRAIN_DIR / "full_avoid_list.csv",
}

_PRIORITY_TYPES = ["moneyline", "full_game_total", "team_total", "f5_total", "f5_winner"]

_OPP_WEAK_DIR = _OUTPUTS / "opp_weak_pregame_report"

# These are the fields allowed in the pre-decision section of a row.
# Closing-line and post-hoc fields are allowed through (for CLV display) but
# the UI must label them POST-HOC ONLY.
_OPP_WEAK_CONTAMINATED = frozenset({
    "team_no_vig_avg", "sbr_home_no_vig_avg",
    "market_edge_pp", "actual_minus_market", "implied_roi_pct",
})


def _ticker_game_date(ticker: str) -> Optional[str]:
    m = _TICKER_DATE_RE.search(ticker)
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    month = _TICKER_MONTH_MAP.get(mon)
    return f"20{yy}-{month}-{dd}" if month else None


def _health_source_date(rows: list[dict]) -> Optional[str]:
    """Return the most common game date found in health CSV tickers."""
    counts: dict[str, int] = {}
    for r in rows:
        d = _ticker_game_date(r.get("market_ticker", ""))
        if d:
            counts[d] = counts.get(d, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


def _read_csv(path: Path) -> tuple[list[dict], Optional[str]]:
    try:
        if not path.exists():
            return [], f"File not found: {path.name}"
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f)), None
    except Exception as exc:
        return [], str(exc)


def _build_health_summary(rows: list[dict]) -> dict:
    if not rows:
        return {}

    def label_count(subset: list[str], label: str) -> int:
        return sum(1 for l in subset if l == label)

    priority_rows = [r for r in rows if r.get("is_priority_type") in ("True", "true", "1")]
    labels = [r.get("coverage_label", "no_snapshots") for r in rows]
    p_labels = [r.get("coverage_label", "no_snapshots") for r in priority_rows]

    p_fresh = label_count(p_labels, "fresh") + label_count(p_labels, "recent")
    p_total = len(priority_rows)
    fresh_pct = round(p_fresh / p_total * 100, 1) if p_total else 0.0

    snap_times = [r["last_snap_at"] for r in rows if r.get("last_snap_at")]
    latest_snap = max(snap_times) if snap_times else None

    by_type: dict[str, dict] = {}
    for mtype in _PRIORITY_TYPES:
        t_rows = [r for r in rows if r.get("market_type") == mtype]
        t_labels = [r.get("coverage_label", "no_snapshots") for r in t_rows]
        t_fresh = label_count(t_labels, "fresh") + label_count(t_labels, "recent")
        t_total = len(t_rows)
        by_type[mtype] = {
            "total":     t_total,
            "fresh":     t_fresh,
            "fresh_pct": round(t_fresh / t_total * 100, 1) if t_total else 0.0,
            "stale":     label_count(t_labels, "stale"),
            "empty":     label_count(t_labels, "stale_empty_book"),
            "missing":   label_count(t_labels, "no_snapshots"),
        }

    overall = "HEALTHY" if fresh_pct >= 80 else ("DEGRADED" if fresh_pct >= 50 else "WARNING")

    return {
        "total_markets":   len(rows),
        "fresh":           label_count(labels, "fresh"),
        "recent":          label_count(labels, "recent"),
        "stale":           label_count(labels, "stale"),
        "stale_empty_book": label_count(labels, "stale_empty_book"),
        "no_snapshots":    label_count(labels, "no_snapshots"),
        "priority_total":  p_total,
        "priority_fresh":  p_fresh,
        "fresh_pct":       fresh_pct,
        "latest_snap_at":  latest_snap,
        "overall_status":  overall,
        "by_type":         by_type,
    }


@router.get("/mlb/slate-monitor")
def slate_monitor_endpoint(
    date_str: Optional[str] = Query(default=None, alias="date"),
) -> dict:
    target = date_str or date.today().isoformat()
    errors: dict[str, str] = {}

    # --- snapshot health (always latest; not date-filtered) ---
    health_rows, err = _read_csv(_HEALTH_CSV)
    if err:
        errors["snapshot_health"] = err
    health_summary = _build_health_summary(health_rows) if not err else {}
    health_source = _health_source_date(health_rows)

    # --- brain candidates (date-filtered by game_date column) ---
    brain: dict[str, list] = {}
    brain_total_rows: dict[str, int] = {}
    for key, path in _BRAIN_FILES.items():
        rows, err = _read_csv(path)
        if err:
            errors[f"brain_{key}"] = err
            brain[key] = []
            brain_total_rows[key] = 0
        else:
            has_date_col = any("game_date" in r for r in rows[:1])
            brain[key] = [r for r in rows if not has_date_col or r.get("game_date") == target]
            brain_total_rows[key] = len(rows)

    # --- EV overlay (date-filtered by game_date column) ---
    ev_rows, err = _read_csv(_EV_CSV)
    if err:
        errors["ev_overlay"] = err
        ev_filtered, ev_source = [], None
    else:
        ev_filtered = [r for r in ev_rows if r.get("game_date") == target]
        ev_source = ev_rows[0].get("game_date") if ev_rows else None

    # --- opp_weak pregame lane ---
    opp_weak_path = _OPP_WEAK_DIR / f"opp_weak_report_{target}.csv"
    opp_weak_rows, err = _read_csv(opp_weak_path)
    if err and "not found" not in err.lower():
        errors["opp_weak"] = err

    opp_weak_summary = _build_opp_weak_summary(opp_weak_rows, target)

    # health_date_matches: True only when health data is for the requested date
    health_date_matches: bool = (
        health_source is not None and health_source == target
    ) if health_source is not None else False

    # opp_weak: distinguish "file missing" from "file present, 0 qualifying games"
    opp_weak_report_exists = opp_weak_path.exists()

    return {
        "date":                  target,
        "snapshot_health":       health_summary,
        "snapshot_health_rows":  health_rows,
        "health_source_date":    health_source,
        "health_date_matches":   health_date_matches,
        "brain_candidates":      brain,
        "brain_total_rows":      brain_total_rows,
        "ev_overlay":            ev_filtered,
        "ev_source_date":        ev_source,
        "opp_weak": {
            "summary":        opp_weak_summary,
            "rows":           opp_weak_rows,
            "source_date":    target if opp_weak_report_exists else None,
            "report_exists":  opp_weak_report_exists,
        },
        "errors":                errors,
    }


def _build_opp_weak_summary(rows: list[dict], source_date: str) -> dict:
    """Compute status counts and averages from a pre-generated opp_weak CSV."""
    if not rows:
        return {}

    def _fv(v: str) -> Optional[float]:
        try:
            return float(v) if v not in ("", "n/a", "None", None) else None
        except (ValueError, TypeError):
            return None

    statuses = [r.get("status", "") for r in rows]
    paper_eligible = statuses.count("paper_eligible")
    observe_only   = statuses.count("observe_only")
    blocked_price  = statuses.count("blocked_by_price")
    blocked_data   = statuses.count("blocked_missing_data")

    open_probs  = [_fv(r.get("opening_no_vig_prob", "")) for r in rows]
    open_probs  = [v for v in open_probs if v is not None]
    kalshi_mids = [_fv(r.get("current_kalshi_mid", "")) for r in rows]
    kalshi_mids = [v for v in kalshi_mids if v is not None]

    return {
        "total_qualifying":  len(rows),
        "paper_eligible":    paper_eligible,
        "observe_only":      observe_only,
        "blocked_by_price":  blocked_price,
        "blocked_missing_data": blocked_data,
        "avg_opening_prob":  round(sum(open_probs) / len(open_probs), 4) if open_probs else None,
        "avg_current_kalshi": round(sum(kalshi_mids) / len(kalshi_mids), 4) if kalshi_mids else None,
        "max_entry_prob":    rows[0].get("max_entry_prob", "") if rows else "",
        "max_entry_ml":      rows[0].get("max_entry_ml", "") if rows else "",
        "lane_hit_rate":     "74.7%",
        "conservative_prob": "73.5%",
        "source_date":       source_date,
    }
