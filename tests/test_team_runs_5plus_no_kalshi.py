"""Unit tests for team_runs_5plus_no_kalshi_validation.py"""
import csv
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from team_runs_5plus_no_kalshi_validation import (
    _parse_team5_ticker,
    _load_game_starts_by_pk,
    _build_ticker_index,
    _no_fill_price,
    _no_spread_cents,
    _assess_fill_quality_no,
    _pnl_no,
    _net_edge_no,
    _is_candidate,
    _to_kalshi_team,
    _is_hit,
    _find_candidate_ticker,
    _load_candidates,
    _match_candidates,
    FEE_BUFFER_CENTS,
    WIDE_SPREAD_THRESHOLD,
    ABSURD_BID_MAX,
    ABSURD_ASK_MIN,
    THRESHOLD,
    CALIBRATED_PROB,
    BRAIN_TO_KALSHI,
)


# ── Existing tests (unchanged) ─────────────────────────────────────────────────

class TestParseTeam5Ticker(unittest.TestCase):
    def test_parses_away_team(self):
        result = _parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH5")
        self.assertIsNotNone(result)
        self.assertEqual(result["team_code"], "ATH")
        self.assertEqual(result["away_team"], "ATH")
        self.assertEqual(result["home_team"], "SF")

    def test_parses_home_team(self):
        result = _parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-SF5")
        self.assertIsNotNone(result)
        self.assertEqual(result["team_code"], "SF")
        self.assertEqual(result["away_team"], "ATH")
        self.assertEqual(result["home_team"], "SF")

    def test_does_not_derive_game_start_utc_from_ticker(self):
        # Fixed per outputs/kalshi_time_audit/: ticker HHMM is not reliably UTC
        # (audited ~4h off from mlb_games.game_start_time_utc). The parser must
        # no longer manufacture a "game_start_utc" from ticker time at all —
        # authoritative start time comes only from mlb_games via game_pk
        # (see _load_game_starts_by_pk).
        result = _parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH5")
        self.assertIsNotNone(result)
        self.assertNotIn("game_start_utc", result)

    def test_returns_none_for_team4(self):
        self.assertIsNone(_parse_team5_ticker("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH4"))

    def test_returns_none_for_moneyline(self):
        self.assertIsNone(_parse_team5_ticker("KXMLBGAME-26JUN232145ATHSF-ATH"))

    def test_returns_none_for_garbage(self):
        self.assertIsNone(_parse_team5_ticker("GARBAGE"))


class TestLoadGameStartsByPk(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE mlb_games (game_pk INT, game_start_time_utc TEXT)"
        )
        return conn

    def test_loads_authoritative_start_from_mlb_games(self):
        conn = self._make_conn()
        conn.execute(
            "INSERT INTO mlb_games VALUES (?, ?)",
            (823521, "2026-07-19T23:20"),
        )
        conn.commit()
        starts = _load_game_starts_by_pk(conn)
        conn.close()
        self.assertEqual(
            starts["823521"], datetime(2026, 7, 19, 23, 20, tzinfo=timezone.utc)
        )

    def test_skips_null_start_time(self):
        conn = self._make_conn()
        conn.execute("INSERT INTO mlb_games VALUES (?, ?)", (1, None))
        conn.commit()
        starts = _load_game_starts_by_pk(conn)
        conn.close()
        self.assertEqual(starts, {})


class TestNoFillPrice(unittest.TestCase):
    def test_returns_no_ask(self):
        snap = {"no_ask": 58, "no_bid": 42, "yes_ask": 43, "yes_bid": 57}
        self.assertEqual(_no_fill_price(snap), 58)

    def test_returns_none_when_no_ask_null(self):
        snap = {"no_ask": None, "no_bid": None, "yes_ask": 43, "yes_bid": 57}
        self.assertIsNone(_no_fill_price(snap))

    def test_returns_none_when_no_ask_zero(self):
        snap = {"no_ask": 0}
        self.assertIsNone(_no_fill_price(snap))


class TestNoSpreadCents(unittest.TestCase):
    def test_computes_spread(self):
        snap = {"no_ask": 58, "no_bid": 42}
        self.assertEqual(_no_spread_cents(snap), 16)

    def test_none_when_bid_null(self):
        snap = {"no_ask": 58, "no_bid": None}
        self.assertIsNone(_no_spread_cents(snap))

    def test_none_when_ask_null(self):
        snap = {"no_ask": None, "no_bid": 42}
        self.assertIsNone(_no_spread_cents(snap))


class TestAssessFillQualityNo(unittest.TestCase):
    GAME_START = datetime(2026, 6, 23, 21, 45, tzinfo=timezone.utc)

    def _snap(self, **kwargs):
        defaults = {
            "no_ask": 58, "no_bid": 53, "yes_ask": 43, "yes_bid": 42,
            "snapped_at": "2026-06-23T21:30:00+00:00",
        }
        defaults.update(kwargs)
        return defaults

    def test_usable_good_book(self):
        quality, reason = _assess_fill_quality_no(self._snap(), self.GAME_START)
        self.assertEqual(quality, "usable")

    def test_no_ask_missing(self):
        quality, reason = _assess_fill_quality_no(self._snap(no_ask=None), self.GAME_START)
        self.assertEqual(quality, "no_ask")

    def test_no_ask_zero(self):
        quality, reason = _assess_fill_quality_no(self._snap(no_ask=0), self.GAME_START)
        self.assertEqual(quality, "no_ask")

    def test_wide_spread(self):
        snap = self._snap(no_ask=58, no_bid=47)
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "wide_spread")

    def test_tight_spread_usable(self):
        snap = self._snap(no_ask=58, no_bid=53)
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "usable")

    def test_stale_snapshot(self):
        snap = self._snap(snapped_at="2026-06-23T19:00:00+00:00")
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "stale_snapshot")

    def test_fresh_within_window_usable(self):
        snap = self._snap(snapped_at="2026-06-23T21:43:30+00:00")
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "usable")

    def test_invalid_book(self):
        snap = self._snap(yes_bid=1, no_ask=97)
        quality, reason = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "invalid_book")


class TestPnlNo(unittest.TestCase):
    def test_win(self):
        result = _pnl_no(no_ask=40, won=True)
        self.assertAlmostEqual(result, 100.0 - 40.0 - FEE_BUFFER_CENTS)

    def test_loss(self):
        result = _pnl_no(no_ask=40, won=False)
        self.assertAlmostEqual(result, -40.0)

    def test_win_at_higher_ask(self):
        result = _pnl_no(no_ask=65, won=True)
        self.assertAlmostEqual(result, 100.0 - 65.0 - FEE_BUFFER_CENTS)


class TestNetEdgeNo(unittest.TestCase):
    def test_positive_edge(self):
        result = _net_edge_no(calib_prob=0.686, no_ask=55)
        self.assertAlmostEqual(result, 12.1)

    def test_negative_edge_at_high_ask(self):
        result = _net_edge_no(calib_prob=0.686, no_ask=77)
        self.assertAlmostEqual(result, -9.9)

    def test_breakeven(self):
        result = _net_edge_no(calib_prob=0.686, no_ask=67.1)
        self.assertAlmostEqual(result, 0.0, places=0)


# ── New tests — candidate selection ───────────────────────────────────────────

class TestIsCandidate(unittest.TestCase):
    def test_qualifies_at_threshold(self):
        self.assertTrue(_is_candidate({"team_runs_5plus_no_score": "0.40"}))

    def test_qualifies_above_threshold(self):
        self.assertTrue(_is_candidate({"team_runs_5plus_no_score": "0.55"}))

    def test_does_not_qualify_below(self):
        self.assertFalse(_is_candidate({"team_runs_5plus_no_score": "0.39"}))

    def test_does_not_qualify_zero(self):
        self.assertFalse(_is_candidate({"team_runs_5plus_no_score": "0.0"}))

    def test_does_not_qualify_missing_key(self):
        self.assertFalse(_is_candidate({}))

    def test_does_not_qualify_blank_value(self):
        self.assertFalse(_is_candidate({"team_runs_5plus_no_score": ""}))

    def test_threshold_constant_is_0_40(self):
        self.assertAlmostEqual(THRESHOLD, 0.40)


# ── New tests — team code translation ─────────────────────────────────────────

class TestToKalshiTeam(unittest.TestCase):
    def test_wsn_maps_to_wsh(self):
        self.assertEqual(_to_kalshi_team("WSN"), "WSH")
        self.assertIn("WSN", BRAIN_TO_KALSHI)

    def test_standard_codes_pass_through(self):
        for team in ("ATH", "LAD", "NYY", "SF", "BOS", "COL"):
            self.assertEqual(_to_kalshi_team(team), team)

    def test_no_reverse_mapping_contamination(self):
        # WSH should not map to WSN (only one-way: WSN→WSH)
        self.assertEqual(_to_kalshi_team("WSH"), "WSH")


# ── New tests — _is_hit (same semantics as in logic audit) ────────────────────

class TestIsHitKalshi(unittest.TestCase):
    def test_hit_when_team_scores_under_5(self):
        self.assertTrue(_is_hit({"actual_team_runs_5plus": "0"}))

    def test_miss_when_team_scores_5plus(self):
        self.assertFalse(_is_hit({"actual_team_runs_5plus": "1"}))

    def test_none_when_blank(self):
        self.assertIsNone(_is_hit({"actual_team_runs_5plus": ""}))

    def test_none_when_missing(self):
        self.assertIsNone(_is_hit({}))


# ── New tests — ticker matching ────────────────────────────────────────────────
# Rewritten per outputs/kalshi_time_audit/: game start time now comes only from
# mlb_games via game_pk (authoritative), never from ticker time. The ticker
# index is keyed by team_code -> {actual snapshot collection date: {tickers}},
# and doubleheader ambiguity (>1 distinct ticker for a team in the date window)
# is refused rather than guessed.

class TestFindCandidateTicker(unittest.TestCase):
    GAME_PK = "823521"
    GAME_START = datetime(2026, 6, 23, 21, 45, tzinfo=timezone.utc)
    STARTS = {GAME_PK: GAME_START}
    INDEX = {
        "ATH": {"2026-06-23": {"KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH5"}},
        "SF":  {"2026-06-23": {"KXMLBTEAMTOTAL-26JUN232145ATHSF-SF5"}},
    }

    def test_matches_away_team(self):
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-23", "team": "ATH", "game_pk": self.GAME_PK},
            self.INDEX, self.STARTS,
        )
        self.assertEqual(status, "ok")
        self.assertEqual(ticker, "KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH5")
        self.assertEqual(start, self.GAME_START)

    def test_matches_home_team(self):
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-23", "team": "SF", "game_pk": self.GAME_PK},
            self.INDEX, self.STARTS,
        )
        self.assertEqual(status, "ok")
        self.assertEqual(ticker, "KXMLBTEAMTOTAL-26JUN232145ATHSF-SF5")

    def test_no_match_wrong_date(self):
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-25", "team": "ATH", "game_pk": self.GAME_PK},
            self.INDEX, self.STARTS,
        )
        self.assertIsNone(ticker)
        self.assertEqual(status, "no_market_match")

    def test_no_match_wrong_team(self):
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-23", "team": "NYY", "game_pk": self.GAME_PK},
            self.INDEX, self.STARTS,
        )
        self.assertIsNone(ticker)
        self.assertEqual(status, "no_market_match")

    def test_no_match_empty_index(self):
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-23", "team": "ATH", "game_pk": self.GAME_PK},
            {}, self.STARTS,
        )
        self.assertIsNone(ticker)
        self.assertEqual(status, "no_market_match")

    def test_no_game_start_match_when_pk_unknown(self):
        # game_pk not present in mlb_games -> refuse, no fallback to ticker time
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-23", "team": "ATH", "game_pk": "999999"},
            self.INDEX, self.STARTS,
        )
        self.assertIsNone(ticker)
        self.assertIsNone(start)
        self.assertEqual(status, "no_game_start_match")

    def test_applies_brain_to_kalshi_mapping(self):
        # WSN (brain) → WSH (Kalshi)
        index = {"WSH": {"2026-06-23": {"KXMLBTEAMTOTAL-26JUN232110WSHPIT-WSH5"}}}
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-23", "team": "WSN", "game_pk": self.GAME_PK},
            index, self.STARTS,
        )
        self.assertEqual(status, "ok")
        self.assertEqual(ticker, "KXMLBTEAMTOTAL-26JUN232110WSHPIT-WSH5")

    def test_non_candidate_team_not_in_index_returns_none(self):
        # Ticker exists in index for NYY but candidate is CWS — no match
        index = {"NYY": {"2026-06-23": {"KXMLBTEAMTOTAL-26JUN232145CWSNYY-NYY5"}}}
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-06-23", "team": "CWS", "game_pk": self.GAME_PK},
            index, self.STARTS,
        )
        self.assertIsNone(ticker)
        self.assertEqual(status, "no_market_match")

    def test_doubleheader_ambiguity_refused_not_guessed(self):
        # Two distinct tickers for the same team on/around the same date (a real
        # doubleheader case found in production data) must be refused, never
        # silently resolved to one of them.
        index = {
            "LAD": {
                "2026-07-19": {
                    "KXMLBTEAMTOTAL-26JUL191920LADNYY-LAD5",
                    "KXMLBTEAMTOTAL-26JUL182008LADNYY-LAD5",
                }
            }
        }
        starts = {"823521": datetime(2026, 7, 19, 23, 20, tzinfo=timezone.utc)}
        ticker, start, status = _find_candidate_ticker(
            {"game_date": "2026-07-19", "team": "LAD", "game_pk": "823521"},
            index, starts,
        )
        self.assertIsNone(ticker)
        self.assertEqual(status, "ambiguous_multiple_tickers")

    def test_ticker_threshold_is_team5_not_team4(self):
        # The ticker in the index must be a [TEAM]5 ticker (enforced by _build_ticker_index
        # which uses _parse_team5_ticker). Verify that a hypothetical [TEAM]4 entry
        # does not appear by testing that _parse_team5_ticker rejects it.
        from team_runs_5plus_no_kalshi_validation import _parse_team5_ticker as p
        self.assertIsNone(p("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH4"))
        self.assertIsNotNone(p("KXMLBTEAMTOTAL-26JUN232145ATHSF-ATH5"))


class TestBuildTickerIndex(unittest.TestCase):
    def _make_conn(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kalshi_orderbook_snapshots "
            "(market_ticker TEXT, market_type TEXT, snapped_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO kalshi_orderbook_snapshots VALUES (?, 'team_total', ?)",
            rows,
        )
        conn.commit()
        return conn

    def test_indexes_by_team_and_actual_snapshot_date_not_ticker_date(self):
        # Ticker embeds date 26JUL18, but snapshots were actually collected on
        # 2026-07-19 (the real, reliable UTC date) — index must key off that,
        # not the ticker's own (unreliable) embedded date.
        conn = self._make_conn([
            ("KXMLBTEAMTOTAL-26JUL182008LADNYY-LAD5", "2026-07-19T17:40:00+00:00"),
        ])
        index = _build_ticker_index(conn)
        conn.close()
        self.assertIn("LAD", index)
        self.assertIn("2026-07-19", index["LAD"])
        self.assertIn("KXMLBTEAMTOTAL-26JUL182008LADNYY-LAD5", index["LAD"]["2026-07-19"])


# ── New tests — candidate loading from CSV ─────────────────────────────────────

class TestLoadCandidates(unittest.TestCase):
    FIELDS = [
        "game_date", "game_id", "team", "home_away",
        "team_runs_5plus_no_score", "actual_team_runs_5plus",
        "top_positive_reasons",
    ]

    def _write_cards(self, rows: list[dict]) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        )
        writer = csv.DictWriter(tmp, fieldnames=self.FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in self.FIELDS})
        tmp.close()
        return Path(tmp.name)

    def test_includes_rows_at_threshold(self):
        path = self._write_cards([
            {"team": "ATH", "team_runs_5plus_no_score": "0.40"},
            {"team": "SF",  "team_runs_5plus_no_score": "0.55"},
        ])
        self.assertEqual(len(_load_candidates(path)), 2)

    def test_excludes_rows_below_threshold(self):
        path = self._write_cards([
            {"team": "ATH", "team_runs_5plus_no_score": "0.39"},
            {"team": "SF",  "team_runs_5plus_no_score": "0.20"},
        ])
        self.assertEqual(len(_load_candidates(path)), 0)

    def test_mixed_includes_and_excludes(self):
        path = self._write_cards([
            {"team": "ATH", "team_runs_5plus_no_score": "0.45"},
            {"team": "SF",  "team_runs_5plus_no_score": "0.30"},
            {"team": "LAD", "team_runs_5plus_no_score": "0.50"},
        ])
        candidates = _load_candidates(path)
        self.assertEqual(len(candidates), 2)
        teams = {c["team"] for c in candidates}
        self.assertIn("ATH", teams)
        self.assertIn("LAD", teams)
        self.assertNotIn("SF", teams)

    def test_empty_csv_returns_empty_list(self):
        path = self._write_cards([])
        self.assertEqual(_load_candidates(path), [])


# ── New tests — NO ask is the fill price, never midpoint or bid ───────────────

class TestNoAskUsedAsFill(unittest.TestCase):
    def test_fill_is_no_ask_not_yes_bid(self):
        snap = {"no_ask": 58, "no_bid": 53, "yes_ask": 43, "yes_bid": 42}
        fill = _no_fill_price(snap)
        self.assertEqual(fill, 58)
        self.assertNotEqual(fill, snap["yes_bid"])

    def test_fill_is_not_midpoint(self):
        snap = {"no_ask": 58, "no_bid": 53, "yes_ask": 43, "yes_bid": 42}
        fill = _no_fill_price(snap)
        midpoint = (snap["no_ask"] + snap["no_bid"]) / 2
        self.assertNotEqual(fill, midpoint)

    def test_fill_none_when_no_ask_absent(self):
        self.assertIsNone(_no_fill_price({"no_ask": None}))

    def test_fill_none_when_no_ask_zero(self):
        self.assertIsNone(_no_fill_price({"no_ask": 0}))


# ── New tests — invalid/no_snapshot rows do not produce positive edge ──────────

class TestInvalidAndNoSnapshotNoEdge(unittest.TestCase):
    GAME_START = datetime(2026, 6, 23, 21, 45, tzinfo=timezone.utc)

    def test_invalid_book_gives_negative_net_edge(self):
        # 99c NO ask → net_edge = 68.6 - 99 - 1.5 < 0
        net = _net_edge_no(CALIBRATED_PROB, 99)
        self.assertLess(net, 0)

    def test_invalid_book_quality_label(self):
        snap = {
            "no_ask": 99, "no_bid": 1, "yes_ask": 99, "yes_bid": 1,
            "snapped_at": "2026-06-23T21:40:00+00:00",
        }
        quality, _ = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "invalid_book")

    def test_stale_snapshot_not_usable(self):
        snap = {
            "no_ask": 45, "no_bid": 44, "yes_ask": 56, "yes_bid": 55,
            "snapped_at": "2026-06-23T19:00:00+00:00",  # 2h45m before game
        }
        quality, _ = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "stale_snapshot")

    def test_no_ask_field_missing_not_usable(self):
        snap = {
            "no_ask": None, "no_bid": 44, "yes_ask": 56, "yes_bid": 55,
            "snapped_at": "2026-06-23T21:40:00+00:00",
        }
        quality, _ = _assess_fill_quality_no(snap, self.GAME_START)
        self.assertEqual(quality, "no_ask")


# ── New tests — empty candidate set handled gracefully ────────────────────────

class TestEmptyCandidateSet(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kalshi_orderbook_snapshots "
            "(market_ticker TEXT, market_type TEXT, snapped_at TEXT, "
            " yes_bid INT, yes_ask INT, no_bid INT, no_ask INT, spread_cents INT)"
        )
        return conn

    def test_empty_list_returns_empty(self):
        conn = self._make_conn()
        result = _match_candidates([], conn, {}, {})
        conn.close()
        self.assertEqual(result, [])

    def test_candidate_with_no_market_match(self):
        conn = self._make_conn()
        # game_pk known to mlb_games (so match fails at the ticker-index stage,
        # specifically "no_market_match", not "no_game_start_match")
        game_starts = {"823999": datetime(2023, 5, 17, 20, 0, tzinfo=timezone.utc)}
        candidates = [{"game_date": "2023-05-17", "game_id": "COL@AZ", "team": "AZ",
                       "home_away": "home", "team_runs_5plus_no_score": "0.45",
                       "actual_team_runs_5plus": "0", "top_positive_reasons": "",
                       "game_pk": "823999"}]
        rows = _match_candidates(candidates, conn, {}, game_starts)
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["match_status"], "no_market_match")
        self.assertEqual(rows[0]["fill_quality"], "no_market_match")
        self.assertEqual(rows[0]["net_edge_at_calib"], "")
        self.assertEqual(rows[0]["would_be_positive_edge"], "")

    def test_candidate_with_unknown_game_pk_refuses_not_guesses(self):
        conn = self._make_conn()
        candidates = [{"game_date": "2023-05-17", "game_id": "COL@AZ", "team": "AZ",
                       "home_away": "home", "team_runs_5plus_no_score": "0.45",
                       "actual_team_runs_5plus": "0", "top_positive_reasons": "",
                       "game_pk": "unknown_pk"}]
        rows = _match_candidates(candidates, conn, {}, {})
        conn.close()
        self.assertEqual(rows[0]["match_status"], "no_game_start_match")


if __name__ == "__main__":
    unittest.main()
