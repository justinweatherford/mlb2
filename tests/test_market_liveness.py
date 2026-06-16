"""
tests/test_market_liveness.py — Market Liveness / Repricing Validator v1 tests.

Tests for:
- Liveness metrics computation
- Stale period detection
- Unique mid count / mid range
- Spread ticker semantics parsing
- Score-event repricing windows
- Read-only validation
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect
import unittest

from mlb.market_liveness import (
    LIVE_RESPONSIVE, SLOW_BUT_MOVING, STALE, INSUFFICIENT_TAPE, SEMANTICS_UNCLEAR,
    STALE_THRESHOLD_SECONDS, REPRICING_WINDOW_SECONDS, LIVE_MOVEMENT_THRESHOLD_CENTS,
    INSUFFICIENT_TAPE_MIN_SNAPS,
    compute_ticker_liveness_metrics,
    classify_liveness_label,
    check_repricing_after_event,
    parse_spread_ticker_for_audit,
    compute_repricing_window_row,
    detect_inning_events,
    detect_lead_change_events,
    compute_type_summary,
    _parse_utc_ts,
    _epoch,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _snap(ts: str, mid: int | None, bid: int | None = None, ask: int | None = None) -> dict:
    return {"snapped_at": ts, "mid_cents": mid, "yes_bid": bid, "yes_ask": ask}


def _snaps_uniform(mid: int, count: int, start: str = "2026-06-15T20:00:00Z",
                   interval_secs: int = 60) -> list[dict]:
    """Build a list of `count` snapshots at uniform intervals."""
    from mlb.market_liveness import _parse_utc_ts
    from datetime import timedelta
    base = _parse_utc_ts(start)
    result = []
    for i in range(count):
        ts = (base + timedelta(seconds=i * interval_secs)).isoformat()
        result.append(_snap(ts, mid))
    return result


def _play_event(et: str, inn: int, half: str, away: int, home: int,
                scoring: int = 0) -> dict:
    return {
        "event_time": et,
        "inning": inn,
        "inning_half": half,
        "away_score": away,
        "home_score": home,
        "is_scoring_play": scoring,
        "event_type": "scoring_play" if scoring else None,
    }


# ── Timestamp helpers ─────────────────────────────────────────────────────────

class TestTimestampHelpers(unittest.TestCase):

    def test_parse_utc_z_suffix(self):
        dt = _parse_utc_ts("2026-06-15T20:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 20)

    def test_parse_utc_plus_suffix(self):
        dt = _parse_utc_ts("2026-06-15T20:00:00+00:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 20)

    def test_parse_utc_with_microseconds(self):
        dt = _parse_utc_ts("2026-06-15T20:00:00.123456+00:00")
        self.assertIsNotNone(dt)

    def test_parse_empty_string_returns_none(self):
        self.assertIsNone(_parse_utc_ts(""))

    def test_epoch_returns_float(self):
        ep = _epoch("2026-06-15T20:00:00Z")
        self.assertIsInstance(ep, float)

    def test_epoch_empty_returns_none(self):
        self.assertIsNone(_epoch(""))


# ── compute_ticker_liveness_metrics ──────────────────────────────────────────

class TestComputeTickerLivenessMetrics(unittest.TestCase):

    def test_empty_returns_zero_snapshot_count(self):
        m = compute_ticker_liveness_metrics([])
        self.assertEqual(m["snapshot_count"], 0)
        self.assertEqual(m["unique_mid_count"], 0)

    def test_single_snapshot_count(self):
        m = compute_ticker_liveness_metrics([_snap("2026-06-15T20:00:00Z", 50)])
        self.assertEqual(m["snapshot_count"], 1)

    def test_snapshot_count_multiple(self):
        snaps = [_snap(f"2026-06-15T20:0{i}:00Z", 50) for i in range(5)]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["snapshot_count"], 5)

    def test_unique_mid_count_no_movement(self):
        snaps = _snaps_uniform(35, 10)
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["unique_mid_count"], 1)

    def test_unique_mid_count_three_values(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T20:05:00Z", 35),
            _snap("2026-06-15T20:10:00Z", 45),
            _snap("2026-06-15T20:15:00Z", 45),
            _snap("2026-06-15T20:20:00Z", 60),
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["unique_mid_count"], 3)

    def test_mid_min_max_range(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 30),
            _snap("2026-06-15T20:05:00Z", 50),
            _snap("2026-06-15T20:10:00Z", 75),
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["mid_min"], 30)
        self.assertEqual(m["mid_max"], 75)
        self.assertEqual(m["mid_range"], 45)

    def test_total_abs_mid_movement(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T20:05:00Z", 45),   # +10
            _snap("2026-06-15T20:10:00Z", 40),   # -5
            _snap("2026-06-15T20:15:00Z", 55),   # +15
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["total_abs_mid_movement"], 30)

    def test_largest_single_move(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T20:05:00Z", 45),   # 10
            _snap("2026-06-15T20:10:00Z", 70),   # 25 ← largest
            _snap("2026-06-15T20:15:00Z", 72),   # 2
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["largest_single_move"], 25)

    def test_avg_seconds_between_snapshots(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 50),
            _snap("2026-06-15T20:05:00Z", 50),   # 300s
            _snap("2026-06-15T20:10:00Z", 50),   # 300s
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertAlmostEqual(m["avg_seconds_between_snapshots"], 300.0, places=0)

    def test_max_seconds_between_snapshots(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 50),
            _snap("2026-06-15T20:05:00Z", 50),   # 300s
            _snap("2026-06-15T22:05:00Z", 50),   # 7200s ← max
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["max_seconds_between_snapshots"], 7200.0)

    def test_all_none_mids(self):
        snaps = [
            {"snapped_at": "2026-06-15T20:00:00Z", "mid_cents": None},
            {"snapped_at": "2026-06-15T20:05:00Z", "mid_cents": None},
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["unique_mid_count"], 0)
        self.assertIsNone(m["mid_min"])
        self.assertIsNone(m["mid_max"])

    def test_first_last_snapshot_time(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 50),
            _snap("2026-06-15T20:30:00Z", 55),
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["first_snapshot_time"], "2026-06-15T20:00:00Z")
        self.assertEqual(m["last_snapshot_time"], "2026-06-15T20:30:00Z")

    def test_moved_after_flags_default_false(self):
        m = compute_ticker_liveness_metrics([_snap("2026-06-15T20:00:00Z", 50)])
        self.assertFalse(m["moved_after_score_event"])
        self.assertFalse(m["moved_after_inning_end"])
        self.assertFalse(m["moved_after_lead_change"])

    def test_sorted_by_timestamp(self):
        # Unsorted input — should still compute correctly
        snaps = [
            _snap("2026-06-15T20:10:00Z", 55),
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T20:05:00Z", 45),
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertEqual(m["mid_min"], 35)
        self.assertEqual(m["mid_max"], 55)


# ── Stale period detection ────────────────────────────────────────────────────

class TestStalePeriodDetection(unittest.TestCase):

    def test_stale_minutes_no_movement_5_intervals(self):
        # 6 snaps, 1-min apart, all mid=35 → 5 intervals × 60s = 300s = 5min
        snaps = _snaps_uniform(35, 6, interval_secs=60)
        m = compute_ticker_liveness_metrics(snaps)
        self.assertAlmostEqual(m["stale_minutes_total"], 5.0, places=1)

    def test_stale_minutes_zero_when_price_always_moves(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T20:01:00Z", 36),
            _snap("2026-06-15T20:02:00Z", 37),
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertAlmostEqual(m["stale_minutes_total"], 0.0, places=1)

    def test_longest_stale_period_two_runs(self):
        # 60-min run, then price change, then 30-min run
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T21:00:00Z", 35),   # 60min stale
            _snap("2026-06-15T21:30:00Z", 40),   # price change, 0 stale
            _snap("2026-06-15T22:00:00Z", 40),   # 30min stale
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertAlmostEqual(m["longest_stale_period_minutes"], 60.0, places=1)
        self.assertAlmostEqual(m["stale_minutes_total"], 90.0, places=1)

    def test_stale_period_at_end_of_sequence(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T20:30:00Z", 50),   # price change at 30min
            _snap("2026-06-15T21:00:00Z", 50),   # 30min stale
            _snap("2026-06-15T21:30:00Z", 50),   # 30min more stale → 60min total run
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertAlmostEqual(m["longest_stale_period_minutes"], 60.0, places=1)

    def test_none_mid_breaks_stale_run(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            {"snapped_at": "2026-06-15T20:30:00Z", "mid_cents": None},
            _snap("2026-06-15T21:00:00Z", 35),
        ]
        m = compute_ticker_liveness_metrics(snaps)
        # None interrupts both stale runs → stale_total = 0
        self.assertAlmostEqual(m["stale_minutes_total"], 0.0, places=1)

    def test_single_snapshot_no_stale(self):
        m = compute_ticker_liveness_metrics([_snap("2026-06-15T20:00:00Z", 50)])
        self.assertEqual(m["stale_minutes_total"], 0.0)
        self.assertEqual(m["longest_stale_period_minutes"], 0.0)

    def test_stale_with_90min_gap(self):
        snaps = [
            _snap("2026-06-15T20:00:00Z", 35),
            _snap("2026-06-15T21:30:00Z", 35),   # 90min stale
        ]
        m = compute_ticker_liveness_metrics(snaps)
        self.assertAlmostEqual(m["stale_minutes_total"], 90.0, places=1)
        self.assertAlmostEqual(m["longest_stale_period_minutes"], 90.0, places=1)


# ── classify_liveness_label ───────────────────────────────────────────────────

class TestClassifyLivenessLabel(unittest.TestCase):

    def _label(self, **kwargs) -> str:
        defaults = dict(
            snapshot_count=20,
            unique_mid_count=3,
            mid_range=15,
            stale_minutes_total=10.0,
            longest_stale_period_minutes=5.0,
            moved_after_score_event=False,
            moved_after_inning_end=False,
            total_abs_mid_movement=20,
            ticker_parse_failed=False,
            is_spread_type=False,
        )
        defaults.update(kwargs)
        return classify_liveness_label(**defaults)

    def test_insufficient_tape_few_snapshots(self):
        self.assertEqual(
            self._label(snapshot_count=3, unique_mid_count=2),
            INSUFFICIENT_TAPE,
        )

    def test_insufficient_tape_zero_unique_mids(self):
        self.assertEqual(
            self._label(snapshot_count=10, unique_mid_count=0),
            INSUFFICIENT_TAPE,
        )

    def test_semantics_unclear_spread_parse_failed(self):
        self.assertEqual(
            self._label(is_spread_type=True, ticker_parse_failed=True),
            SEMANTICS_UNCLEAR,
        )

    def test_semantics_unclear_only_for_spread_type(self):
        # Non-spread with parse failure shouldn't get semantics_unclear
        result = self._label(is_spread_type=False, ticker_parse_failed=True)
        self.assertNotEqual(result, SEMANTICS_UNCLEAR)

    def test_stale_single_unique_mid(self):
        self.assertEqual(
            self._label(unique_mid_count=1, stale_minutes_total=120.0),
            STALE,
        )

    def test_stale_long_stale_time_few_mids(self):
        self.assertEqual(
            self._label(unique_mid_count=2, stale_minutes_total=150.0),
            STALE,
        )

    def test_stale_long_period_few_mids(self):
        self.assertEqual(
            self._label(unique_mid_count=2, longest_stale_period_minutes=90.0),
            STALE,
        )

    def test_live_responsive_moved_after_score_with_3_mids(self):
        self.assertEqual(
            self._label(moved_after_score_event=True, unique_mid_count=3),
            LIVE_RESPONSIVE,
        )

    def test_live_responsive_moved_after_inning_with_range(self):
        self.assertEqual(
            self._label(moved_after_inning_end=True, unique_mid_count=2, mid_range=10),
            LIVE_RESPONSIVE,
        )

    def test_slow_but_moving_3_unique_mids_large_range(self):
        self.assertEqual(
            self._label(unique_mid_count=3, mid_range=20,
                        moved_after_score_event=False, stale_minutes_total=5.0),
            SLOW_BUT_MOVING,
        )

    def test_slow_but_moving_2_mids_moderate_range(self):
        self.assertEqual(
            self._label(unique_mid_count=2, mid_range=15, total_abs_mid_movement=15,
                        moved_after_score_event=False, stale_minutes_total=5.0),
            SLOW_BUT_MOVING,
        )

    def test_stale_fallback_tiny_range(self):
        self.assertEqual(
            self._label(unique_mid_count=2, mid_range=2, total_abs_mid_movement=2),
            STALE,
        )


# ── Spread ticker semantics ───────────────────────────────────────────────────

class TestParseSpreadTickerForAudit(unittest.TestCase):

    def test_parses_det_runline_2(self):
        r = parse_spread_ticker_for_audit(
            "KXMLBSPREAD-26JUN152010DETHOU-DET2", "DET", "HOU"
        )
        self.assertTrue(r["parse_success"])
        self.assertEqual(r["selected_team"], "DET")
        self.assertEqual(r["run_line"], 2)
        self.assertFalse(r["is_f5"])
        self.assertTrue(r["selected_is_away"])
        self.assertFalse(r["selected_is_home"])

    def test_parses_hou_runline_3(self):
        r = parse_spread_ticker_for_audit(
            "KXMLBSPREAD-26JUN152010DETHOU-HOU3", "DET", "HOU"
        )
        self.assertTrue(r["parse_success"])
        self.assertEqual(r["selected_team"], "HOU")
        self.assertEqual(r["run_line"], 3)
        self.assertFalse(r["selected_is_away"])
        self.assertTrue(r["selected_is_home"])

    def test_parses_f5_spread_ticker(self):
        r = parse_spread_ticker_for_audit(
            "KXMLBF5SPREAD-26JUN152010DETHOU-DET2", "DET", "HOU"
        )
        self.assertTrue(r["parse_success"])
        self.assertTrue(r["is_f5"])
        self.assertEqual(r["selected_team"], "DET")
        self.assertEqual(r["run_line"], 2)

    def test_non_spread_ticker_fails(self):
        r = parse_spread_ticker_for_audit(
            "KXMLBTEAMTOTAL-26JUN152010DETHOU-DET7", "DET", "HOU"
        )
        self.assertFalse(r["parse_success"])
        self.assertIsNone(r["selected_team"])

    def test_empty_ticker_fails(self):
        r = parse_spread_ticker_for_audit("")
        self.assertFalse(r["parse_success"])

    def test_team_not_in_game_sets_parse_note(self):
        r = parse_spread_ticker_for_audit(
            "KXMLBSPREAD-26JUN152010DETHOU-NYY2", "DET", "HOU"
        )
        self.assertTrue(r["parse_success"])  # pattern matched
        self.assertIn("not_in_game", r["parse_note"])

    def test_no_team_abbrs_provided(self):
        r = parse_spread_ticker_for_audit(
            "KXMLBSPREAD-26JUN152010DETHOU-DET2"
        )
        self.assertTrue(r["parse_success"])
        self.assertIsNone(r["selected_is_away"])
        self.assertIsNone(r["selected_is_home"])

    def test_double_digit_runline(self):
        r = parse_spread_ticker_for_audit(
            "KXMLBSPREAD-26JUN151910NYMCIN-CIN10", "NYM", "CIN"
        )
        self.assertTrue(r["parse_success"])
        self.assertEqual(r["run_line"], 10)


# ── check_repricing_after_event ───────────────────────────────────────────────

class TestCheckRepricingAfterEvent(unittest.TestCase):

    def test_repricing_detected_within_window(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),   # baseline
            _snap("2026-06-15T20:12:00Z", 45),   # +5c within 5min ✓
        ]
        self.assertTrue(
            check_repricing_after_event(snaps, "2026-06-15T20:10:00Z")
        )

    def test_no_repricing_price_unchanged(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),
            _snap("2026-06-15T20:12:00Z", 40),   # same price
        ]
        self.assertFalse(
            check_repricing_after_event(snaps, "2026-06-15T20:10:00Z")
        )

    def test_repricing_outside_window_not_counted(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),
            _snap("2026-06-15T20:20:00Z", 50),   # 10 min after event — outside 5min window
        ]
        self.assertFalse(
            check_repricing_after_event(
                snaps, "2026-06-15T20:10:00Z", window_seconds=300
            )
        )

    def test_small_movement_below_threshold_ignored(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),
            _snap("2026-06-15T20:12:00Z", 41),   # only +1c; threshold=2
        ]
        self.assertFalse(
            check_repricing_after_event(
                snaps, "2026-06-15T20:10:00Z", movement_threshold_cents=2
            )
        )

    def test_movement_exactly_at_threshold_counts(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),
            _snap("2026-06-15T20:12:00Z", 42),   # +2c = threshold
        ]
        self.assertTrue(
            check_repricing_after_event(
                snaps, "2026-06-15T20:10:00Z", movement_threshold_cents=2
            )
        )

    def test_empty_snapshots_returns_false(self):
        self.assertFalse(
            check_repricing_after_event([], "2026-06-15T20:10:00Z")
        )

    def test_no_snapshots_after_event_returns_false(self):
        snaps = [_snap("2026-06-15T20:05:00Z", 40)]
        self.assertFalse(
            check_repricing_after_event(snaps, "2026-06-15T20:10:00Z")
        )

    def test_custom_window_seconds(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),
            _snap("2026-06-15T20:15:00Z", 50),   # 5 min after event
        ]
        self.assertTrue(
            check_repricing_after_event(
                snaps, "2026-06-15T20:10:00Z", window_seconds=600
            )
        )
        self.assertFalse(
            check_repricing_after_event(
                snaps, "2026-06-15T20:10:00Z", window_seconds=240
            )
        )

    def test_invalid_event_time_returns_false(self):
        snaps = [_snap("2026-06-15T20:05:00Z", 40)]
        self.assertFalse(check_repricing_after_event(snaps, ""))
        self.assertFalse(check_repricing_after_event(snaps, "not-a-timestamp"))


# ── compute_repricing_window_row ──────────────────────────────────────────────

class TestRepricingWindowRow(unittest.TestCase):

    def _event(self, ts: str, away: int, home: int) -> dict:
        return {
            "event_time": ts,
            "event_type": "scoring_play",
            "inning": 3,
            "inning_half": "bottom",
            "away_score": away,
            "home_score": home,
            "is_scoring_play": 1,
        }

    def test_basic_repricing_row_fields(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),
            _snap("2026-06-15T20:12:00Z", 48),
        ]
        event = self._event("2026-06-15T20:10:00Z", 1, 2)
        row = compute_repricing_window_row("TICKER1", "spread_run_line", "DET@HOU", snaps, event)
        self.assertEqual(row["market_ticker"], "TICKER1")
        self.assertEqual(row["market_type"], "spread_run_line")
        self.assertEqual(row["game_id"], "DET@HOU")
        self.assertEqual(row["score_after"], "1-2")
        self.assertEqual(row["mid_at_event"], 40)
        self.assertEqual(row["mid_5min_after"], 48)
        self.assertEqual(row["movement_5min_cents"], 8)
        self.assertTrue(row["repriced_within_5min"])

    def test_no_repricing_row_when_price_flat(self):
        snaps = [
            _snap("2026-06-15T20:05:00Z", 40),
            _snap("2026-06-15T20:12:00Z", 40),
        ]
        event = self._event("2026-06-15T20:10:00Z", 0, 1)
        row = compute_repricing_window_row("T", "moneyline", "G1", snaps, event)
        self.assertFalse(row["repriced_within_5min"])
        self.assertEqual(row["movement_5min_cents"], 0)

    def test_empty_snapshots_returns_none_fields(self):
        event = self._event("2026-06-15T20:10:00Z", 1, 0)
        row = compute_repricing_window_row("T", "moneyline", "G1", [], event)
        self.assertIsNone(row["mid_at_event"])
        self.assertIsNone(row["mid_5min_after"])
        self.assertFalse(row["repriced_within_5min"])


# ── detect_inning_events ──────────────────────────────────────────────────────

class TestDetectInningEvents(unittest.TestCase):

    def test_detects_first_play_of_each_half_inning(self):
        plays = [
            _play_event("2026-06-15T20:05:00Z", 1, "top", 0, 0),
            _play_event("2026-06-15T20:08:00Z", 1, "top", 0, 0),
            _play_event("2026-06-15T20:12:00Z", 1, "bottom", 0, 0),
            _play_event("2026-06-15T20:20:00Z", 2, "top", 0, 1),
        ]
        events = detect_inning_events(plays)
        keys = [(e["inning"], e["inning_half"]) for e in events]
        self.assertEqual(keys, [(1, "top"), (1, "bottom"), (2, "top")])

    def test_each_event_has_event_time(self):
        plays = [_play_event("2026-06-15T20:05:00Z", 1, "top", 0, 0)]
        events = detect_inning_events(plays)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_time"], "2026-06-15T20:05:00Z")

    def test_empty_plays_returns_empty(self):
        self.assertEqual(detect_inning_events([]), [])

    def test_inning_event_type_set(self):
        plays = [_play_event("2026-06-15T20:05:00Z", 1, "top", 0, 0)]
        events = detect_inning_events(plays)
        self.assertEqual(events[0]["event_type"], "inning_start")


# ── detect_lead_change_events ─────────────────────────────────────────────────

class TestDetectLeadChangeEvents(unittest.TestCase):

    def test_detects_lead_flip(self):
        plays = [
            _play_event("2026-06-15T20:05:00Z", 1, "top", 2, 0),   # away leads
            _play_event("2026-06-15T20:20:00Z", 2, "bottom", 2, 3), # home takes lead
        ]
        events = detect_lead_change_events(plays)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["away_score"], 2)
        self.assertEqual(events[0]["home_score"], 3)

    def test_no_lead_change_if_team_stays_ahead(self):
        plays = [
            _play_event("2026-06-15T20:05:00Z", 1, "top", 2, 0),
            _play_event("2026-06-15T20:10:00Z", 1, "top", 3, 0),
            _play_event("2026-06-15T20:15:00Z", 2, "top", 4, 0),
        ]
        events = detect_lead_change_events(plays)
        self.assertEqual(len(events), 0)

    def test_empty_plays_returns_empty(self):
        self.assertEqual(detect_lead_change_events([]), [])

    def test_lead_change_event_type_set(self):
        plays = [
            _play_event("2026-06-15T20:05:00Z", 1, "top", 2, 0),
            _play_event("2026-06-15T20:20:00Z", 2, "bottom", 2, 3),
        ]
        events = detect_lead_change_events(plays)
        self.assertEqual(events[0]["event_type"], "lead_change")


# ── compute_type_summary ──────────────────────────────────────────────────────

class TestComputeTypeSummary(unittest.TestCase):

    def _row(self, mtype: str, label: str, mid_range: int = 10,
             unique_mids: int = 3, moved_score: bool = False,
             moved_inning: bool = False, cadence: float = 200.0) -> dict:
        return {
            "market_type": mtype,
            "market_liveness_label": label,
            "mid_range": mid_range,
            "unique_mid_count": unique_mids,
            "moved_after_score_event": moved_score,
            "moved_after_inning_end": moved_inning,
            "avg_seconds_between_snapshots": cadence,
        }

    def test_groups_by_market_type(self):
        rows = [
            self._row("moneyline", LIVE_RESPONSIVE),
            self._row("moneyline", SLOW_BUT_MOVING),
            self._row("spread_run_line", STALE),
        ]
        summary = compute_type_summary(rows)
        types = {r["market_type"] for r in summary}
        self.assertIn("moneyline", types)
        self.assertIn("spread_run_line", types)

    def test_counts_responsive_tickers(self):
        rows = [
            self._row("moneyline", LIVE_RESPONSIVE),
            self._row("moneyline", LIVE_RESPONSIVE),
            self._row("moneyline", STALE),
        ]
        summary = compute_type_summary(rows)
        ml = next(r for r in summary if r["market_type"] == "moneyline")
        self.assertEqual(ml["total_tickers"], 3)
        self.assertEqual(ml["responsive_tickers"], 2)
        self.assertEqual(ml["stale_tickers"], 1)

    def test_pct_moved_after_score_event(self):
        rows = [
            self._row("full_game_total", LIVE_RESPONSIVE, moved_score=True),
            self._row("full_game_total", LIVE_RESPONSIVE, moved_score=True),
            self._row("full_game_total", STALE, moved_score=False),
            self._row("full_game_total", STALE, moved_score=False),
        ]
        summary = compute_type_summary(rows)
        fgt = next(r for r in summary if r["market_type"] == "full_game_total")
        self.assertAlmostEqual(fgt["pct_moved_after_score_event"], 50.0, places=1)

    def test_avg_mid_range(self):
        rows = [
            self._row("team_total", SLOW_BUT_MOVING, mid_range=10),
            self._row("team_total", SLOW_BUT_MOVING, mid_range=30),
        ]
        summary = compute_type_summary(rows)
        tt = next(r for r in summary if r["market_type"] == "team_total")
        self.assertAlmostEqual(tt["avg_mid_range"], 20.0, places=1)

    def test_empty_returns_empty(self):
        self.assertEqual(compute_type_summary([]), [])


# ── Read-only validation ──────────────────────────────────────────────────────

class TestReadOnly(unittest.TestCase):

    def test_market_liveness_module_has_no_sql_writes(self):
        import mlb.market_liveness as mod
        source = inspect.getsource(mod)
        for op in ("INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "ALTER TABLE",
                   "DROP TABLE"):
            self.assertNotIn(
                op, source.upper(),
                f"mlb/market_liveness.py contains SQL write: {op}",
            )

    def test_validator_opens_db_readonly(self):
        import market_liveness_validator as v
        source = inspect.getsource(v)
        self.assertIn("mode=ro", source,
                      "market_liveness_validator.py must open DB with mode=ro")

    def test_pure_functions_have_no_conn_param(self):
        for func in [
            compute_ticker_liveness_metrics,
            classify_liveness_label,
            check_repricing_after_event,
            parse_spread_ticker_for_audit,
        ]:
            sig = inspect.signature(func)
            self.assertNotIn("conn", sig.parameters,
                             f"{func.__name__} must not take a DB connection")

    def test_validator_module_has_no_sql_writes(self):
        import market_liveness_validator as v
        source = inspect.getsource(v)
        for op in ("INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "ALTER TABLE",
                   "DROP TABLE"):
            self.assertNotIn(
                op, source.upper(),
                f"market_liveness_validator.py contains SQL write: {op}",
            )


if __name__ == "__main__":
    unittest.main()
