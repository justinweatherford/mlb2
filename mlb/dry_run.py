"""
mlb/dry_run.py — Pre-Slate End-to-End Dry Run v1.

Inserts synthetic rows under an isolated test date (2099-01-01) and verifies
the full learning pipeline:
  candidate -> tape -> paper_setup -> good_entry_eval -> weather -> monitor -> report

Read-only for real data. No candidate generation. No scoring changes.
No action labels. No order placement. No network calls.
"""
from __future__ import annotations

import sqlite3

DRY_RUN_DATE = "2099-01-01"
DRY_RUN_GAME_PK = 999_999_999
DRY_RUN_TICKER = "KXMLB-DRYRUN-TEST"
DRY_RUN_GAME_ID = "DRY_DRY_RUN_2099-01-01"


# ── Step helper ───────────────────────────────────────────────────────────────

def _step(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail}


# ── Synthetic data ────────────────────────────────────────────────────────────

def _insert_synthetic_data(conn: sqlite3.Connection, date_str: str = DRY_RUN_DATE) -> None:
    """
    Insert isolated synthetic rows for the dry run.
    Uses DRY_RUN_GAME_PK and DRY_RUN_TICKER to avoid collision with real data.
    """
    at = f"{date_str}T18:00:00"
    at10 = f"{date_str}T10:00:00"

    conn.execute(
        "INSERT OR IGNORE INTO mlb_games "
        "(game_pk,game_date,away_team,home_team,away_abbr,home_abbr,"
        "status,is_final,last_checked_at,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (DRY_RUN_GAME_PK, date_str, "Dry Run Away", "Dry Run Home",
         "DRY", "RUN", "Live", 0, at, at10),
    )

    conn.execute(
        "INSERT INTO mlb_game_states "
        "(game_pk,checked_at,status,inning,inning_half,outs,away_score,home_score) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (DRY_RUN_GAME_PK, at, "Live", 3, "top", 0, 0, 0),
    )

    conn.execute(
        "INSERT INTO candidate_events "
        "(candidate_type,game_pk,game_id,market_ticker,market_type,"
        "settlement_horizon,status,derivative_type,read_type,"
        "baseball_support_score,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("trailing_team_total_lag_watch", DRY_RUN_GAME_PK, DRY_RUN_GAME_ID,
         DRY_RUN_TICKER, "team_total", "full_game",
         "observed_only", "team_total", "live",
         60.0, at, at),
    )

    # Three snapshots: one before, two within the after-window (+0s, +60s, +120s)
    for offset_s, suffix in [(0, "18:00:00"), (60, "18:01:00"), (120, "18:02:00")]:
        conn.execute(
            "INSERT INTO kalshi_orderbook_snapshots "
            "(market_ticker,snapped_at,mid_cents,spread_cents,"
            "yes_bid,yes_ask,raw_json,sport) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (DRY_RUN_TICKER, f"{date_str}T{suffix}", 35, 2, 34, 36, "{}", "mlb"),
        )

    conn.execute(
        "INSERT OR IGNORE INTO mlb_weather_reference "
        "(game_date,away_abbr,home_abbr,source,imported_at,"
        "wre_label,wre_score,wre_confidence) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (date_str, "DRY", "RUN", "dry_run", at10,
         "neutral", 0, "medium"),
    )

    conn.commit()


# ── Cleanup ───────────────────────────────────────────────────────────────────

def cleanup_dry_run(conn: sqlite3.Connection, date_str: str = DRY_RUN_DATE) -> None:
    """Remove all synthetic dry-run rows. Safe to call multiple times."""
    conn.execute("DELETE FROM paper_setups WHERE game_pk=?", (DRY_RUN_GAME_PK,))
    conn.execute("DELETE FROM candidate_events WHERE game_pk=?", (DRY_RUN_GAME_PK,))
    conn.execute("DELETE FROM kalshi_orderbook_snapshots WHERE market_ticker=?", (DRY_RUN_TICKER,))
    conn.execute("DELETE FROM mlb_game_states WHERE game_pk=?", (DRY_RUN_GAME_PK,))
    conn.execute("DELETE FROM mlb_games WHERE game_pk=?", (DRY_RUN_GAME_PK,))
    conn.execute(
        "DELETE FROM mlb_weather_reference WHERE game_date=? AND source='dry_run'",
        (date_str,),
    )
    conn.commit()


# ── Core dry run ──────────────────────────────────────────────────────────────

def run_dry_run(
    conn: sqlite3.Connection,
    *,
    cleanup: bool = True,
) -> dict:
    """
    Run the full pre-slate dry run using isolated synthetic data.

    Returns:
        {
            "success": bool,
            "date": str,          # DRY_RUN_DATE
            "steps": list[dict],  # each: {"name", "status", "detail"}
        }

    Read-only for real data. No candidate generation. No scoring changes.
    No action labels. No order placement.
    """
    steps: list[dict] = []
    date_str = DRY_RUN_DATE

    # Step 1: DB connection
    try:
        conn.execute("SELECT 1").fetchone()
        steps.append(_step("DB connection", "PASS"))
    except Exception as e:
        steps.append(_step("DB connection", "FAIL", str(e)))
        return {"success": False, "date": date_str, "steps": steps}

    # Step 2: Insert synthetic data / candidate
    try:
        _insert_synthetic_data(conn, date_str)
        n = conn.execute(
            "SELECT COUNT(*) FROM candidate_events WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()[0]
        if n > 0:
            steps.append(_step("Candidate inserted", "PASS", f"{n} candidate(s)"))
        else:
            steps.append(_step("Candidate inserted", "FAIL", "No candidate rows found after insert"))
    except Exception as e:
        steps.append(_step("Candidate inserted", "FAIL", str(e)))
        return {"success": False, "date": date_str, "steps": steps}

    # Step 3: Market tape snapshots
    try:
        snaps = conn.execute(
            "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE market_ticker=?",
            (DRY_RUN_TICKER,)
        ).fetchone()[0]
        if snaps > 0:
            steps.append(_step("Market tape matched", "PASS", f"{snaps} snapshot(s)"))
        else:
            steps.append(_step("Market tape matched", "FAIL", "No snapshots found"))
    except Exception as e:
        steps.append(_step("Market tape matched", "FAIL", str(e)))

    # Step 4: Paper setup sync
    try:
        from mlb.paper_lifecycle import sync_paper_setups_for_date
        sync_result = sync_paper_setups_for_date(conn, date_str)
        if sync_result.get("created", 0) > 0:
            steps.append(_step("Paper setup created", "PASS",
                               f"created={sync_result['created']}"))
        else:
            steps.append(_step("Paper setup created", "FAIL",
                               f"sync result: {sync_result}"))
    except Exception as e:
        steps.append(_step("Paper setup created", "FAIL", str(e)))

    # Step 5 & 6: Entry price + Good Entry eval
    try:
        row = conn.execute(
            "SELECT entry_price_cents, good_entry_label, good_entry_score, "
            "evaluation_version, entry_snapshot_id "
            "FROM paper_setups WHERE game_pk=? LIMIT 1",
            (DRY_RUN_GAME_PK,)
        ).fetchone()

        if row and dict(row)["entry_price_cents"] is not None:
            steps.append(_step("Entry price attached", "PASS",
                               f"{dict(row)['entry_price_cents']}c"))
        else:
            ep = dict(row)["entry_price_cents"] if row else "no setup row"
            steps.append(_step("Entry price attached", "FAIL",
                               f"entry_price_cents={ep}"))

        if row and dict(row)["good_entry_label"] is not None:
            steps.append(_step("Good Entry evaluated", "PASS",
                               f"label={dict(row)['good_entry_label']} "
                               f"version={dict(row)['evaluation_version']}"))
        else:
            lbl = dict(row)["good_entry_label"] if row else "no setup row"
            steps.append(_step("Good Entry evaluated", "FAIL",
                               f"good_entry_label={lbl}"))
    except Exception as e:
        steps.append(_step("Entry price attached", "FAIL", str(e)))
        steps.append(_step("Good Entry evaluated", "FAIL", str(e)))

    # Step 7: Weather context
    try:
        w = conn.execute(
            "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND source='dry_run'",
            (date_str,)
        ).fetchone()[0]
        if w > 0:
            steps.append(_step("Weather context present", "PASS", f"{w} row(s)"))
        else:
            steps.append(_step("Weather context present", "FAIL", "No dry-run weather rows"))
    except Exception as e:
        steps.append(_step("Weather context present", "FAIL", str(e)))

    # Step 8: Live capture monitor
    try:
        from mlb.live_capture_monitor import get_live_capture_monitor
        monitor = get_live_capture_monitor(conn, date_str)
        cands = monitor.get("candidates_today", 0)
        if cands > 0:
            steps.append(_step("Live capture monitor reads it", "PASS",
                               f"candidates={cands} "
                               f"readiness={monitor.get('capture_readiness')}"))
        else:
            steps.append(_step("Live capture monitor reads it", "FAIL",
                               f"candidates_today={cands}"))
    except Exception as e:
        steps.append(_step("Live capture monitor reads it", "FAIL", str(e)))

    # Step 9: Post-slate report
    try:
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, date_str)
        setups = report["overview"]["total_paper_setups"]
        if setups > 0:
            steps.append(_step("Post-slate report reads it", "PASS",
                               f"setups={setups}"))
        else:
            steps.append(_step("Post-slate report reads it", "FAIL",
                               "total_paper_setups=0"))
    except Exception as e:
        steps.append(_step("Post-slate report reads it", "FAIL", str(e)))

    # Step 10: Cleanup
    if cleanup:
        try:
            cleanup_dry_run(conn, date_str)
            steps.append(_step("Cleanup complete", "PASS"))
        except Exception as e:
            steps.append(_step("Cleanup complete", "FAIL", str(e)))

    success = all(s["status"] == "PASS" for s in steps)
    return {"success": success, "date": date_str, "steps": steps}
