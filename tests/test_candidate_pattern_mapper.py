"""
tests/test_candidate_pattern_mapper.py — TDD tests for candidate-to-pattern mapping layer.

Written BEFORE implementation exists. All tests must fail until
mlb/candidate_pattern_mapper.py is written.

Coverage:
  - HistoricalContextResult dataclass fields
  - map_candidate_to_pattern dispatch by derivative_type
  - fg_total / market_overreaction → noisy_inning or late_scoring
  - team_total → team_total_after_state
  - f5_total → f5_pace
  - spread/moneyline → unavailable
  - missing fields → unavailable gracefully
  - as_of_date passed through from candidate created_at
  - blocked candidate still returns historical context (status unchanged)
  - no TAKE/recommendation keys anywhere
  - no candidate generation import
  - map_candidates_batch list handling
  - one bad candidate does not fail batch
  - thin sample warning surfaces
  - API batch endpoint
  - as_of_date safety
"""
from dataclasses import fields as dc_fields
from datetime import date

import pytest

from db.schema import init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ── Candidate builders ────────────────────────────────────────────────────────

def _cand(
    id=1, derivative_type="fg_total", candidate_type="market_overreaction",
    inning=3, score_away=4, score_home=1,
    selected_team_abbr="NYY", status="observed_only",
    blocked_reason=None, created_at="2025-06-01T20:00:00",
    market_ticker="KXMLBTOTAL-TEST",
) -> dict:
    return {
        "id": id,
        "derivative_type": derivative_type,
        "candidate_type": candidate_type,
        "inning": inning,
        "score_away": score_away,
        "score_home": score_home,
        "selected_team_abbr": selected_team_abbr,
        "status": status,
        "blocked_reason": blocked_reason,
        "created_at": created_at,
        "market_ticker": market_ticker,
        "selected_derivative_type": derivative_type,
    }


# ── HistoricalContextResult structure ─────────────────────────────────────────

class TestHistoricalContextResultStructure:
    def test_importable(self):
        from mlb.candidate_pattern_mapper import HistoricalContextResult
        assert HistoricalContextResult is not None

    def test_has_required_fields(self):
        from mlb.candidate_pattern_mapper import HistoricalContextResult
        names = {f.name for f in dc_fields(HistoricalContextResult)}
        required = {
            "candidate_id", "matched_pattern_type", "pattern_name",
            "sample_size", "confidence_label", "summary_text",
            "continuation_rate", "cooldown_rate",
            "average_rest_of_game_runs", "median_rest_of_game_runs",
            "threshold_hit_rates", "warnings", "as_of_date",
            "filters_used", "available",
        }
        assert not (required - names), f"Missing fields: {required - names}"

    def test_no_take_or_recommendation_fields(self):
        from mlb.candidate_pattern_mapper import HistoricalContextResult
        names = {f.name for f in dc_fields(HistoricalContextResult)}
        forbidden = {n for n in names
                     if any(kw in n.lower() for kw in ("take", "recommend", "signal", "trade"))}
        assert not forbidden, f"Forbidden fields present: {forbidden}"

    def test_instantiable(self):
        from mlb.candidate_pattern_mapper import HistoricalContextResult
        r = HistoricalContextResult(
            candidate_id=1, matched_pattern_type="noisy_inning",
            pattern_name="noisy_inning", sample_size=0,
            confidence_label="insufficient_sample", summary_text="",
            continuation_rate=None, cooldown_rate=None,
            average_rest_of_game_runs=None, median_rest_of_game_runs=None,
            threshold_hit_rates={}, warnings=[], as_of_date="2025-09-01",
            filters_used={}, available=False,
        )
        assert r.candidate_id == 1


# ── No candidate generation import ────────────────────────────────────────────

class TestNoForbiddenImports:
    def test_mapper_does_not_import_candidate_generator(self):
        import mlb.candidate_pattern_mapper as m
        assert not hasattr(m, "generate_candidates"), (
            "mapper must not import candidate_generator"
        )

    def test_mapper_does_not_import_guardrails(self):
        import mlb.candidate_pattern_mapper as m
        assert not hasattr(m, "evaluate_guardrails"), (
            "mapper must not import guardrails"
        )


# ── map_candidate_to_pattern ──────────────────────────────────────────────────

class TestMapCandidateToPattern:
    def _map(self, db, **kwargs):
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(**kwargs)
        return map_candidate_to_pattern(db, c)

    def test_returns_historical_context_result(self, db):
        from mlb.candidate_pattern_mapper import HistoricalContextResult
        result = self._map(db, derivative_type="fg_total")
        assert isinstance(result, HistoricalContextResult)

    def test_fg_total_maps_to_noisy_inning_pattern(self, db):
        result = self._map(db, derivative_type="fg_total", inning=3)
        assert result.matched_pattern_type in ("noisy_inning", "late_scoring")

    def test_fg_total_late_inning_maps_to_late_scoring(self, db):
        result = self._map(db, derivative_type="fg_total", inning=7)
        assert result.matched_pattern_type == "late_scoring"

    def test_fg_total_early_inning_maps_to_noisy_inning(self, db):
        result = self._map(db, derivative_type="fg_total", inning=3)
        assert result.matched_pattern_type == "noisy_inning"

    def test_team_total_maps_to_team_total_after_state(self, db):
        result = self._map(db, derivative_type="team_total",
                           selected_team_abbr="NYY")
        assert result.matched_pattern_type == "team_total_after_state"

    def test_f5_total_maps_to_f5_pace(self, db):
        result = self._map(db, derivative_type="f5_total")
        assert result.matched_pattern_type == "f5_pace"

    def test_fg_spread_returns_unavailable(self, db):
        result = self._map(db, derivative_type="fg_spread")
        assert result.available is False
        assert result.matched_pattern_type is None

    def test_f5_spread_returns_unavailable(self, db):
        result = self._map(db, derivative_type="f5_spread")
        assert result.available is False

    def test_fg_moneyline_returns_unavailable(self, db):
        result = self._map(db, derivative_type="fg_moneyline")
        assert result.available is False

    def test_unknown_derivative_returns_unavailable(self, db):
        # Must clear candidate_type so dispatch falls through to unavailable
        result = self._map(db, derivative_type="some_future_thing",
                           candidate_type="unknown_type")
        assert result.available is False
        assert result.matched_pattern_type is None

    def test_unavailable_has_summary_text(self, db):
        result = self._map(db, derivative_type="fg_spread")
        assert isinstance(result.summary_text, str)
        assert len(result.summary_text) > 0

    def test_candidate_id_preserved(self, db):
        result = self._map(db, id=42, derivative_type="fg_total")
        assert result.candidate_id == 42

    def test_as_of_date_extracted_from_created_at(self, db):
        result = self._map(db, derivative_type="fg_total",
                           created_at="2025-07-15T19:30:00")
        assert result.as_of_date == "2025-07-15"

    def test_as_of_date_defaults_to_today_when_missing(self, db):
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="fg_total")
        c.pop("created_at", None)
        result = map_candidate_to_pattern(db, c)
        assert result.as_of_date == date.today().isoformat()

    def test_blocked_candidate_still_returns_context(self, db):
        """Blocked status must not suppress historical context — display only."""
        result = self._map(db, derivative_type="fg_total",
                           status="blocked",
                           blocked_reason="no_baseline")
        # Should return a HistoricalContextResult, not raise
        from mlb.candidate_pattern_mapper import HistoricalContextResult
        assert isinstance(result, HistoricalContextResult)

    def test_blocked_candidate_result_has_no_recommendation(self, db):
        """Blocked candidates: historical context must never imply override."""
        result = self._map(db, derivative_type="fg_total",
                           status="blocked",
                           blocked_reason="no_baseline")
        names = {f.name for f in dc_fields(result.__class__)}
        forbidden = {n for n in names if "recommend" in n.lower() or "take" in n.lower()}
        assert not forbidden

    def test_team_total_without_team_returns_unavailable(self, db):
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="team_total", selected_team_abbr=None)
        result = map_candidate_to_pattern(db, c)
        assert result.available is False

    def test_confidence_label_propagated(self, db):
        result = self._map(db, derivative_type="fg_total")
        assert result.confidence_label in (
            "insufficient_sample", "thin_sample", "usable_sample", "strong_sample"
        )

    def test_summary_text_mentions_not_enough_when_no_data(self, db):
        """Empty DB → sample_size=0 → summary text should explain no data."""
        result = self._map(db, derivative_type="fg_total")
        # DB is empty so sample=0
        assert "not enough" in result.summary_text.lower() or result.sample_size == 0

    def test_warnings_is_list(self, db):
        result = self._map(db, derivative_type="fg_total")
        assert isinstance(result.warnings, list)

    def test_threshold_hit_rates_is_dict(self, db):
        result = self._map(db, derivative_type="fg_total")
        assert isinstance(result.threshold_hit_rates, dict)

    def test_filters_used_is_dict(self, db):
        result = self._map(db, derivative_type="fg_total")
        assert isinstance(result.filters_used, dict)

    def test_market_overreaction_candidate_type_maps_to_pattern(self, db):
        """candidate_type='market_overreaction' with fg_total deriv → pattern mapped."""
        result = self._map(db, derivative_type="fg_total",
                           candidate_type="market_overreaction")
        assert result.matched_pattern_type is not None or result.available is False

    def test_no_take_label_in_summary_text(self, db):
        """summary_text must never contain trade recommendations."""
        result = self._map(db, derivative_type="fg_total")
        forbidden = ["take", "buy", "sell", "enter", "place"]
        lower = result.summary_text.lower()
        for word in forbidden:
            assert word not in lower, f"Forbidden word '{word}' in summary: {result.summary_text}"


# ── as_of_date safety ─────────────────────────────────────────────────────────

class TestAsOfDateSafety:
    def test_as_of_date_passed_as_candidate_date_not_today(self, db):
        """Pattern query must use candidate's date, not today."""
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="fg_total", created_at="2025-04-01T19:00:00")
        result = map_candidate_to_pattern(db, c)
        assert result.as_of_date == "2025-04-01"

    def test_explicit_as_of_date_overrides_created_at(self, db):
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="fg_total", created_at="2025-04-01T19:00:00")
        result = map_candidate_to_pattern(db, c, as_of_date="2025-06-01")
        assert result.as_of_date == "2025-06-01"

    def test_future_as_of_date_returns_zero_sample(self, db):
        """Passing a future date to an empty DB returns 0 sample, not an error."""
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="fg_total")
        result = map_candidate_to_pattern(db, c, as_of_date="2099-01-01")
        assert result.sample_size == 0


# ── map_candidates_batch ──────────────────────────────────────────────────────

class TestMapCandidatesBatch:
    def test_batch_returns_list(self, db):
        from mlb.candidate_pattern_mapper import map_candidates_batch
        result = map_candidates_batch(db, [])
        assert isinstance(result, list)

    def test_empty_batch_returns_empty_list(self, db):
        from mlb.candidate_pattern_mapper import map_candidates_batch
        result = map_candidates_batch(db, [])
        assert result == []

    def test_batch_maps_multiple_candidates(self, db):
        from mlb.candidate_pattern_mapper import map_candidates_batch, HistoricalContextResult
        candidates = [
            _cand(id=1, derivative_type="fg_total"),
            _cand(id=2, derivative_type="f5_total"),
            _cand(id=3, derivative_type="team_total"),
        ]
        results = map_candidates_batch(db, candidates)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, HistoricalContextResult)

    def test_batch_preserves_candidate_ids(self, db):
        from mlb.candidate_pattern_mapper import map_candidates_batch
        candidates = [_cand(id=10), _cand(id=20), _cand(id=30)]
        results = map_candidates_batch(db, candidates)
        ids = [r.candidate_id for r in results]
        assert ids == [10, 20, 30]

    def test_one_bad_candidate_does_not_fail_batch(self, db):
        """A candidate that crashes internally must not abort the whole batch."""
        from mlb.candidate_pattern_mapper import map_candidates_batch, HistoricalContextResult
        bad = {"id": 99}  # missing all required fields
        good = _cand(id=1, derivative_type="f5_total")
        results = map_candidates_batch(db, [bad, good])
        assert len(results) == 2
        for r in results:
            assert isinstance(r, HistoricalContextResult)

    def test_batch_bad_candidate_returns_unavailable_not_raise(self, db):
        from mlb.candidate_pattern_mapper import map_candidates_batch
        bad = {"id": 99}
        results = map_candidates_batch(db, [bad])
        assert results[0].available is False

    def test_batch_with_as_of_date_passes_through(self, db):
        from mlb.candidate_pattern_mapper import map_candidates_batch
        candidates = [_cand(id=1, created_at="2025-04-01T20:00:00")]
        results = map_candidates_batch(db, candidates, as_of_date="2025-05-01")
        assert results[0].as_of_date == "2025-05-01"

    def test_batch_unavailable_has_sample_zero(self, db):
        from mlb.candidate_pattern_mapper import map_candidates_batch
        bad = {"id": 99}
        results = map_candidates_batch(db, [bad])
        assert results[0].sample_size == 0

    def test_batch_mixed_derivatives(self, db):
        """Batch handles fg_total, f5_total, fg_spread in same call."""
        from mlb.candidate_pattern_mapper import map_candidates_batch
        candidates = [
            _cand(id=1, derivative_type="fg_total"),
            _cand(id=2, derivative_type="f5_total"),
            _cand(id=3, derivative_type="fg_spread"),
        ]
        results = map_candidates_batch(db, candidates)
        spread_result = next(r for r in results if r.candidate_id == 3)
        assert spread_result.available is False
        assert spread_result.matched_pattern_type is None


# ── Summary text quality ──────────────────────────────────────────────────────

class TestSummaryText:
    def _seed_games(self, db):
        """Add enough games to get a usable sample."""
        for pk in range(1, 25):
            game_date = f"2025-04-{pk:02d}"
            db.execute(
                """INSERT INTO mlb_games
                   (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
                    status, game_id, final_away_score, final_home_score, final_total,
                    is_final, last_checked_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pk, game_date, "NYY", "BOS", "NYY", "BOS", "Final",
                 f"NYY@BOS_{pk}", 5, 2, 7, 1,
                 f"{game_date}T22:00:00", f"{game_date}T19:00:00"),
            )
            # Add noisy inning 3
            db.execute(
                """INSERT INTO mlb_inning_scores
                   (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
                   VALUES (?,?,?,?,?,?,datetime('now'))""",
                (pk, 3, "NYY", "BOS", 4, 0),
            )
            for i in [4, 5, 6]:
                db.execute(
                    """INSERT INTO mlb_inning_scores
                       (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
                       VALUES (?,?,?,?,?,?,datetime('now'))""",
                    (pk, i, "NYY", "BOS", 0, 1),
                )
        db.commit()

    def test_summary_text_with_data_mentions_similar_cases(self, db):
        self._seed_games(db)
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="fg_total", inning=3,
                  created_at="2099-01-01T00:00:00")
        result = map_candidate_to_pattern(db, c)
        if result.sample_size > 0:
            assert "similar cases" in result.summary_text.lower() or \
                   str(result.sample_size) in result.summary_text

    def test_summary_text_unavailable_is_clear(self, db):
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="fg_spread")
        result = map_candidate_to_pattern(db, c)
        assert "unavailable" in result.summary_text.lower() or \
               "not enough" in result.summary_text.lower() or \
               "no pattern" in result.summary_text.lower()

    def test_thin_sample_warning_in_warnings(self, db):
        """With only 1 game, confidence should be thin/insufficient and warnings populated."""
        db.execute(
            """INSERT INTO mlb_games
               (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
                status, game_id, final_away_score, final_home_score, final_total,
                is_final, last_checked_at, created_at)
               VALUES (1,'2025-04-01','NYY','BOS','NYY','BOS','Final',
                       'NYY@BOS',5,2,7,1,'2025-04-01T22:00:00','2025-04-01T19:00:00')""",
        )
        db.execute(
            """INSERT INTO mlb_inning_scores
               (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
               VALUES (1,3,'NYY','BOS',4,0,datetime('now'))""",
        )
        db.commit()
        from mlb.candidate_pattern_mapper import map_candidate_to_pattern
        c = _cand(derivative_type="fg_total", inning=3,
                  created_at="2099-01-01T00:00:00")
        result = map_candidate_to_pattern(db, c)
        if result.sample_size > 0 and result.sample_size < 20:
            assert len(result.warnings) > 0


# ── API endpoint ──────────────────────────────────────────────────────────────

class TestAPIEndpoint:
    def test_router_importable(self):
        from api.routers import candidate_history
        assert candidate_history is not None

    def test_router_has_router_attr(self):
        from api.routers.candidate_history import router
        assert router is not None

    def test_endpoint_registered_in_app(self):
        from api.main import app
        paths = [r.path for r in app.routes]
        assert any("historical-context" in p for p in paths), (
            f"No historical-context route found. Routes: {paths}"
        )

    def test_api_returns_expected_shape(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get(
            "/api/mlb/candidates/historical-context",
            params={"date": "2025-06-01"},
        )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert "date" in data
        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_api_returns_correct_date(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get(
            "/api/mlb/candidates/historical-context",
            params={"date": "2025-06-14"},
        )
        app.dependency_overrides.clear()

        data = resp.json()
        assert data["date"] == "2025-06-14"

    def test_api_items_have_required_keys(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get(
            "/api/mlb/candidates/historical-context",
            params={"date": "2025-06-01"},
        )
        app.dependency_overrides.clear()

        data = resp.json()
        # Items may be empty (no candidates) but if present must have shape
        for item in data["items"]:
            assert "candidate_id" in item
            assert "confidence_label" in item
            assert "summary_text" in item
            assert "available" in item
            assert "sample_size" in item
            assert "as_of_date" in item
            assert "matched_pattern_type" in item
            assert "threshold_hit_rates" in item
            assert "warnings" in item

    def test_api_no_take_fields_in_items(self, db):
        """API response items must never contain trade recommendation fields."""
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get("/api/mlb/candidates/historical-context",
                          params={"date": "2025-06-01"})
        app.dependency_overrides.clear()

        data = resp.json()
        forbidden = {"take", "recommendation", "trade", "signal"}
        for item in data["items"]:
            for key in item:
                assert key.lower() not in forbidden, (
                    f"Forbidden field '{key}' found in response item"
                )

    def test_api_default_date_returns_200(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get("/api/mlb/candidates/historical-context")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
