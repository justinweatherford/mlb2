#!/usr/bin/env python3
"""
ev_fill_reconciler.py — Reconcile shadow review log rows against realistic Kalshi fill prices.

For each logged candidate, finds the nearest orderbook snapshot at or before decision_time_utc
and computes the realistic fill price, net edge at fill, outcome, P&L, and CLV.
Does NOT place orders, call Kalshi APIs, or create real trades. Read-only research tool.

Usage:
    python ev_fill_reconciler.py --date 2026-06-23
    python ev_fill_reconciler.py --date 2026-06-23 --dry-run
    python ev_fill_reconciler.py --date 2026-06-23 --max-snapshot-age-seconds 300
"""
import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SHADOW_LOG     = Path("outputs/ev_shadow_review_log/shadow_review_log.csv")
RECON_DIR      = Path("outputs/ev_fill_reconciler")
RECON_CSV      = RECON_DIR / "fill_reconciliation.csv"
LATEST_RECON   = RECON_DIR / "latest_fill_reconciliation.csv"
RECON_SUMMARY  = RECON_DIR / "fill_reconciliation_summary.md"
KALSHI_DB      = Path("kalshi_mlb.db")
SBR_CONSENSUS  = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")

WIDE_SPREAD_THRESHOLD = 10   # cents — spread >= this → wide_spread
ABSURD_BID_MAX        = 2    # yes_bid <= this AND yes_ask >= ABSURD_ASK_MIN → invalid_book
ABSURD_ASK_MIN        = 95
FEE_BUFFER_CENTS      = 1.5  # approximate Kalshi settlement fee

RECON_FIELDS = [
    "shadow_id",
    "game_date",
    "game",
    "team",
    "lane",
    "direction",
    "market_ticker",
    "decision_time_utc",
    "calibrated_probability",
    "estimated_ask_cents",
    "estimated_net_edge_cents",
    "nearest_snapshot_time_utc",
    "snapshot_age_seconds",
    "fill_source",
    "realistic_fill_price_cents",
    "yes_bid_cents",
    "yes_ask_cents",
    "no_bid_cents",
    "no_ask_cents",
    "spread_cents",
    "depth_at_fill",
    "fill_quality",
    "fill_quality_reason",
    "fee_buffer_cents",
    "net_edge_at_fill_cents",
    "breakeven_probability",
    "actual_result",
    "pnl_per_1_contract_cents",
    "fee_adjusted_pnl_cents",
    "clv_open_points",
    "clv_current_or_close_points",
    "outcome_status",
]


# ── Snapshot lookup ────────────────────────────────────────────────────────────

def _find_snapshot(
    conn: sqlite3.Connection,
    ticker: str,
    decision_time_utc: datetime,
    max_age_s: int,
    allow_after_s: int,
) -> tuple[dict | None, str]:
    """
    Find the best orderbook snapshot for `ticker` at or near `decision_time_utc`.

    Prefers the latest snapshot at or before decision time within max_age_s.
    Falls back to the nearest snapshot after decision time within allow_after_s.

    Returns (snapshot_dict_or_None, quality_note) where quality_note is:
        ''                 — clean before-decision snapshot within age limit
        'stale'            — before-decision snapshot but older than max_age_s
        'after_tolerance'  — no before snap, used nearest-after within allow_after_s
        'none'             — nothing usable found
    """
    dt_iso = decision_time_utc.isoformat()

    row = conn.execute(
        """
        SELECT id, market_ticker, snapped_at,
               yes_bid, yes_ask, no_bid, no_ask, spread_cents,
               yes_bids_json, yes_asks_json
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at <= ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (ticker, dt_iso),
    ).fetchone()

    if row:
        snapped_at = datetime.fromisoformat(row["snapped_at"].replace("Z", "+00:00"))
        age_s = (decision_time_utc - snapped_at).total_seconds()
        snap_dict = dict(row)
        if age_s <= max_age_s:
            return snap_dict, ""
        return snap_dict, "stale"

    # Fallback: nearest snapshot after decision time within tolerance
    row = conn.execute(
        """
        SELECT id, market_ticker, snapped_at,
               yes_bid, yes_ask, no_bid, no_ask, spread_cents,
               yes_bids_json, yes_asks_json
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at > ?
        ORDER BY snapped_at ASC
        LIMIT 1
        """,
        (ticker, dt_iso),
    ).fetchone()

    if row:
        snapped_at = datetime.fromisoformat(row["snapped_at"].replace("Z", "+00:00"))
        gap_s = (snapped_at - decision_time_utc).total_seconds()
        if gap_s <= allow_after_s:
            return dict(row), "after_tolerance"

    return None, "none"


# ── Fill quality ───────────────────────────────────────────────────────────────

def _assess_fill_quality(
    snap: dict,
    snap_age_s: float,
    max_age_s: int,
    direction: str,
    quality_note: str,
    spread_threshold: int = WIDE_SPREAD_THRESHOLD,
) -> tuple[str, str]:
    """Return (fill_quality, fill_quality_reason).

    fill_quality values: usable | stale_snapshot | wide_spread | invalid_book | no_ask
    """
    # Stale check (quality_note already computed by _find_snapshot)
    if quality_note == "stale" or snap_age_s > max_age_s:
        return "stale_snapshot", f"age_{int(snap_age_s)}s_gt_{max_age_s}s"

    yes_bid = snap.get("yes_bid") or 0
    yes_ask = snap.get("yes_ask") or 0
    no_bid  = snap.get("no_bid")  or 0
    no_ask  = snap.get("no_ask")  or 0

    # Absurd book guard
    if yes_bid <= ABSURD_BID_MAX and yes_ask >= ABSURD_ASK_MIN:
        return "invalid_book", f"absurd_yes_{yes_bid}_{yes_ask}"

    # Ask availability for the chosen direction
    fill_ask = yes_ask if direction == "YES" else no_ask
    if not fill_ask or fill_ask <= 0:
        return "no_ask", f"no_{direction.lower()}_ask"

    # Spread check
    spread = snap.get("spread_cents") or (yes_ask - yes_bid)
    if spread >= spread_threshold:
        return "wide_spread", f"spread_{spread}c"

    # Book consistency: YES ask + NO ask should sum to ~100 for a standard binary
    total = yes_ask + no_ask
    if yes_ask > 0 and no_ask > 0 and (total < 99 or total > 101):
        return "invalid_book", f"book_sums_to_{total}"

    if quality_note == "after_tolerance":
        return "usable", f"after_tolerance_{quality_note}"
    return "usable", "ok"


# ── Fill price ─────────────────────────────────────────────────────────────────

def _realistic_fill_price(snap: dict, direction: str) -> int | None:
    """YES → yes_ask; NO → no_ask. Never bid or midpoint."""
    val = snap.get("yes_ask") if direction == "YES" else snap.get("no_ask")
    if val is None:
        return None
    v = int(val)
    return v if v > 0 else None


# ── Outcome ────────────────────────────────────────────────────────────────────

def _team_won(shadow_row: dict, game_row: sqlite3.Row) -> bool | None:
    """True if the team in shadow_row won. None if undetermined (tie or parse error)."""
    try:
        home_score = int(game_row["final_home_score"] or 0)
        away_score = int(game_row["final_away_score"] or 0)
    except (TypeError, ValueError):
        return None
    if home_score == away_score:
        return None
    ha = shadow_row.get("home_away", "")
    if ha == "home":
        return home_score > away_score
    if ha == "away":
        return away_score > home_score
    return None


def _actual_result(direction: str, team_won: bool) -> str:
    """'win' if the contract direction matches what happened, else 'loss'."""
    if direction == "YES":
        return "win" if team_won else "loss"
    # NO = bet team loses
    return "win" if not team_won else "loss"


# ── P&L ───────────────────────────────────────────────────────────────────────

def _pnl(fill_price: int, won: bool) -> float:
    """Binary contract P&L per 1 contract (cents). win = 100 - fill, loss = -fill."""
    return round((100 - fill_price) if won else -fill_price, 2)


def _fee_adjusted_pnl(fill_price: int, won: bool, fee_buffer: float = FEE_BUFFER_CENTS) -> float:
    """Subtract fee_buffer from winnings only (fee is baked into the ask on losses)."""
    raw = _pnl(fill_price, won)
    return round(raw - fee_buffer if won else raw, 2)


# ── Outcome DB lookup ──────────────────────────────────────────────────────────

def _lookup_game(conn: sqlite3.Connection, shadow_row: dict) -> sqlite3.Row | None:
    game_id   = shadow_row.get("game", "") or shadow_row.get("game_id", "")
    game_date = shadow_row.get("game_date", "")
    if not game_date:
        return None
    return conn.execute(
        "SELECT * FROM mlb_games WHERE game_id = ? AND game_date = ?",
        (game_id, game_date),
    ).fetchone()


# ── SBR lookup ────────────────────────────────────────────────────────────────

def _load_sbr_index(date: str) -> dict:
    """Load SBR consensus for `date`. Returns dict keyed by (game_date, home_abbr)."""
    index: dict = {}
    if not SBR_CONSENSUS.exists():
        return index
    with open(SBR_CONSENSUS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("game_date") == date:
                key = (r["game_date"], r.get("home_abbr", ""))
                index[key] = r
    return index


def _lookup_sbr(shadow_row: dict, sbr_index: dict) -> dict:
    """Return {'open': float, 'close': float} for the team's no-vig prob from SBR."""
    game_date = shadow_row.get("game_date", "")
    game_id   = shadow_row.get("game", "") or shadow_row.get("game_id", "")
    home_abbr = game_id.split("@")[1] if "@" in game_id else ""
    sbr = sbr_index.get((game_date, home_abbr))
    if not sbr:
        return {}

    ha        = shadow_row.get("home_away", "")
    direction = shadow_row.get("direction", "YES")

    try:
        if direction == "YES":
            if ha == "home":
                open_prob  = float(sbr.get("home_no_vig_open_avg") or 0)
                close_prob = float(sbr.get("home_no_vig_avg")      or 0)
            else:
                open_prob  = float(sbr.get("away_no_vig_open_avg") or 0)
                close_prob = float(sbr.get("away_no_vig_avg")      or 0)
        else:
            # NO = bet team loses → complement of team win prob
            if ha == "home":
                team_open  = float(sbr.get("home_no_vig_open_avg") or 0)
                team_close = float(sbr.get("home_no_vig_avg")      or 0)
            else:
                team_open  = float(sbr.get("away_no_vig_open_avg") or 0)
                team_close = float(sbr.get("away_no_vig_avg")      or 0)
            open_prob  = (1.0 - team_open)  if team_open  > 0 else 0.0
            close_prob = (1.0 - team_close) if team_close > 0 else 0.0
    except (TypeError, ValueError):
        return {}

    return {"open": open_prob, "close": close_prob}


# ── Main reconcile ────────────────────────────────────────────────────────────

def _reconcile_row(
    shadow_row: dict,
    conn: sqlite3.Connection,
    sbr_index: dict,
    max_age_s: int,
    allow_after_s: int,
    fee_buffer: float,
) -> dict:
    """Produce one fill_reconciliation row from one shadow row."""
    out: dict = {k: "" for k in RECON_FIELDS}

    # Pass-through from shadow log
    for field in [
        "shadow_id", "game_date", "game", "team", "lane", "direction",
        "market_ticker", "decision_time_utc", "calibrated_probability",
        "estimated_ask_cents", "estimated_net_edge_cents",
    ]:
        out[field] = shadow_row.get(field, "")

    ticker    = shadow_row.get("market_ticker", "")
    direction = shadow_row.get("direction", "YES")
    out["fee_buffer_cents"] = fee_buffer

    # No ticker → near-miss or unmatched; can't look up snapshot
    if not ticker:
        out["fill_quality"]        = "missing_orderbook"
        out["fill_quality_reason"] = "no_market_ticker"
        out["outcome_status"]      = "pending"
        return out

    # Parse decision time
    try:
        decision_time = datetime.fromisoformat(
            shadow_row["decision_time_utc"].replace("Z", "+00:00")
        )
    except (KeyError, ValueError):
        out["fill_quality"]        = "missing_orderbook"
        out["fill_quality_reason"] = "bad_decision_time"
        out["outcome_status"]      = "pending"
        return out

    # Find snapshot
    snap, quality_note = _find_snapshot(conn, ticker, decision_time, max_age_s, allow_after_s)

    if snap is None:
        out["fill_quality"]        = "missing_orderbook"
        out["fill_quality_reason"] = "no_snapshot_found"
        out["outcome_status"]      = "pending"
        return out

    # Compute snapshot age (positive = before decision, negative = after)
    snapped_at = datetime.fromisoformat(snap["snapped_at"].replace("Z", "+00:00"))
    if snapped_at <= decision_time:
        snap_age_s = (decision_time - snapped_at).total_seconds()
    else:
        snap_age_s = -(snapped_at - decision_time).total_seconds()

    out["nearest_snapshot_time_utc"] = snap["snapped_at"]
    out["snapshot_age_seconds"]      = round(snap_age_s, 1)
    out["fill_source"]               = "kalshi_orderbook_snapshot"
    out["yes_bid_cents"]             = snap.get("yes_bid", "")
    out["yes_ask_cents"]             = snap.get("yes_ask", "")
    out["no_bid_cents"]              = snap.get("no_bid",  "")
    out["no_ask_cents"]              = snap.get("no_ask",  "")
    out["spread_cents"]              = snap.get("spread_cents", "")
    out["depth_at_fill"]             = ""  # ws_ticker rows have no depth levels

    fill_quality, fill_quality_reason = _assess_fill_quality(
        snap, abs(snap_age_s), max_age_s, direction, quality_note
    )
    out["fill_quality"]        = fill_quality
    out["fill_quality_reason"] = fill_quality_reason

    fill_price = _realistic_fill_price(snap, direction)
    if fill_price is not None:
        out["realistic_fill_price_cents"] = fill_price
        try:
            calib_prob = float(shadow_row.get("calibrated_probability") or 0)
        except ValueError:
            calib_prob = 0.0
        out["net_edge_at_fill_cents"] = round(calib_prob * 100 - fill_price - fee_buffer, 2)
        out["breakeven_probability"]  = round(fill_price / 100, 4)

    # Outcome
    game_row = _lookup_game(conn, shadow_row)
    if game_row is None:
        out["outcome_status"] = "pending"
    elif not game_row["is_final"]:
        out["outcome_status"] = "pending"
    else:
        won = _team_won(shadow_row, game_row)
        if won is None:
            out["outcome_status"] = "missing_actuals"
        else:
            result = _actual_result(direction, won)
            out["actual_result"]  = result
            out["outcome_status"] = "graded"
            if fill_price is not None:
                did_win = (result == "win")
                out["pnl_per_1_contract_cents"] = _pnl(fill_price, did_win)
                out["fee_adjusted_pnl_cents"]   = _fee_adjusted_pnl(fill_price, did_win, fee_buffer)

    # CLV
    sbr_probs = _lookup_sbr(shadow_row, sbr_index)
    if sbr_probs:
        try:
            calib_prob = float(shadow_row.get("calibrated_probability") or 0)
            if sbr_probs.get("open", 0) > 0:
                out["clv_open_points"] = round((calib_prob - sbr_probs["open"]) * 100, 2)
            if sbr_probs.get("close", 0) > 0:
                out["clv_current_or_close_points"] = round(
                    (calib_prob - sbr_probs["close"]) * 100, 2
                )
        except (TypeError, ValueError):
            pass

    return out


# ── Summary + verdict ─────────────────────────────────────────────────────────

def _verdict(rows: list[dict]) -> str:
    if not rows:
        return "No shadow candidates for this date."
    usable  = [r for r in rows if r.get("fill_quality") == "usable"]
    if not usable:
        return "Candidates existed, but fill quality failed (stale/wide/missing)."
    graded  = [r for r in rows if r.get("outcome_status") == "graded"]
    if not graded:
        return "Candidates existed and realistic fill data recorded — awaiting outcomes."
    wins    = sum(1 for r in graded if r.get("actual_result") == "win")
    rate    = wins / len(graded)
    edges   = [float(r["net_edge_at_fill_cents"]) for r in usable if r.get("net_edge_at_fill_cents") not in ("", None)]
    avg_edge = sum(edges) / len(edges) if edges else 0.0
    if avg_edge > 0:
        return (
            f"Candidates existed and realistic fill preserved edge "
            f"(avg {avg_edge:+.1f}c, hit {wins}/{len(graded)} = {rate:.0%})."
        )
    return (
        f"Candidate edge disappeared at fill "
        f"(avg {avg_edge:+.1f}c, hit {wins}/{len(graded)} = {rate:.0%})."
    )


def _write_summary(rows: list[dict], summary_path: Path, date: str) -> None:
    total  = len(rows)
    usable = [r for r in rows if r.get("fill_quality") == "usable"]
    graded = [r for r in rows if r.get("outcome_status") == "graded"]
    stale  = sum(1 for r in rows if r.get("fill_quality") == "stale_snapshot")
    wide   = sum(1 for r in rows if r.get("fill_quality") == "wide_spread")
    miss   = sum(1 for r in rows if r.get("fill_quality") == "missing_orderbook")
    wins   = sum(1 for r in graded if r.get("actual_result") == "win")

    pnl_vals = [
        float(r["pnl_per_1_contract_cents"])
        for r in graded
        if r.get("pnl_per_1_contract_cents") not in ("", None)
    ]
    est_edges = [
        float(r["estimated_net_edge_cents"])
        for r in usable
        if r.get("estimated_net_edge_cents") not in ("", None)
    ]
    fill_edges = [
        float(r["net_edge_at_fill_cents"])
        for r in usable
        if r.get("net_edge_at_fill_cents") not in ("", None)
    ]

    avg_est  = sum(est_edges)  / len(est_edges)  if est_edges  else None
    avg_fill = sum(fill_edges) / len(fill_edges) if fill_edges else None
    total_pnl = sum(pnl_vals)

    price_drifts = []
    for r in usable:
        try:
            est  = float(r.get("estimated_ask_cents")        or 0)
            fill = float(r.get("realistic_fill_price_cents") or 0)
            if est and fill:
                price_drifts.append(abs(fill - est))
        except (TypeError, ValueError):
            pass
    any_drift = sum(1 for d in price_drifts if d > 0)

    clv_open = [
        float(r["clv_open_points"])
        for r in rows
        if r.get("clv_open_points") not in ("", None)
    ]

    lines = [
        f"# Fill Reconciliation Summary — {date}",
        "",
        f"**Verdict:** {_verdict(rows)}",
        "",
        "## Counts",
        f"- Shadow candidates: {total}",
        f"- Usable fills: {len(usable)}",
        f"- Graded: {len(graded)} | Pending: {total - len(graded)}",
        f"- Stale snapshots: {stale} | Wide spread: {wide} | Missing orderbook: {miss}",
        "",
        "## Edge",
        f"- Avg estimated edge: {f'{avg_est:+.2f}c' if avg_est is not None else 'n/a'}",
        f"- Avg fill edge: {f'{avg_fill:+.2f}c' if avg_fill is not None else 'n/a'}",
        f"- Price drift vs estimate: {any_drift}/{len(price_drifts)} rows had drift",
        "",
        "## Outcomes (graded only)",
    ]
    if graded:
        lines.append(f"- Hit rate: {wins}/{len(graded)} = {wins/len(graded):.1%}")
        lines.append(f"- Total P&L: {total_pnl:+.2f}c per contract" if pnl_vals else "- Total P&L: n/a")
    else:
        lines.append("- Hit rate: n/a (no graded rows)")

    if clv_open:
        avg_clv = sum(clv_open) / len(clv_open)
        lines.append(f"- Avg CLV vs SBR open: {avg_clv:+.2f} pp ({len(clv_open)} rows)")

    lines += ["", f"_Generated {datetime.now(timezone.utc).isoformat()}_"]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines), encoding="utf-8")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile shadow review log rows against Kalshi orderbook snapshots."
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Slate date to reconcile (YYYY-MM-DD). Default: today UTC.",
    )
    parser.add_argument(
        "--max-snapshot-age-seconds",
        type=int,
        default=120,
        help="Reject snapshots older than this many seconds before decision time. Default: 120.",
    )
    parser.add_argument(
        "--allow-after-seconds",
        type=int,
        default=60,
        help="Allow snapshots up to this many seconds AFTER decision time. Default: 60.",
    )
    parser.add_argument(
        "--fee-buffer",
        type=float,
        default=FEE_BUFFER_CENTS,
        help=f"Fee buffer in cents subtracted from winning P&L. Default: {FEE_BUFFER_CENTS}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print reconciliation results without writing any files.",
    )
    args = parser.parse_args()

    if not SHADOW_LOG.exists():
        print(
            "[ev_fill_reconciler] No shadow review log found. "
            "Run ev_shadow_review_log.py first.",
            file=sys.stderr,
        )
        return

    # Load shadow rows for this date
    shadow_rows: list[dict] = []
    with open(SHADOW_LOG, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("game_date") == args.date:
                shadow_rows.append(r)

    if not shadow_rows:
        print(f"[ev_fill_reconciler] No shadow rows for {args.date}. Nothing to reconcile.")
        _write_summary([], RECON_SUMMARY, args.date)
        return

    if not KALSHI_DB.exists():
        print(f"[ev_fill_reconciler] ERROR: {KALSHI_DB} not found.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(KALSHI_DB))
    conn.row_factory = sqlite3.Row
    sbr_index = _load_sbr_index(args.date)

    recon_rows: list[dict] = []
    for row in shadow_rows:
        recon_rows.append(
            _reconcile_row(
                row, conn, sbr_index,
                args.max_snapshot_age_seconds,
                args.allow_after_seconds,
                args.fee_buffer,
            )
        )
    conn.close()

    if args.dry_run:
        print(f"[DRY RUN] [ev_fill_reconciler] {args.date}: {len(recon_rows)} rows")
        for r in recon_rows:
            print(
                f"  {r['shadow_id']} | fill_quality={r['fill_quality']} "
                f"| fill={r.get('realistic_fill_price_cents', '?')}c "
                f"| net_edge={r.get('net_edge_at_fill_cents', '?')}c "
                f"| outcome={r.get('outcome_status', '?')}"
            )
        return

    RECON_DIR.mkdir(parents=True, exist_ok=True)

    # Accumulating CSV: keep other-date rows, replace today's
    all_rows: list[dict] = []
    if RECON_CSV.exists():
        with open(RECON_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game_date") != args.date:
                    all_rows.append(r)
    all_rows.extend(recon_rows)

    with open(RECON_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECON_FIELDS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in RECON_FIELDS})

    # Latest: today only
    with open(LATEST_RECON, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECON_FIELDS)
        w.writeheader()
        for r in recon_rows:
            w.writerow({k: r.get(k, "") for k in RECON_FIELDS})

    _write_summary(recon_rows, RECON_SUMMARY, args.date)

    usable = sum(1 for r in recon_rows if r["fill_quality"] == "usable")
    graded = sum(1 for r in recon_rows if r["outcome_status"] == "graded")
    print(
        f"[ev_fill_reconciler] {args.date}: {len(recon_rows)} rows | "
        f"{usable} usable fills | {graded} graded outcomes"
    )
    print(f"  CSV:     {RECON_CSV}")
    print(f"  Latest:  {LATEST_RECON}")
    print(f"  Summary: {RECON_SUMMARY}")


if __name__ == "__main__":
    main()
