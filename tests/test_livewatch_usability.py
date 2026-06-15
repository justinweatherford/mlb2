"""
tests/test_livewatch_usability.py — Live Watch usability cleanup (Session B).

Covers:
  1. latest_unique=True (Current Setups) collapses by dedupe_key
  2. latest_unique=False (History) returns all rows
  3. duplicate_candidate excluded from default view; present when include_internal_dedup=True
  4. Guardrail structure: failed vs passed separation, warnings preserved
  5. Market layer summary endpoint counts — noisy / unsupported / needs_review
  6. /kalshi/markets/live hide_noisy and supported_only params

All tests use in-memory SQLite. No external services, no real trading.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app
from db.schema import init_db
from mlb.candidates import insert_candidate_event, list_candidate_events, upsert_candidate_event


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert(conn, *, dedupe_key=None, blocked_reason=None, status="observed_only",
            game_id="NYY@BOS", candidate_type="f5_total_overreaction_fade_watch",
            guardrails_json=None):
    cid = insert_candidate_event(
        conn,
        candidate_type=candidate_type,
        game_id=game_id,
        status=status,
        blocked_reason=blocked_reason,
        guardrails_json=guardrails_json,
    )
    if dedupe_key is not None:
        conn.execute(
            "UPDATE candidate_events SET dedupe_key = ? WHERE id = ?",
            (dedupe_key, cid),
        )
        conn.commit()
    return cid


_KALSHI_TICKER_COUNTER = 0


def _insert_kalshi(conn, *, market_type, game_id="NYY@BOS", market_layer_status=None,
                   supported_by_bot=0, is_noisy_market=0, yes_bid_cents=40, yes_ask_cents=60):
    global _KALSHI_TICKER_COUNTER
    _KALSHI_TICKER_COUNTER += 1
    # raw_json is NOT NULL; market_type_label is computed from market_type in schema validator
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type,
            game_id, status, market_layer_status, supported_by_bot, is_noisy_market,
            yes_bid_cents, yes_ask_cents, match_confidence,
            raw_json, discovered_at, updated_at)
           VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, 'high', '{}',
                   datetime('now'), datetime('now'))""",
        (
            f"KXMLB-{market_type}-{game_id}-{_KALSHI_TICKER_COUNTER}",
            f"EVT-{game_id}",
            market_type,
            game_id,
            market_layer_status,
            supported_by_bot,
            is_noisy_market,
            yes_bid_cents,
            yes_ask_cents,
        ),
    )
    conn.commit()


def _make_client(db_path: str):
    def _override():
        c = init_db(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=True)


# ── 1. latest_unique — Current Setups mode ────────────────────────────────────

class TestLatestUnique:
    def test_latest_unique_collapses_same_dedupe_key(self):
        """Two rows sharing a dedupe_key → only the latest is returned."""
        conn = _mem()
        _insert(conn, dedupe_key="game:NYY@BOS:fg_total:8.5")
        _insert(conn, dedupe_key="game:NYY@BOS:fg_total:8.5")
        rows = list_candidate_events(conn, latest_unique=True)
        assert len(rows) == 1
        conn.close()

    def test_latest_unique_returns_highest_id_for_key(self):
        """latest_unique picks the most recent (highest id) row per key."""
        conn = _mem()
        _insert(conn, dedupe_key="k1", status="blocked")
        cid2 = _insert(conn, dedupe_key="k1", status="observed_only")
        rows = list_candidate_events(conn, latest_unique=True)
        assert len(rows) == 1
        assert rows[0]["id"] == cid2
        conn.close()

    def test_latest_unique_different_keys_not_collapsed(self):
        """Different dedupe keys are not collapsed."""
        conn = _mem()
        _insert(conn, dedupe_key="k1")
        _insert(conn, dedupe_key="k2")
        rows = list_candidate_events(conn, latest_unique=True)
        assert len(rows) == 2
        conn.close()

    def test_latest_unique_null_key_always_returned(self):
        """Rows with no dedupe_key each get their own slot (never collapsed)."""
        conn = _mem()
        _insert(conn)
        _insert(conn)
        rows = list_candidate_events(conn, latest_unique=True)
        assert len(rows) == 2
        conn.close()

    def test_history_mode_returns_all_rows(self):
        """latest_unique=False returns every row."""
        conn = _mem()
        _insert(conn, dedupe_key="k1")
        _insert(conn, dedupe_key="k1")
        _insert(conn, dedupe_key="k2")
        rows = list_candidate_events(conn, latest_unique=False)
        assert len(rows) == 3
        conn.close()

    def test_latest_unique_respects_limit(self):
        """latest_unique applies limit after deduplication."""
        conn = _mem()
        for i in range(5):
            _insert(conn, dedupe_key=f"unique-{i}")
        rows = list_candidate_events(conn, latest_unique=True, limit=3)
        assert len(rows) == 3
        conn.close()


# ── 2. duplicate_candidate exclusion ─────────────────────────────────────────

class TestDuplicateCandidateExclusion:
    def test_duplicate_candidate_excluded_by_default(self):
        """Default view hides rows with blocked_reason='duplicate_candidate'."""
        conn = _mem()
        _insert(conn, blocked_reason=None)
        _insert(conn, blocked_reason="duplicate_candidate", status="blocked")
        rows = list_candidate_events(conn)
        assert len(rows) == 1
        assert rows[0]["blocked_reason"] is None
        conn.close()

    def test_duplicate_candidate_visible_when_include_all(self):
        """exclude_blocked_reason=None exposes duplicate_candidate rows."""
        conn = _mem()
        _insert(conn, blocked_reason=None)
        _insert(conn, blocked_reason="duplicate_candidate", status="blocked")
        rows = list_candidate_events(conn, exclude_blocked_reason=None)
        assert len(rows) == 2
        conn.close()

    def test_other_blocked_reasons_not_excluded(self):
        """Only duplicate_candidate is suppressed; other blocked reasons are visible."""
        conn = _mem()
        _insert(conn, blocked_reason="spread_too_wide", status="blocked")
        _insert(conn, blocked_reason="duplicate_candidate", status="blocked")
        rows = list_candidate_events(conn)
        assert len(rows) == 1
        assert rows[0]["blocked_reason"] == "spread_too_wide"
        conn.close()

    def test_api_excludes_duplicate_candidate_by_default(self, tmp_path):
        """API default (include_internal_dedup=False) hides duplicate_candidate."""
        db_path = str(tmp_path / "dup_test.db")
        conn = init_db(db_path)
        _insert(conn, blocked_reason=None)
        _insert(conn, blocked_reason="duplicate_candidate", status="blocked")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 1
        assert all(
            item["blocked_reason"] != "duplicate_candidate"
            for item in body["items"]
        )

    def test_api_includes_duplicate_candidate_when_requested(self, tmp_path):
        """include_internal_dedup=true exposes the suppressed row."""
        db_path = str(tmp_path / "dup_incl_test.db")
        conn = init_db(db_path)
        _insert(conn, blocked_reason=None)
        _insert(conn, blocked_reason="duplicate_candidate", status="blocked")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live?include_internal_dedup=true")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 2


# ── 3. API latest_unique endpoint param ──────────────────────────────────────

class TestApiLatestUniqueParam:
    def test_api_latest_unique_true_collapses(self, tmp_path):
        """?latest_unique=true returns one row per dedupe_key."""
        db_path = str(tmp_path / "lu_true.db")
        conn = init_db(db_path)
        _insert(conn, dedupe_key="k1")
        _insert(conn, dedupe_key="k1")
        _insert(conn, dedupe_key="k2")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live?latest_unique=true")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_api_latest_unique_false_returns_all(self, tmp_path):
        """?latest_unique=false returns all rows (history mode)."""
        db_path = str(tmp_path / "lu_false.db")
        conn = init_db(db_path)
        _insert(conn, dedupe_key="k1")
        _insert(conn, dedupe_key="k1")
        _insert(conn, dedupe_key="k2")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live?latest_unique=false")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3

    def test_api_default_is_latest_unique_true(self, tmp_path):
        """Default behavior collapses duplicate dedupe_keys."""
        db_path = str(tmp_path / "lu_default.db")
        conn = init_db(db_path)
        _insert(conn, dedupe_key="same-key")
        _insert(conn, dedupe_key="same-key")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 1


# ── 4. Guardrail structure ────────────────────────────────────────────────────

class TestGuardrailStructure:
    """Tests for the guardrails_json structure and the failed/passed split logic."""

    def _make_guardrails(self, blocked_reason=None, passed=True, warnings=None,
                         guardrails_checked=None):
        return json.dumps({
            "passed": passed,
            "blocked_reason": blocked_reason,
            "warnings": warnings or [],
            "guardrails_checked": guardrails_checked or [],
        })

    def test_guardrails_json_round_trips(self):
        """guardrails_json is stored and retrieved intact."""
        conn = _mem()
        payload = {
            "passed": True,
            "blocked_reason": None,
            "warnings": [],
            "guardrails_checked": ["spread_check", "price_check"],
        }
        cid = _insert(conn, guardrails_json=json.dumps(payload))
        row = conn.execute(
            "SELECT guardrails_json FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()
        assert json.loads(row["guardrails_json"]) == payload
        conn.close()

    def test_blocked_reason_is_failed_check(self):
        """blocked_reason names the single check that fired; others are passed."""
        payload = {
            "passed": False,
            "blocked_reason": "spread_too_wide",
            "warnings": [],
            "guardrails_checked": ["spread_too_wide", "price_check", "game_active"],
        }
        blocked_reason = payload["blocked_reason"]
        all_checks = payload["guardrails_checked"]

        failed = [g for g in all_checks if g == blocked_reason]
        passed = [g for g in all_checks if g != blocked_reason and g != "duplicate_candidate"]

        assert failed == ["spread_too_wide"]
        assert "spread_too_wide" not in passed
        assert set(passed) == {"price_check", "game_active"}

    def test_no_blocked_reason_means_all_passed(self):
        """When blocked_reason is None, all guardrails_checked are passed."""
        payload = {
            "passed": True,
            "blocked_reason": None,
            "warnings": [],
            "guardrails_checked": ["spread_check", "price_check"],
        }
        failed = [g for g in payload["guardrails_checked"] if g == payload["blocked_reason"]]
        assert failed == []

    def test_duplicate_candidate_excluded_from_operator_passed_list(self):
        """duplicate_candidate is stripped from the operator-visible passed list."""
        all_checks = ["spread_check", "duplicate_candidate", "price_check"]
        blocked_reason = None
        operator_passed = [
            g for g in all_checks
            if g != blocked_reason and g != "duplicate_candidate"
        ]
        assert "duplicate_candidate" not in operator_passed
        assert set(operator_passed) == {"spread_check", "price_check"}

    def test_warnings_preserved_separately(self):
        """Warnings appear alongside passed/failed; they don't affect the split."""
        payload = {
            "passed": True,
            "blocked_reason": None,
            "warnings": ["price_stale_30s"],
            "guardrails_checked": ["spread_check"],
        }
        assert payload["warnings"] == ["price_stale_30s"]
        passed = [g for g in payload["guardrails_checked"] if g != payload["blocked_reason"]]
        assert passed == ["spread_check"]

    def test_guardrails_json_in_api_response(self, tmp_path):
        """guardrails_json is returned as a string in the API response."""
        db_path = str(tmp_path / "gr_test.db")
        conn = init_db(db_path)
        payload = {"passed": True, "blocked_reason": None, "warnings": [],
                   "guardrails_checked": ["spread_check"]}
        _insert(conn, guardrails_json=json.dumps(payload))
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live")
        app.dependency_overrides.clear()
        item = resp.json()["items"][0]
        # Should be a JSON string in the response
        assert isinstance(item["guardrails_json"], str)
        parsed = json.loads(item["guardrails_json"])
        assert parsed["guardrails_checked"] == ["spread_check"]


# ── 5. Market layer summary endpoint ─────────────────────────────────────────

class TestMarketLayerSummary:
    def test_layer_summary_counts_by_status(self, tmp_path):
        """Layer summary returns correct counts per status."""
        db_path = str(tmp_path / "layer_sum.db")
        conn = init_db(db_path)
        _insert_kalshi(conn, market_type="full_game_total", market_layer_status="candidate_worthy",
                       supported_by_bot=1)
        _insert_kalshi(conn, market_type="f5_total", market_layer_status="supported",
                       supported_by_bot=1, game_id="SEA@HOU")
        _insert_kalshi(conn, market_type="player_hr", market_layer_status="noisy_ignored",
                       is_noisy_market=1, game_id="LAD@SF")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/kalshi/markets/layer-summary")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 3
        assert body["candidate_worthy"] == 1
        assert body["supported"] == 1
        assert body["noisy_ignored"] == 1

    def test_layer_summary_empty_db_returns_zeros(self, tmp_path):
        """Empty DB returns all-zero summary."""
        db_path = str(tmp_path / "empty_sum.db")
        init_db(db_path).close()

        client = _make_client(db_path)
        resp = client.get("/api/kalshi/markets/layer-summary")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 0
        assert body["candidate_worthy"] == 0

    def test_layer_summary_null_status_counts_as_discovered(self, tmp_path):
        """Markets with null market_layer_status are counted under 'discovered'."""
        db_path = str(tmp_path / "null_sum.db")
        conn = init_db(db_path)
        _insert_kalshi(conn, market_type="full_game_total", market_layer_status=None)
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/kalshi/markets/layer-summary")
        app.dependency_overrides.clear()
        body = resp.json()
        # NULL market_layer_status is coalesced to 'discovered' in the query
        assert body["discovered"] == 1


# ── 6. /kalshi/markets/live filters ──────────────────────────────────────────

class TestKalshiLiveFilters:
    def test_hide_noisy_excludes_noisy_markets(self, tmp_path):
        """hide_noisy=true excludes is_noisy_market=1 rows."""
        db_path = str(tmp_path / "noisy_live.db")
        conn = init_db(db_path)
        _insert_kalshi(conn, market_type="full_game_total", is_noisy_market=0,
                       market_layer_status="supported")
        _insert_kalshi(conn, market_type="player_hr", is_noisy_market=1,
                       market_layer_status="noisy_ignored", game_id="SEA@HOU")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/kalshi/markets/live?hide_noisy=true&status=open")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 1
        assert all(item["is_noisy_market"] == 0 for item in body["items"])

    def test_supported_only_filter(self, tmp_path):
        """supported_only=true returns only supported_by_bot=1 rows."""
        db_path = str(tmp_path / "supp_live.db")
        conn = init_db(db_path)
        _insert_kalshi(conn, market_type="full_game_total", supported_by_bot=1,
                       market_layer_status="candidate_worthy")
        _insert_kalshi(conn, market_type="extra_innings", supported_by_bot=0,
                       market_layer_status="unsupported", game_id="SEA@HOU")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/kalshi/markets/live?supported_only=true&status=open")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["supported_by_bot"] == 1

    def test_live_response_includes_layer_fields(self, tmp_path):
        """Live endpoint response includes market_layer_status and candidate_surface."""
        db_path = str(tmp_path / "live_fields.db")
        conn = init_db(db_path)
        conn.execute(
            """INSERT INTO kalshi_markets
               (market_ticker, event_ticker, market_type, game_id, status,
                market_layer_status, candidate_surface, supported_by_bot, is_noisy_market,
                selected_team_abbr, yes_bid_cents, yes_ask_cents,
                match_confidence, raw_json, discovered_at, updated_at)
               VALUES ('KXMLB-1','EVT-1','full_game_total','NYY@BOS','open',
                       'candidate_worthy','fg_total',1,0,NULL,40,60,
                       'high','{}',datetime('now'),datetime('now'))"""
        )
        conn.commit()
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/kalshi/markets/live?status=open")
        app.dependency_overrides.clear()
        item = resp.json()["items"][0]
        assert item["market_layer_status"] == "candidate_worthy"
        assert item["candidate_surface"] == "fg_total"
        assert item["supported_by_bot"] == 1
        assert item["is_noisy_market"] == 0


# ── 7. current_setups — broad setup grouping ─────────────────────────────────

def _insert_full(
    conn,
    *,
    game_id="CHC@SF",
    game_pk=None,
    market_ticker="KXMLB-SFTEAM-001",
    candidate_type="trailing_team_total_lag_watch",
    derivative_type="team_total",
    read_type="team_total_lag",
    selected_derivative_type="team_total",
    score_away=2,
    score_home=4,
    inning=5,
    overall_watch_score=0.65,
    status="observed_only",
    entry_yes_bid=40,
    entry_yes_ask=60,
    seen_count=1,
) -> int:
    """Insert a candidate row with full setup fields for current_setups tests."""
    return insert_candidate_event(
        conn,
        candidate_type=candidate_type,
        game_pk=game_pk,
        game_id=game_id,
        market_ticker=market_ticker,
        derivative_type=derivative_type,
        read_type=read_type,
        selected_derivative_type=selected_derivative_type,
        score_away=score_away,
        score_home=score_home,
        inning=inning,
        overall_watch_score=overall_watch_score,
        status=status,
        entry_yes_bid=entry_yes_bid,
        entry_yes_ask=entry_yes_ask,
        seen_count=seen_count,
    )


def _seed_live_game(conn, game_pk: int, game_id: str = "CHC@SF") -> None:
    """Insert a Live mlb_games row so the current_setups live filter passes."""
    away, home = (game_id.split("@") + ["HME"])[:2]
    conn.execute(
        """
        INSERT OR REPLACE INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,date('now'),?,?,?,?,?,'Live',0,datetime('now'),datetime('now'))
        """,
        (game_pk, away, home, away, home, game_id),
    )
    conn.commit()


class TestCurrentSetups:
    """current_setups=True collapses repeated observations of the same setup.

    Broad key: game_id|market_ticker|derivative_type|read_type|selected_derivative_type|candidate_type.
    Ignored: score, inning, outs, runners, watch_score, status, timestamp.
    """

    def test_collapses_same_setup_across_score_changes(self):
        """Same game/market/derivative/read at different scores → one row."""
        conn = _mem()
        _insert_full(conn, score_away=2, score_home=4, inning=5)
        _insert_full(conn, score_away=3, score_home=4, inning=6)
        _insert_full(conn, score_away=3, score_home=5, inning=7)
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 1
        conn.close()

    def test_returns_latest_game_state(self):
        """After collapsing, the returned row has the most recent score/inning."""
        conn = _mem()
        _insert_full(conn, score_away=2, score_home=4, inning=5, overall_watch_score=0.60)
        _insert_full(conn, score_away=3, score_home=5, inning=7, overall_watch_score=0.72)
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 1
        # Latest row has the higher inning/score
        assert rows[0]["inning"] == 7
        assert rows[0]["score_away"] == 3
        assert rows[0]["score_home"] == 5
        conn.close()

    def test_seen_count_aggregated_across_group(self):
        """seen_count is summed across all rows in the broad-key group."""
        conn = _mem()
        _insert_full(conn, score_away=2, score_home=4, inning=5, seen_count=3)
        _insert_full(conn, score_away=3, score_home=5, inning=7, seen_count=5)
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 1
        assert rows[0]["seen_count"] == 8
        conn.close()

    def test_different_markets_same_game_not_collapsed(self):
        """SF Team Total and FG Total are different setups — kept separate."""
        conn = _mem()
        _insert_full(conn, market_ticker="KXMLB-SFTEAM-001",
                     derivative_type="team_total", candidate_type="trailing_team_total_lag_watch")
        _insert_full(conn, market_ticker="KXMLB-FGTOTAL-001",
                     derivative_type="fg_total", candidate_type="full_game_total_extreme_reprice_watch")
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 2
        conn.close()

    def test_different_read_type_same_market_not_collapsed(self):
        """Same market but different read_type → separate rows."""
        conn = _mem()
        _insert_full(conn, read_type="team_total_lag")
        _insert_full(conn, read_type="market_overreaction")
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 2
        conn.close()

    def test_different_derivative_type_not_collapsed(self):
        """Same game/read but different derivative_type → separate rows."""
        conn = _mem()
        _insert_full(conn, derivative_type="team_total", market_ticker="KXMLB-TEAM-001")
        _insert_full(conn, derivative_type="fg_total", market_ticker="KXMLB-FG-001")
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 2
        conn.close()

    def test_different_candidate_type_not_collapsed(self):
        """Same game/market but different candidate_type → separate rows."""
        conn = _mem()
        _insert_full(conn, candidate_type="trailing_team_total_lag_watch")
        _insert_full(conn, candidate_type="f5_total_overreaction_fade_watch")
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 2
        conn.close()

    def test_different_games_same_setup_type_not_collapsed(self):
        """Same derivative/read on different games → separate rows."""
        conn = _mem()
        _insert_full(conn, game_id="CHC@SF", market_ticker="KXMLB-SF-001")
        _insert_full(conn, game_id="NYY@BOS", market_ticker="KXMLB-BOS-001")
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 2
        conn.close()

    def test_latest_bid_ask_returned(self):
        """The latest bid/ask from the most recent observation is returned."""
        conn = _mem()
        _insert_full(conn, inning=5, entry_yes_bid=38, entry_yes_ask=62)
        _insert_full(conn, inning=7, entry_yes_bid=45, entry_yes_ask=55)
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 1
        assert rows[0]["entry_yes_bid"] == 45
        assert rows[0]["entry_yes_ask"] == 55
        conn.close()

    def test_latest_watch_score_returned(self):
        """overall_watch_score from the latest observation is displayed."""
        conn = _mem()
        _insert_full(conn, inning=5, overall_watch_score=0.60)
        _insert_full(conn, inning=7, overall_watch_score=0.78)
        rows = list_candidate_events(conn, current_setups=True)
        assert len(rows) == 1
        assert rows[0]["overall_watch_score"] == pytest.approx(0.78)
        conn.close()

    def test_history_still_returns_all_rows(self):
        """current_setups=False (history) returns every row unchanged."""
        conn = _mem()
        _insert_full(conn, score_away=2, score_home=4, inning=5)
        _insert_full(conn, score_away=3, score_home=4, inning=6)
        _insert_full(conn, score_away=3, score_home=5, inning=7)
        rows = list_candidate_events(conn, current_setups=False, latest_unique=False)
        assert len(rows) == 3
        conn.close()

    def test_current_setups_respects_limit(self):
        """Limit is applied after broad-key deduplication."""
        conn = _mem()
        for i in range(5):
            _insert_full(
                conn,
                game_id=f"TEAM{i}@OPP{i}",
                market_ticker=f"KXMLB-{i:03d}",
            )
        rows = list_candidate_events(conn, current_setups=True, limit=3)
        assert len(rows) == 3
        conn.close()

    def test_api_current_setups_true(self, tmp_path):
        """?current_setups=true returns one row per broad setup key (live games only)."""
        db_path = str(tmp_path / "cs_api.db")
        conn = init_db(db_path)
        _seed_live_game(conn, game_pk=9001)
        # Three rows, same setup, different scores
        _insert_full(conn, game_pk=9001, score_away=2, score_home=4, inning=5)
        _insert_full(conn, game_pk=9001, score_away=3, score_home=4, inning=6)
        _insert_full(conn, game_pk=9001, score_away=3, score_home=5, inning=7)
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live?current_setups=true&latest_unique=false")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["inning"] == 7  # latest state

    def test_api_current_setups_false_returns_all(self, tmp_path):
        """?current_setups=false&latest_unique=false returns all rows (history)."""
        db_path = str(tmp_path / "cs_api_false.db")
        conn = init_db(db_path)
        _insert_full(conn, score_away=2, score_home=4, inning=5)
        _insert_full(conn, score_away=3, score_home=4, inning=6)
        _insert_full(conn, score_away=3, score_home=5, inning=7)
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live?current_setups=false&latest_unique=false")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3

    def test_api_different_markets_separate_rows(self, tmp_path):
        """Different markets for the same live game remain separate in current_setups mode."""
        db_path = str(tmp_path / "cs_markets.db")
        conn = init_db(db_path)
        _seed_live_game(conn, game_pk=9002)
        _insert_full(conn, game_pk=9002, market_ticker="KXMLB-SFTEAM-001",
                     derivative_type="team_total", candidate_type="trailing_team_total_lag_watch")
        _insert_full(conn, game_pk=9002, market_ticker="KXMLB-FGTOTAL-001",
                     derivative_type="fg_total", candidate_type="full_game_total_extreme_reprice_watch")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live?current_setups=true&latest_unique=false")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_api_seen_count_aggregated(self, tmp_path):
        """seen_count in current_setups response is the sum across all grouped rows."""
        db_path = str(tmp_path / "cs_count.db")
        conn = init_db(db_path)
        _seed_live_game(conn, game_pk=9003)
        _insert_full(conn, game_pk=9003, score_away=2, score_home=4, inning=5, seen_count=3)
        _insert_full(conn, game_pk=9003, score_away=3, score_home=5, inning=7, seen_count=5)
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/candidates/live?current_setups=true&latest_unique=false")
        app.dependency_overrides.clear()
        body = resp.json()
        assert body["items"][0]["seen_count"] == 8
