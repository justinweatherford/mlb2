"""Unit tests for ev_shadow_review_log.py"""
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ev_shadow_review_log import (
    _decision_time_bucket,
    _make_shadow_id,
    _shadow_row,
    _load_existing_ids,
    _append_shadow_log,
    _load_ev_candidates,
    SHADOW_FIELDS,
)


# ── Bucket helpers ─────────────────────────────────────────────────────────────

class TestDecisionTimeBucket(unittest.TestCase):
    def test_floors_to_15_min_boundary(self):
        dt = datetime(2026, 6, 23, 13, 22, 45, tzinfo=timezone.utc)
        bucket = _decision_time_bucket(dt)
        self.assertIn("13:15:00", bucket)

    def test_same_bucket_for_times_within_same_window(self):
        dt1 = datetime(2026, 6, 23, 13, 14, 59, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 23, 13,  0,  0, tzinfo=timezone.utc)
        self.assertEqual(_decision_time_bucket(dt1), _decision_time_bucket(dt2))

    def test_different_bucket_at_boundary(self):
        dt1 = datetime(2026, 6, 23, 13, 14, 59, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 23, 13, 15,  0, tzinfo=timezone.utc)
        self.assertNotEqual(_decision_time_bucket(dt1), _decision_time_bucket(dt2))


# ── Shadow ID ──────────────────────────────────────────────────────────────────

class TestShadowId(unittest.TestCase):
    def test_deterministic(self):
        id1 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:15:00")
        id2 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:15:00")
        self.assertEqual(id1, id2)

    def test_different_bucket_produces_different_id(self):
        id1 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:00:00")
        id2 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:15:00")
        self.assertNotEqual(id1, id2)

    def test_length_is_12(self):
        sid = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:00:00")
        self.assertEqual(len(sid), 12)

    def test_different_direction_differs(self):
        id_yes = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:00:00")
        id_no  = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "NO",  "2026-06-23T13:00:00")
        self.assertNotEqual(id_yes, id_no)


# ── Append / dedup ─────────────────────────────────────────────────────────────

def _make_ev_row(
    game_date="2026-06-23",
    ticker="KXMLB-ATH",
    lane="side",
    direction="YES",
) -> dict:
    dt = datetime(2026, 6, 23, 13, 0, tzinfo=timezone.utc)
    bucket = _decision_time_bucket(dt)
    ev = {
        "_source": "ev_overlay",
        "game_date": game_date,
        "game_id": "ATH@SF",
        "team": "ATH",
        "opponent": "SF",
        "home_away": "away",
        "lane": lane,
        "entry_side": direction,
        "matched_ticker": ticker,
        "proxy_brain_score": "0.42",
        "entry_price_cents": "45",
        "yes_bid_cents": "44",
        "no_bid_cents": "55",
        "bid_ask_spread_cents": "1",
        "estimated_edge_cents": "15.0",
        "calibrated_probability": "0.60",
        "calibration_sample_size": "2000",
        "calibration_hit_rate": "0.608",
        "tradeability_label": "tradeable_candidate",
        "moneyline_core_lane": "",
        "reason_not_tradeable": "",
        "orderbook_snapped_at": "2026-06-23T13:01:00+00:00",
    }
    return _shadow_row(ev, dt, bucket)


class TestAppendShadowLog(unittest.TestCase):
    def test_no_duplicate_append(self):
        row = _make_ev_row()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow.csv"
            _append_shadow_log([row], log_path, dry_run=False)
            _append_shadow_log([row], log_path, dry_run=False)
            with open(log_path, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)

    def test_dry_run_does_not_write_file(self):
        row = _make_ev_row(ticker="KXMLB-NYY")
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow.csv"
            count = _append_shadow_log([row], log_path, dry_run=True)
            self.assertFalse(log_path.exists())
            self.assertEqual(count, 1)

    def test_graceful_empty_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow.csv"
            count = _append_shadow_log([], log_path, dry_run=False)
            self.assertEqual(count, 0)
            self.assertFalse(log_path.exists())

    def test_different_rows_both_written(self):
        row1 = _make_ev_row(ticker="KXMLB-ATH", lane="side")
        row2 = _make_ev_row(ticker="KXMLB-ATH", lane="team_runs_4plus")
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow.csv"
            _append_shadow_log([row1, row2], log_path, dry_run=False)
            with open(log_path, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)

    def test_all_shadow_fields_present(self):
        row = _make_ev_row()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow.csv"
            _append_shadow_log([row], log_path, dry_run=False)
            with open(log_path, newline="") as f:
                reader = csv.DictReader(f)
                cols = reader.fieldnames or []
                written = list(reader)
            for field in SHADOW_FIELDS:
                self.assertIn(field, cols, f"Missing field: {field}")
            self.assertEqual(written[0]["observe_only"], "true")


# ── Candidate loading ──────────────────────────────────────────────────────────

class TestLoadEvCandidates(unittest.TestCase):
    def _write_ev_csv(self, path: Path, rows: list[dict]) -> None:
        fields = ["game_date", "tradeability_label", "lane", "game_id", "team",
                  "opponent", "home_away", "entry_side", "matched_ticker",
                  "proxy_brain_score", "entry_price_cents", "yes_bid_cents",
                  "no_bid_cents", "bid_ask_spread_cents", "estimated_edge_cents",
                  "calibrated_probability", "calibration_sample_size",
                  "calibration_hit_rate", "moneyline_core_lane", "reason_not_tradeable",
                  "orderbook_snapped_at"]
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    def test_filters_by_date(self):
        with tempfile.TemporaryDirectory() as td:
            overlay_dir = Path(td)
            self._write_ev_csv(overlay_dir / "ev_overlay_rows.csv", [
                {"game_date": "2026-06-23", "tradeability_label": "tradeable_candidate", "lane": "side"},
                {"game_date": "2026-06-22", "tradeability_label": "tradeable_candidate", "lane": "side"},
            ])
            result = _load_ev_candidates("2026-06-23", False, overlay_dir)
            self.assertEqual(len(result), 1)

    def test_filters_by_tradeability_label(self):
        with tempfile.TemporaryDirectory() as td:
            overlay_dir = Path(td)
            self._write_ev_csv(overlay_dir / "ev_overlay_rows.csv", [
                {"game_date": "2026-06-23", "tradeability_label": "tradeable_candidate", "lane": "side"},
                {"game_date": "2026-06-23", "tradeability_label": "watch_only", "lane": "side"},
                {"game_date": "2026-06-23", "tradeability_label": "not_tradeable", "lane": "side"},
                {"game_date": "2026-06-23", "tradeability_label": "unsupported_market_type", "lane": "side"},
            ])
            result = _load_ev_candidates("2026-06-23", False, overlay_dir)
            self.assertEqual(len(result), 2)  # only tradeable + watch_only

    def test_near_misses_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            overlay_dir = Path(td)
            (overlay_dir / "ev_overlay_rows.csv").write_text(
                "game_date,tradeability_label,lane\n", encoding="utf-8"
            )
            nm = overlay_dir / "moneyline_core_near_misses.csv"
            nm.write_text(
                "game_date,game_id,team,home_away,side_score,failed_reasons,"
                "near_miss_bucket,top_positive_reasons,kalshi_ask_cents,bid_ask_spread_cents\n"
                "2026-06-23,ATH@SF,ATH,away,0.38,low_score,near_miss_0.35_0.40,,45,1\n",
                encoding="utf-8",
            )
            result = _load_ev_candidates("2026-06-23", False, overlay_dir)
            self.assertEqual(len(result), 0)

    def test_near_misses_included_with_flag(self):
        with tempfile.TemporaryDirectory() as td:
            overlay_dir = Path(td)
            (overlay_dir / "ev_overlay_rows.csv").write_text(
                "game_date,tradeability_label,lane\n", encoding="utf-8"
            )
            nm = overlay_dir / "moneyline_core_near_misses.csv"
            nm.write_text(
                "game_date,game_id,team,home_away,side_score,failed_reasons,"
                "near_miss_bucket,top_positive_reasons,kalshi_ask_cents,bid_ask_spread_cents\n"
                "2026-06-23,ATH@SF,ATH,away,0.38,low_score,near_miss_0.35_0.40,,45,1\n",
                encoding="utf-8",
            )
            result = _load_ev_candidates("2026-06-23", True, overlay_dir)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["_source"], "near_miss")


if __name__ == "__main__":
    unittest.main()
