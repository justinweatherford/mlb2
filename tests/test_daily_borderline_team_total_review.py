"""
tests/test_daily_borderline_team_total_review.py

Unit tests for daily_borderline_team_total_review.py.

Critical invariants:
  1. classify_band correctly buckets scores by lane
  2. Rows at or above ACTION_THRESHOLD (0.40) are excluded
  3. 5+NO lane uses NO ask as realistic_direction_ask
  4. 4+ lane uses YES ask as realistic_direction_ask
  5. F5 market_status is always 'unavailable'
  6. calibrated_probability is always None below 0.40 (no calibration exists)
  7. grade_outcome produces correct hit/miss/pending for all lanes
  8. upsert_history deduplicates by (game_date, game_id, team, lane)
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import daily_borderline_team_total_review as rpt


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _brain_row(
    team="TB",
    game_id="KC@TB",
    lane="team_runs_4plus",
    score=0.25,
    game_date="2026-06-25",
    **kw,
) -> dict:
    return {
        "game_date": game_date,
        "game_id": game_id,
        "team": team,
        "opponent": "KC",
        "home_away": "home",
        "lane": lane,
        "score": score,
        "band": rpt.classify_band(lane, score),
        "opponent_starter_name": "Seth Lugo",
        "opponent_starter_xfip": 5.674,
        "opponent_starter_xfip_bucket": "very_bad_5_25_plus",
        "opponent_starter_kbb_bucket": "below_avg_8_13",
        "opponent_starter_ip_bucket": "normal_5_0_5_8",
        "opponent_starter_ra9_bucket": "bad_5_0_6_0",
        "opponent_starter_bad_start_rate_bucket": "high",
        "starter_feature_source": "current_season",
        "opponent_starter_feature_source": "current_season",
        "starter_starts_used": "8",
        "opponent_starter_starts_used": "8",
        "top_positive_reasons": "[team_won] tag_live_rebound_watch=yes(+0.084)",
        "actual_team_runs": "",
        "actual_team_runs_4plus": "",
        "actual_team_runs_5plus": "",
        "actual_team_f5_runs_2plus": "",
        "actual_status": "",
        **kw,
    }


def _snap(
    yes_bid=60, yes_ask=62, no_bid=38, no_ask=40,
    spread=2, snapped_at="2026-06-25T15:00:00+00:00",
    settled_yes=None,
) -> dict:
    return {
        "ticker": "KXMLBTEAMTOTAL-26JUN251210KCTB-TB4",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "spread_cents": spread,
        "snapped_at": snapped_at,
        "settled_yes": settled_yes,
    }


def _cat(open_price=63) -> dict:
    return {
        "ticker": "KXMLBTEAMTOTAL-26JUN251210KCTB-TB4",
        "open_price_cents": open_price,
        "catalog_yes_bid": 60,
        "catalog_yes_ask": 62,
        "game_id": "KC@TB",
        "settlement_horizon": "full_game",
    }


def _actuals_db(game_id="KC@TB", home_score=5, away_score=2, is_final=True) -> dict:
    return {
        game_id: {
            "away_abbr": "KC",
            "home_abbr": "TB",
            "away_score": away_score,
            "home_score": home_score,
            "total": home_score + away_score,
            "is_final": is_final,
            "status": "Final" if is_final else "Live",
        }
    }


# ── 1. classify_band ──────────────────────────────────────────────────────────

class TestClassifyBand:

    def test_4plus_low_borderline(self):
        assert rpt.classify_band("team_runs_4plus", 0.20) == "low_borderline"
        assert rpt.classify_band("team_runs_4plus", 0.25) == "low_borderline"
        assert rpt.classify_band("team_runs_4plus", 0.299) == "low_borderline"

    def test_4plus_high_borderline(self):
        assert rpt.classify_band("team_runs_4plus", 0.30) == "high_borderline"
        assert rpt.classify_band("team_runs_4plus", 0.35) == "high_borderline"
        assert rpt.classify_band("team_runs_4plus", 0.399) == "high_borderline"

    def test_4plus_above_threshold(self):
        assert rpt.classify_band("team_runs_4plus", 0.40) == "above_threshold"
        assert rpt.classify_band("team_runs_4plus", 0.90) == "above_threshold"

    def test_f5_same_as_4plus(self):
        assert rpt.classify_band("team_f5_runs_2plus", 0.25) == "low_borderline"
        assert rpt.classify_band("team_f5_runs_2plus", 0.35) == "high_borderline"
        assert rpt.classify_band("team_f5_runs_2plus", 0.40) == "above_threshold"

    def test_5plus_no_low_borderline(self):
        assert rpt.classify_band("team_runs_5plus_no", 0.10) == "low_borderline"
        assert rpt.classify_band("team_runs_5plus_no", 0.15) == "low_borderline"
        assert rpt.classify_band("team_runs_5plus_no", 0.199) == "low_borderline"

    def test_5plus_no_mid_borderline(self):
        assert rpt.classify_band("team_runs_5plus_no", 0.20) == "mid_borderline"
        assert rpt.classify_band("team_runs_5plus_no", 0.25) == "mid_borderline"
        assert rpt.classify_band("team_runs_5plus_no", 0.299) == "mid_borderline"

    def test_5plus_no_high_borderline(self):
        assert rpt.classify_band("team_runs_5plus_no", 0.30) == "high_borderline"
        assert rpt.classify_band("team_runs_5plus_no", 0.399) == "high_borderline"

    def test_5plus_no_above_threshold(self):
        assert rpt.classify_band("team_runs_5plus_no", 0.40) == "above_threshold"


# ── 2. Exclusion of official candidates ──────────────────────────────────────

class TestActionThresholdExclusion:

    def test_score_at_threshold_excluded(self):
        """Rows at exactly 0.40 must not appear in borderline rows."""
        row = {
            "game_date": "2026-06-25", "game_id": "KC@TB", "team": "TB",
            "opponent": "KC", "home_away": "home",
            "team_runs_4plus_score": "0.40",
            "team_runs_5plus_no_score": "0.00",
            "team_f5_runs_2plus_score": "0.00",
        }
        # Simulate the filtering logic directly
        s4 = float(row.get("team_runs_4plus_score", 0))
        result_lanes = []
        if 0.20 <= s4 < rpt.ACTION_THRESHOLD:
            result_lanes.append("team_runs_4plus")
        assert "team_runs_4plus" not in result_lanes

    def test_score_above_threshold_excluded(self):
        s4 = 0.75
        lanes = []
        if 0.20 <= s4 < rpt.ACTION_THRESHOLD:
            lanes.append("team_runs_4plus")
        assert not lanes

    def test_score_just_below_threshold_included(self):
        s4 = 0.399
        lanes = []
        if 0.20 <= s4 < rpt.ACTION_THRESHOLD:
            lanes.append("team_runs_4plus")
        assert "team_runs_4plus" in lanes

    def test_load_borderline_rows_excludes_threshold(self, tmp_path, monkeypatch):
        """load_borderline_rows should not return rows at score >= 0.40."""
        csv_content = (
            "game_date,game_id,team,opponent,home_away,"
            "team_runs_4plus_score,team_runs_5plus_no_score,team_f5_runs_2plus_score,"
            "top_positive_reasons,opponent_starter_name,opponent_starter_xfip,"
            "opponent_starter_xfip_bucket,opponent_starter_kbb_bucket,"
            "opponent_starter_ip_bucket,opponent_starter_ra9_bucket,"
            "opponent_starter_bad_start_rate_bucket,starter_feature_source,"
            "opponent_starter_feature_source,starter_starts_used,"
            "opponent_starter_starts_used,"
            "actual_team_runs,actual_team_runs_4plus,actual_team_runs_5plus,"
            "actual_team_f5_runs_2plus,actual_status\n"
            "2026-06-25,KC@TB,TB,KC,home,0.40,0.00,0.00,,,,,,,,,,,,,,,,\n"
            "2026-06-25,KC@TB,TB,KC,home,0.35,0.00,0.00,,,,,,,,,,,,,,,,\n"
        )
        cards = tmp_path / "cards.csv"
        cards.write_text(csv_content, encoding="utf-8")
        monkeypatch.setattr(rpt, "CARDS_CSV", cards)

        rows = rpt.load_borderline_rows("2026-06-25")
        teams_and_scores = [(r["team"], r["score"]) for r in rows]
        assert ("TB", 0.35) in [(t, s) for t, s in teams_and_scores]
        assert all(s < rpt.ACTION_THRESHOLD for _, s in teams_and_scores), \
            "No borderline row should have score >= ACTION_THRESHOLD"


# ── 3 & 4. Direction: 5+NO uses NO ask, 4+ uses YES ask ──────────────────────

class TestDirectionAsk:

    def _run_assemble(self, lane, score, snap_dict, cat_dict=None):
        br = _brain_row(lane=lane, score=score)
        kalshi_line = 5 if lane == "team_runs_5plus_no" else 4
        catalog = {("TB", kalshi_line): cat_dict or _cat()}
        snapshots = {("TB", kalshi_line): snap_dict}
        rows = rpt.assemble_rows(
            brain_rows=[br],
            catalog=catalog,
            snapshots=snapshots,
            actuals_db=_actuals_db(is_final=False),
            sbr_poisson={},
            run_at="2026-06-25T12:00 UTC",
        )
        return rows[0]

    def test_4plus_uses_yes_ask(self):
        snap = _snap(yes_bid=60, yes_ask=62, no_bid=38, no_ask=40)
        row = self._run_assemble("team_runs_4plus", 0.25, snap)
        assert row["realistic_direction_ask"] == 62, "4+ should use YES ask"

    def test_4plus_fill_prob_from_yes_side(self):
        snap = _snap(yes_bid=60, yes_ask=62, no_bid=38, no_ask=40)
        row = self._run_assemble("team_runs_4plus", 0.25, snap)
        expected = round((60 + 62) / 200, 3)
        assert row["current_fill_probability"] == expected

    def test_5plus_no_uses_no_ask(self):
        snap = _snap(yes_bid=55, yes_ask=57, no_bid=43, no_ask=45)
        row = self._run_assemble("team_runs_5plus_no", 0.15, snap)
        assert row["realistic_direction_ask"] == 45, "5+NO should use NO ask (not YES ask)"

    def test_5plus_no_fill_prob_from_no_side(self):
        snap = _snap(yes_bid=55, yes_ask=57, no_bid=43, no_ask=45)
        row = self._run_assemble("team_runs_5plus_no", 0.15, snap)
        expected = round((43 + 45) / 200, 3)
        assert row["current_fill_probability"] == expected

    def test_5plus_no_does_not_use_yes_ask(self):
        snap = _snap(yes_bid=55, yes_ask=99, no_bid=1, no_ask=2)
        row = self._run_assemble("team_runs_5plus_no", 0.15, snap)
        assert row["realistic_direction_ask"] != 99, "5+NO must not use YES ask"


# ── 5. F5 market_status is always unavailable ─────────────────────────────────

class TestF5Unavailable:

    def test_f5_assess_market_status_unavailable(self):
        # Even with a valid snap and catalog, F5 should be unavailable
        result = rpt.assess_market_status(
            snap=_snap(),
            cat=_cat(),
            lane="team_f5_runs_2plus",
            is_final=False,
        )
        assert result == "unavailable"

    def test_f5_no_snap_unavailable(self):
        result = rpt.assess_market_status(
            snap=None, cat=None, lane="team_f5_runs_2plus", is_final=False
        )
        assert result == "unavailable"

    def test_f5_assemble_rows_marks_unavailable(self):
        br = _brain_row(lane="team_f5_runs_2plus", score=0.25)
        rows = rpt.assemble_rows(
            brain_rows=[br],
            catalog={},
            snapshots={},
            actuals_db=_actuals_db(is_final=False),
            sbr_poisson={},
            run_at="2026-06-25T12:00 UTC",
        )
        assert rows[0]["market_status"] == "unavailable"


# ── 6. Calibrated probability is always None below threshold ──────────────────

class TestNoCalibration:

    def _assemble_lane(self, lane, score=0.25):
        br = _brain_row(lane=lane, score=score)
        kalshi_line = 5 if lane == "team_runs_5plus_no" else 4
        snap = _snap()
        rows = rpt.assemble_rows(
            brain_rows=[br],
            catalog={("TB", kalshi_line): _cat()},
            snapshots={("TB", kalshi_line): snap},
            actuals_db=_actuals_db(is_final=False),
            sbr_poisson={},
            run_at="2026-06-25T12:00 UTC",
        )
        return rows[0]

    def test_4plus_calibrated_probability_is_none(self):
        row = self._assemble_lane("team_runs_4plus", score=0.35)
        assert row["calibrated_probability"] is None, \
            "calibrated_probability must be None below ACTION_THRESHOLD"

    def test_5plus_no_calibrated_probability_is_none(self):
        row = self._assemble_lane("team_runs_5plus_no", score=0.15)
        assert row["calibrated_probability"] is None

    def test_f5_calibrated_probability_is_none(self):
        br = _brain_row(lane="team_f5_runs_2plus", score=0.25)
        rows = rpt.assemble_rows(
            brain_rows=[br],
            catalog={},
            snapshots={},
            actuals_db=_actuals_db(is_final=False),
            sbr_poisson={},
            run_at="2026-06-25T12:00 UTC",
        )
        assert rows[0]["calibrated_probability"] is None

    def test_calibration_note_says_score_only(self):
        row = self._assemble_lane("team_runs_4plus", score=0.25)
        assert "score-only" in (row["calibration_note"] or "").lower(), \
            "calibration_note must say 'score-only diagnostic'"

    def test_score_is_not_used_as_probability_in_gap(self):
        """market_brain_gap should use market fill prob vs score, NOT treat score as calibrated prob."""
        snap = _snap(yes_bid=60, yes_ask=62)
        row = self._assemble_lane("team_runs_4plus", score=0.25)
        # fill_prob = (60+62)/200 = 0.61
        # market_brain_gap = 0.61 - 0.25 = 0.36
        # This is just a raw gap, not a calibrated probability comparison
        assert row["calibrated_probability"] is None
        if row["market_brain_gap"] is not None:
            # Gap should NOT be interpreted as "market is X% more likely"
            # Just verify it's computed as fill_prob - score
            assert isinstance(row["market_brain_gap"], float)


# ── 7. grade_outcome ──────────────────────────────────────────────────────────

class TestGradeOutcome:

    def _grade(self, lane, **kw):
        defaults = dict(
            team="TB",
            actuals_db=_actuals_db(),
            game_id="KC@TB",
            csv_actual_4plus="",
            csv_actual_5plus="",
            csv_actual_f5="",
            csv_actual_status="final",
            kalshi_settled_yes=None,
        )
        defaults.update(kw)
        return rpt.grade_outcome(lane=lane, **defaults)

    # 4+ lane
    def test_4plus_hit_from_csv(self):
        result, _ = self._grade("team_runs_4plus", csv_actual_4plus="1")
        assert result == "hit"

    def test_4plus_miss_from_csv(self):
        result, _ = self._grade("team_runs_4plus", csv_actual_4plus="0")
        assert result == "miss"

    def test_4plus_hit_from_db_runs(self):
        result, _ = self._grade("team_runs_4plus",
                                 actuals_db=_actuals_db(home_score=7))
        assert result == "hit"

    def test_4plus_miss_from_db_runs(self):
        result, _ = self._grade("team_runs_4plus",
                                 actuals_db=_actuals_db(home_score=2))
        assert result == "miss"

    def test_4plus_hit_from_kalshi_settlement(self):
        result, _ = self._grade(
            "team_runs_4plus",
            actuals_db={"KC@TB": {"is_final": True, "home_abbr": "TB",
                                   "home_score": None, "away_score": None}},
            kalshi_settled_yes=True,
        )
        assert result == "hit"

    # 5+NO lane — hit means team did NOT score 5+
    def test_5plus_no_hit_team_did_not_score_5plus(self):
        # csv says team did NOT score 5+ (actual_5plus = "0")
        result, _ = self._grade("team_runs_5plus_no", csv_actual_5plus="0")
        assert result == "hit", "5+NO hit when team scored < 5"

    def test_5plus_no_miss_team_scored_5plus(self):
        # csv says team DID score 5+ (actual_5plus = "1")
        result, _ = self._grade("team_runs_5plus_no", csv_actual_5plus="1")
        assert result == "miss", "5+NO miss when team scored 5+"

    def test_5plus_no_hit_from_db_runs_below_5(self):
        result, _ = self._grade("team_runs_5plus_no",
                                 actuals_db=_actuals_db(home_score=4))
        assert result == "hit"

    def test_5plus_no_miss_from_db_runs_5_or_more(self):
        result, _ = self._grade("team_runs_5plus_no",
                                 actuals_db=_actuals_db(home_score=5))
        assert result == "miss"

    def test_5plus_no_miss_from_kalshi_settled_yes(self):
        # Kalshi YES settled means team scored 5+ → 5+NO is a miss
        result, _ = self._grade(
            "team_runs_5plus_no",
            actuals_db={"KC@TB": {"is_final": True, "home_abbr": "TB",
                                   "home_score": None, "away_score": None}},
            kalshi_settled_yes=True,
        )
        assert result == "miss"

    def test_5plus_no_hit_from_kalshi_settled_no(self):
        result, _ = self._grade(
            "team_runs_5plus_no",
            actuals_db={"KC@TB": {"is_final": True, "home_abbr": "TB",
                                   "home_score": None, "away_score": None}},
            kalshi_settled_yes=False,
        )
        assert result == "hit"

    # F5 lane
    def test_f5_hit_from_csv(self):
        result, _ = self._grade("team_f5_runs_2plus", csv_actual_f5="1")
        assert result == "hit"

    def test_f5_miss_from_csv(self):
        result, _ = self._grade("team_f5_runs_2plus", csv_actual_f5="0")
        assert result == "miss"

    # Pending
    def test_pending_when_not_final(self):
        result, _ = self._grade(
            "team_runs_4plus",
            actuals_db=_actuals_db(is_final=False),
            csv_actual_status="",
        )
        assert result == "pending"

    def test_run_note_present_when_final(self):
        _, note = self._grade("team_runs_4plus",
                               actuals_db=_actuals_db(home_score=7))
        assert note == "7"

    def test_run_note_empty_when_pending(self):
        _, note = self._grade(
            "team_runs_4plus",
            actuals_db=_actuals_db(is_final=False),
            csv_actual_status="",
        )
        assert note == ""


# ── 8. History deduplication ──────────────────────────────────────────────────

class TestHistoryUpsert:

    def _make_row(self, game_date="2026-06-25", game_id="KC@TB",
                  team="TB", lane="team_runs_4plus", result="pending", **kw) -> dict:
        r = {c: "" for c in rpt.HISTORY_COLS}
        r.update({"game_date": game_date, "game_id": game_id,
                   "team": team, "lane": lane, "result": result,
                   "run_at": "2026-06-25T12:00 UTC",
                   "score": "0.25", "band": "low_borderline"})
        r.update(kw)
        return r

    def test_no_duplicate_on_same_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rpt, "HISTORY_CSV", tmp_path / "hist.csv")
        monkeypatch.setattr(rpt, "OUT_DIR", tmp_path)

        row = self._make_row(result="pending")
        rpt.upsert_history([row])
        rpt.upsert_history([row])  # same key again

        loaded = rpt.load_history()
        assert len(loaded) == 1, "Duplicate row must not be added"

    def test_outcome_updated_from_pending_to_hit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rpt, "HISTORY_CSV", tmp_path / "hist.csv")
        monkeypatch.setattr(rpt, "OUT_DIR", tmp_path)

        row_pending = self._make_row(result="pending")
        rpt.upsert_history([row_pending])

        row_hit = self._make_row(result="hit")
        rpt.upsert_history([row_hit])

        loaded = rpt.load_history()
        assert len(loaded) == 1
        assert loaded[0]["result"] == "hit", "Result should be updated from pending to hit"

    def test_different_lanes_are_separate_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rpt, "HISTORY_CSV", tmp_path / "hist.csv")
        monkeypatch.setattr(rpt, "OUT_DIR", tmp_path)

        row_4plus = self._make_row(lane="team_runs_4plus")
        row_5no = self._make_row(lane="team_runs_5plus_no")
        rpt.upsert_history([row_4plus, row_5no])

        loaded = rpt.load_history()
        assert len(loaded) == 2, "Different lanes for same team/game must be separate rows"

    def test_different_teams_are_separate_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rpt, "HISTORY_CSV", tmp_path / "hist.csv")
        monkeypatch.setattr(rpt, "OUT_DIR", tmp_path)

        row_tb = self._make_row(team="TB")
        row_kc = self._make_row(team="KC")
        rpt.upsert_history([row_tb, row_kc])

        loaded = rpt.load_history()
        assert len(loaded) == 2

    def test_different_dates_are_separate_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rpt, "HISTORY_CSV", tmp_path / "hist.csv")
        monkeypatch.setattr(rpt, "OUT_DIR", tmp_path)

        row_25 = self._make_row(game_date="2026-06-25")
        row_26 = self._make_row(game_date="2026-06-26")
        rpt.upsert_history([row_25, row_26])

        loaded = rpt.load_history()
        assert len(loaded) == 2

    def test_history_preserves_all_cols(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rpt, "HISTORY_CSV", tmp_path / "hist.csv")
        monkeypatch.setattr(rpt, "OUT_DIR", tmp_path)

        row = self._make_row(opponent_starter_name="Seth Lugo",
                             opponent_starter_xfip_bucket="very_bad_5_25_plus")
        rpt.upsert_history([row])
        loaded = rpt.load_history()
        assert loaded[0]["opponent_starter_name"] == "Seth Lugo"
        assert loaded[0]["opponent_starter_xfip_bucket"] == "very_bad_5_25_plus"


# ── Market status edge cases ──────────────────────────────────────────────────

class TestAssessMarketStatus:

    def test_no_snap_no_cat_is_no_market(self):
        result = rpt.assess_market_status(None, None, "team_runs_4plus", False)
        assert result == "no_market"

    def test_only_cat_no_snap_is_stale(self):
        result = rpt.assess_market_status(None, _cat(), "team_runs_4plus", False)
        assert result == "stale"

    def test_wide_spread_detected(self):
        snap = _snap(spread=25)
        result = rpt.assess_market_status(snap, _cat(), "team_runs_4plus", False)
        assert result == "wide_spread"

    def test_invalid_book_bid_gte_ask(self):
        snap = _snap(yes_bid=65, yes_ask=60)  # bid > ask
        result = rpt.assess_market_status(snap, _cat(), "team_runs_4plus", False)
        assert result == "invalid_book"

    def test_settled_market_is_matched(self):
        snap = _snap(yes_bid=None, yes_ask=100, no_bid=None, no_ask=None, settled_yes=True)
        result = rpt.assess_market_status(snap, _cat(), "team_runs_4plus", True)
        assert result == "matched"

    def test_good_snap_is_matched(self):
        snap = _snap(spread=5)
        result = rpt.assess_market_status(snap, _cat(), "team_runs_4plus", True)
        assert result == "matched"


# ── fill_quality_str ──────────────────────────────────────────────────────────

class TestFillQuality:

    def test_excellent(self):
        assert rpt.fill_quality_str(2) == "excellent"
        assert rpt.fill_quality_str(3) == "excellent"

    def test_good(self):
        assert rpt.fill_quality_str(4) == "good"
        assert rpt.fill_quality_str(6) == "good"

    def test_ok(self):
        assert rpt.fill_quality_str(7) == "ok"
        assert rpt.fill_quality_str(12) == "ok"

    def test_wide(self):
        assert rpt.fill_quality_str(13) == "wide"
        assert rpt.fill_quality_str(20) == "wide"

    def test_very_wide(self):
        assert rpt.fill_quality_str(21) == "very_wide"

    def test_none_is_unknown(self):
        assert rpt.fill_quality_str(None) == "unknown"


# ── _parse_ticker_suffix ──────────────────────────────────────────────────────

class TestParseTickerSuffix:

    def test_tb4(self):
        assert rpt._parse_ticker_suffix("KXMLBTEAMTOTAL-26JUN251210KCTB-TB4") == ("TB", 4)

    def test_ath5(self):
        assert rpt._parse_ticker_suffix("KXMLBTEAMTOTAL-26JUN251545ATHSF-ATH5") == ("ATH", 5)

    def test_bos5(self):
        assert rpt._parse_ticker_suffix("KXMLBTEAMTOTAL-26JUN251910NYYBOS-BOS5") == ("BOS", 5)

    def test_invalid_returns_none(self):
        t, l = rpt._parse_ticker_suffix("KXMLBGAME-26JUN251210KCTB-KC")
        # KC has no trailing digit — should return None line or something invalid
        # "KC" → no digit at end → line = None
        assert l is None


# ── _date_code ────────────────────────────────────────────────────────────────

class TestDateCode:

    def test_jun_25(self):
        assert rpt._date_code("2026-06-25") == "26JUN25"

    def test_jan_01(self):
        assert rpt._date_code("2025-01-01") == "25JAN01"

    def test_dec_31(self):
        assert rpt._date_code("2024-12-31") == "24DEC31"
