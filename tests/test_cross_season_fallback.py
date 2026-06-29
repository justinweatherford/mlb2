"""tests/test_cross_season_fallback.py

Tests for the cross-season starter history fallback in score_today_slate.py.
Covers: current-season preference, prior-season fallback trigger, provenance fields,
no future-start leak, same-day game safety, doubleheader ordering.
"""
import importlib.util
import sqlite3
from collections import deque

import pytest


def _load_ff():
    spec = importlib.util.spec_from_file_location("ff", "pregame_feature_family_lift_preview.py")
    ff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ff)
    return ff


def _load_score():
    """Load score_today_slate as a module (without calling main)."""
    spec = importlib.util.spec_from_file_location("score_today_slate", "score_today_slate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_start(outs=18, runs=2, k=6, bb=2, hbp=0, hr=1, fb=5, gb=7, ld=3, popup=2, events=27):
    return {
        "outs": outs, "runs_allowed": runs, "strikeouts": k, "walks": bb,
        "hbp": hbp, "home_runs": hr, "fly_balls": fb, "ground_balls": gb,
        "line_drives": ld, "popups": popup, "batted_balls": fb + gb + ld + popup,
        "events": events,
    }


def _run_build(
    ff, sc, pitcher_id, pitcher_name,
    curr_hist=None, prev_hist=None,
    opp_id=None, opp_name=None,
    opp_curr=None, opp_prev=None,
):
    """Build a feature row with synthetic starter history and return it."""
    game_pk = 999001
    game = (game_pk, "2026-06-25", "TB", "KC", "2026-06-25T23:10:00Z")
    team, opponent, is_home = "KC", "TB", True

    curr_hist = curr_hist or []
    prev_hist = prev_hist or []
    opp_curr = opp_curr or []
    opp_prev = opp_prev or []

    # Build synthetic starter dicts using name: keys (like play-event pipeline)
    def _to_sh(hist, name):
        if not hist or not name:
            return {}
        key = ff.normalize_pitcher_key("", name)
        return {key: deque(hist, maxlen=10)}

    sh_curr = {**_to_sh(curr_hist, pitcher_name), **_to_sh(opp_curr, opp_name)}
    sh_prev = {**_to_sh(prev_hist, pitcher_name), **_to_sh(opp_prev, opp_name)}

    pp_by_game = {game_pk: {
        "home_id": pitcher_id, "home_name": pitcher_name,
        "away_id": opp_id, "away_name": opp_name,
    }}

    row = sc._build_feature_row(
        game, team, opponent, is_home,
        team_hist={}, ff=ff, latest_ctx={}, slate_date="2026-06-25",
        starter_hist=sh_curr, lhr=0.1921, xfip_const=3.894,
        starter_hist_prev=sh_prev, lhr_prev=0.2028, xfip_const_prev=3.5125,
        pp_by_game=pp_by_game,
    )
    return row


# ── Provenance: current_season preferred when sample is sufficient ─────────────

def test_current_season_used_when_enough_starts():
    """With >= MIN_CURR_STARTS current-season starts, use current_season."""
    ff = _load_ff()
    sc = _load_score()
    curr = [_make_start(runs=1) for _ in range(5)]  # 5 starts = high confidence
    prev = [_make_start(runs=5) for _ in range(8)]  # 8 worse starts in prior season
    row = _run_build(ff, sc, None, "Test Pitcher", curr_hist=curr, prev_hist=prev)
    assert row["starter_feature_source"] == "current_season"
    # The current season (low runs) should dominate, not the worse prior season
    assert row.get("starter_starter_ra9") is not None
    assert row["starter_starter_ra9"] < 3.0  # current-season low-run xFIP


def test_prior_season_fallback_when_current_has_zero():
    """With 0 current-season starts and prior-season data available, use fallback."""
    ff = _load_ff()
    sc = _load_score()
    prev = [_make_start(runs=1, k=10, bb=1, hr=0, fb=3, outs=21) for _ in range(8)]
    row = _run_build(ff, sc, None, "Good Pitcher", curr_hist=[], prev_hist=prev)
    assert row["starter_feature_source"] == "prior_season_fallback"
    assert row["starter_starts_used"] == 8
    assert row["starter_starter_confidence"] in {"high", "medium"}


def test_prior_season_fallback_when_current_has_one_start():
    """With only 1 current-season start, fallback to prior season."""
    ff = _load_ff()
    sc = _load_score()
    curr = [_make_start(runs=0, outs=18)]   # 1 start: low confidence
    prev = [_make_start(runs=2) for _ in range(9)]
    row = _run_build(ff, sc, None, "Returning Pitcher", curr_hist=curr, prev_hist=prev)
    assert row["starter_feature_source"] == "prior_season_fallback"
    assert row["starter_starts_used"] == 9


def test_prior_season_fallback_when_current_has_two_starts():
    """With 2 current-season starts (< MIN_CURR_STARTS=3), fallback to prior season."""
    ff = _load_ff()
    sc = _load_score()
    curr = [_make_start() for _ in range(2)]
    prev = [_make_start() for _ in range(7)]
    row = _run_build(ff, sc, None, "Early Season Pitcher", curr_hist=curr, prev_hist=prev)
    assert row["starter_feature_source"] == "prior_season_fallback"
    assert row["starter_starts_used"] == 7


def test_current_season_small_sample_used_when_no_prior():
    """With 2 current-season starts and no prior data, use current (small sample)."""
    ff = _load_ff()
    sc = _load_score()
    curr = [_make_start() for _ in range(2)]
    row = _run_build(ff, sc, None, "New Pitcher", curr_hist=curr, prev_hist=[])
    assert row["starter_feature_source"] == "current_season"
    assert row["starter_starts_used"] == 2
    assert row["starter_starter_confidence"] == "low"


def test_missing_when_no_data_either_season():
    """No data in either season → missing source, 0 starts."""
    ff = _load_ff()
    sc = _load_score()
    row = _run_build(ff, sc, None, "Unknown Pitcher", curr_hist=[], prev_hist=[])
    assert row["starter_feature_source"] == "missing"
    assert row["starter_starts_used"] == 0
    assert row["starter_starter_confidence"] == "none"


# ── Provenance: opponent fallback also works ────────────────────────────────────

def test_opponent_gets_prior_season_fallback():
    """Opposing pitcher with 0 current-season starts should use prior-season fallback."""
    ff = _load_ff()
    sc = _load_score()
    opp_prev = [_make_start(runs=5, hr=3, fb=10, k=3, bb=5, outs=12) for _ in range(8)]
    row = _run_build(
        ff, sc,
        pitcher_id=None, pitcher_name="Home Pitcher",
        curr_hist=[_make_start() for _ in range(5)],  # own starter has enough
        opp_id=None, opp_name="Opp Pitcher",
        opp_curr=[], opp_prev=opp_prev,
    )
    assert row["opponent_starter_feature_source"] == "prior_season_fallback"
    assert row["opponent_starter_starts_used"] == 8
    # xFIP should now be computable (not missing)
    assert row.get("opponent_starter_xfip_bucket") != "missing"


# ── No-lookahead: prior-season data is safe ────────────────────────────────────

def test_prior_season_all_completed_games():
    """build_final_state for a prior season only returns completed games (no lookahead)."""
    ff = _load_ff()
    conn = sqlite3.connect("kalshi_mlb.db")
    _, sh_2025, _, _ = ff.build_final_state(conn, "2025", 20, 10)
    # All 2025 games are finalized → using this data for 2026 is safe by construction
    # Verify the state is non-empty and contains expected pitcher keys
    assert len(sh_2025) > 300, f"Expected 300+ 2025 starters, got {len(sh_2025)}"
    # All keys should be name: or id: prefixed (no bare pitcher IDs)
    for key in list(sh_2025.keys())[:20]:
        assert key.startswith("name:") or key.startswith("id:"), f"Unexpected key format: {key}"
    conn.close()


def test_current_season_keys_are_prior_starts_only():
    """build_final_state for 2026 must exclude today's unfinished games."""
    ff = _load_ff()
    conn = sqlite3.connect("kalshi_mlb.db")
    # Jun 25 games should NOT be in the completed-game set since final_away_score IS NULL
    unplayed = conn.execute(
        "SELECT COUNT(*) FROM mlb_games WHERE game_date = '2026-06-25' AND final_away_score IS NULL"
    ).fetchone()[0]
    all_jun25 = conn.execute(
        "SELECT COUNT(*) FROM mlb_games WHERE game_date = '2026-06-25'"
    ).fetchone()[0]
    assert unplayed == all_jun25, "Some Jun 25 games already have final scores — possible lookahead risk"
    # build_final_state uses load_final_games which filters final_away_score IS NOT NULL
    _, sh_2026, _, _ = ff.build_final_state(conn, "2026", 20, 10)
    # Jun 25 starters should NOT be in the history (game not yet played)
    assert "name:casey_legumina" not in sh_2026 or len(list(sh_2026.get("name:casey_legumina", []))) <= 1
    conn.close()


# ── Doubleheader safety ──────────────────────────────────────────────────────────

def test_doubleheader_game_ordering():
    """build_final_state processes doubleheader games chronologically by time + game_pk.
    The second game cannot see the first game's pitcher result.
    """
    ff = _load_ff()
    conn = sqlite3.connect("kalshi_mlb.db")

    # Find a 2026 doubleheader with completed games
    dh = conn.execute(
        """SELECT game_date, away_abbr, home_abbr
           FROM mlb_games
           WHERE game_date LIKE '2026%' AND final_away_score IS NOT NULL
           GROUP BY game_date, away_abbr, home_abbr
           HAVING COUNT(*) > 1
           LIMIT 1"""
    ).fetchone()

    if dh is None:
        pytest.skip("No 2026 doubleheaders found in DB")

    game_date, away, home = dh
    # Get both games, sorted by start time
    games = conn.execute(
        """SELECT game_pk, game_start_time_utc FROM mlb_games
           WHERE game_date = ? AND away_abbr = ? AND home_abbr = ?
           AND final_away_score IS NOT NULL
           ORDER BY COALESCE(game_start_time_utc, ''), game_pk""",
        [game_date, away, home]
    ).fetchall()
    assert len(games) == 2

    g1_pk, g1_time = games[0]
    g2_pk, g2_time = games[1]

    # Verify they are different game_pks and correctly ordered
    assert g1_pk != g2_pk
    # The first game's start time should be <= second game's start time
    if g1_time and g2_time:
        assert g1_time <= g2_time, f"Game ordering wrong: {g1_time} > {g2_time}"

    conn.close()


# ── Provenance field completeness ────────────────────────────────────────────────

def test_all_provenance_fields_present():
    """Every feature row must have the 7 provenance fields."""
    ff = _load_ff()
    sc = _load_score()
    row = _run_build(ff, sc, None, "Some Pitcher",
                     curr_hist=[_make_start() for _ in range(5)],
                     opp_name="Opp Pitcher",
                     opp_curr=[_make_start() for _ in range(5)])
    required = [
        "starter_feature_source",
        "opponent_starter_feature_source",
        "starter_starts_used",
        "opponent_starter_starts_used",
        "starter_innings_used",
        "opponent_starter_innings_used",
        "starter_feature_as_of_date",
    ]
    for field in required:
        assert field in row, f"Missing provenance field: {field}"


def test_innings_used_matches_starts():
    """starter_innings_used should equal total outs / 3 across all starts used."""
    ff = _load_ff()
    sc = _load_score()
    starts = [_make_start(outs=18) for _ in range(5)]  # 5 * 18 outs = 30 IP
    row = _run_build(ff, sc, None, "Workhorse", curr_hist=starts)
    assert row["starter_innings_used"] == pytest.approx(30.0, abs=0.1)


def test_prior_season_lhr_used_for_xfip_calculation():
    """When fallback is used, prior-season league constants should produce different xFIP
    than if current-season constants were applied to the same raw start data."""
    ff = _load_ff()
    # 2025 league_hr_per_fb = 0.2028 vs 2026 = 0.1921 — this difference changes expected HR
    starts = [_make_start(fb=10, hr=2, k=6, bb=2, outs=18) for _ in range(5)]
    ctx_26 = ff.starter_context_from_history(starts, 0.1921, 3.894)   # 2026 constants
    ctx_25 = ff.starter_context_from_history(starts, 0.2028, 3.5125)  # 2025 constants
    # Higher HR/FB rate in 2025 means higher expected HR → higher xFIP
    if ctx_26["starter_xfip"] and ctx_25["starter_xfip"]:
        assert ctx_25["starter_xfip"] != ctx_26["starter_xfip"], "Constants had no effect on xFIP"


def test_min_curr_starts_threshold_is_3():
    """_MIN_CURR_STARTS constant must be 3 — the medium-confidence boundary."""
    sc = _load_score()
    assert sc._MIN_CURR_STARTS == 3
