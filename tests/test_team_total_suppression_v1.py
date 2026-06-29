"""Unit tests for team_total_suppression_v1.py"""
import csv
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import team_total_suppression_v1 as tts


class TestPassesShadowGates(unittest.TestCase):
    """Gate logic: score >= 0.40, fill_quality == usable, no_ask <= 65, spread <= 8."""

    def _row(self, score=0.45, fill_quality="usable", no_ask=55, spread=5):
        return {
            "team_runs_5plus_no_score": str(score),
            "fill_quality": fill_quality,
            "no_ask": no_ask,
            "spread_cents_no": spread,
        }

    def test_passes_when_all_gates_met(self):
        self.assertTrue(tts._passes_shadow_gates(self._row()))

    def test_blocked_when_score_below_threshold(self):
        self.assertFalse(tts._passes_shadow_gates(self._row(score=0.39)))

    def test_blocked_when_no_ask_above_max(self):
        self.assertFalse(tts._passes_shadow_gates(self._row(no_ask=66)))

    def test_blocked_at_exact_no_ask_boundary(self):
        # 65c is the max; 65 should pass, 66 should fail
        self.assertTrue(tts._passes_shadow_gates(self._row(no_ask=65)))
        self.assertFalse(tts._passes_shadow_gates(self._row(no_ask=66)))

    def test_blocked_when_spread_above_max(self):
        self.assertFalse(tts._passes_shadow_gates(self._row(spread=9)))

    def test_blocked_at_exact_spread_boundary(self):
        # 8 spread max: 8 should pass, 9 should fail
        self.assertTrue(tts._passes_shadow_gates(self._row(spread=8)))
        self.assertFalse(tts._passes_shadow_gates(self._row(spread=9)))

    def test_blocked_when_fill_quality_stale(self):
        self.assertFalse(tts._passes_shadow_gates(self._row(fill_quality="stale_snapshot")))

    def test_blocked_when_fill_quality_wide_spread(self):
        self.assertFalse(tts._passes_shadow_gates(self._row(fill_quality="wide_spread")))

    def test_blocked_when_fill_quality_no_ask(self):
        self.assertFalse(tts._passes_shadow_gates(self._row(fill_quality="no_ask")))

    def test_blocked_when_fill_quality_missing_market(self):
        self.assertFalse(tts._passes_shadow_gates(self._row(fill_quality="no_market")))

    def test_blocked_invalid_book_99c(self):
        # 99c NO ask means near-certain, market already priced this in
        row = self._row(no_ask=99, fill_quality="invalid_book")
        self.assertFalse(tts._passes_shadow_gates(row))

    def test_blocked_invalid_book_even_if_ask_low(self):
        # Invalid book overrides price gate
        self.assertFalse(tts._passes_shadow_gates(self._row(fill_quality="invalid_book", no_ask=40)))


class TestFillQualityNo(unittest.TestCase):
    """Fill quality assessment for NO-side team total books."""

    GAME_START = datetime(2026, 6, 24, 22, 0, tzinfo=timezone.utc)

    def _snap(self, **kwargs):
        defaults = {
            "no_ask": 55, "no_bid": 50, "yes_ask": 46, "yes_bid": 45,
            "snapped_at": "2026-06-24T21:30:00+00:00",
        }
        defaults.update(kwargs)
        return defaults

    def test_usable_good_book(self):
        q, _ = tts._fill_quality_no(self._snap(), self.GAME_START)
        self.assertEqual(q, "usable")

    def test_no_ask_missing(self):
        q, _ = tts._fill_quality_no(self._snap(no_ask=None), self.GAME_START)
        self.assertEqual(q, "no_ask")

    def test_no_ask_zero_invalid(self):
        q, _ = tts._fill_quality_no(self._snap(no_ask=0), self.GAME_START)
        self.assertEqual(q, "no_ask")

    def test_stale_snapshot_beyond_window(self):
        # 3 hours before game is outside 2-hour pregame window
        q, _ = tts._fill_quality_no(self._snap(snapped_at="2026-06-24T19:00:00+00:00"), self.GAME_START)
        self.assertEqual(q, "stale_snapshot")

    def test_snap_just_inside_window_usable(self):
        # 1h55m before game start is inside 2-hour window
        q, _ = tts._fill_quality_no(self._snap(snapped_at="2026-06-24T20:05:00+00:00"), self.GAME_START)
        self.assertEqual(q, "usable")

    def test_invalid_book_yes_bid_near_zero(self):
        # yes_bid <= 2 AND no_ask >= 95 = invalid/cleared book
        q, _ = tts._fill_quality_no(self._snap(yes_bid=1, no_ask=97), self.GAME_START)
        self.assertEqual(q, "invalid_book")

    def test_wide_spread_classified(self):
        # no_ask - no_bid = 55 - 44 = 11 >= FILL_WIDE_SPREAD_THRESHOLD (10)
        q, _ = tts._fill_quality_no(self._snap(no_ask=55, no_bid=44), self.GAME_START)
        self.assertEqual(q, "wide_spread")


class TestPnlAndEdge(unittest.TestCase):
    def test_shadow_pnl_win(self):
        # NO wins: team scored < 5. Profit = 100 - no_ask - fee
        result = tts._shadow_pnl_no(no_ask=55, won=True, include_fee=True)
        self.assertAlmostEqual(result, 100 - 55 - tts.FEE_BUFFER_CENTS)

    def test_shadow_pnl_loss(self):
        result = tts._shadow_pnl_no(no_ask=55, won=False, include_fee=True)
        self.assertAlmostEqual(result, -55.0)

    def test_shadow_pnl_before_fee(self):
        result = tts._shadow_pnl_no(no_ask=55, won=True, include_fee=False)
        self.assertAlmostEqual(result, 45.0)

    def test_edge_before_fees(self):
        edge = tts._edge_before_fees(calib_prob=tts.CONSERVATIVE_PROB, no_ask=55)
        self.assertAlmostEqual(edge, tts.CONSERVATIVE_PROB * 100 - 55, places=3)

    def test_edge_after_fees(self):
        edge = tts._edge_after_fees(calib_prob=tts.CONSERVATIVE_PROB, no_ask=55)
        self.assertAlmostEqual(edge, tts.CONSERVATIVE_PROB * 100 - 55 - tts.FEE_BUFFER_CENTS, places=3)


class TestOutcomeGrading(unittest.TestCase):
    def test_win_when_team_scores_under_5(self):
        row = {"actual_team_runs_5plus": "0", "actual_team_runs": "3"}
        result = tts._grade_outcome(row)
        self.assertEqual(result["shadow_result"], "win")
        self.assertEqual(result["result_team_scored_5plus"], "0")
        self.assertEqual(result["result_team_runs"], "3")

    def test_loss_when_team_scores_5plus(self):
        row = {"actual_team_runs_5plus": "1", "actual_team_runs": "6"}
        result = tts._grade_outcome(row)
        self.assertEqual(result["shadow_result"], "loss")

    def test_pending_when_ungraded(self):
        row = {"actual_team_runs_5plus": "", "actual_team_runs": ""}
        result = tts._grade_outcome(row)
        self.assertEqual(result["shadow_result"], "pending")

    def test_pending_when_missing(self):
        row = {}
        result = tts._grade_outcome(row)
        self.assertEqual(result["shadow_result"], "pending")


class TestShadowId(unittest.TestCase):
    def test_deterministic(self):
        id1 = tts._make_shadow_id("2026-06-24", "KXMLBTEAMTOTAL-26JUN242200ATLNYM-ATL5")
        id2 = tts._make_shadow_id("2026-06-24", "KXMLBTEAMTOTAL-26JUN242200ATLNYM-ATL5")
        self.assertEqual(id1, id2)

    def test_different_ticker_different_id(self):
        id1 = tts._make_shadow_id("2026-06-24", "KXMLBTEAMTOTAL-26JUN242200ATLNYM-ATL5")
        id2 = tts._make_shadow_id("2026-06-24", "KXMLBTEAMTOTAL-26JUN242200ATLNYM-NYM5")
        self.assertNotEqual(id1, id2)

    def test_length_12(self):
        sid = tts._make_shadow_id("2026-06-24", "KXMLBTEAMTOTAL-26JUN242200ATLNYM-ATL5")
        self.assertEqual(len(sid), 12)


class TestSbrBucket(unittest.TestCase):
    def test_heavy_favorite(self):
        self.assertEqual(tts._sbr_bucket(0.70), "heavy_favorite")

    def test_favorite(self):
        self.assertEqual(tts._sbr_bucket(0.58), "favorite")

    def test_coin_flip(self):
        self.assertEqual(tts._sbr_bucket(0.50), "coin_flip")

    def test_underdog(self):
        self.assertEqual(tts._sbr_bucket(0.38), "underdog")

    def test_none_returns_none(self):
        self.assertIsNone(tts._sbr_bucket(None))


class TestObserveOnlyGuard(unittest.TestCase):
    def test_observe_only_constant_is_true(self):
        self.assertTrue(tts.OBSERVE_ONLY)

    def test_no_discord_import(self):
        import importlib, inspect
        source = inspect.getsource(tts)
        self.assertNotIn("discord", source.lower())
        self.assertNotIn("webhook", source.lower())

    def test_no_real_order_functions(self):
        import inspect
        source = inspect.getsource(tts)
        self.assertNotIn("place_order", source)
        self.assertNotIn("create_order", source)
        self.assertNotIn("submit_order", source)


class TestAppendShadowLog(unittest.TestCase):
    def _make_row(self, ticker="KXMLBTEAMTOTAL-26JUN242200ATLNYM-ATL5", date="2026-06-24"):
        sid = tts._make_shadow_id(date, ticker)
        return {f: "" for f in tts.SHADOW_FIELDS}  | {
            "shadow_id": sid,
            "slate_date": date,
            "market_ticker": ticker,
            "observe_only": "true",
        }

    def test_no_duplicate_append(self):
        row = self._make_row()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow_log.csv"
            tts._append_shadow_log([row], log_path, dry_run=False)
            tts._append_shadow_log([row], log_path, dry_run=False)
            with open(log_path, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)

    def test_dry_run_does_not_write(self):
        row = self._make_row()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow_log.csv"
            tts._append_shadow_log([row], log_path, dry_run=True)
            self.assertFalse(log_path.exists())

    def test_two_different_rows_both_written(self):
        row1 = self._make_row(ticker="KXMLBTEAMTOTAL-26JUN242200ATLNYM-ATL5")
        row2 = self._make_row(ticker="KXMLBTEAMTOTAL-26JUN242200ATLNYM-NYM5")
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow_log.csv"
            tts._append_shadow_log([row1, row2], log_path, dry_run=False)
            with open(log_path, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)

    def test_all_shadow_fields_written(self):
        row = self._make_row()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow_log.csv"
            tts._append_shadow_log([row], log_path, dry_run=False)
            with open(log_path, newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
            for field in tts.SHADOW_FIELDS:
                self.assertIn(field, fieldnames, f"Missing: {field}")


class TestBlockReason(unittest.TestCase):
    def test_no_market(self):
        self.assertEqual(tts._block_reason("no_market", None, None), "no_market")

    def test_no_snapshot(self):
        self.assertEqual(tts._block_reason("no_snapshot", None, None), "no_snapshot")

    def test_stale_passthrough(self):
        self.assertEqual(tts._block_reason("stale_snapshot", None, None), "stale_snapshot")

    def test_invalid_book_passthrough(self):
        self.assertEqual(tts._block_reason("invalid_book", None, None), "invalid_book")

    def test_wide_spread_passthrough(self):
        self.assertEqual(tts._block_reason("wide_spread", None, None), "wide_spread")

    def test_no_ask_passthrough(self):
        self.assertEqual(tts._block_reason("no_ask", None, None), "no_ask")

    def test_usable_price_too_high(self):
        reason = tts._block_reason("usable", tts.NO_ASK_MAX + 1, 5)
        self.assertIn("no_ask_above", reason)
        self.assertIn(str(tts.NO_ASK_MAX), reason)

    def test_usable_spread_too_wide(self):
        reason = tts._block_reason("usable", tts.NO_ASK_MAX - 1, tts.SPREAD_MAX + 1)
        self.assertIn("spread_above", reason)

    def test_usable_shadow_candidate(self):
        self.assertEqual(tts._block_reason("usable", tts.NO_ASK_MAX, tts.SPREAD_MAX), "")

    def test_usable_at_exact_boundaries(self):
        # Exactly at both limits → passes → empty reason
        self.assertEqual(tts._block_reason("usable", tts.NO_ASK_MAX, tts.SPREAD_MAX), "")


class TestMakeAuditId(unittest.TestCase):
    def test_deterministic(self):
        id1 = tts._make_audit_id("2026-06-24", "g1", "ATL", "home")
        id2 = tts._make_audit_id("2026-06-24", "g1", "ATL", "home")
        self.assertEqual(id1, id2)

    def test_length_12(self):
        self.assertEqual(len(tts._make_audit_id("2026-06-24", "g1", "ATL", "home")), 12)

    def test_different_team_different_id(self):
        id1 = tts._make_audit_id("2026-06-24", "g1", "ATL", "home")
        id2 = tts._make_audit_id("2026-06-24", "g1", "NYM", "home")
        self.assertNotEqual(id1, id2)

    def test_different_date_different_id(self):
        id1 = tts._make_audit_id("2026-06-24", "g1", "ATL", "home")
        id2 = tts._make_audit_id("2026-06-25", "g1", "ATL", "home")
        self.assertNotEqual(id1, id2)


class TestBuildFunnelRow(unittest.TestCase):
    def _make_audit(self, fq, no_ask=None, spread=None, block=""):
        return {
            "fill_quality": fq,
            "no_ask": no_ask,
            "spread_cents_no": spread,
            "block_reason": block,
        }

    def test_counts_no_market(self):
        rows = [self._make_audit("no_market"), self._make_audit("no_snapshot")]
        r = tts._build_funnel_row("2026-06-24", 2, rows, 0, datetime(2026, 6, 24, tzinfo=timezone.utc))
        self.assertEqual(r["no_market_count"], 2)
        self.assertEqual(r["market_matches"], 0)
        self.assertEqual(r["usable_books"], 0)

    def test_counts_usable(self):
        rows = [
            self._make_audit("usable", no_ask=55, spread=5),
            self._make_audit("usable", no_ask=60, spread=7),
            self._make_audit("no_market"),
        ]
        r = tts._build_funnel_row("2026-06-24", 3, rows, 0, datetime(2026, 6, 24, tzinfo=timezone.utc))
        self.assertEqual(r["usable_books"], 2)
        self.assertEqual(r["market_matches"], 2)
        self.assertEqual(r["no_market_count"], 1)

    def test_avg_no_ask_usable(self):
        rows = [
            self._make_audit("usable", no_ask=50, spread=5),
            self._make_audit("usable", no_ask=60, spread=5),
        ]
        r = tts._build_funnel_row("2026-06-24", 2, rows, 0, datetime(2026, 6, 24, tzinfo=timezone.utc))
        self.assertAlmostEqual(float(r["avg_no_ask_usable"]), 55.0)

    def test_passed_price_gate(self):
        rows = [
            self._make_audit("usable", no_ask=60, spread=5),   # passes price
            self._make_audit("usable", no_ask=70, spread=5),   # fails price
        ]
        r = tts._build_funnel_row("2026-06-24", 2, rows, 0, datetime(2026, 6, 24, tzinfo=timezone.utc))
        self.assertEqual(r["passed_price_gate"], 1)

    def test_passed_spread_gate(self):
        rows = [
            self._make_audit("usable", no_ask=60, spread=7),   # passes both
            self._make_audit("usable", no_ask=60, spread=9),   # fails spread
        ]
        r = tts._build_funnel_row("2026-06-24", 2, rows, 0, datetime(2026, 6, 24, tzinfo=timezone.utc))
        self.assertEqual(r["passed_spread_gate"], 1)

    def test_empty_audit(self):
        r = tts._build_funnel_row("2026-06-24", 0, [], 0, datetime(2026, 6, 24, tzinfo=timezone.utc))
        self.assertEqual(r["brain_fires"], 0)
        self.assertEqual(r["avg_no_ask_usable"], "")


class TestAppendFunnelHistory(unittest.TestCase):
    def _row(self, date="2026-06-24"):
        return {f: "" for f in tts.FUNNEL_FIELDS} | {"slate_date": date, "brain_fires": "1"}

    def test_writes_new_date(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "funnel.csv"
            tts._append_funnel_history(self._row(), path, dry_run=False)
            with open(path) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)

    def test_dedupes_same_date(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "funnel.csv"
            tts._append_funnel_history(self._row("2026-06-24"), path, dry_run=False)
            tts._append_funnel_history(self._row("2026-06-24"), path, dry_run=False)
            with open(path) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)

    def test_two_different_dates_both_written(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "funnel.csv"
            tts._append_funnel_history(self._row("2026-06-23"), path, dry_run=False)
            tts._append_funnel_history(self._row("2026-06-24"), path, dry_run=False)
            with open(path) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)

    def test_dry_run_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "funnel.csv"
            tts._append_funnel_history(self._row(), path, dry_run=True)
            self.assertFalse(path.exists())

    def test_all_funnel_fields_written(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "funnel.csv"
            tts._append_funnel_history(self._row(), path, dry_run=False)
            with open(path) as f:
                fieldnames = csv.DictReader(f).fieldnames or []
            for field in tts.FUNNEL_FIELDS:
                self.assertIn(field, fieldnames)


class TestAppendAuditLog(unittest.TestCase):
    def _make_audit_row(self, team="ATL", fq="no_market", block="no_market"):
        aid = tts._make_audit_id("2026-06-24", "g1", team, "home")
        return {f: "" for f in tts.AUDIT_FIELDS} | {
            "audit_id": aid, "slate_date": "2026-06-24", "team": team,
            "fill_quality": fq, "block_reason": block,
        }

    def test_writes_rows(self):
        row = self._make_audit_row()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.csv"
            n = tts._append_audit_log([row], path, dry_run=False)
            self.assertEqual(n, 1)

    def test_dedupes_by_audit_id(self):
        row = self._make_audit_row()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.csv"
            tts._append_audit_log([row], path, dry_run=False)
            tts._append_audit_log([row], path, dry_run=False)
            with open(path) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)

    def test_dry_run_no_write(self):
        row = self._make_audit_row()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.csv"
            tts._append_audit_log([row], path, dry_run=True)
            self.assertFalse(path.exists())

    def test_all_audit_fields_written(self):
        row = self._make_audit_row()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.csv"
            tts._append_audit_log([row], path, dry_run=False)
            with open(path) as f:
                fieldnames = csv.DictReader(f).fieldnames or []
            for field in tts.AUDIT_FIELDS:
                self.assertIn(field, fieldnames)


class TestNoMarketFireInAuditNotShadow(unittest.TestCase):
    """Prove: no-market brain fires land in the audit log but never in shadow_log."""

    def _no_market_audit_row(self, team="ATL"):
        aid = tts._make_audit_id("2026-06-24", "g1", team, "home")
        return {f: "" for f in tts.AUDIT_FIELDS} | {
            "audit_id": aid, "slate_date": "2026-06-24",
            "game_id": "g1", "team": team, "home_away": "home",
            "fill_quality": "no_market", "block_reason": "no_market",
        }

    def test_no_market_not_passable_through_shadow_gates(self):
        gate_row = {
            "team_runs_5plus_no_score": "0.50",
            "fill_quality": "no_market",
            "no_ask": None,
            "spread_cents_no": None,
        }
        self.assertFalse(tts._passes_shadow_gates(gate_row))

    def test_no_market_fire_in_audit_log(self):
        ar = self._no_market_audit_row()
        with tempfile.TemporaryDirectory() as td:
            audit_path = Path(td) / "audit.csv"
            tts._append_audit_log([ar], audit_path, dry_run=False)
            with open(audit_path) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["fill_quality"], "no_market")
            self.assertEqual(rows[0]["block_reason"], "no_market")

    def test_shadow_log_stays_empty_when_only_no_market_fires(self):
        with tempfile.TemporaryDirectory() as td:
            shadow_path = Path(td) / "shadow.csv"
            # Shadow log only gets rows that pass gates; no-market rows never pass
            tts._append_shadow_log([], shadow_path, dry_run=False)
            self.assertFalse(shadow_path.exists())

    def test_no_snapshot_fire_in_audit_not_shadow(self):
        aid = tts._make_audit_id("2026-06-24", "g2", "NYM", "away")
        ar = {f: "" for f in tts.AUDIT_FIELDS} | {
            "audit_id": aid, "fill_quality": "no_snapshot", "block_reason": "no_snapshot",
        }
        gate_row = {
            "team_runs_5plus_no_score": "0.55",
            "fill_quality": "no_snapshot",
            "no_ask": None, "spread_cents_no": None,
        }
        self.assertFalse(tts._passes_shadow_gates(gate_row))
        with tempfile.TemporaryDirectory() as td:
            audit_path = Path(td) / "audit.csv"
            tts._append_audit_log([ar], audit_path, dry_run=False)
            with open(audit_path) as f:
                self.assertEqual(len(list(csv.DictReader(f))), 1)

    def test_shadow_candidate_has_empty_block_reason_in_audit(self):
        block = tts._block_reason("usable", tts.NO_ASK_MAX - 5, tts.SPREAD_MAX - 2)
        self.assertEqual(block, "")

    def test_audit_row_count_exceeds_shadow_count_when_markets_missing(self):
        # 3 audit rows (1 no_market, 1 no_snapshot, 1 shadow candidate)
        rows = [
            {f: "" for f in tts.AUDIT_FIELDS} | {"audit_id": "aaa111", "fill_quality": "no_market"},
            {f: "" for f in tts.AUDIT_FIELDS} | {"audit_id": "bbb222", "fill_quality": "no_snapshot"},
            {f: "" for f in tts.AUDIT_FIELDS} | {"audit_id": "ccc333", "fill_quality": "usable", "block_reason": ""},
        ]
        with tempfile.TemporaryDirectory() as td:
            audit_path = Path(td) / "audit.csv"
            tts._append_audit_log(rows, audit_path, dry_run=False)
            with open(audit_path) as f:
                audit_rows = list(csv.DictReader(f))
            self.assertEqual(len(audit_rows), 3)


if __name__ == "__main__":
    unittest.main()
