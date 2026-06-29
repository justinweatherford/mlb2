"""Tests for sbr/odds_parser.py -- all offline, no network."""
import json
import math
from pathlib import Path
import pytest
from sbr.odds_parser import (
    american_to_implied,
    no_vig_normalize,
    implied_to_american,
    normalize_team_name,
    team_full_to_abbr,
    team_abbr_to_full,
    parse_sbr_next_data,
    compute_game_consensus,
)

# ── american_to_implied ───────────────────────────────────────────────────────

def test_implied_even_money():
    assert math.isclose(american_to_implied(100), 0.5, rel_tol=1e-6)

def test_implied_minus_150():
    assert math.isclose(american_to_implied(-150), 0.6, rel_tol=1e-4)

def test_implied_plus_130():
    assert math.isclose(american_to_implied(130), 100 / 230, rel_tol=1e-4)

def test_implied_plus_200():
    assert math.isclose(american_to_implied(200), 100 / 300, rel_tol=1e-4)

def test_implied_minus_110():
    assert math.isclose(american_to_implied(-110), 110 / 210, rel_tol=1e-4)

# ── no_vig_normalize ──────────────────────────────────────────────────────────

def test_no_vig_sums_to_one():
    h, a = no_vig_normalize(0.55, 0.50)
    assert math.isclose(h + a, 1.0, rel_tol=1e-9)

def test_no_vig_even_vig():
    h, a = no_vig_normalize(0.5238, 0.5238)
    assert math.isclose(h, 0.5, rel_tol=1e-4)
    assert math.isclose(a, 0.5, rel_tol=1e-4)

def test_no_vig_favorite():
    h_imp = american_to_implied(-150)
    a_imp = american_to_implied(130)
    h_nv, a_nv = no_vig_normalize(h_imp, a_imp)
    assert h_nv > a_nv
    assert math.isclose(h_nv + a_nv, 1.0, rel_tol=1e-9)
    assert 0.55 < h_nv < 0.65

def test_no_vig_zero_input():
    h, a = no_vig_normalize(0.0, 0.0)
    assert h == 0.5 and a == 0.5

# ── implied_to_american ───────────────────────────────────────────────────────

def test_american_roundtrip_favorite():
    orig = -150
    prob = american_to_implied(orig)
    back = implied_to_american(prob)
    assert back == orig

def test_american_roundtrip_underdog():
    orig = 130
    prob = american_to_implied(orig)
    back = implied_to_american(prob)
    assert back == orig

# ── team normalization ────────────────────────────────────────────────────────

def test_normalize_known_team():
    assert normalize_team_name("Tampa Bay Rays") == "Tampa Bay Rays"
    assert normalize_team_name("Athletics") == "Athletics"

def test_normalize_unknown_team():
    assert normalize_team_name("Fake City Fakers") == "Fake City Fakers"

def test_full_to_abbr():
    assert team_full_to_abbr("Tampa Bay Rays") == "TB"
    assert team_full_to_abbr("Milwaukee Brewers") == "MIL"
    assert team_full_to_abbr("Athletics") == "ATH"
    assert team_full_to_abbr("Oakland Athletics") == "OAK"

def test_abbr_to_full():
    assert team_abbr_to_full("TB") == "Tampa Bay Rays"
    assert team_abbr_to_full("MIL") == "Milwaukee Brewers"
    assert team_abbr_to_full("ATH") == "Athletics"

def test_full_to_abbr_unknown():
    assert team_full_to_abbr("Fake City Fakers") is None

# ── parse_sbr_next_data ───────────────────────────────────────────────────────

FIXTURE = Path(__file__).parent / "fixtures" / "sbr_next_data_sample.json"


def _make_minimal_html(odds_tables: list) -> str:
    nd = {"props": {"pageProps": {"oddsTables": odds_tables}}}
    return f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></head></html>'


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not yet generated")
def test_parse_real_fixture():
    odds_tables = json.loads(FIXTURE.read_text())
    html = _make_minimal_html(odds_tables)
    rows = parse_sbr_next_data(html, "2023-07-15", "https://example.com")
    assert len(rows) > 0
    row = rows[0]
    assert "away_team" in row and "home_team" in row
    assert "home_no_vig_current" in row
    assert row["parse_method"] == "embedded_json"
    if row["home_no_vig_current"] is not None and row["away_no_vig_current"] is not None:
        assert math.isclose(row["home_no_vig_current"] + row["away_no_vig_current"], 1.0, rel_tol=1e-4)


def test_parse_synthetic():
    odds_tables = [{"oddsTableModel": {
        "sportsbooks": [{"machineName": "betmgm", "name": "BetMGM"}],
        "gameRows": [{
            "gameView": {
                "startDate": "2023-07-15T17:10:00+00:00",
                "awayTeam": {"fullName": "Kansas City Royals"},
                "homeTeam": {"fullName": "Tampa Bay Rays"},
                "awayStarter": {"firstInital": "B", "lastName": "Singer"},
                "homeStarter": {"firstInital": "Z", "lastName": "Eflin"},
            },
            "oddsViews": [{
                "sportsbook": "betmgm",
                "currentLine": {"awayOdds": 155, "homeOdds": -190},
                "openingLine": {"awayOdds": 145, "homeOdds": -175},
            }],
        }],
    }}]
    html = _make_minimal_html(odds_tables)
    rows = parse_sbr_next_data(html, "2023-07-15", "https://example.com")
    assert len(rows) == 1
    r = rows[0]
    assert r["away_team"] == "Kansas City Royals"
    assert r["home_team"] == "Tampa Bay Rays"
    assert r["away_abbr"] == "KC"
    assert r["home_abbr"] == "TB"
    assert r["sportsbook"] == "BetMGM"
    assert r["away_ml_current"] == "+155"
    assert r["home_ml_current"] == "-190"
    assert r["away_ml_open"] == "+145"
    assert r["home_ml_open"] == "-175"
    assert r["home_no_vig_current"] is not None
    assert math.isclose(r["home_no_vig_current"] + r["away_no_vig_current"], 1.0, rel_tol=1e-4)
    assert r["away_pitcher"] == "B. Singer"
    assert r["home_pitcher"] == "Z. Eflin"


def test_parse_missing_odds_skipped():
    odds_tables = [{"oddsTableModel": {
        "sportsbooks": [],
        "gameRows": [{"gameView": {
            "awayTeam": {"fullName": "Tampa Bay Rays"},
            "homeTeam": {"fullName": "New York Yankees"},
        }, "oddsViews": [{"sportsbook": "betmgm", "currentLine": {}, "openingLine": {}}]}],
    }}]
    html = _make_minimal_html(odds_tables)
    rows = parse_sbr_next_data(html, "2023-07-15", "https://example.com")
    assert rows == []

# ── compute_game_consensus ────────────────────────────────────────────────────

def test_consensus_two_books():
    book_rows = [
        {"game_date": "2023-07-15", "away_team": "KC", "home_team": "TB",
         "away_abbr": "KC", "home_abbr": "TB", "away_pitcher": "", "home_pitcher": "",
         "start_time": "", "sportsbook": "BetMGM", "sportsbook_machine": "betmgm",
         "home_no_vig_current": 0.60, "away_no_vig_current": 0.40,
         "home_no_vig_open": 0.58, "away_no_vig_open": 0.42, "source_url": ""},
        {"game_date": "2023-07-15", "away_team": "KC", "home_team": "TB",
         "away_abbr": "KC", "home_abbr": "TB", "away_pitcher": "", "home_pitcher": "",
         "start_time": "", "sportsbook": "FanDuel", "sportsbook_machine": "fanduel",
         "home_no_vig_current": 0.62, "away_no_vig_current": 0.38,
         "home_no_vig_open": 0.59, "away_no_vig_open": 0.41, "source_url": ""},
    ]
    c = compute_game_consensus(book_rows)
    assert c["book_count"] == 2
    assert math.isclose(c["home_no_vig_avg"], 0.61, rel_tol=1e-4)
    assert math.isclose(c["away_no_vig_avg"], 0.39, rel_tol=1e-4)
    assert math.isclose(c["home_no_vig_open_avg"], 0.585, rel_tol=1e-4)
