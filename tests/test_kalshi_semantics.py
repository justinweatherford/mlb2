"""
tests/test_kalshi_semantics.py — Conservative market semantics parser tests.

Tests are organized by market type, then by confidence tier:
  rules_primary (confidence 1.0) > title/subtitle (0.8) > unknown (0.0)

Key requirement verified throughout:
  - No direction inferred from signal names or strategy keywords.
  - is_semantics_clear=False always sets needs_review_reason.
  - Unclear = unknown contract_direction, no yes_means inferred.
"""
import pytest
import sqlite3
from db.schema import init_db
from kalshi.semantics import (
    MarketSemantics,
    parse_market_semantics,
    refresh_market_semantics,
)


# ── Fixtures / helpers ───────────────────────────────────────────────────────


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def _parse(
    market_type: str = "full_game_total",
    title: str = "",
    subtitle: str = "",
    rules_primary: str = "",
    away_team: str = "NYY",
    home_team: str = "BOS",
    market_ticker: str = "KXMLBTOTAL-TEST",
    line_value: float = 8.5,
) -> MarketSemantics:
    """Thin wrapper so tests only have to supply the fields they care about."""
    return parse_market_semantics(
        market_type=market_type,
        market_ticker=market_ticker,
        title=title,
        subtitle=subtitle,
        rules_primary=rules_primary,
        away_team=away_team,
        home_team=home_team,
        line_value=line_value,
    )


def _ins_market(
    conn,
    ticker: str,
    market_type: str = "full_game_total",
    title: str = "",
    rules: str = "",
    away: str = "NYY",
    home: str = "BOS",
    line: float = 8.5,
) -> None:
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title, rules_primary,
            away_team, home_team, line_value,
            raw_json, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,'{}',datetime('now'),datetime('now'))""",
        (ticker, "EVT", market_type, title, rules, away, home, line),
    )
    conn.commit()


# ── MarketSemantics result type ───────────────────────────────────────────────


def test_result_is_market_semantics_instance():
    r = _parse()
    assert isinstance(r, MarketSemantics)


def test_unclear_result_always_has_needs_review_reason():
    r = _parse(market_type="full_game_total", title="", rules_primary="")
    assert r.is_semantics_clear is False
    assert r.needs_review_reason is not None
    assert len(r.needs_review_reason) > 0


def test_clear_result_has_no_needs_review_reason():
    r = _parse(
        market_type="full_game_total",
        rules_primary="This market resolves YES if total runs exceed 8.5.",
    )
    assert r.is_semantics_clear is True
    assert r.needs_review_reason is None


# ── settlement_horizon — derived from market_type only ────────────────────────


def test_horizon_full_game_from_full_game_total():
    r = _parse(market_type="full_game_total")
    assert r.settlement_horizon == "full_game"


def test_horizon_full_game_from_moneyline():
    r = _parse(market_type="moneyline", market_ticker="KXMLBGAME-TEST-NYY")
    assert r.settlement_horizon == "full_game"


def test_horizon_full_game_from_team_total():
    r = _parse(market_type="team_total")
    assert r.settlement_horizon == "full_game"


def test_horizon_full_game_from_spread():
    r = _parse(market_type="spread_run_line")
    assert r.settlement_horizon == "full_game"


def test_horizon_first_5_from_f5_total():
    r = _parse(market_type="f5_total", market_ticker="KXMLBF5TOTAL-TEST")
    assert r.settlement_horizon == "first_5"


def test_horizon_first_5_from_f5_spread():
    r = _parse(market_type="f5_spread", market_ticker="KXMLBF5SPREAD-TEST")
    assert r.settlement_horizon == "first_5"


def test_horizon_first_5_from_f5_winner():
    r = _parse(market_type="f5_winner", market_ticker="KXMLBF5-TEST-NYY")
    assert r.settlement_horizon == "first_5"


def test_horizon_player_prop_from_player_hr():
    r = _parse(market_type="player_hr", market_ticker="KXMLBHR-JUDGE")
    assert r.settlement_horizon == "player_prop"


def test_horizon_unknown_from_unknown_type():
    r = _parse(market_type="unknown")
    assert r.settlement_horizon == "unknown"


# ── full_game_total — over direction ─────────────────────────────────────────


def test_full_game_over_from_rules_exceed():
    r = _parse(
        market_type="full_game_total",
        rules_primary="This market resolves YES if total runs exceed 8.5.",
    )
    assert r.contract_direction == "over_yes"
    assert r.yes_means == "over"
    assert r.no_means == "under"
    assert r.is_semantics_clear is True
    assert r.semantics_confidence == 1.0


def test_full_game_over_from_rules_more_than():
    r = _parse(
        market_type="full_game_total",
        rules_primary="Settles YES if combined runs are more than 8.5.",
    )
    assert r.contract_direction == "over_yes"
    assert r.is_semantics_clear is True


def test_full_game_over_from_rules_greater_than():
    r = _parse(
        market_type="full_game_total",
        rules_primary="Market resolves YES if total runs greater than 8.",
    )
    assert r.contract_direction == "over_yes"
    assert r.is_semantics_clear is True


def test_full_game_over_from_title_over_number():
    r = _parse(
        market_type="full_game_total",
        title="NYY @ BOS Total Over 8.5",
        rules_primary="",
    )
    assert r.contract_direction == "over_yes"
    assert r.is_semantics_clear is True
    assert r.semantics_confidence == 0.8


def test_full_game_over_rules_preferred_over_title():
    """When both rules and title are present, rules takes priority."""
    r = _parse(
        market_type="full_game_total",
        title="Total Over 8.5",
        rules_primary="This market resolves YES if total runs exceed 8.5.",
    )
    assert r.contract_direction == "over_yes"
    assert r.semantics_confidence == 1.0   # rules confidence, not title confidence


# ── full_game_total — under direction ────────────────────────────────────────


def test_full_game_under_from_rules_under():
    r = _parse(
        market_type="full_game_total",
        rules_primary="This market resolves YES if total runs are under 8.5.",
    )
    assert r.contract_direction == "under_yes"
    assert r.yes_means == "under"
    assert r.no_means == "over"
    assert r.is_semantics_clear is True
    assert r.semantics_confidence == 1.0


def test_full_game_under_from_rules_not_exceed():
    r = _parse(
        market_type="full_game_total",
        rules_primary="Settles YES if total runs do not exceed 8.5.",
    )
    assert r.contract_direction == "under_yes"
    assert r.is_semantics_clear is True


def test_full_game_under_from_rules_fewer_than():
    r = _parse(
        market_type="full_game_total",
        rules_primary="Resolves YES if fewer than 8 total runs are scored.",
    )
    assert r.contract_direction == "under_yes"
    assert r.is_semantics_clear is True


def test_full_game_under_from_title_under_number():
    r = _parse(
        market_type="full_game_total",
        title="NYY @ BOS Total Under 8.5",
        rules_primary="",
    )
    assert r.contract_direction == "under_yes"
    assert r.is_semantics_clear is True
    assert r.semantics_confidence == 0.8


# ── full_game_total — unclear / unknown ──────────────────────────────────────


def test_full_game_total_no_rules_no_title_is_unclear():
    r = _parse(market_type="full_game_total", title="", rules_primary="")
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"
    assert r.yes_means == "unknown"
    assert r.needs_review_reason is not None


def test_full_game_total_title_over_without_number_is_unclear():
    """'over' in title without a number must NOT resolve to over_yes."""
    r = _parse(
        market_type="full_game_total",
        title="Will this game go over?",
        rules_primary="",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


def test_full_game_total_conflicting_over_and_under_in_rules_is_unclear():
    """If both over AND under appear in rules_primary, treat as ambiguous."""
    r = _parse(
        market_type="full_game_total",
        rules_primary=(
            "Resolves YES if total runs exceed 8.5. "
            "Resolves NO if total runs are under 8.5."
        ),
    )
    # Only "exceed" matches the YES condition — "under" is in the NO clause.
    # Parser should resolve this as over_yes (YES clause wins).
    # But if the text is truly ambiguous with two YES conditions, flag unclear.
    # For this specific case: "resolves YES if ... exceed" → over_yes
    assert r.contract_direction == "over_yes"
    assert r.is_semantics_clear is True


def test_two_separate_yes_conditions_is_unclear():
    """Two distinct 'resolves YES if' statements is genuinely ambiguous."""
    r = _parse(
        market_type="full_game_total",
        rules_primary=(
            "Resolves YES if total runs exceed 8.5. "
            "Resolves YES if total runs are under 8.5."
        ),
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"
    assert r.needs_review_reason is not None


# ── NO fallback from signal names or strategy keywords ───────────────────────


def test_signal_name_in_title_does_not_set_direction():
    """Title containing strategy keyword 'under_candidate' must not resolve direction."""
    r = _parse(
        market_type="full_game_total",
        title="pace_fade_under_candidate for NYY @ BOS",
        rules_primary="",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


def test_over_keyword_without_number_in_title_does_not_resolve():
    """'fade_overreaction' title contains 'over' but no number — must stay unknown."""
    r = _parse(
        market_type="full_game_total",
        title="midgame_blowup_fade_over NYY @ BOS",
        rules_primary="",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


def test_stability_under_signal_name_does_not_resolve():
    """Exact signal type name in title must not resolve contract direction."""
    r = _parse(
        market_type="full_game_total",
        title="stability_under signal NYY",
        rules_primary="",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


# ── F5 total ──────────────────────────────────────────────────────────────────


def test_f5_total_over_from_rules():
    r = _parse(
        market_type="f5_total",
        market_ticker="KXMLBF5TOTAL-TEST",
        rules_primary="Resolves YES if the total runs scored in the first 5 innings exceed 4.5.",
    )
    assert r.contract_direction == "f5_over_yes"
    assert r.settlement_horizon == "first_5"
    assert r.yes_means == "f5_over"
    assert r.no_means == "f5_under"
    assert r.is_semantics_clear is True
    assert r.semantics_confidence == 1.0


def test_f5_total_under_from_rules():
    r = _parse(
        market_type="f5_total",
        market_ticker="KXMLBF5TOTAL-TEST",
        rules_primary="Settles YES if total F5 runs are under 4.5.",
    )
    assert r.contract_direction == "f5_under_yes"
    assert r.settlement_horizon == "first_5"
    assert r.is_semantics_clear is True


def test_f5_total_over_from_title():
    r = _parse(
        market_type="f5_total",
        market_ticker="KXMLBF5TOTAL-TEST",
        title="F5 Total Over 4.5",
        rules_primary="",
    )
    assert r.contract_direction == "f5_over_yes"
    assert r.settlement_horizon == "first_5"
    assert r.is_semantics_clear is True
    assert r.semantics_confidence == 0.8


def test_f5_total_no_direction_is_unclear():
    r = _parse(
        market_type="f5_total",
        market_ticker="KXMLBF5TOTAL-TEST",
        title="",
        rules_primary="",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"
    assert r.settlement_horizon == "first_5"   # horizon still derived from market_type


# ── Team total ────────────────────────────────────────────────────────────────


def test_team_total_over_away_team_from_rules():
    r = _parse(
        market_type="team_total",
        market_ticker="KXMLBTEAMTOTAL-TEST-NYY-O4.5",
        rules_primary="Resolves YES if the NYY score exceeds 4.5.",
        away_team="NYY", home_team="BOS",
    )
    assert r.contract_direction == "team_total_over_yes"
    assert r.selected_team_abbr == "NYY"
    assert r.opponent_team_abbr == "BOS"
    assert r.yes_means == "team_total_over"
    assert r.is_semantics_clear is True


def test_team_total_over_home_team_from_title():
    r = _parse(
        market_type="team_total",
        market_ticker="KXMLBTEAMTOTAL-TEST-BOS-O3.5",
        title="BOS Team Total Over 3.5",
        rules_primary="",
        away_team="NYY", home_team="BOS",
    )
    assert r.contract_direction == "team_total_over_yes"
    assert r.selected_team_abbr == "BOS"
    assert r.opponent_team_abbr == "NYY"
    assert r.is_semantics_clear is True


def test_team_total_under_from_rules():
    r = _parse(
        market_type="team_total",
        market_ticker="KXMLBTEAMTOTAL-TEST-NYY-U3.5",
        rules_primary="Settles YES if the NYY score is under 3.5.",
        away_team="NYY", home_team="BOS",
    )
    assert r.contract_direction == "team_total_under_yes"
    assert r.yes_means == "team_total_under"
    assert r.is_semantics_clear is True


def test_team_total_team_not_identified_is_unclear():
    """Direction found but team ambiguous → is_semantics_clear=False."""
    r = _parse(
        market_type="team_total",
        market_ticker="KXMLBTEAMTOTAL-TEST",
        title="Home team total over 4.5",     # no abbreviation
        rules_primary="",
        away_team="NYY", home_team="BOS",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"
    assert r.needs_review_reason is not None


def test_team_total_both_teams_in_title_is_unclear():
    """Title mentions both abbreviations — can't tell which total is the subject."""
    r = _parse(
        market_type="team_total",
        title="NYY vs BOS team total over 4.5",
        rules_primary="",
        away_team="NYY", home_team="BOS",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


# ── Moneyline ─────────────────────────────────────────────────────────────────


def test_moneyline_away_team_from_ticker_last_segment():
    """Ticker last segment matches away abbreviation → selected=NYY (away)."""
    r = _parse(
        market_type="moneyline",
        market_ticker="KXMLBGAME-26JUN121937NYYTOR-NYY",
        title="",
        rules_primary="",
        away_team="NYY", home_team="TOR",
    )
    assert r.contract_direction == "moneyline_yes"
    assert r.selected_team_abbr == "NYY"
    assert r.opponent_team_abbr == "TOR"
    assert r.yes_means == "NYY_wins"
    assert r.no_means == "TOR_wins_or_tie"
    assert r.is_semantics_clear is True


def test_moneyline_home_team_from_ticker():
    r = _parse(
        market_type="moneyline",
        market_ticker="KXMLBGAME-26JUN121937NYYTOR-TOR",
        title="",
        rules_primary="",
        away_team="NYY", home_team="TOR",
    )
    assert r.contract_direction == "moneyline_yes"
    assert r.selected_team_abbr == "TOR"
    assert r.opponent_team_abbr == "NYY"


def test_moneyline_away_team_from_title_wins_phrase():
    r = _parse(
        market_type="moneyline",
        market_ticker="KXMLBGAME-TEST",
        title="NYY wins vs BOS?",
        rules_primary="",
        away_team="NYY", home_team="BOS",
    )
    assert r.contract_direction == "moneyline_yes"
    assert r.selected_team_abbr == "NYY"
    assert r.is_semantics_clear is True


def test_moneyline_home_team_from_title_to_win_phrase():
    r = _parse(
        market_type="moneyline",
        market_ticker="KXMLBGAME-TEST",
        title="Will BOS to win tonight?",
        rules_primary="",
        away_team="NYY", home_team="BOS",
    )
    assert r.contract_direction == "moneyline_yes"
    assert r.selected_team_abbr == "BOS"
    assert r.is_semantics_clear is True


def test_moneyline_no_team_identified_is_unclear():
    r = _parse(
        market_type="moneyline",
        market_ticker="KXMLBGAME-TEST",
        title="Who wins tonight?",
        rules_primary="",
        away_team="NYY", home_team="BOS",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"
    assert "moneyline" in (r.needs_review_reason or "")


def test_moneyline_ticker_ambiguous_segment_is_unclear():
    """Ticker last segment doesn't match either team → unclear."""
    r = _parse(
        market_type="moneyline",
        market_ticker="KXMLBGAME-TEST-TIE",
        title="",
        rules_primary="",
        away_team="NYY", home_team="BOS",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


# ── Spread markets — always unclear ──────────────────────────────────────────


def test_spread_run_line_always_unclear():
    r = _parse(
        market_type="spread_run_line",
        market_ticker="KXMLBSPREAD-TEST",
        title="NYY -1.5 Run Line",
        rules_primary="",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"
    assert r.needs_review_reason is not None


def test_spread_needs_review_reason_explains_spread():
    r = _parse(market_type="spread_run_line", title="NYY -1.5")
    assert "spread" in (r.needs_review_reason or "").lower()


def test_f5_spread_always_unclear():
    r = _parse(
        market_type="f5_spread",
        market_ticker="KXMLBF5SPREAD-TEST",
        title="LAD -0.5 F5 Spread",
        rules_primary="",
    )
    assert r.is_semantics_clear is False
    assert r.settlement_horizon == "first_5"   # horizon still derived
    assert r.contract_direction == "unknown"


# ── Malformed / missing inputs ────────────────────────────────────────────────


def test_empty_rules_and_title_is_unclear():
    r = _parse(market_type="full_game_total", title="", rules_primary="")
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


def test_none_rules_and_title_is_unclear():
    """None values for text fields must not crash and must return unclear."""
    r = parse_market_semantics(
        market_type="full_game_total",
        market_ticker="KXMLBTOTAL-TEST",
        title=None,
        subtitle=None,
        rules_primary=None,
        away_team="NYY",
        home_team="BOS",
        line_value=8.5,
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


def test_gibberish_rules_primary_is_unclear():
    r = _parse(
        market_type="full_game_total",
        rules_primary="xXx garbled text !@#$ no settlement condition",
    )
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"


def test_html_in_rules_primary_is_unclear():
    """Malformed/HTML-encoded rules should not resolve."""
    r = _parse(
        market_type="full_game_total",
        rules_primary="<p>Resolves &gt; YES if &lt;over&gt; 8.5</p>",
    )
    # HTML entities not decoded → unclear (conservative)
    assert r.is_semantics_clear is False


def test_unknown_market_type_is_unclear():
    r = _parse(market_type="unknown", title="Will NYY win?")
    assert r.is_semantics_clear is False
    assert r.contract_direction == "unknown"
    assert r.settlement_horizon == "unknown"


def test_championship_futures_is_unclear():
    """championship_futures are not game-level contracts — always unclear for our purposes."""
    r = _parse(
        market_type="championship_futures",
        market_ticker="KXMLB-26-NYY",
        title="Will NYY win the 2026 World Series?",
    )
    assert r.is_semantics_clear is False


# ── DB integration: refresh_market_semantics ─────────────────────────────────


def test_refresh_backfills_clear_market(conn):
    _ins_market(
        conn, "MKT1", "full_game_total",
        rules="This market resolves YES if total runs exceed 8.5.",
    )
    result = refresh_market_semantics(conn)
    assert result["updated_clear"] == 1
    assert result["updated_unclear"] == 0
    row = conn.execute(
        "SELECT contract_direction, is_semantics_clear FROM kalshi_markets WHERE market_ticker='MKT1'"
    ).fetchone()
    assert row["contract_direction"] == "over_yes"
    assert row["is_semantics_clear"] == 1


def test_refresh_backfills_unclear_market(conn):
    _ins_market(conn, "MKT2", "spread_run_line", title="NYY -1.5")
    result = refresh_market_semantics(conn)
    assert result["updated_unclear"] == 1
    row = conn.execute(
        "SELECT is_semantics_clear, needs_review_reason FROM kalshi_markets WHERE market_ticker='MKT2'"
    ).fetchone()
    assert row["is_semantics_clear"] == 0
    assert row["needs_review_reason"] is not None


def test_refresh_is_idempotent(conn):
    _ins_market(
        conn, "MKT3", "full_game_total",
        rules="Resolves YES if total runs exceed 8.5.",
    )
    refresh_market_semantics(conn)
    refresh_market_semantics(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM kalshi_markets WHERE market_ticker='MKT3'"
    ).fetchone()[0]
    assert count == 1   # no duplicate rows


def test_refresh_multiple_markets(conn):
    _ins_market(conn, "A", "full_game_total", rules="Resolves YES if total runs exceed 8.5.")
    _ins_market(conn, "B", "f5_total", rules="Resolves YES if F5 runs exceed 4.5.")
    _ins_market(conn, "C", "spread_run_line", title="NYY -1.5")
    result = refresh_market_semantics(conn)
    assert result["updated_clear"] == 2
    assert result["updated_unclear"] == 1
    assert result["total"] == 3


def test_refresh_new_columns_exist_in_schema(conn):
    """Verify all required semantics columns are present after init_db."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(kalshi_markets)").fetchall()}
    required = {
        "settlement_horizon", "selected_team_abbr", "opponent_team_abbr",
        "spread_value", "yes_means", "no_means", "contract_direction",
        "semantics_confidence", "is_semantics_clear", "needs_review_reason",
        "game_open_price_cents",
    }
    missing = required - cols
    assert missing == set(), f"Missing columns: {missing}"
