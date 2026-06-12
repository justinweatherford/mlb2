"""
tests/test_pace_fade.py — STL@NYM pace-fade classifier tests.

Scenario: STL@NYM, top of inning 2, score 4-3 (total=7), run just scored.
Four totals lines: 10.5, 11.5, 12.5, 13.5.
Context: PlaceholderMLBContextProvider → all UNKNOWN.

Expected classifications per spec:
  10.5 → NO_CHASE_OVER    (cushion=3.5 < 4.5 minimum)
  11.5 → UNRESOLVED_NEEDS_ENRICHMENT  (0.30 <= score < 0.45, placeholder ctx)
  12.5 → PACE_FADE_UNDER  (score >= 0.45)
  13.5 → PACE_FADE_UNDER  (score >= 0.45, primary candidate — highest score)
"""
import json
import sqlite3
from datetime import datetime

import pytest

from mlb.context import MLBGameContext, PlaceholderMLBContextProvider, RunEnvTag
from mlb.line_metrics import compute_line_metrics
from mlb.pace_fade import (
    PaceFadeScore,
    classify_pace_fade,
    is_early_explosion,
    _early_explosion_component,
    _line_cushion_component,
    _under_entry_value_component,
)
from mlb.training import PaceFadeTrainingRow, create_training_rows
from models import GameStateSnapshot, SignalType, TotalsLine
from db.schema import init_db
from db.repository import (
    insert_pace_fade_training_rows,
    update_training_row_outcome,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

def _stl_nym_snap() -> GameStateSnapshot:
    """T2, STL 4 NYM 3, total=7, run just scored, bases empty."""
    return GameStateSnapshot(
        game_id="STL@NYM",
        away_team="STL",
        home_team="NYM",
        away_score=4,
        home_score=3,
        inning_half="T",
        inning_number=2,
        outs=1,
        prev_away_score=3,
        prev_home_score=3,
        prev_inning_half="T",
        prev_inning_number=2,
        totals_lines=[
            TotalsLine(line=10.5, over_bid_cents=85, over_ask_cents=87),
            TotalsLine(line=11.5, over_bid_cents=78, over_ask_cents=80),
            TotalsLine(line=12.5, over_bid_cents=71, over_ask_cents=73),
            TotalsLine(line=13.5, over_bid_cents=59, over_ask_cents=61),
        ],
        prev_totals_lines=[],
        kalshi_yes_prices=None,
        prev_kalshi_yes_prices=None,
        last_updated=datetime.utcnow(),
        run_just_scored=True,
        runs_scored_this_update=1,
        updates_since_last_score=0,
        runners=[],  # bases empty after scoring play
    )


def _placeholder_ctx() -> MLBGameContext:
    return PlaceholderMLBContextProvider().get_context_for_game(None, "STL@NYM")


# ---------------------------------------------------------------------------
# is_early_explosion
# ---------------------------------------------------------------------------

class TestIsEarlyExplosion:
    def test_stl_nym_qualifies(self):
        snap = _stl_nym_snap()
        assert is_early_explosion(snap) is True

    def test_inning_4_does_not_qualify(self):
        snap = _stl_nym_snap()
        snap.inning_number = 4
        assert is_early_explosion(snap) is False

    def test_low_total_does_not_qualify(self):
        snap = _stl_nym_snap()
        snap.away_score = 2
        snap.home_score = 2   # total=4, below threshold of 6
        assert is_early_explosion(snap) is False

    def test_no_recent_score_does_not_qualify(self):
        snap = _stl_nym_snap()
        snap.run_just_scored = False
        assert is_early_explosion(snap) is False


# ---------------------------------------------------------------------------
# classify_pace_fade — classification per line
# ---------------------------------------------------------------------------

class TestClassifyPaceFade:
    def setup_method(self):
        self.snap = _stl_nym_snap()
        self.ctx = _placeholder_ctx()
        self.candidates = classify_pace_fade(self.snap, self.ctx)
        # Build lookup by line for convenience
        self.by_line = {c.line: c for c in self.candidates}

    def test_produces_four_candidates(self):
        assert len(self.candidates) == 4

    def test_10_5_is_no_chase_over(self):
        c = self.by_line[10.5]
        assert c.classification == SignalType.NO_CHASE_OVER

    def test_11_5_is_unresolved_needs_enrichment(self):
        c = self.by_line[11.5]
        assert c.classification == SignalType.UNRESOLVED_NEEDS_ENRICHMENT

    def test_12_5_is_pace_fade_under(self):
        c = self.by_line[12.5]
        assert c.classification == SignalType.PACE_FADE_UNDER

    def test_13_5_is_pace_fade_under_primary(self):
        c = self.by_line[13.5]
        assert c.classification == SignalType.PACE_FADE_UNDER

    def test_sorted_by_score_descending(self):
        scores = [c.score.total for c in self.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_13_5_has_highest_score(self):
        """13.5 should rank first — best cushion and entry price."""
        assert self.candidates[0].line == 13.5

    def test_all_candidates_have_metrics(self):
        for c in self.candidates:
            assert c.metrics is not None

    def test_context_unavailable_flag_present(self):
        """Placeholder context should always trigger the context_unavailable risk flag."""
        for c in self.candidates:
            assert "context_unavailable" in c.risk_flags


# ---------------------------------------------------------------------------
# Score component sanity checks
# ---------------------------------------------------------------------------

class TestScoreComponents:
    def setup_method(self):
        self.snap = _stl_nym_snap()
        self.ctx = _placeholder_ctx()
        self.candidates = classify_pace_fade(self.snap, self.ctx)
        self.by_line = {c.line: c for c in self.candidates}

    def test_early_explosion_score_positive_t2(self):
        """T2 with run_just_scored should produce a positive early explosion score."""
        c = self.by_line[13.5]
        assert c.score.early_explosion_score > 0

    def test_larger_cushion_higher_line_cushion_score(self):
        c13 = self.by_line[13.5]
        c12 = self.by_line[12.5]
        assert c13.score.line_cushion_score >= c12.score.line_cushion_score

    def test_13_5_under_entry_is_41_cents(self):
        """over_bid=59 → estimated_under_entry = 100-59 = 41."""
        c = self.by_line[13.5]
        assert c.estimated_under_entry == 41

    def test_10_5_under_entry_is_15_cents(self):
        """over_bid=85 → estimated_under_entry = 100-85 = 15."""
        c = self.by_line[10.5]
        assert c.estimated_under_entry == 15

    def test_cushion_values(self):
        """Line cushion = line - current_total (total=7)."""
        assert self.by_line[10.5].metrics.line_cushion == pytest.approx(3.5, abs=0.01)
        assert self.by_line[11.5].metrics.line_cushion == pytest.approx(4.5, abs=0.01)
        assert self.by_line[12.5].metrics.line_cushion == pytest.approx(5.5, abs=0.01)
        assert self.by_line[13.5].metrics.line_cushion == pytest.approx(6.5, abs=0.01)

    def test_no_run_env_penalty_for_unknown(self):
        """UNKNOWN run/HR environment → no penalties applied."""
        c = self.by_line[13.5]
        assert c.score.high_run_env_penalty == 0.0
        assert c.score.high_hr_env_penalty == 0.0

    def test_no_strong_offense_penalty_for_unknown(self):
        c = self.by_line[13.5]
        assert c.score.strong_offense_penalty == 0.0

    def test_no_active_rally_penalty_bases_empty(self):
        c = self.by_line[13.5]
        assert c.score.active_rally_penalty == 0.0

    def test_13_5_score_above_0_45(self):
        """Ensure the calibration holds — 13.5 should exceed the PACE_FADE threshold."""
        c = self.by_line[13.5]
        assert c.score.total >= 0.45

    def test_11_5_score_between_0_30_and_0_45(self):
        """11.5 should land in the UNRESOLVED band."""
        c = self.by_line[11.5]
        assert 0.30 <= c.score.total < 0.45


# ---------------------------------------------------------------------------
# Training row creation
# ---------------------------------------------------------------------------

class TestCreateTrainingRows:
    def setup_method(self):
        self.snap = _stl_nym_snap()
        self.ctx = _placeholder_ctx()
        self.candidates = classify_pace_fade(self.snap, self.ctx)
        self.ts = datetime(2026, 6, 11, 18, 30, 0)
        self.rows = create_training_rows(self.snap, self.ctx, self.candidates, self.ts)

    def test_one_row_per_candidate(self):
        assert len(self.rows) == 4

    def test_game_id_propagated(self):
        for r in self.rows:
            assert r.game_id == "STL@NYM"

    def test_signal_timestamp_set(self):
        for r in self.rows:
            assert r.signal_timestamp == self.ts

    def test_outcome_fields_none_initially(self):
        for r in self.rows:
            assert r.final_total is None
            assert r.under_won is None
            assert r.net_pnl_if_under is None

    def test_label_source_unresolved(self):
        from models import LabelSource
        for r in self.rows:
            assert r.label_source == LabelSource.UNRESOLVED.value

    def test_classification_serialised_as_string(self):
        """classification on training rows is stored as the enum value string."""
        by_line = {r.line: r for r in self.rows}
        assert by_line[13.5].classification == SignalType.PACE_FADE_UNDER.value

    def test_context_source_is_placeholder(self):
        for r in self.rows:
            assert r.context_source == "placeholder"

    def test_risk_flags_list(self):
        for r in self.rows:
            assert isinstance(r.risk_flags, list)
            assert "context_unavailable" in r.risk_flags


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

class TestPaceFadeDB:
    def setup_method(self):
        self.conn = init_db(":memory:")
        snap = _stl_nym_snap()
        ctx = _placeholder_ctx()
        candidates = classify_pace_fade(snap, ctx)
        ts = datetime(2026, 6, 11, 18, 30, 0)
        self.rows = create_training_rows(snap, ctx, candidates, ts)

    def teardown_method(self):
        self.conn.close()

    def test_insert_returns_four_ids(self):
        ids = insert_pace_fade_training_rows(self.conn, self.rows)
        assert len(ids) == 4
        assert all(i > 0 for i in ids)

    def test_duplicate_insert_is_idempotent(self):
        insert_pace_fade_training_rows(self.conn, self.rows)
        ids2 = insert_pace_fade_training_rows(self.conn, self.rows)
        assert all(i == 0 for i in ids2)

    def test_rows_stored_in_db(self):
        insert_pace_fade_training_rows(self.conn, self.rows)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM pace_fade_training_rows"
        ).fetchone()[0]
        assert count == 4

    def test_update_outcome_settles_row(self):
        ids = insert_pace_fade_training_rows(self.conn, self.rows)
        # Settle the 13.5 row (first in sorted order = highest score)
        row_id = ids[0]
        update_training_row_outcome(
            self.conn,
            row_id=row_id,
            final_total=9,
            under_won=True,
            net_pnl_if_under=41,   # 1 unit × 41¢ entry = 41¢ profit
            label_source="transcript_final",
            label_confidence=0.95,
        )
        settled = self.conn.execute(
            "SELECT * FROM pace_fade_training_rows WHERE id=?", (row_id,)
        ).fetchone()
        assert settled["final_total"] == 9
        assert settled["under_won"] == 1
        assert settled["net_pnl_if_under"] == 41
        assert settled["label_source"] == "transcript_final"
        assert settled["label_confidence"] == pytest.approx(0.95, abs=0.001)

    def test_risk_flags_json_parseable(self):
        insert_pace_fade_training_rows(self.conn, self.rows)
        flags_json = self.conn.execute(
            "SELECT risk_flags_json FROM pace_fade_training_rows LIMIT 1"
        ).fetchone()["risk_flags_json"]
        flags = json.loads(flags_json)
        assert isinstance(flags, list)

    def test_classification_stored_correctly(self):
        insert_pace_fade_training_rows(self.conn, self.rows)
        rows = self.conn.execute(
            "SELECT line, classification FROM pace_fade_training_rows ORDER BY line"
        ).fetchall()
        by_line = {r["line"]: r["classification"] for r in rows}
        assert by_line[10.5] == SignalType.NO_CHASE_OVER.value
        assert by_line[11.5] == SignalType.UNRESOLVED_NEEDS_ENRICHMENT.value
        assert by_line[12.5] == SignalType.PACE_FADE_UNDER.value
        assert by_line[13.5] == SignalType.PACE_FADE_UNDER.value
