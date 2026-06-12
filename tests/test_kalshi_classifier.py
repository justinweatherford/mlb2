"""
tests/test_kalshi_classifier.py — Unit tests for Kalshi market classifier.

Covers classify_market_type_with_reason() for all six market types plus
the unknown fallback.  Also verifies that series-prefix check runs BEFORE
the text-regex so KXMLBTT markets with "over X runs" in the title are not
mis-tagged as full_game_total.
"""
import pytest

from kalshi.discovery import classify_market_type, classify_market_type_with_reason


# ── Helpers ───────────────────────────────────────────────────────────────────

def mtype(ticker="", title="", subtitle="", rules=""):
    return classify_market_type(ticker, title, subtitle, rules)


def reason(ticker="", title="", subtitle="", rules=""):
    _, r = classify_market_type_with_reason(ticker, title, subtitle, rules)
    return r


# ── moneyline ─────────────────────────────────────────────────────────────────

class TestMoneyline:
    def test_kxmlbgame_ticker_prefix(self):
        assert mtype(ticker="KXMLBGAME-26JUN121937NYYTOR-NYY") == "moneyline"

    def test_kxmlbgame_prefix_reason(self):
        assert reason(ticker="KXMLBGAME-26JUN121937NYYTOR-NYY") == "series_prefix:KXMLBGAME"

    def test_winner_in_title(self):
        assert mtype(ticker="KXMLBGAME-26JUN121937NYYTOR-NYY",
                     title="New York Y vs Toronto Winner?") == "moneyline"

    def test_moneyline_keyword(self):
        assert mtype(title="Yankees Moneyline") == "moneyline"

    def test_to_win_keyword(self):
        assert mtype(title="Will Houston to win tonight?") == "moneyline"

    def test_kxmlbgame_reason_is_series_prefix(self):
        _, r = classify_market_type_with_reason("KXMLBGAME-26JUN14-HOU", "")
        assert r.startswith("series_prefix:")


# ── full_game_total ───────────────────────────────────────────────────────────

class TestFullGameTotal:
    def test_kxmlbtotal_prefix(self):
        assert mtype(ticker="KXMLBTOTAL-26JUN14-NYYTOR-O8.5") == "full_game_total"

    def test_kxmlbtotal_prefix_reason(self):
        assert reason(ticker="KXMLBTOTAL-26JUN14-O8.5") == "series_prefix:KXMLBTOTAL"

    def test_total_runs_in_title(self):
        assert mtype(title="Will there be over 8.5 total runs?") == "full_game_total"

    def test_over_under_slash(self):
        assert mtype(title="O/U 8.5 Runs Tonight") == "full_game_total"

    def test_over_under_abbrev(self):
        assert mtype(title="Game OU: 8.5") == "full_game_total"

    def test_runs_over_phrase(self):
        assert mtype(rules="This market resolves Yes if runs over 8 total runs scored.") \
               == "full_game_total"

    def test_reason_is_regex(self):
        _, r = classify_market_type_with_reason("", "Total runs over 8.5")
        assert r == "regex:full_game_total"

    def test_kxmlbtotal_not_shadowed_by_team_total(self):
        """KXMLBTOTAL must not be shadowed by KXMLBTEAMTOTAL prefix."""
        assert mtype(ticker="KXMLBTOTAL-26JUN14-O9") == "full_game_total"


# ── team_total ────────────────────────────────────────────────────────────────

class TestTeamTotal:
    def test_kxmlbteamtotal_prefix(self):
        assert mtype(ticker="KXMLBTEAMTOTAL-26JUN14-HOU-O4.5") == "team_total"

    def test_kxmlbteamtotal_prefix_reason(self):
        assert reason(ticker="KXMLBTEAMTOTAL-26JUN14-HOU-O4.5") == "series_prefix:KXMLBTEAMTOTAL"

    def test_team_total_in_title(self):
        assert mtype(title="Houston team total over 4.5") == "team_total"

    def test_team_total_in_subtitle(self):
        assert mtype(subtitle="Home team total") == "team_total"

    def test_team_total_in_rules(self):
        assert mtype(rules="Resolves Yes if the home team total exceeds 4.5 runs.") \
               == "team_total"

    def test_reason_is_regex(self):
        _, r = classify_market_type_with_reason("", "Yankees team total over 3")
        assert r == "regex:team_total"

    def test_series_prefix_beats_over_regex(self):
        """KXMLBTEAMTOTAL title may contain 'over X runs' — prefix must win."""
        assert mtype(ticker="KXMLBTEAMTOTAL-26JUN14-HOU-O4.5",
                     title="Houston Over 4.5 Runs") == "team_total"


# ── spread_run_line ───────────────────────────────────────────────────────────

class TestSpreadRunLine:
    def test_kxmlbspread_prefix(self):
        assert mtype(ticker="KXMLBSPREAD-26JUN14-NYYTOR-NYY4") == "spread_run_line"

    def test_kxmlbspread_prefix_reason(self):
        assert reason(ticker="KXMLBSPREAD-26JUN14-NYY4") == "series_prefix:KXMLBSPREAD"

    def test_run_line_in_title(self):
        assert mtype(title="Yankees Run Line -1.5") == "spread_run_line"

    def test_spread_keyword(self):
        assert mtype(title="Will the Dodgers cover the spread?") == "spread_run_line"

    def test_minus_1_5_in_ticker(self):
        assert mtype(ticker="SOMERL-1.5") == "spread_run_line"

    def test_plus_1_5_in_ticker(self):
        assert mtype(ticker="SOMERL+1.5") == "spread_run_line"

    def test_reason_is_regex(self):
        _, r = classify_market_type_with_reason("", "Yankees run line -1.5")
        assert r == "regex:spread_run_line"


# ── f5_winner ─────────────────────────────────────────────────────────────────

class TestF5Winner:
    def test_kxmlbf5_prefix(self):
        assert mtype(ticker="KXMLBF5-26JUN14LADCWS-TIE") == "f5_winner"

    def test_kxmlbf5_prefix_reason(self):
        assert reason(ticker="KXMLBF5-26JUN14LADCWS-LAD") == "series_prefix:KXMLBF5"

    def test_kxmlbf5spread_not_f5_winner(self):
        """KXMLBF5SPREAD must be f5_spread, not f5_winner."""
        assert mtype(ticker="KXMLBF5SPREAD-26JUN14LADCWS-LAD3") == "f5_spread"

    def test_kxmlbf5total_not_f5_winner(self):
        """KXMLBF5TOTAL must be f5_total, not f5_winner."""
        assert mtype(ticker="KXMLBF5TOTAL-26JUN14LADCWS-7") == "f5_total"


# ── f5_spread ─────────────────────────────────────────────────────────────────

class TestF5Spread:
    def test_kxmlbf5spread_prefix(self):
        assert mtype(ticker="KXMLBF5SPREAD-26JUN14LADCWS-LAD3") == "f5_spread"

    def test_kxmlbf5spread_prefix_reason(self):
        assert reason(ticker="KXMLBF5SPREAD-26JUN14LADCWS-LAD3") == "series_prefix:KXMLBF5SPREAD"


# ── f5_total ──────────────────────────────────────────────────────────────────

class TestF5Total:
    def test_kxmlbf5total_prefix(self):
        assert mtype(ticker="KXMLBF5TOTAL-26JUN14LADCWS-7") == "f5_total"

    def test_kxmlbf5total_prefix_reason(self):
        assert reason(ticker="KXMLBF5TOTAL-26JUN14LADCWS-7") == "series_prefix:KXMLBF5TOTAL"


# ── extra_innings ─────────────────────────────────────────────────────────────

class TestExtraInnings:
    def test_kxmlbextras_prefix(self):
        assert mtype(ticker="KXMLBEXTRAS-26JUN141920TEXBOS-EXTRAS") == "extra_innings"

    def test_kxmlbextras_prefix_reason(self):
        assert reason(ticker="KXMLBEXTRAS-26JUN141920TEXBOS-EXTRAS") == "series_prefix:KXMLBEXTRAS"

    def test_extra_innings_in_title(self):
        assert mtype(title="Will this game go to extra innings?") == "extra_innings"


# ── player_hr ─────────────────────────────────────────────────────────────────

class TestPlayerHR:
    def test_kxmlbhr_prefix(self):
        assert mtype(ticker="KXMLBHR-26JUN14-JUDGE") == "player_hr"

    def test_kxmlbhr_prefix_reason(self):
        assert reason(ticker="KXMLBHR-26JUN14-JUDGE") == "series_prefix:KXMLBHR"

    def test_home_run_in_title(self):
        assert mtype(title="Will Aaron Judge hit a home run?") == "player_hr"

    def test_hr_abbreviation(self):
        assert mtype(title="Shohei Ohtani HR tonight") == "player_hr"

    def test_homer_keyword(self):
        assert mtype(title="Will Vlad Jr. hit a homer?") == "player_hr"

    def test_hit_a_hr(self):
        assert mtype(rules="Resolves Yes if Trout hits a HR in tonight's game.") \
               == "player_hr"

    def test_reason_is_regex(self):
        _, r = classify_market_type_with_reason("", "Will Soto hit a home run?")
        assert r == "regex:player_hr"


# ── championship_futures ─────────────────────────────────────────────────────

class TestChampionshipFutures:
    def test_kxmlb_hyphen_prefix(self):
        assert mtype(ticker="KXMLB-26-COL") == "championship_futures"

    def test_kxmlb_hyphen_prefix_reason(self):
        assert reason(ticker="KXMLB-26-NYY") == "series_prefix:KXMLB-"

    def test_kxmlb_without_hyphen_not_matched(self):
        """KXMLBGAME- must not be swallowed by the KXMLB- prefix."""
        assert mtype(ticker="KXMLBGAME-26JUN12-NYY") == "moneyline"

    def test_kxmlb_futures_all_teams(self):
        for team in ("COL", "NYY", "LAD", "BOS", "HOU"):
            assert mtype(ticker=f"KXMLB-26-{team}") == "championship_futures"


# ── unknown ───────────────────────────────────────────────────────────────────

class TestUnknown:
    def test_empty_inputs(self):
        assert mtype() == "unknown"

    def test_unrecognised_series(self):
        assert mtype(ticker="KXXX-26-SOMETHINGELSE") == "unknown"

    def test_unrecognised_title(self):
        assert mtype(title="Will it rain in New York tomorrow?") == "unknown"

    def test_reason_is_no_match(self):
        _, r = classify_market_type_with_reason("KXXX-26", "Some random question?")
        assert r == "no_match"

    def test_partial_series_prefix_no_match(self):
        # "KXML" is not a known prefix — should not match KXMLB* prefixes
        assert mtype(ticker="KXML-26-SOMETHING") == "unknown"


# ── Priority: series prefix beats text regex ──────────────────────────────────

class TestPriority:
    def test_kxmlbgame_beats_winner_regex(self):
        """Series prefix should be the reason, not the winner regex."""
        _, r = classify_market_type_with_reason(
            "KXMLBGAME-26JUN121937NYYTOR-NYY",
            "New York Y vs Toronto Winner?",
        )
        assert r == "series_prefix:KXMLBGAME"

    def test_kxmlbtotal_beats_total_regex(self):
        """KXMLBTOTAL must not be overridden by 'total runs' in title."""
        t, r = classify_market_type_with_reason(
            "KXMLBTOTAL-26JUN14-O8.5",
            "Will total runs exceed 8.5?",
        )
        assert t == "full_game_total"
        assert r == "series_prefix:KXMLBTOTAL"

    def test_kxmlbteamtotal_beats_over_runs_regex(self):
        """KXMLBTEAMTOTAL with 'over X runs' title must resolve to team_total, not full_game_total."""
        t, r = classify_market_type_with_reason(
            "KXMLBTEAMTOTAL-26JUN14-HOU-O4.5",
            "Houston Over 4.5 Runs",
        )
        assert t == "team_total"
        assert r == "series_prefix:KXMLBTEAMTOTAL"

    def test_kxmlbspread_beats_run_line_regex(self):
        """KXMLBSPREAD prefix should be the reason even when title also matches."""
        t, r = classify_market_type_with_reason(
            "KXMLBSPREAD-26JUN14-NYY4",
            "Yankees Run Line -1.5",
        )
        assert t == "spread_run_line"
        assert r == "series_prefix:KXMLBSPREAD"

    def test_kxmlbf5total_beats_total_regex(self):
        """KXMLBF5TOTAL with 'total runs' title must resolve to f5_total, not full_game_total."""
        t, r = classify_market_type_with_reason(
            "KXMLBF5TOTAL-26JUN14-O7",
            "Will total runs through 5 exceed 7?",
        )
        assert t == "f5_total"
        assert r == "series_prefix:KXMLBF5TOTAL"
