"""Unit tests for ev_fill_reconciler.py"""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ev_fill_reconciler import (
    _assess_fill_quality,
    _realistic_fill_price,
    _pnl,
    _fee_adjusted_pnl,
    _team_won,
    _actual_result,
    _find_snapshot,
    _reconcile_row,
    WIDE_SPREAD_THRESHOLD,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_conn(
    snapshots: list[dict] | None = None,
    games: list[dict] | None = None,
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE kalshi_orderbook_snapshots (
            id INTEGER PRIMARY KEY,
            market_ticker TEXT,
            snapped_at TEXT,
            yes_bid INTEGER,
            yes_ask INTEGER,
            no_bid INTEGER,
            no_ask INTEGER,
            spread_cents INTEGER,
            yes_bids_json TEXT,
            yes_asks_json TEXT
        );
        CREATE TABLE mlb_games (
            game_pk INTEGER,
            game_date TEXT,
            away_abbr TEXT,
            home_abbr TEXT,
            away_team TEXT,
            home_team TEXT,
            status TEXT,
            game_id TEXT,
            final_away_score INTEGER,
            final_home_score INTEGER,
            final_total INTEGER,
            is_final INTEGER,
            last_checked_at TEXT,
            created_at TEXT,
            game_start_time_utc TEXT
        );
    """)
    if snapshots:
        for s in snapshots:
            conn.execute(
                "INSERT INTO kalshi_orderbook_snapshots "
                "(id, market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask, spread_cents) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    s.get("id", 1),
                    s["market_ticker"],
                    s["snapped_at"],
                    s.get("yes_bid", 44),
                    s.get("yes_ask", 45),
                    s.get("no_bid",  55),
                    s.get("no_ask",  56),
                    s.get("spread_cents", 1),
                ),
            )
    if games:
        for g in games:
            conn.execute(
                "INSERT INTO mlb_games "
                "(game_id, game_date, home_abbr, away_abbr, final_home_score, final_away_score, is_final) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    g["game_id"],
                    g["game_date"],
                    g.get("home_abbr", "SF"),
                    g.get("away_abbr", "ATH"),
                    g.get("home", 3),
                    g.get("away", 5),
                    g.get("is_final", 1),
                ),
            )
    conn.commit()
    return conn


def _good_snap() -> dict:
    return {"yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}


def _base_shadow(
    ticker="KXMLB-ATH",
    direction="YES",
    calib="0.60",
    game="ATH@SF",
    ha="away",
    decision_time="2026-06-23T13:00:00+00:00",
) -> dict:
    return {
        "shadow_id":              "abc123",
        "game_date":              "2026-06-23",
        "game":                   game,
        "team":                   "ATH",
        "lane":                   "side",
        "direction":              direction,
        "market_ticker":          ticker,
        "decision_time_utc":      decision_time,
        "calibrated_probability": calib,
        "estimated_ask_cents":    "45",
        "estimated_net_edge_cents": "15.0",
        "home_away":              ha,
    }


# ── Fill price selection ───────────────────────────────────────────────────────

class TestFillPriceSelection(unittest.TestCase):
    def test_yes_fill_uses_yes_ask(self):
        self.assertEqual(_realistic_fill_price(_good_snap(), "YES"), 45)

    def test_no_fill_uses_no_ask(self):
        self.assertEqual(_realistic_fill_price(_good_snap(), "NO"), 56)

    def test_zero_yes_ask_returns_none(self):
        snap = {**_good_snap(), "yes_ask": 0}
        self.assertIsNone(_realistic_fill_price(snap, "YES"))

    def test_zero_no_ask_returns_none(self):
        snap = {**_good_snap(), "no_ask": 0}
        self.assertIsNone(_realistic_fill_price(snap, "NO"))

    def test_none_ask_returns_none(self):
        snap = {**_good_snap(), "yes_ask": None}
        self.assertIsNone(_realistic_fill_price(snap, "YES"))


# ── Fill quality ───────────────────────────────────────────────────────────────

class TestFillQuality(unittest.TestCase):
    def test_usable_clean_snapshot(self):
        q, _ = _assess_fill_quality(_good_snap(), 30, 120, "YES", "")
        self.assertEqual(q, "usable")

    def test_stale_snapshot_rejected_by_note(self):
        q, reason = _assess_fill_quality(_good_snap(), 150, 120, "YES", "stale")
        self.assertEqual(q, "stale_snapshot")

    def test_stale_snapshot_rejected_by_age(self):
        q, reason = _assess_fill_quality(_good_snap(), 200, 120, "YES", "")
        self.assertEqual(q, "stale_snapshot")
        self.assertIn("200", reason)

    def test_wide_spread_classified(self):
        snap = {**_good_snap(), "yes_bid": 40, "yes_ask": 52, "spread_cents": 12}
        q, reason = _assess_fill_quality(snap, 30, 120, "YES", "")
        self.assertEqual(q, "wide_spread")
        self.assertIn("12", reason)

    def test_absurd_book_invalid(self):
        snap = {"yes_bid": 1, "yes_ask": 99, "no_bid": 1, "no_ask": 99, "spread_cents": 98}
        q, _ = _assess_fill_quality(snap, 30, 120, "YES", "")
        self.assertEqual(q, "invalid_book")

    def test_after_tolerance_is_usable(self):
        q, reason = _assess_fill_quality(_good_snap(), 10, 120, "YES", "after_tolerance")
        self.assertEqual(q, "usable")
        self.assertIn("after_tolerance", reason)

    def test_no_ask_for_no_direction(self):
        snap = {**_good_snap(), "no_ask": 0}
        q, reason = _assess_fill_quality(snap, 30, 120, "NO", "")
        self.assertEqual(q, "no_ask")


# ── P&L ───────────────────────────────────────────────────────────────────────

class TestPnl(unittest.TestCase):
    def test_win_yes_fill_45(self):
        self.assertAlmostEqual(_pnl(45, True), 55.0)

    def test_loss_yes_fill_45(self):
        self.assertAlmostEqual(_pnl(45, False), -45.0)

    def test_win_no_fill_56(self):
        self.assertAlmostEqual(_pnl(56, True), 44.0)

    def test_loss_no_fill_56(self):
        self.assertAlmostEqual(_pnl(56, False), -56.0)

    def test_fee_adjusted_subtracts_buffer_on_win(self):
        self.assertAlmostEqual(_fee_adjusted_pnl(45, True, 1.5), 53.5)

    def test_fee_adjusted_loss_unchanged(self):
        self.assertAlmostEqual(_fee_adjusted_pnl(45, False, 1.5), -45.0)


# ── Outcome ────────────────────────────────────────────────────────────────────

class TestOutcome(unittest.TestCase):
    def _game(self, home: int, away: int) -> sqlite3.Row:
        conn = _make_conn(
            games=[{"game_id": "ATH@SF", "game_date": "2026-06-23", "home": home, "away": away}]
        )
        return conn.execute("SELECT * FROM mlb_games WHERE game_id = ?", ("ATH@SF",)).fetchone()

    def test_home_team_wins(self):
        shadow = {"home_away": "home"}
        self.assertTrue(_team_won(shadow, self._game(5, 3)))

    def test_away_team_wins(self):
        shadow = {"home_away": "away"}
        self.assertTrue(_team_won(shadow, self._game(3, 5)))

    def test_tie_returns_none(self):
        shadow = {"home_away": "home"}
        self.assertIsNone(_team_won(shadow, self._game(3, 3)))

    def test_yes_win_when_team_won(self):
        self.assertEqual(_actual_result("YES", True), "win")

    def test_yes_loss_when_team_lost(self):
        self.assertEqual(_actual_result("YES", False), "loss")

    def test_no_win_when_team_lost(self):
        self.assertEqual(_actual_result("NO", False), "win")

    def test_no_loss_when_team_won(self):
        self.assertEqual(_actual_result("NO", True), "loss")


# ── Snapshot lookup ────────────────────────────────────────────────────────────

class TestFindSnapshot(unittest.TestCase):
    def test_finds_snapshot_before_decision_time(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T13:00:00+00:00",
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        conn = _make_conn(snapshots=snaps)
        decision = datetime(2026, 6, 23, 13, 1, 0, tzinfo=timezone.utc)
        snap, note = _find_snapshot(conn, "KXMLB-ATH", decision, 120, 60)
        self.assertIsNotNone(snap)
        self.assertEqual(note, "")

    def test_missing_ticker_returns_none(self):
        conn = _make_conn()
        decision = datetime(2026, 6, 23, 13, 1, 0, tzinfo=timezone.utc)
        snap, note = _find_snapshot(conn, "UNKNOWN", decision, 120, 60)
        self.assertIsNone(snap)
        self.assertEqual(note, "none")

    def test_stale_note_when_too_old(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T12:00:00+00:00",  # 60 min before decision
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        conn = _make_conn(snapshots=snaps)
        decision = datetime(2026, 6, 23, 13, 0, 0, tzinfo=timezone.utc)
        snap, note = _find_snapshot(conn, "KXMLB-ATH", decision, 120, 60)
        self.assertIsNotNone(snap)
        self.assertEqual(note, "stale")

    def test_after_tolerance_within_limit(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T13:00:30+00:00",  # 30s after decision
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        conn = _make_conn(snapshots=snaps)
        decision = datetime(2026, 6, 23, 13, 0, 0, tzinfo=timezone.utc)
        snap, note = _find_snapshot(conn, "KXMLB-ATH", decision, 120, 60)
        self.assertIsNotNone(snap)
        self.assertEqual(note, "after_tolerance")

    def test_after_tolerance_outside_limit_returns_none(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T13:02:00+00:00",  # 120s after decision
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        conn = _make_conn(snapshots=snaps)
        decision = datetime(2026, 6, 23, 13, 0, 0, tzinfo=timezone.utc)
        snap, note = _find_snapshot(conn, "KXMLB-ATH", decision, 120, 60)
        self.assertIsNone(snap)
        self.assertEqual(note, "none")


# ── reconcile_row edge cases ───────────────────────────────────────────────────

class TestReconcileRow(unittest.TestCase):
    def test_missing_ticker_gets_missing_orderbook(self):
        conn = _make_conn()
        row = _base_shadow(ticker="")
        result = _reconcile_row(row, conn, {}, 120, 60, 1.5)
        self.assertEqual(result["fill_quality"], "missing_orderbook")
        self.assertEqual(result["fill_quality_reason"], "no_market_ticker")

    def test_graceful_no_snapshot_in_db(self):
        conn = _make_conn()
        row = _base_shadow(ticker="KXMLB-ATH-NOMATCH")
        result = _reconcile_row(row, conn, {}, 120, 60, 1.5)
        self.assertEqual(result["fill_quality"], "missing_orderbook")
        self.assertEqual(result["fill_quality_reason"], "no_snapshot_found")
        self.assertEqual(result["outcome_status"], "pending")

    def test_usable_fill_computes_net_edge(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T12:59:00+00:00",
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        conn = _make_conn(snapshots=snaps)
        row = _base_shadow(calib="0.60")
        result = _reconcile_row(row, conn, {}, 120, 60, 1.5)
        self.assertEqual(result["fill_quality"], "usable")
        self.assertEqual(result["realistic_fill_price_cents"], 45)
        # net_edge = 0.60 * 100 - 45 - 1.5 = 13.5
        self.assertAlmostEqual(float(result["net_edge_at_fill_cents"]), 13.5)

    def test_graded_win_fills_pnl(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T12:59:00+00:00",
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        # ATH (away) wins: away score > home score
        games = [{"game_id": "ATH@SF", "game_date": "2026-06-23",
                  "home": 3, "away": 5, "is_final": 1}]
        conn = _make_conn(snapshots=snaps, games=games)
        row = _base_shadow(direction="YES", ha="away")
        result = _reconcile_row(row, conn, {}, 120, 60, 1.5)
        self.assertEqual(result["outcome_status"], "graded")
        self.assertEqual(result["actual_result"], "win")
        self.assertAlmostEqual(float(result["pnl_per_1_contract_cents"]), 55.0)
        self.assertAlmostEqual(float(result["fee_adjusted_pnl_cents"]), 53.5)

    def test_graded_loss_fills_pnl(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T12:59:00+00:00",
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        # ATH (away) loses: home score > away score
        games = [{"game_id": "ATH@SF", "game_date": "2026-06-23",
                  "home": 5, "away": 3, "is_final": 1}]
        conn = _make_conn(snapshots=snaps, games=games)
        row = _base_shadow(direction="YES", ha="away")
        result = _reconcile_row(row, conn, {}, 120, 60, 1.5)
        self.assertEqual(result["outcome_status"], "graded")
        self.assertEqual(result["actual_result"], "loss")
        self.assertAlmostEqual(float(result["pnl_per_1_contract_cents"]), -45.0)
        self.assertAlmostEqual(float(result["fee_adjusted_pnl_cents"]), -45.0)

    def test_pending_when_game_not_final(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T12:59:00+00:00",
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        games = [{"game_id": "ATH@SF", "game_date": "2026-06-23",
                  "home": 0, "away": 0, "is_final": 0}]
        conn = _make_conn(snapshots=snaps, games=games)
        row = _base_shadow()
        result = _reconcile_row(row, conn, {}, 120, 60, 1.5)
        self.assertEqual(result["outcome_status"], "pending")

    def test_all_recon_fields_present(self):
        from ev_fill_reconciler import RECON_FIELDS
        conn = _make_conn()
        row = _base_shadow(ticker="")
        result = _reconcile_row(row, conn, {}, 120, 60, 1.5)
        for field in RECON_FIELDS:
            self.assertIn(field, result, f"Missing field: {field}")


if __name__ == "__main__":
    unittest.main()
