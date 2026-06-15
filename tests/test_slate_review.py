"""
tests/test_slate_review.py — Slate Activity Review endpoint tests.

Covers:
  - summary counts (total / watched / blocked / spread_blocked / games / markets)
  - game-level summary groups correctly by game_id
  - derivative-level summary groups by derivative_type
  - spread derivative types counted in spread_blocked
  - export CSV includes required columns
  - empty slate returns clean empty state (zeros, empty lists)
  - run_health rows surface in response
  - watcher_cycles for date appear in response
"""
import csv
import io
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app
from db.schema import init_db, log_watcher_cycle, write_run_health
from mlb.candidates import insert_candidate_event


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def mem() -> sqlite3.Connection:
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def client(mem):
    app.dependency_overrides[get_db] = lambda: mem
    yield TestClient(app)
    app.dependency_overrides.clear()


TODAY = date.today().isoformat()


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _add_game(conn: sqlite3.Connection, game_pk: int, game_id: str, status: str = "Live") -> None:
    away, home = (game_id.split("@") + ["HME"])[:2]
    conn.execute(
        """
        INSERT OR REPLACE INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (game_pk, TODAY, away, home, away, home, game_id,
         status, 1 if status == "Final" else 0),
    )
    conn.commit()


def _add_candidate(
    conn: sqlite3.Connection,
    *,
    game_pk: int,
    game_id: str = "NYY@BOS",
    status: str = "observed_only",
    blocked_reason: str | None = None,
    derivative_type: str | None = "fg_total",
    overall_watch_score: float | None = None,
    market_ticker: str | None = None,
) -> int:
    cid = insert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=game_pk,
        game_id=game_id,
        market_ticker=market_ticker or f"KXMLB-T-{game_pk}",
        status=status,
        blocked_reason=blocked_reason,
        derivative_type=derivative_type,
        overall_watch_score=overall_watch_score,
    )
    return cid


# ── 1. Empty slate ────────────────────────────────────────────────────────────

class TestEmptySlate:
    def test_empty_returns_zero_summary(self, client):
        r = client.get("/api/slate/review", params={"date": TODAY})
        assert r.status_code == 200
        data = r.json()
        s = data["summary"]
        assert s["total_candidates"] == 0
        assert s["watched"] == 0
        assert s["blocked"] == 0
        assert s["spread_blocked"] == 0
        assert s["games_with_activity"] == 0
        assert s["unique_markets"] == 0
        assert s["latest_event_at"] is None

    def test_empty_returns_empty_lists(self, client):
        r = client.get("/api/slate/review", params={"date": TODAY})
        data = r.json()
        assert data["games"] == []
        assert data["derivatives"] == []
        assert data["events"] == []

    def test_wrong_date_returns_empty(self, client, mem):
        _add_game(mem, 1, "NYY@BOS")
        _add_candidate(mem, game_pk=1, game_id="NYY@BOS")
        r = client.get("/api/slate/review", params={"date": "2020-01-01"})
        data = r.json()
        assert data["summary"]["total_candidates"] == 0


# ── 2. Summary counts ─────────────────────────────────────────────────────────

class TestSummaryCounts:
    def test_total_watched_blocked(self, client, mem):
        _add_game(mem, 1, "NYY@BOS")
        _add_candidate(mem, game_pk=1, status="watched")
        _add_candidate(mem, game_pk=1, status="blocked", blocked_reason="low_watch_score")
        _add_candidate(mem, game_pk=1, status="blocked", blocked_reason="low_watch_score")
        _add_candidate(mem, game_pk=1, status="observed_only")

        r = client.get("/api/slate/review", params={"date": TODAY})
        s = r.json()["summary"]
        assert s["total_candidates"] == 4
        assert s["watched"] == 1
        assert s["blocked"] == 2
        assert s["observed_only"] == 1

    def test_spread_blocked_count(self, client, mem):
        _add_game(mem, 2, "CHC@SF")
        _add_candidate(mem, game_pk=2, game_id="CHC@SF", derivative_type="fg_spread", status="blocked")
        _add_candidate(mem, game_pk=2, game_id="CHC@SF", derivative_type="f5_spread", status="blocked")
        _add_candidate(mem, game_pk=2, game_id="CHC@SF", derivative_type="fg_total",  status="watched")

        s = client.get("/api/slate/review", params={"date": TODAY}).json()["summary"]
        assert s["spread_blocked"] == 2

    def test_games_and_markets_count(self, client, mem):
        _add_game(mem, 10, "NYY@BOS")
        _add_game(mem, 11, "LAD@SF")
        _add_candidate(mem, game_pk=10, game_id="NYY@BOS", market_ticker="TICK1")
        _add_candidate(mem, game_pk=10, game_id="NYY@BOS", market_ticker="TICK2")
        _add_candidate(mem, game_pk=11, game_id="LAD@SF",  market_ticker="TICK3")

        s = client.get("/api/slate/review", params={"date": TODAY}).json()["summary"]
        assert s["games_with_activity"] == 2
        assert s["unique_markets"] == 3


# ── 3. Game-level summary ─────────────────────────────────────────────────────

class TestGameSummary:
    def test_groups_by_game(self, client, mem):
        _add_game(mem, 20, "NYY@BOS")
        _add_game(mem, 21, "LAD@SF")
        _add_candidate(mem, game_pk=20, game_id="NYY@BOS", status="watched")
        _add_candidate(mem, game_pk=20, game_id="NYY@BOS", status="blocked", blocked_reason="low_score")
        _add_candidate(mem, game_pk=21, game_id="LAD@SF",  status="observed_only")

        games = client.get("/api/slate/review", params={"date": TODAY}).json()["games"]
        game_ids = {g["game_id"] for g in games}
        assert game_ids == {"NYY@BOS", "LAD@SF"}

    def test_game_counts_correct(self, client, mem):
        _add_game(mem, 30, "NYY@BOS")
        _add_candidate(mem, game_pk=30, game_id="NYY@BOS", status="watched")
        _add_candidate(mem, game_pk=30, game_id="NYY@BOS", status="blocked", blocked_reason="x")
        _add_candidate(mem, game_pk=30, game_id="NYY@BOS", status="blocked", blocked_reason="x")

        g = client.get("/api/slate/review", params={"date": TODAY}).json()["games"][0]
        assert g["total_candidates"] == 3
        assert g["watched"] == 1
        assert g["blocked"] == 2

    def test_top_block_reason(self, client, mem):
        _add_game(mem, 40, "NYY@BOS")
        _add_candidate(mem, game_pk=40, game_id="NYY@BOS", status="blocked", blocked_reason="low_score")
        _add_candidate(mem, game_pk=40, game_id="NYY@BOS", status="blocked", blocked_reason="low_score")
        _add_candidate(mem, game_pk=40, game_id="NYY@BOS", status="blocked", blocked_reason="market_wide")

        g = client.get("/api/slate/review", params={"date": TODAY}).json()["games"][0]
        assert g["top_block_reason"] == "low_score"

    def test_has_spread_blocked_flag(self, client, mem):
        _add_game(mem, 50, "NYY@BOS")
        _add_candidate(mem, game_pk=50, game_id="NYY@BOS", derivative_type="fg_spread", status="blocked")

        g = client.get("/api/slate/review", params={"date": TODAY}).json()["games"][0]
        assert g["has_spread_blocked"] is True

    def test_no_spread_flag_when_not_present(self, client, mem):
        _add_game(mem, 51, "LAD@SF")
        _add_candidate(mem, game_pk=51, game_id="LAD@SF", derivative_type="fg_total", status="blocked")

        g = client.get("/api/slate/review", params={"date": TODAY}).json()["games"][0]
        assert g["has_spread_blocked"] is False

    def test_derivative_types_list(self, client, mem):
        _add_game(mem, 60, "NYY@BOS")
        _add_candidate(mem, game_pk=60, game_id="NYY@BOS", derivative_type="fg_total")
        _add_candidate(mem, game_pk=60, game_id="NYY@BOS", derivative_type="team_total")

        g = client.get("/api/slate/review", params={"date": TODAY}).json()["games"][0]
        assert set(g["derivative_types"]) == {"fg_total", "team_total"}


# ── 4. Derivative-level summary ───────────────────────────────────────────────

class TestDerivativeSummary:
    def test_groups_by_derivative_type(self, client, mem):
        _add_game(mem, 70, "NYY@BOS")
        _add_candidate(mem, game_pk=70, derivative_type="fg_total",   status="watched")
        _add_candidate(mem, game_pk=70, derivative_type="fg_total",   status="blocked")
        _add_candidate(mem, game_pk=70, derivative_type="team_total", status="blocked")

        derivs = {
            d["derivative_type"]: d
            for d in client.get("/api/slate/review", params={"date": TODAY}).json()["derivatives"]
        }
        assert "fg_total" in derivs
        assert "team_total" in derivs
        assert derivs["fg_total"]["total"] == 2
        assert derivs["fg_total"]["watched"] == 1
        assert derivs["fg_total"]["blocked"] == 1
        assert derivs["team_total"]["total"] == 1

    def test_avg_watch_score(self, client, mem):
        _add_game(mem, 80, "NYY@BOS")
        _add_candidate(mem, game_pk=80, derivative_type="fg_total",
                       overall_watch_score=0.60)
        _add_candidate(mem, game_pk=80, derivative_type="fg_total",
                       overall_watch_score=0.40)

        derivs = {
            d["derivative_type"]: d
            for d in client.get("/api/slate/review", params={"date": TODAY}).json()["derivatives"]
        }
        assert derivs["fg_total"]["avg_watch_score"] == pytest.approx(0.50, abs=0.01)

    def test_unknown_derivative_type_grouped(self, client, mem):
        _add_game(mem, 90, "NYY@BOS")
        cid = insert_candidate_event(
            mem,
            candidate_type="full_game_total_extreme_reprice_watch",
            game_pk=90, game_id="NYY@BOS",
        )
        # derivative_type left NULL → should appear as "unknown"

        derivs = {
            d["derivative_type"]: d
            for d in client.get("/api/slate/review", params={"date": TODAY}).json()["derivatives"]
        }
        assert "unknown" in derivs


# ── 5. Run health in response ─────────────────────────────────────────────────

class TestRunHealth:
    def test_health_surfaces_process_entries(self, client, mem):
        write_run_health(mem, "live_watcher", last_run_at="2026-06-14T22:00:00", error_count=0)
        write_run_health(mem, "mlb_poller",   last_run_at="2026-06-14T22:01:00", error_count=2,
                         last_error="timeout")

        health = client.get("/api/slate/review", params={"date": TODAY}).json()["health"]
        assert "live_watcher" in health
        assert health["live_watcher"]["last_run_at"] == "2026-06-14T22:00:00"
        assert health["mlb_poller"]["error_count"] == 2
        assert health["mlb_poller"]["last_error"] == "timeout"

    def test_health_empty_when_no_process_rows(self, client):
        health = client.get("/api/slate/review", params={"date": TODAY}).json()["health"]
        assert isinstance(health, dict)


# ── 6. Watcher cycles in response ─────────────────────────────────────────────

class TestWatcherCycles:
    def test_cycles_for_date_included(self, client, mem):
        log_watcher_cycle(
            mem, started_at=f"{TODAY}T21:00:00", finished_at=f"{TODAY}T21:00:15",
            games_scanned=5, markets_seen=30, candidates_inserted=3,
            watched_count=1, blocked_count=2, errors_count=0,
        )

        cycles = client.get("/api/slate/review", params={"date": TODAY}).json()["cycles"]
        assert len(cycles) == 1
        assert cycles[0]["games_scanned"] == 5
        assert cycles[0]["markets_seen"] == 30
        assert cycles[0]["candidates_inserted"] == 3

    def test_cycles_other_date_excluded(self, client, mem):
        log_watcher_cycle(mem, started_at="2020-01-01T10:00:00", finished_at="2020-01-01T10:00:10")

        cycles = client.get("/api/slate/review", params={"date": TODAY}).json()["cycles"]
        assert cycles == []


# ── 7. Export CSV ─────────────────────────────────────────────────────────────

class TestExportCsv:
    def test_export_csv_returns_csv(self, client, mem):
        _add_game(mem, 100, "NYY@BOS")
        _add_candidate(mem, game_pk=100, game_id="NYY@BOS", status="watched",
                       derivative_type="fg_total")

        r = client.get("/api/slate/export", params={"date": TODAY, "format": "csv"})
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]

    def test_export_csv_has_required_columns(self, client, mem):
        _add_game(mem, 101, "NYY@BOS")
        _add_candidate(mem, game_pk=101, game_id="NYY@BOS", status="blocked",
                       blocked_reason="low_score", derivative_type="fg_total")

        r = client.get("/api/slate/export", params={"date": TODAY, "format": "csv"})
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        assert len(rows) == 1

        row = rows[0]
        assert "created_at" in row
        assert "game_id" in row
        assert "matchup" in row
        assert "derivative_type" in row
        assert "read_type" in row
        assert "status" in row
        assert "blocked_reason" in row
        assert "overall_watch_score" in row
        assert "derivative_rationale" in row
        assert "seen_count" in row

    def test_export_matchup_column(self, client, mem):
        _add_game(mem, 102, "NYY@BOS")
        _add_candidate(mem, game_pk=102, game_id="NYY@BOS")

        r = client.get("/api/slate/export", params={"date": TODAY, "format": "csv"})
        reader = csv.DictReader(io.StringIO(r.text))
        row = next(reader)
        assert row["matchup"] == "NYY@BOS"

    def test_export_empty_slate(self, client):
        r = client.get("/api/slate/export", params={"date": "2020-01-01", "format": "csv"})
        assert r.status_code == 200
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        assert rows == []

    def test_export_json_format(self, client, mem):
        _add_game(mem, 103, "NYY@BOS")
        _add_candidate(mem, game_pk=103, game_id="NYY@BOS")

        r = client.get("/api/slate/export", params={"date": TODAY, "format": "json"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert "game_id" in data[0]
        assert "derivative_type" in data[0]
