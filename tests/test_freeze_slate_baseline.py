"""
tests/test_freeze_slate_baseline.py — Tests for freeze_slate_baseline.py

Covers:
  - build_derivative_performance: candidate/paper counts, outcome aggregation, hit rate
  - build_baseline_summary_dict: key metric computation, missing derivatives, fd count
  - build_comparison_manifest: structure, key_metrics, artifact_paths
  - build_logic_findings_md: content sanity checks
  - load_focused_watch_count: DB query against in-memory DB
  - load_row_counts: DB row counting
  - safety: no trading, no forbidden imports, no SQL writes
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import freeze_slate_baseline as fsb


# ── In-memory DB ───────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS kalshi_orderbook_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    market_ticker TEXT NOT NULL,
    snapped_at    TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'rest_poll'
);
CREATE TABLE IF NOT EXISTS candidate_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_type TEXT NOT NULL DEFAULT 'trailing_team_total_lag_watch',
    derivative_type TEXT,
    settlement_horizon TEXT NOT NULL DEFAULT 'full_game',
    inning         INTEGER,
    half_inning    TEXT,
    blocked_reason TEXT,
    baseline_source TEXT,
    created_at     TEXT NOT NULL DEFAULT '2026-06-15T19:00:00'
);
CREATE TABLE IF NOT EXISTS paper_setups (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_key                TEXT NOT NULL UNIQUE,
    first_candidate_event_id INTEGER NOT NULL,
    market_ticker            TEXT,
    derivative_type          TEXT,
    paper_status             TEXT NOT NULL DEFAULT 'observation_only',
    entry_price_cents        INTEGER,
    outcome                  TEXT NOT NULL DEFAULT 'unknown',
    good_entry_score         REAL,
    good_entry_label         TEXT,
    net_pnl_cents            INTEGER,
    gross_pnl_cents          INTEGER,
    created_at               TEXT NOT NULL DEFAULT '2026-06-15T19:00:00'
);
CREATE TABLE IF NOT EXISTS mlb_game_states (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk    INTEGER NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mlb_play_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk    INTEGER NOT NULL,
    at_bat_index INTEGER,
    play_index INTEGER DEFAULT 0,
    event_time TEXT,
    UNIQUE(game_pk, at_bat_index, play_index)
);
CREATE TABLE IF NOT EXISTS mlb_weather_reference (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date TEXT NOT NULL,
    away_abbr TEXT NOT NULL,
    home_abbr TEXT NOT NULL,
    temperature_f REAL,
    wind_speed_mph REAL,
    condition_text TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    UNIQUE(game_date, away_abbr, home_abbr, source)
);
"""


def _make_db(**kwargs) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()

    for snap in kwargs.get("snaps", []):
        conn.execute(
            "INSERT INTO kalshi_orderbook_snapshots (market_ticker, snapped_at, source) VALUES (?,?,?)",
            (snap["market_ticker"], snap["snapped_at"], snap.get("source", "rest_poll")),
        )
    for cand in kwargs.get("candidates", []):
        conn.execute(
            "INSERT INTO candidate_events "
            "(candidate_type, derivative_type, settlement_horizon, inning, half_inning, "
            "blocked_reason, baseline_source, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                cand.get("candidate_type", "trailing_team_total_lag_watch"),
                cand.get("derivative_type"),
                cand.get("settlement_horizon", "full_game"),
                cand.get("inning"),
                cand.get("half_inning"),
                cand.get("blocked_reason"),
                cand.get("baseline_source"),
                cand.get("created_at", "2026-06-15T19:00:00"),
            ),
        )
    for i, ps in enumerate(kwargs.get("paper_setups", [])):
        conn.execute(
            "INSERT INTO paper_setups "
            "(setup_key, first_candidate_event_id, market_ticker, derivative_type, "
            "paper_status, entry_price_cents, outcome, good_entry_label, net_pnl_cents, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                ps.get("setup_key", f"key_{i}"),
                ps.get("first_candidate_event_id", i + 1),
                ps.get("market_ticker"),
                ps.get("derivative_type"),
                ps.get("paper_status", "observation_only"),
                ps.get("entry_price_cents"),
                ps.get("outcome", "unknown"),
                ps.get("good_entry_label"),
                ps.get("net_pnl_cents"),
                ps.get("created_at", "2026-06-15T19:00:00"),
            ),
        )
    conn.commit()
    return conn


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cand(**kw) -> dict:
    base = {
        "id": 1,
        "candidate_type": "trailing_team_total_lag_watch",
        "derivative_type": "team_total",
        "settlement_horizon": "full_game",
        "inning": 4,
        "half_inning": "top",
        "status": "observed_only",
        "blocked_reason": None,
        "baseline_source": "game_open",
        "overall_watch_score": 0.5,
        "market_ticker": "KXMLB-T",
        "market_mismatch_score": 0.3,
        "baseball_support_score": 0.5,
    }
    base.update(kw)
    return base


def _ps(**kw) -> dict:
    base = {
        "id": 1,
        "setup_key": "k1",
        "market_ticker": "KXMLB-T",
        "derivative_type": "team_total",
        "paper_status": "observation_only",
        "entry_price_cents": None,
        "outcome": "unknown",
        "good_entry_label": None,
        "net_pnl_cents": None,
        "gross_pnl_cents": None,
    }
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# build_derivative_performance
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildDerivativePerformance:
    def test_empty_input_returns_empty(self):
        assert fsb.build_derivative_performance([], []) == []

    def test_counts_candidates_by_derivative_type(self):
        cands = [_cand(derivative_type="team_total"), _cand(derivative_type="fg_total")]
        result = fsb.build_derivative_performance(cands, [])
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["team_total"]["total_candidates"] == 1
        assert by_type["fg_total"]["total_candidates"] == 1

    def test_blocked_vs_observed(self):
        cands = [
            _cand(derivative_type="team_total", blocked_reason=None),
            _cand(derivative_type="team_total", blocked_reason="rally_still_active"),
        ]
        result = fsb.build_derivative_performance(cands, [])
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["team_total"]["observed"] == 1
        assert by_type["team_total"]["blocked"] == 1

    def test_paper_setup_counts(self):
        cands  = [_cand(derivative_type="team_total")]
        papers = [_ps(derivative_type="team_total")]
        result = fsb.build_derivative_performance(cands, papers)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["team_total"]["paper_setups"] == 1

    def test_outcome_win_loss_counts(self):
        cands  = [_cand(derivative_type="fg_total")]
        papers = [
            _ps(derivative_type="fg_total", outcome="win"),
            _ps(derivative_type="fg_total", outcome="loss"),
            _ps(derivative_type="fg_total", outcome="unknown"),
        ]
        result = fsb.build_derivative_performance(cands, papers)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["fg_total"]["wins"] == 1
        assert by_type["fg_total"]["losses"] == 1
        assert by_type["fg_total"]["unknowns"] == 1

    def test_hit_rate_calculated_from_evaluable(self):
        cands  = [_cand(derivative_type="fg_total")]
        papers = [
            _ps(derivative_type="fg_total", outcome="win"),
            _ps(derivative_type="fg_total", outcome="loss"),
        ]
        result = fsb.build_derivative_performance(cands, papers)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["fg_total"]["hit_rate"] == 0.5

    def test_hit_rate_none_when_no_evaluable(self):
        cands  = [_cand(derivative_type="team_total")]
        papers = [_ps(derivative_type="team_total", outcome="unknown")]
        result = fsb.build_derivative_performance(cands, papers)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["team_total"]["hit_rate"] is None

    def test_pnl_summed(self):
        cands  = [_cand(derivative_type="fg_total")]
        papers = [
            _ps(derivative_type="fg_total", net_pnl_cents=50),
            _ps(derivative_type="fg_total", net_pnl_cents=-30),
        ]
        result = fsb.build_derivative_performance(cands, papers)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["fg_total"]["total_net_pnl_cents"] == 20

    def test_good_entry_labels_counted(self):
        cands  = [_cand(derivative_type="team_total")]
        papers = [
            _ps(derivative_type="team_total", good_entry_label="strong_value"),
            _ps(derivative_type="team_total", good_entry_label="watch_only"),
        ]
        result = fsb.build_derivative_performance(cands, papers)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["team_total"]["strong_value"] == 1
        assert by_type["team_total"]["watch_only"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# build_baseline_summary_dict
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildBaselineSummaryDict:
    def test_total_candidate_count(self):
        cands = [_cand(), _cand(id=2), _cand(id=3)]
        s = fsb.build_baseline_summary_dict(cands, [], 0, 0, "2026-06-15", "pre_tuning_v1")
        assert s["candidates"]["total"] == 3

    def test_by_derivative_counts(self):
        cands = [
            _cand(derivative_type="team_total"),
            _cand(derivative_type="team_total"),
            _cand(derivative_type="fg_total"),
        ]
        s = fsb.build_baseline_summary_dict(cands, [], 0, 0, "2026-06-15", "pre_tuning_v1")
        assert s["candidates"]["by_derivative"]["team_total"] == 2
        assert s["candidates"]["by_derivative"]["fg_total"] == 1

    def test_team_total_pct(self):
        cands = [_cand(derivative_type="team_total")] * 7 + [_cand(derivative_type="fg_total")] * 3
        s = fsb.build_baseline_summary_dict(cands, [], 0, 0, "2026-06-15", "pre_tuning_v1")
        assert abs(s["candidates"]["team_total_pct"] - 70.0) < 0.2

    def test_first_discovery_count(self):
        cands = [
            _cand(baseline_source="first_discovery"),
            _cand(baseline_source="first_discovery"),
            _cand(baseline_source="game_open"),
        ]
        s = fsb.build_baseline_summary_dict(cands, [], 0, 0, "2026-06-15", "pre_tuning_v1")
        assert s["candidates"]["first_discovery_count"] == 2
        assert abs(s["candidates"]["first_discovery_pct"] - 66.7) < 0.2

    def test_rally_blocks_counted(self):
        cands = [
            _cand(blocked_reason="rally_still_active"),
            _cand(blocked_reason="rally_still_active"),
            _cand(blocked_reason=None),
        ]
        s = fsb.build_baseline_summary_dict(cands, [], 0, 0, "2026-06-15", "pre_tuning_v1")
        assert s["candidates"]["rally_still_active_blocks"] == 2

    def test_missing_derivative_lanes_detected(self):
        cands = [_cand(derivative_type="team_total")]
        s = fsb.build_baseline_summary_dict(cands, [], 0, 0, "2026-06-15", "pre_tuning_v1")
        missing = s["candidates"]["missing_derivative_lanes"]
        assert "spread" in missing
        assert "f5_spread" in missing
        assert "team_total" not in missing

    def test_focused_watch_count_in_market_data(self):
        s = fsb.build_baseline_summary_dict([], [], 42, 1000, "2026-06-15", "pre_tuning_v1")
        assert s["market_data"]["focused_watch_snap_count"] == 42
        assert s["market_data"]["total_snap_count"] == 1000

    def test_date_and_label_preserved(self):
        s = fsb.build_baseline_summary_dict([], [], 0, 0, "2026-06-15", "pre_tuning_v1")
        assert s["date"] == "2026-06-15"
        assert s["label"] == "pre_tuning_v1"

    def test_paper_setups_counted(self):
        papers = [_ps(derivative_type="team_total"), _ps(derivative_type="fg_total")]
        s = fsb.build_baseline_summary_dict([], papers, 0, 0, "2026-06-15", "pre_tuning_v1")
        assert s["paper_setups"]["total"] == 2


# ══════════════════════════════════════════════════════════════════════════════
# build_comparison_manifest
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildComparisonManifest:
    def _manifest(self, **kw):
        summary = {
            "date": "2026-06-15",
            "label": "pre_tuning_v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "candidates": {
                "total": 146,
                "team_total_pct": 65.8,
                "first_discovery_pct": 100.0,
                "rally_still_active_blocks": 50,
                "near_settled_missed": 0,
            },
            "paper_setups": {"total": 62},
            "market_data": {"focused_watch_snap_count": 0, "total_snap_count": 1000},
        }
        summary.update(kw)
        row_counts = {"candidate_events": 146, "paper_setups": 62}
        out_dir = Path("outputs/baselines/2026-06-15/pre_tuning_v1")
        return fsb.build_comparison_manifest(summary, row_counts, out_dir, "2026-06-15", "pre_tuning_v1", "abc1234")

    def test_has_date_and_label(self):
        m = self._manifest()
        assert m["date"] == "2026-06-15"
        assert m["label"] == "pre_tuning_v1"

    def test_has_git_commit(self):
        m = self._manifest()
        assert m["git_commit"] == "abc1234"

    def test_has_row_counts(self):
        m = self._manifest()
        assert m["row_counts"]["candidate_events"] == 146

    def test_has_key_metrics(self):
        m = self._manifest()
        assert "total_candidates" in m["key_metrics"]
        assert "team_total_pct" in m["key_metrics"]
        assert "first_discovery_pct" in m["key_metrics"]

    def test_has_artifact_paths(self):
        m = self._manifest()
        assert "baseline_summary_json" in m["artifact_paths"]
        assert "candidate_summary_csv" in m["artifact_paths"]
        assert "logic_findings_md" in m["artifact_paths"]
        assert "comparison_manifest_json" in m["artifact_paths"]

    def test_has_script_version(self):
        m = self._manifest()
        assert "script_version" in m


# ══════════════════════════════════════════════════════════════════════════════
# build_logic_findings_md
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildLogicFindingsMd:
    def _summary(self):
        return {
            "date": "2026-06-15",
            "label": "pre_tuning_v1",
            "candidates": {
                "total": 146,
                "team_total_pct": 65.8,
                "first_discovery_count": 146,
                "first_discovery_pct": 100.0,
                "rally_still_active_blocks": 50,
                "near_settled_missed": 0,
                "by_derivative": {"team_total": 96, "fg_total": 42, "f5_total": 8},
                "missing_derivative_lanes": ["f5_spread", "spread"],
            },
            "paper_setups": {
                "total": 62,
                "by_outcome": {"unknown": 62},
                "by_good_entry_label": {},
            },
            "market_data": {"focused_watch_snap_count": 0},
        }

    def test_contains_all_10_findings(self):
        md = fsb.build_logic_findings_md(self._summary(), "2026-06-15")
        for i in range(1, 11):
            assert str(i) in md, f"Finding {i} missing from logic_findings.md"

    def test_contains_date(self):
        md = fsb.build_logic_findings_md(self._summary(), "2026-06-15")
        assert "2026-06-15" in md

    def test_mentions_team_total(self):
        md = fsb.build_logic_findings_md(self._summary(), "2026-06-15")
        assert "team_total" in md.lower() or "team total" in md.lower()

    def test_mentions_first_discovery(self):
        md = fsb.build_logic_findings_md(self._summary(), "2026-06-15")
        assert "first_discovery" in md


# ══════════════════════════════════════════════════════════════════════════════
# load_focused_watch_count  (in-memory DB)
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadFocusedWatchCount:
    def test_counts_focused_watch_snaps(self):
        conn = _make_db(snaps=[
            {"market_ticker": "T1", "snapped_at": "2026-06-15T23:00:00+00:00", "source": "focused_watch"},
            {"market_ticker": "T1", "snapped_at": "2026-06-15T23:05:00+00:00", "source": "focused_watch"},
            {"market_ticker": "T1", "snapped_at": "2026-06-15T23:10:00+00:00", "source": "rest_poll"},
        ])
        assert fsb.load_focused_watch_count(conn, "2026-06-15") == 2

    def test_excludes_other_dates(self):
        conn = _make_db(snaps=[
            {"market_ticker": "T1", "snapped_at": "2026-06-14T23:00:00+00:00", "source": "focused_watch"},
        ])
        assert fsb.load_focused_watch_count(conn, "2026-06-15") == 0

    def test_zero_when_no_snaps(self):
        conn = _make_db()
        assert fsb.load_focused_watch_count(conn, "2026-06-15") == 0


# ══════════════════════════════════════════════════════════════════════════════
# load_row_counts  (in-memory DB)
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadRowCounts:
    def test_counts_candidates(self):
        conn = _make_db(candidates=[
            {"created_at": "2026-06-15T19:00:00"},
            {"created_at": "2026-06-15T20:00:00"},
        ])
        counts = fsb.load_row_counts(conn, "2026-06-15")
        assert counts["candidate_events"] == 2

    def test_counts_paper_setups(self):
        conn = _make_db(paper_setups=[
            {"setup_key": "k1", "first_candidate_event_id": 1, "created_at": "2026-06-15T19:00:00"},
        ])
        counts = fsb.load_row_counts(conn, "2026-06-15")
        assert counts["paper_setups"] == 1

    def test_all_expected_keys_present(self):
        conn = _make_db()
        counts = fsb.load_row_counts(conn, "2026-06-15")
        for key in ("candidate_events", "paper_setups", "kalshi_orderbook_snapshots",
                    "mlb_game_states", "mlb_play_events"):
            assert key in counts


# ══════════════════════════════════════════════════════════════════════════════
# Safety constraints
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyConstraints:
    def _src(self) -> str:
        return (ROOT / "freeze_slate_baseline.py").read_text(encoding="utf-8")

    def _imports(self, src: str, name: str) -> bool:
        import re
        return bool(re.search(rf"^\s*(import {name}|from {name})\b", src, re.MULTILINE))

    def test_no_import_candidates_module(self):
        assert not self._imports(self._src(), "candidates")

    def test_no_import_live_watcher(self):
        assert not self._imports(self._src(), "live_watcher")

    def test_no_import_paper_lifecycle(self):
        assert not self._imports(self._src(), "paper_lifecycle")

    def test_no_import_scoring(self):
        assert not self._imports(self._src(), "scoring")

    def test_no_place_order(self):
        src = self._src()
        for fn in ("place_order", "create_order", "submit_order"):
            assert fn not in src

    def test_no_take_label(self):
        src = self._src()
        assert '"TAKE"' not in src and "'TAKE'" not in src

    def test_no_sql_writes(self):
        import re
        src = self._src()
        writes = re.findall(r"\b(INSERT|UPDATE|DELETE|DROP)\b", src, re.IGNORECASE)
        assert not writes, f"Forbidden SQL: {writes}"

    def test_script_version_defined(self):
        assert hasattr(fsb, "SCRIPT_VERSION")
        assert isinstance(fsb.SCRIPT_VERSION, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
