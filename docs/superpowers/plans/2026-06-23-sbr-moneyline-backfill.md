# SBR MLB Moneyline Backfill & ML Core v1 Market Validation

## Goal
Fetch and cache 2023–2025 historical MLB moneyline odds from SportsbookReview, compute per-game sportsbook consensus no-vig probabilities, and join to Moneyline Core v1 pregame card rows to determine whether the lane beat market-implied probability (not just random baseline).

## Architecture

```
sbr/
  __init__.py              -- empty package marker
  odds_parser.py           -- pure stateless parsing utilities (no I/O)

sbr_mlb_odds_fetcher.py    -- CLI orchestrator: date list -> SBR fetch -> cache -> parse -> CSV
sbr_moneyline_core_validation.py -- join SBR consensus to ML Core v1 cards -> validation report

tests/
  test_sbr_odds_parser.py  -- unit tests for all parser functions
  fixtures/
    sbr_next_data_sample.json  -- saved __NEXT_DATA__ snippet for offline testing

outputs/sbr_mlb_odds/
  cache/YYYY-MM-DD.html    -- one cached HTML page per date (never re-fetch unless --force-refresh)
  sbr_moneyline_odds.csv   -- one row per game/sportsbook/date
  sbr_moneyline_game_consensus.csv  -- one row per game/date with consensus no-vig probs
  sbr_unmatched_games.csv  -- SBR games that didn't match our DB
  sbr_fetch_summary.md     -- fetch run stats

outputs/sbr_moneyline_core_validation/
  moneyline_core_market_validation.md   -- full analysis report
  moneyline_core_market_validation_rows.csv  -- one row per ML Core v1 card with market odds joined
```

**Data flow:**
1. `sbr_mlb_odds_fetcher.py` reads unique dates from `pregame_identifier_cards.csv` (2023–2025 only)
2. For each date: check cache, fetch if missing, parse `__NEXT_DATA__`, write per-game rows
3. Compute consensus: per-game average/median no-vig probability across all books
4. `sbr_moneyline_core_validation.py` reads consensus CSV + pregame cards, classifies ML Core v1 rows, joins, reports

## Tech Stack
- `requests` + `BeautifulSoup` (lxml) for raw HTML fetch/parse — no Playwright needed
- `statistics` stdlib for median
- `csv`, `json`, `sqlite3` from stdlib
- No new pip dependencies required

---

## Files Created / Modified

| File | Action | Purpose |
|------|--------|---------|
| `sbr/__init__.py` | CREATE | Package marker |
| `sbr/odds_parser.py` | CREATE | All pure parsing/conversion utilities |
| `sbr_mlb_odds_fetcher.py` | CREATE | Main fetch+parse+cache CLI script |
| `sbr_moneyline_core_validation.py` | CREATE | ML Core v1 join + validation report |
| `tests/test_sbr_odds_parser.py` | CREATE | Unit tests |
| `tests/fixtures/sbr_next_data_sample.json` | CREATE | Offline test fixture |

---

## Step 1 — Create `sbr/__init__.py`

**File:** `sbr/__init__.py`

```python
# SBR odds parsing package
```

**Shell:**
```
mkdir sbr  (if not exists)
```

No tests for this step.

---

## Step 2 — Create `sbr/odds_parser.py` (pure utilities)

**File:** `sbr/odds_parser.py`

```python
"""
sbr/odds_parser.py -- Pure stateless utilities for SportsbookReview MLB odds parsing.

No I/O. All functions take dicts/strings, return dicts/strings/floats.
"""
import json
import re
from typing import Optional
from bs4 import BeautifulSoup

# ── Team name normalization ────────────────────────────────────────────────────

# SBR full name -> canonical full name (covers variations seen in SBR data)
_TEAM_NORMALIZE: dict[str, str] = {
    # Standard full names (pass-through)
    "Arizona Diamondbacks": "Arizona Diamondbacks",
    "Atlanta Braves": "Atlanta Braves",
    "Baltimore Orioles": "Baltimore Orioles",
    "Boston Red Sox": "Boston Red Sox",
    "Chicago Cubs": "Chicago Cubs",
    "Chicago White Sox": "Chicago White Sox",
    "Cincinnati Reds": "Cincinnati Reds",
    "Cleveland Guardians": "Cleveland Guardians",
    "Colorado Rockies": "Colorado Rockies",
    "Detroit Tigers": "Detroit Tigers",
    "Houston Astros": "Houston Astros",
    "Kansas City Royals": "Kansas City Royals",
    "Los Angeles Angels": "Los Angeles Angels",
    "Los Angeles Dodgers": "Los Angeles Dodgers",
    "Miami Marlins": "Miami Marlins",
    "Milwaukee Brewers": "Milwaukee Brewers",
    "Minnesota Twins": "Minnesota Twins",
    "New York Mets": "New York Mets",
    "New York Yankees": "New York Yankees",
    "Oakland Athletics": "Oakland Athletics",
    "Athletics": "Athletics",           # Sacramento A's (2025+)
    "Philadelphia Phillies": "Philadelphia Phillies",
    "Pittsburgh Pirates": "Pittsburgh Pirates",
    "San Diego Padres": "San Diego Padres",
    "San Francisco Giants": "San Francisco Giants",
    "Seattle Mariners": "Seattle Mariners",
    "St. Louis Cardinals": "St. Louis Cardinals",
    "Tampa Bay Rays": "Tampa Bay Rays",
    "Texas Rangers": "Texas Rangers",
    "Toronto Blue Jays": "Toronto Blue Jays",
    "Washington Nationals": "Washington Nationals",
}

# Canonical full name -> DB abbreviation (from mlb_games.home_abbr)
_FULL_TO_ABBR: dict[str, str] = {
    "Arizona Diamondbacks": "AZ",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Oakland Athletics": "OAK",
    "Athletics": "ATH",                 # Sacramento A's (2025+)
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSN",
}

# DB abbreviation -> canonical full name (reverse of _FULL_TO_ABBR)
_ABBR_TO_FULL: dict[str, str] = {v: k for k, v in _FULL_TO_ABBR.items()}


def normalize_team_name(name: str) -> str:
    """Normalize an SBR team name to canonical full name. Returns original if unrecognized."""
    return _TEAM_NORMALIZE.get(name, name)


def team_full_to_abbr(full_name: str) -> Optional[str]:
    """Convert canonical full team name to DB abbreviation. Returns None if unmapped."""
    return _FULL_TO_ABBR.get(normalize_team_name(full_name))


def team_abbr_to_full(abbr: str) -> Optional[str]:
    """Convert DB team abbreviation to canonical full name. Returns None if unmapped."""
    return _ABBR_TO_FULL.get(abbr)


# ── Odds conversion ────────────────────────────────────────────────────────────

def american_to_implied(odds: int) -> float:
    """Convert American moneyline odds to raw implied probability (0-1).

    Examples:
      -150 -> 0.6000  (150 / (150+100))
      +130 -> 0.4348  (100 / (130+100))
    """
    if odds >= 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def no_vig_normalize(home_implied: float, away_implied: float) -> tuple[float, float]:
    """Remove the vig from home/away implied probabilities.

    Normalizes so that home_no_vig + away_no_vig = 1.0.
    Returns (home_no_vig, away_no_vig).
    """
    total = home_implied + away_implied
    if total <= 0:
        return 0.5, 0.5
    return home_implied / total, away_implied / total


def implied_to_american(prob: float) -> int:
    """Convert probability (0-1) to American odds (rounded to nearest integer).

    prob >= 0.5 -> negative odds (favorite)
    prob < 0.5  -> positive odds (underdog)
    """
    if prob <= 0:
        return 99999
    if prob >= 1:
        return -99999
    if prob >= 0.5:
        return round(-prob / (1 - prob) * 100)
    return round((1 - prob) / prob * 100)


# ── __NEXT_DATA__ parser ───────────────────────────────────────────────────────

def _pitcher_name(starter: Optional[dict]) -> str:
    if not isinstance(starter, dict):
        return ""
    first = starter.get("firstInital") or starter.get("firstName") or ""
    last = starter.get("lastName") or ""
    if last:
        return f"{first}. {last}".strip(". ")
    return ""


def _format_ml(v) -> str:
    if v is None:
        return ""
    try:
        n = int(v)
        return f"+{n}" if n > 0 else str(n)
    except Exception:
        return str(v)


def parse_sbr_next_data(html: str, game_date: str, source_url: str) -> list[dict]:
    """Parse SBR __NEXT_DATA__ embedded JSON and return one dict per game/sportsbook pair.

    Confirmed SBR structure:
      props.pageProps.oddsTables[0].oddsTableModel
        .sportsbooks[i].machineName -> .name (book id -> display name)
        .gameRows[j]
          .gameView.awayTeam.fullName / .homeTeam.fullName
          .gameView.startDate  (ISO timestamp)
          .gameView.awayStarter / .homeStarter
          .oddsViews[k].sportsbook  (machine name)
          .oddsViews[k].currentLine.{awayOdds, homeOdds}
          .oddsViews[k].openingLine.{awayOdds, homeOdds}

    Returns list of row dicts with fields:
      game_date, away_team, home_team, away_abbr, home_abbr,
      away_pitcher, home_pitcher, start_time,
      sportsbook, sportsbook_machine,
      away_ml_current, home_ml_current,
      away_ml_open, home_ml_open,
      away_implied_current, home_implied_current,
      away_no_vig_current, home_no_vig_current,
      away_implied_open, home_implied_open,
      away_no_vig_open, home_no_vig_open,
      source_url, parse_method
    """
    rows: list[dict] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        nd_script = soup.find("script", {"id": "__NEXT_DATA__"})
        if not nd_script or not nd_script.string:
            return rows
        nd = json.loads(nd_script.string)
    except Exception:
        return rows

    tables = nd.get("props", {}).get("pageProps", {}).get("oddsTables", [])
    if not tables:
        return rows

    otm = tables[0].get("oddsTableModel", {})
    sb_map = {
        sb.get("machineName"): sb.get("name", sb.get("machineName", "unknown"))
        for sb in otm.get("sportsbooks", [])
        if sb.get("machineName")
    }

    for gr in otm.get("gameRows", []):
        if not isinstance(gr, dict):
            continue
        gv = gr.get("gameView") or {}
        away_raw = (gv.get("awayTeam") or {}).get("fullName", "")
        home_raw = (gv.get("homeTeam") or {}).get("fullName", "")
        if not away_raw or not home_raw:
            continue

        away_team = normalize_team_name(away_raw)
        home_team = normalize_team_name(home_raw)
        away_abbr = team_full_to_abbr(away_team) or ""
        home_abbr = team_full_to_abbr(home_team) or ""
        start_time = (gv.get("startDate") or "")[:19]
        away_pitcher = _pitcher_name(gv.get("awayStarter"))
        home_pitcher = _pitcher_name(gv.get("homeStarter"))

        for ov in (gr.get("oddsViews") or []):
            if not isinstance(ov, dict):
                continue
            sb_machine = ov.get("sportsbook", "")
            sb_name = sb_map.get(sb_machine, sb_machine)

            cl = ov.get("currentLine") or {}
            ol = ov.get("openingLine") or {}

            away_curr = cl.get("awayOdds")
            home_curr = cl.get("homeOdds")
            away_open = ol.get("awayOdds")
            home_open = ol.get("homeOdds")

            if away_curr is None and home_curr is None:
                continue

            # Implied and no-vig for current line
            away_imp_c = american_to_implied(int(away_curr)) if away_curr is not None else None
            home_imp_c = american_to_implied(int(home_curr)) if home_curr is not None else None
            if away_imp_c is not None and home_imp_c is not None:
                home_nv_c, away_nv_c = no_vig_normalize(home_imp_c, away_imp_c)
            else:
                home_nv_c = away_nv_c = None

            # Implied and no-vig for opening line
            away_imp_o = american_to_implied(int(away_open)) if away_open is not None else None
            home_imp_o = american_to_implied(int(home_open)) if home_open is not None else None
            if away_imp_o is not None and home_imp_o is not None:
                home_nv_o, away_nv_o = no_vig_normalize(home_imp_o, away_imp_o)
            else:
                home_nv_o = away_nv_o = None

            rows.append({
                "game_date": game_date,
                "away_team": away_team,
                "home_team": home_team,
                "away_abbr": away_abbr,
                "home_abbr": home_abbr,
                "away_pitcher": away_pitcher[:40],
                "home_pitcher": home_pitcher[:40],
                "start_time": start_time,
                "sportsbook": sb_name[:40],
                "sportsbook_machine": sb_machine[:20],
                "away_ml_current": _format_ml(away_curr),
                "home_ml_current": _format_ml(home_curr),
                "away_ml_open": _format_ml(away_open),
                "home_ml_open": _format_ml(home_open),
                "away_implied_current": round(away_imp_c, 4) if away_imp_c is not None else None,
                "home_implied_current": round(home_imp_c, 4) if home_imp_c is not None else None,
                "away_no_vig_current": round(away_nv_c, 4) if away_nv_c is not None else None,
                "home_no_vig_current": round(home_nv_c, 4) if home_nv_c is not None else None,
                "away_implied_open": round(away_imp_o, 4) if away_imp_o is not None else None,
                "home_implied_open": round(home_imp_o, 4) if home_imp_o is not None else None,
                "away_no_vig_open": round(away_nv_o, 4) if away_nv_o is not None else None,
                "home_no_vig_open": round(home_nv_o, 4) if home_nv_o is not None else None,
                "source_url": source_url,
                "parse_method": "embedded_json",
            })

    return rows


def compute_game_consensus(book_rows: list[dict]) -> dict:
    """Given all book rows for one game, compute consensus no-vig probabilities.

    book_rows: list of dicts from parse_sbr_next_data, all with same game_date/home_team/away_team.
    Returns dict with consensus fields.
    """
    import statistics as _stat

    home_nv_curr = [r["home_no_vig_current"] for r in book_rows if r.get("home_no_vig_current") is not None]
    away_nv_curr = [r["away_no_vig_current"] for r in book_rows if r.get("away_no_vig_current") is not None]
    home_nv_open = [r["home_no_vig_open"] for r in book_rows if r.get("home_no_vig_open") is not None]
    away_nv_open = [r["away_no_vig_open"] for r in book_rows if r.get("away_no_vig_open") is not None]

    def _avg(xs): return round(sum(xs) / len(xs), 4) if xs else None
    def _med(xs): return round(_stat.median(xs), 4) if xs else None

    first = book_rows[0] if book_rows else {}
    return {
        "game_date": first.get("game_date", ""),
        "away_team": first.get("away_team", ""),
        "home_team": first.get("home_team", ""),
        "away_abbr": first.get("away_abbr", ""),
        "home_abbr": first.get("home_abbr", ""),
        "away_pitcher": first.get("away_pitcher", ""),
        "home_pitcher": first.get("home_pitcher", ""),
        "start_time": first.get("start_time", ""),
        "book_count": len(book_rows),
        "home_no_vig_avg": _avg(home_nv_curr),
        "away_no_vig_avg": _avg(away_nv_curr),
        "home_no_vig_median": _med(home_nv_curr),
        "away_no_vig_median": _med(away_nv_curr),
        "home_no_vig_open_avg": _avg(home_nv_open),
        "away_no_vig_open_avg": _avg(away_nv_open),
        "home_no_vig_open_median": _med(home_nv_open),
        "away_no_vig_open_median": _med(away_nv_open),
        "books_with_current": len(home_nv_curr),
        "books_with_open": len(home_nv_open),
        "source_url": first.get("source_url", ""),
    }
```

---

## Step 3 — Save fixture + write tests

**File:** `tests/fixtures/sbr_next_data_sample.json`

Run this once to capture real data:
```python
import requests, json
from bs4 import BeautifulSoup
resp = requests.get(
    "https://www.sportsbookreview.com/betting-odds/mlb-baseball/money-line/full-game/?date=2023-07-15",
    headers={"User-Agent": "Mozilla/5.0 ..."},
)
soup = BeautifulSoup(resp.text, "lxml")
nd = json.loads(soup.find("script", {"id": "__NEXT_DATA__"}).string)
# Save just the oddsTables slice to keep fixture small
fixture = nd["props"]["pageProps"]["oddsTables"]
with open("tests/fixtures/sbr_next_data_sample.json", "w") as f:
    json.dump(fixture, f)
```

**File:** `tests/test_sbr_odds_parser.py`

```python
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
    # 150 / 250 = 0.6
    assert math.isclose(american_to_implied(-150), 0.6, rel_tol=1e-4)

def test_implied_plus_130():
    # 100 / 230 = 0.4348
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
    # Both 0.5238 (like -110/-110): should normalize to 50/50
    h, a = no_vig_normalize(0.5238, 0.5238)
    assert math.isclose(h, 0.5, rel_tol=1e-4)
    assert math.isclose(a, 0.5, rel_tol=1e-4)

def test_no_vig_favorite():
    # -150 / +130: home is big favorite
    h_imp = american_to_implied(-150)
    a_imp = american_to_implied(130)
    h_nv, a_nv = no_vig_normalize(h_imp, a_imp)
    assert h_nv > a_nv
    assert math.isclose(h_nv + a_nv, 1.0, rel_tol=1e-9)
    # No-vig home should be between 0.55 and 0.65
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

# ── parse_sbr_next_data (offline, from fixture) ───────────────────────────────

FIXTURE = Path(__file__).parent / "fixtures" / "sbr_next_data_sample.json"

def _make_minimal_html(odds_tables: list) -> str:
    """Wrap a fixture oddsTables list into a minimal __NEXT_DATA__ HTML page."""
    nd = {"props": {"pageProps": {"oddsTables": odds_tables}}}
    return f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></head></html>'


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not yet generated")
def test_parse_real_fixture():
    odds_tables = json.loads(FIXTURE.read_text())
    html = _make_minimal_html(odds_tables)
    rows = parse_sbr_next_data(html, "2023-07-15", "https://example.com")
    assert len(rows) > 0, "Should parse at least one row"
    row = rows[0]
    # Required fields present
    assert "away_team" in row and "home_team" in row
    assert "home_no_vig_current" in row
    assert row["parse_method"] == "embedded_json"
    # No-vig sums to 1
    if row["home_no_vig_current"] is not None and row["away_no_vig_current"] is not None:
        assert math.isclose(row["home_no_vig_current"] + row["away_no_vig_current"], 1.0, rel_tol=1e-4)


def test_parse_synthetic():
    """Test parser with minimal synthetic __NEXT_DATA__."""
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
    assert rows == [], "Should skip rows with no odds"


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
```

**Shell to run tests:**
```bash
python -m pytest tests/test_sbr_odds_parser.py -v
```

---

## Step 4 — Create `sbr_mlb_odds_fetcher.py`

**File:** `sbr_mlb_odds_fetcher.py`

```python
"""
sbr_mlb_odds_fetcher.py -- Fetch and cache SBR MLB moneyline odds for 2023-2025.

Reads unique game dates from pregame_identifier_cards.csv, fetches SBR once per date,
caches raw HTML locally, parses __NEXT_DATA__, computes per-game consensus no-vig
probabilities, writes output CSVs.

Read-only research. No trades. No paper entries. No model changes.

Usage:
    python sbr_mlb_odds_fetcher.py --years 2023,2024,2025
    python sbr_mlb_odds_fetcher.py --limit-dates 5 --sleep-seconds 1
    python sbr_mlb_odds_fetcher.py --start-date 2023-03-30 --end-date 2023-12-31
    python sbr_mlb_odds_fetcher.py --force-refresh --years 2025
"""
import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

from sbr.odds_parser import parse_sbr_next_data, compute_game_consensus, team_full_to_abbr

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CARDS_CSV = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
OUT_DIR = Path("outputs/sbr_mlb_odds")
CACHE_DIR = OUT_DIR / "cache"
DB_PATH = os.environ.get("DB_PATH", "kalshi_mlb.db")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_SBR_URL = "https://www.sportsbookreview.com/betting-odds/mlb-baseball/?date={date}"

_BOOK_FIELDS = [
    "game_date", "away_team", "home_team", "away_abbr", "home_abbr",
    "away_pitcher", "home_pitcher", "start_time",
    "sportsbook", "sportsbook_machine",
    "away_ml_current", "home_ml_current",
    "away_ml_open", "home_ml_open",
    "away_implied_current", "home_implied_current",
    "away_no_vig_current", "home_no_vig_current",
    "away_implied_open", "home_implied_open",
    "away_no_vig_open", "home_no_vig_open",
    "source_url", "parse_method",
]

_CONSENSUS_FIELDS = [
    "game_date", "away_team", "home_team", "away_abbr", "home_abbr",
    "away_pitcher", "home_pitcher", "start_time",
    "book_count", "books_with_current", "books_with_open",
    "home_no_vig_avg", "away_no_vig_avg",
    "home_no_vig_median", "away_no_vig_median",
    "home_no_vig_open_avg", "away_no_vig_open_avg",
    "home_no_vig_open_median", "away_no_vig_open_median",
    "source_url",
]

_UNMATCHED_FIELDS = [
    "game_date", "away_team", "home_team", "away_abbr", "home_abbr",
    "reason", "source_url",
]


def load_card_dates(years: list[int] | None = None, start: str | None = None, end: str | None = None) -> list[str]:
    """Return sorted unique dates from pregame_identifier_cards.csv filtered by year/date range."""
    if not CARDS_CSV.exists():
        print(f"ERROR: {CARDS_CSV} not found")
        return []
    dates = set()
    with open(CARDS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = row.get("game_date", "")
            if not d or len(d) < 10:
                continue
            if years and int(d[:4]) not in years:
                continue
            if start and d < start:
                continue
            if end and d > end:
                continue
            dates.add(d)
    return sorted(dates)


def fetch_date(date: str, cache_dir: Path, sleep_s: float, force: bool) -> str | None:
    """Fetch SBR page for date. Returns HTML string or None on error. Caches to disk."""
    cache_file = cache_dir / f"{date}.html"
    if cache_file.exists() and not force:
        return cache_file.read_text(encoding="utf-8")

    url = _SBR_URL.format(date=date)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=25,
        )
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code} for {date}")
            return None
        html = resp.text
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html, encoding="utf-8")
        time.sleep(sleep_s)
        return html
    except Exception as exc:
        print(f"    fetch error {date}: {exc}")
        return None


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_summary(
    path: Path,
    dates_attempted: int,
    dates_ok: int,
    dates_empty: int,
    dates_error: int,
    book_rows: int,
    consensus_rows: int,
    unmatched_rows: int,
    years: str,
    elapsed_s: float,
) -> None:
    lines = [
        "# SBR MLB Moneyline Odds Fetch Summary",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Years: {years}",
        "",
        "## Stats",
        f"- Dates attempted: {dates_attempted}",
        f"- Dates with games: {dates_ok}",
        f"- Dates empty (off-day): {dates_empty}",
        f"- Dates with errors: {dates_error}",
        f"- Book-level odds rows: {book_rows}",
        f"- Consensus rows (unique games): {consensus_rows}",
        f"- Unmatched SBR games: {unmatched_rows}",
        f"- Elapsed: {elapsed_s:.0f}s",
        "",
        "## Outputs",
        "- `sbr_moneyline_odds.csv` -- one row per game/sportsbook",
        "- `sbr_moneyline_game_consensus.csv` -- one row per game with consensus no-vig probs",
        "- `sbr_unmatched_games.csv` -- games that could not be matched to our DB",
        "",
        "## Notes",
        "- Read-only research. No trades. No model changes.",
        "- Raw HTML cached in `cache/YYYY-MM-DD.html`. Re-run with --force-refresh to re-fetch.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SBR MLB moneyline odds. Read-only research.")
    parser.add_argument("--years", default="2023,2024,2025",
                        help="Comma-separated years to fetch (default: 2023,2024,2025)")
    parser.add_argument("--start-date", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--sleep-seconds", type=float, default=3.0,
                        help="Sleep between fetches (default: 3.0)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Re-fetch even if cached HTML exists")
    parser.add_argument("--limit-dates", type=int, default=None, metavar="N",
                        help="Stop after N dates (for testing)")
    args = parser.parse_args()

    years = [int(y.strip()) for y in args.years.split(",") if y.strip().isdigit()]
    dates = load_card_dates(years, args.start_date, args.end_date)
    if args.limit_dates:
        dates = dates[:args.limit_dates]

    print(f"\nSBR MLB Moneyline Fetcher")
    print(f"  Years: {years}  |  Dates: {len(dates)}  |  Sleep: {args.sleep_seconds}s")
    print(f"  Force-refresh: {args.force_refresh}")
    print()

    t0 = time.time()
    all_book_rows: list[dict] = []
    all_consensus_rows: list[dict] = []
    all_unmatched: list[dict] = []
    dates_ok = dates_empty = dates_error = 0

    for i, date in enumerate(dates, 1):
        url = _SBR_URL.format(date=date)
        cached = (CACHE_DIR / f"{date}.html").exists() and not args.force_refresh
        print(f"  [{i:4d}/{len(dates)}] {date}{'  (cached)' if cached else ''}")

        html = fetch_date(date, CACHE_DIR, args.sleep_seconds, args.force_refresh)
        if html is None:
            dates_error += 1
            continue

        rows = parse_sbr_next_data(html, date, url)
        if not rows:
            dates_empty += 1
            continue

        dates_ok += 1
        all_book_rows.extend(rows)

        # Group by game (away_abbr + home_abbr) and compute consensus
        games: defaultdict[tuple, list] = defaultdict(list)
        for r in rows:
            key = (r["game_date"], r["away_abbr"] or r["away_team"], r["home_abbr"] or r["home_team"])
            games[key].append(r)

        for (gdate, away_key, home_key), book_rows in games.items():
            consensus = compute_game_consensus(book_rows)
            all_consensus_rows.append(consensus)
            # Check matchability: if abbr is missing, flag as unmatched
            if not book_rows[0].get("home_abbr") or not book_rows[0].get("away_abbr"):
                all_unmatched.append({
                    "game_date": gdate,
                    "away_team": book_rows[0].get("away_team", ""),
                    "home_team": book_rows[0].get("home_team", ""),
                    "away_abbr": book_rows[0].get("away_abbr", ""),
                    "home_abbr": book_rows[0].get("home_abbr", ""),
                    "reason": "team_name_not_in_mapping",
                    "source_url": book_rows[0].get("source_url", ""),
                })

        print(f"           {len(games)} games, {len(rows)} book rows")

    # Write outputs
    write_csv(OUT_DIR / "sbr_moneyline_odds.csv", all_book_rows, _BOOK_FIELDS)
    write_csv(OUT_DIR / "sbr_moneyline_game_consensus.csv", all_consensus_rows, _CONSENSUS_FIELDS)
    write_csv(OUT_DIR / "sbr_unmatched_games.csv", all_unmatched, _UNMATCHED_FIELDS)
    elapsed = time.time() - t0
    write_summary(
        OUT_DIR / "sbr_fetch_summary.md",
        len(dates), dates_ok, dates_empty, dates_error,
        len(all_book_rows), len(all_consensus_rows), len(all_unmatched),
        args.years, elapsed,
    )

    print(f"\n=== DONE ===")
    print(f"  Dates: {dates_ok} ok, {dates_empty} empty, {dates_error} error")
    print(f"  Book rows: {len(all_book_rows)}")
    print(f"  Consensus games: {len(all_consensus_rows)}")
    print(f"  Unmatched: {len(all_unmatched)}")
    print(f"  Outputs -> {OUT_DIR}/")


if __name__ == "__main__":
    main()
```

**Shell (test run):**
```bash
python sbr_mlb_odds_fetcher.py --limit-dates 5 --sleep-seconds 1
```

**Shell (full run):**
```bash
python sbr_mlb_odds_fetcher.py --years 2023,2024,2025 --sleep-seconds 3
```

---

## Step 5 — Create `sbr_moneyline_core_validation.py`

**File:** `sbr_moneyline_core_validation.py`

```python
"""
sbr_moneyline_core_validation.py -- Join SBR consensus moneyline odds to
Moneyline Core v1 pregame card rows and validate whether the lane beats
market-implied probability.

Read-only research. No trades. No paper entries. No model changes.
Do not lower the Moneyline Core v1 threshold. Observe only.

Usage:
    python sbr_moneyline_core_validation.py
    python sbr_moneyline_core_validation.py --years 2023,2024,2025
"""
import argparse
import csv
import math
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CARDS_CSV = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
CONSENSUS_CSV = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")
CALIB_CSV = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")
OUT_DIR = Path("outputs/sbr_moneyline_core_validation")

# ML Core v1 constants (must not be changed here)
_ML_CORE_THRESHOLD = 0.40
_SUPPRESSOR_TAGS = {"tag_weak_leader_fade_watch", "tag_live_rebound_watch"}

_ROW_FIELDS = [
    "game_date", "season", "game_id", "team", "opponent", "home_away",
    "side_score", "ml_core_lane",
    "tag_weak_leader", "tag_live_rebound", "opponent_strength_bucket",
    "actual_team_won",
    "brain_calibrated_prob", "lane_hist_prob",
    "sbr_home_no_vig_avg", "sbr_away_no_vig_avg",
    "sbr_home_no_vig_open_avg", "sbr_away_no_vig_open_avg",
    "sbr_book_count",
    "team_no_vig_avg", "team_no_vig_open_avg",
    "market_edge_pp",
    "actual_minus_market",
    "implied_roi_pct",
]


# ── ML Core v1 lane classifier ────────────────────────────────────────────────

_REASON_RE = re.compile(r"\[.*?\]\s*([\w+_]+)=([\w._+-]+)")


def _parse_reasons(reasons_str: str) -> dict[str, str]:
    if not reasons_str or str(reasons_str).strip().lower() in {"", "nan", "none"}:
        return {}
    return {m.group(1): m.group(2).strip() for m in _REASON_RE.finditer(reasons_str)}


def classify_ml_core_lane(card: dict) -> str | None:
    """Return ML Core v1 lane label or None if not in scope.

    None -> not a ML Core v1 candidate
    'suppressed' -> has weak_leader or live_rebound tag
    'core_home_opp_weak' -> home + opp_weak + side >= 0.40
    'core_home_standard' -> home + NOT opp_weak + side >= 0.40
    """
    try:
        side_score = float(card.get("side_score") or 0)
    except (ValueError, TypeError):
        side_score = 0.0

    if side_score < _ML_CORE_THRESHOLD:
        return None
    if card.get("home_away") != "home":
        return None

    parsed = _parse_reasons(card.get("top_positive_reasons", ""))
    if (parsed.get("tag_weak_leader_fade_watch") == "yes"
            or parsed.get("tag_live_rebound_watch") == "yes"):
        return "suppressed"

    opp_bucket = (
        card.get("opponent_strength_bucket")
        or parsed.get("opponent_strength_bucket")
        or ""
    )
    if opp_bucket == "lt_40":
        return "core_home_opp_weak"
    return "core_home_standard"


# ── Calibration lookup ────────────────────────────────────────────────────────

def load_calibration(path: Path) -> dict[str, float]:
    """Return {lane_bin_key: conservative_probability}."""
    calib: dict[str, float] = {}
    if not path.exists():
        return calib
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lane = row.get("lane", "")
            bin_ = row.get("score_bin", "")
            prob = row.get("conservative_probability")
            if lane and bin_ and prob:
                try:
                    calib[f"{lane}:{bin_}"] = float(prob)
                except ValueError:
                    pass
    return calib


def lookup_calib(calib: dict[str, float], lane: str, side_score: float) -> float | None:
    bins = [
        ("<0.00", float("-inf"), 0.0),
        ("0.00-0.10", 0.0, 0.10),
        ("0.10-0.20", 0.10, 0.20),
        ("0.20-0.30", 0.20, 0.30),
        ("0.30-0.40", 0.30, 0.40),
        ("0.40+", 0.40, float("inf")),
    ]
    for label, lo, hi in bins:
        if lo <= side_score < hi or (hi == float("inf") and side_score >= lo):
            return calib.get(f"{lane}:{label}")
    return None


# ── Load SBR consensus ────────────────────────────────────────────────────────

def load_sbr_consensus(path: Path) -> dict[tuple, dict]:
    """Index consensus rows by (game_date, home_abbr, away_abbr)."""
    idx: dict[tuple, dict] = {}
    if not path.exists():
        print(f"WARNING: {path} not found. Run sbr_mlb_odds_fetcher.py first.")
        return idx
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("game_date", ""), row.get("home_abbr", ""), row.get("away_abbr", ""))
            if all(key):
                idx[key] = row
    return idx


def _as_float(v) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ── Join and validate ─────────────────────────────────────────────────────────

def build_validation_rows(
    cards: list[dict],
    sbr: dict[tuple, dict],
    calib: dict[str, float],
    years: list[int] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Join ML Core v1 cards to SBR consensus. Returns (matched_rows, unmatched_rows)."""
    matched: list[dict] = []
    unmatched: list[dict] = []

    for card in cards:
        gdate = card.get("game_date", "")
        if years and int(gdate[:4]) not in years:
            continue

        lane = classify_ml_core_lane(card)
        if lane is None or lane == "suppressed":
            continue

        # Determine team abbreviations for SBR key
        game_id = card.get("game_id", "")       # e.g. "KC@TB"
        team_abbr = card.get("team", "")         # e.g. "TB" (home team)
        opponent_abbr = card.get("opponent", "") # e.g. "KC" (away team)

        # Card home_away=home means: home_abbr=team, away_abbr=opponent
        sbr_key = (gdate, team_abbr, opponent_abbr)
        sbr_row = sbr.get(sbr_key)

        parsed = _parse_reasons(card.get("top_positive_reasons", ""))

        side_score = _as_float(card.get("side_score")) or 0.0
        actual_won = card.get("actual_team_won")
        actual_won_int = 1 if str(actual_won).strip() == "1" else (0 if str(actual_won).strip() == "0" else None)

        # Calibration lookup
        calib_prob = lookup_calib(calib, "side", side_score)

        # Lane historical probability (hardcoded from audit)
        lane_hist = 0.685 if lane == "core_home_opp_weak" else 0.617

        # SBR no-vig probabilities (for HOME team = team in this card)
        home_nv_avg = _as_float(sbr_row.get("home_no_vig_avg")) if sbr_row else None
        home_nv_open = _as_float(sbr_row.get("home_no_vig_open_avg")) if sbr_row else None
        away_nv_avg = _as_float(sbr_row.get("away_no_vig_avg")) if sbr_row else None
        away_nv_open = _as_float(sbr_row.get("away_no_vig_open_avg")) if sbr_row else None
        book_count = int(sbr_row.get("book_count", 0)) if sbr_row else 0

        # team_no_vig = home side (since card is always home team for ML Core v1)
        team_nv = home_nv_avg
        team_nv_open = home_nv_open

        # Market edge: brain calibrated prob - market no-vig prob (in pp)
        market_edge = None
        if calib_prob is not None and team_nv is not None:
            market_edge = round((calib_prob - team_nv) * 100, 2)

        # Actual outcome minus market implied (brier-style component)
        actual_minus_market = None
        if actual_won_int is not None and team_nv is not None:
            actual_minus_market = round(actual_won_int - team_nv, 4)

        # Implied ROI: if bet YES at (team_nv * 100) cents implied probability,
        # paying team_nv*100 cents per $1 contract:
        # ROI = (actual_won - team_nv) / team_nv * 100 -- approximate
        # This ignores real market vig. Observe-only.
        implied_roi = None
        if actual_won_int is not None and team_nv is not None and team_nv > 0:
            implied_roi = round((actual_won_int - team_nv) / team_nv * 100, 2)

        row = {
            "game_date": gdate,
            "season": gdate[:4],
            "game_id": game_id,
            "team": team_abbr,
            "opponent": opponent_abbr,
            "home_away": card.get("home_away", ""),
            "side_score": round(side_score, 4),
            "ml_core_lane": lane,
            "tag_weak_leader": parsed.get("tag_weak_leader_fade_watch", ""),
            "tag_live_rebound": parsed.get("tag_live_rebound_watch", ""),
            "opponent_strength_bucket": card.get("opponent_strength_bucket", "")
                                        or parsed.get("opponent_strength_bucket", ""),
            "actual_team_won": actual_won_int,
            "brain_calibrated_prob": round(calib_prob, 4) if calib_prob is not None else None,
            "lane_hist_prob": lane_hist,
            "sbr_home_no_vig_avg": round(home_nv_avg, 4) if home_nv_avg is not None else None,
            "sbr_away_no_vig_avg": round(away_nv_avg, 4) if away_nv_avg is not None else None,
            "sbr_home_no_vig_open_avg": round(home_nv_open, 4) if home_nv_open is not None else None,
            "sbr_away_no_vig_open_avg": round(away_nv_open, 4) if away_nv_open is not None else None,
            "sbr_book_count": book_count,
            "team_no_vig_avg": round(team_nv, 4) if team_nv is not None else None,
            "team_no_vig_open_avg": round(team_nv_open, 4) if team_nv_open is not None else None,
            "market_edge_pp": market_edge,
            "actual_minus_market": actual_minus_market,
            "implied_roi_pct": implied_roi,
        }

        if sbr_row:
            matched.append(row)
        else:
            unmatched.append(row)

    return matched, unmatched


# ── Report ────────────────────────────────────────────────────────────────────

def _rate(rows, field="actual_team_won") -> float | None:
    vals = [r[field] for r in rows if r.get(field) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _avg(rows, field) -> float | None:
    vals = [r[field] for r in rows if r.get(field) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _section(lines, rows, label, min_n=10):
    n = len(rows)
    graded = [r for r in rows if r.get("actual_team_won") is not None]
    sbr_rows = [r for r in rows if r.get("team_no_vig_avg") is not None]
    hit = _rate(graded) or 0
    mkt = _avg(sbr_rows, "team_no_vig_avg") or 0
    edge = round((hit - mkt) * 100, 2) if graded and sbr_rows else None
    mkt_open = _avg([r for r in sbr_rows if r.get("team_no_vig_open_avg") is not None], "team_no_vig_open_avg")
    lines.append(f"### {label}")
    lines.append(f"n={n}  graded={len(graded)}  sbr_matched={len(sbr_rows)}")
    if len(graded) >= min_n:
        lines.append(f"hit_rate={hit:.3f}  sbr_no_vig={mkt:.3f}  **actual_minus_mkt={edge:+.2f}pp**")
        if mkt_open:
            open_edge = round((hit - mkt_open) * 100, 2)
            lines.append(f"sbr_open_no_vig={mkt_open:.3f}  actual_minus_open={open_edge:+.2f}pp")
    else:
        lines.append(f"LOW SAMPLE (n<{min_n}) -- do not interpret")
    lines.append("")


def generate_report(
    matched: list[dict],
    unmatched: list[dict],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    total_core = len(matched) + len(unmatched)
    sbr_matched = len(matched)
    graded = [r for r in matched if r.get("actual_team_won") is not None]
    sbr_graded = [r for r in graded if r.get("team_no_vig_avg") is not None]

    lines = [
        "# Moneyline Core v1 Market Validation",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "Read-only research. No trades. No paper entries. Do not change ML Core v1.",
        "",
        "---",
        "",
        "## 1. Coverage",
        "",
        f"- Total ML Core v1 rows (home, side>=0.40, NOT suppressed): {total_core}",
        f"- Matched to SBR consensus odds: {sbr_matched} ({sbr_matched/max(total_core,1):.0%})",
        f"- Unmatched (no SBR odds found): {len(unmatched)}",
        f"- Graded rows (actual_team_won known): {len(graded)}",
        f"- Graded + SBR matched: {len(sbr_graded)}",
        "",
    ]

    # Section 2: Overall
    lines += ["## 2. Overall Moneyline Core v1 vs Market", ""]
    _section(lines, matched, "All ML Core v1 (home, side>=0.40, not suppressed)")

    # Section 3: Sub-lane split
    lines += ["## 3. Sub-Lane Split", ""]
    for lane_label in ("core_home_opp_weak", "core_home_standard"):
        subset = [r for r in matched if r.get("ml_core_lane") == lane_label]
        _section(lines, subset, lane_label)

    # Section 4: Edge buckets (brain vs market)
    lines += ["## 4. Brain Edge Bucket Split (brain_calib_prob - sbr_no_vig)", ""]
    edge_buckets = [
        ("edge_leq_0", lambda r: (r.get("market_edge_pp") or 0) <= 0),
        ("edge_0_to_2pp", lambda r: 0 < (r.get("market_edge_pp") or 0) <= 2),
        ("edge_2_to_5pp", lambda r: 2 < (r.get("market_edge_pp") or 0) <= 5),
        ("edge_5pp_plus", lambda r: (r.get("market_edge_pp") or 0) > 5),
    ]
    for label, fn in edge_buckets:
        subset = [r for r in matched if r.get("market_edge_pp") is not None and fn(r)]
        _section(lines, subset, label)

    # Section 5: Price buckets (market implied probability)
    lines += ["## 5. Market Implied Probability Buckets (SBR no-vig)", ""]
    price_buckets = [
        ("<50%", lambda r: (r.get("team_no_vig_avg") or 0) < 0.50),
        ("50-55%", lambda r: 0.50 <= (r.get("team_no_vig_avg") or 0) < 0.55),
        ("55-60%", lambda r: 0.55 <= (r.get("team_no_vig_avg") or 0) < 0.60),
        ("60-65%", lambda r: 0.60 <= (r.get("team_no_vig_avg") or 0) < 0.65),
        ("65-70%", lambda r: 0.65 <= (r.get("team_no_vig_avg") or 0) < 0.70),
        ("70%+", lambda r: (r.get("team_no_vig_avg") or 0) >= 0.70),
    ]
    for label, fn in price_buckets:
        subset = [r for r in matched if r.get("team_no_vig_avg") is not None and fn(r)]
        _section(lines, subset, label)

    # Section 6: Opening vs closing line movement
    lines += ["## 6. Opening vs Current Line Movement", ""]
    both = [r for r in matched if r.get("team_no_vig_avg") is not None
            and r.get("team_no_vig_open_avg") is not None]
    if both:
        moved_toward = [r for r in both if r["team_no_vig_avg"] > r["team_no_vig_open_avg"]]
        moved_away = [r for r in both if r["team_no_vig_avg"] < r["team_no_vig_open_avg"]]
        stable = [r for r in both if r["team_no_vig_avg"] == r["team_no_vig_open_avg"]]
        lines.append(f"Games with both open and current: n={len(both)}")
        lines.append(f"Market shortened (team implied rose from open to close): n={len(moved_toward)}")
        lines.append(f"Market lengthened (team implied fell from open to close): n={len(moved_away)}")
        lines.append(f"No movement: n={len(stable)}")
        lines.append("")
        if moved_toward:
            _section(lines, moved_toward, "Market moved TOWARD team (team shortened)")
        if moved_away:
            _section(lines, moved_away, "Market moved AWAY from team (team lengthened)")
    else:
        lines.append("Insufficient data for opening vs current comparison.")
        lines.append("")

    # Section 7: Season splits
    lines += ["## 7. Season Splits", ""]
    for yr in ("2023", "2024", "2025"):
        subset = [r for r in matched if r.get("season") == yr]
        _section(lines, subset, f"Season {yr}")

    # Section 8: Plain-English verdict
    lines += ["## 8. Plain-English Verdict", ""]
    overall_hit = _rate(graded)
    overall_mkt = _avg(sbr_graded, "team_no_vig_avg")
    if overall_hit is not None and overall_mkt is not None and len(sbr_graded) >= 20:
        diff = round((overall_hit - overall_mkt) * 100, 2)
        if diff >= 3:
            verdict = (
                f"ENCOURAGING (observe only): ML Core v1 shows {overall_hit:.1%} actual hit rate "
                f"vs {overall_mkt:.1%} market-implied ({diff:+.2f}pp above market). "
                f"Consistent if season splits agree. This warrants further investigation "
                f"but does NOT authorize trading until sample is larger and price data is verified."
            )
        elif diff >= 0:
            verdict = (
                f"INCONCLUSIVE: ML Core v1 hit rate ({overall_hit:.1%}) is marginally above "
                f"market implied ({overall_mkt:.1%}) by only {diff:+.2f}pp. "
                f"Insufficient to distinguish from noise. Observe only."
            )
        else:
            verdict = (
                f"NOT PROMISING: ML Core v1 hit rate ({overall_hit:.1%}) is BELOW "
                f"market implied ({overall_mkt:.1%}) by {abs(diff):.2f}pp. "
                f"The market was pricing these teams correctly or higher. "
                f"The lane is picking expensive favorites. Do not trade."
            )
        lines.append(verdict)
    else:
        lines.append("Insufficient matched+graded rows for a reliable verdict. Run full backfill first.")
    lines += [
        "",
        "**Interpretation rules:**",
        "- Hit rate alone means nothing. The question is: did we beat the market-implied probability?",
        "- A 63% hit rate is good if market implied 58%. It is not good if market implied 66%.",
        "- This report is observe-only. No model changes based on this alone.",
        "- Do not change Moneyline Core v1 thresholds without consistent multi-season market edge evidence.",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "moneyline_core_market_validation.md").write_text("\n".join(lines), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate ML Core v1 against SBR market odds. Read-only research."
    )
    parser.add_argument("--years", default="2023,2024,2025")
    args = parser.parse_args()
    years = [int(y.strip()) for y in args.years.split(",") if y.strip().isdigit()]

    calib = load_calibration(CALIB_CSV)
    sbr = load_sbr_consensus(CONSENSUS_CSV)

    if not CARDS_CSV.exists():
        print(f"ERROR: {CARDS_CSV} not found")
        return

    cards = []
    with open(CARDS_CSV, encoding="utf-8") as f:
        cards = list(csv.DictReader(f))

    print(f"\nML Core v1 Market Validation")
    print(f"  Cards: {len(cards)}  |  SBR consensus rows: {len(sbr)}  |  Years: {years}")

    matched, unmatched = build_validation_rows(cards, sbr, calib, years)

    write_csv(
        OUT_DIR / "moneyline_core_market_validation_rows.csv",
        matched + unmatched, _ROW_FIELDS,
    )
    generate_report(matched, unmatched, OUT_DIR)

    print(f"  ML Core rows: {len(matched)+len(unmatched)}")
    print(f"  SBR matched: {len(matched)}  unmatched: {len(unmatched)}")
    print(f"\nOutputs -> {OUT_DIR}/")
    print("  moneyline_core_market_validation.md")
    print("  moneyline_core_market_validation_rows.csv")


if __name__ == "__main__":
    main()
```

---

## Step 6 — Save fixture and run tests

```bash
# Save the fixture (one-time, needs network)
python -c "
import requests, json
from bs4 import BeautifulSoup
from pathlib import Path
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
resp = requests.get(
    'https://www.sportsbookreview.com/betting-odds/mlb-baseball/money-line/full-game/?date=2023-07-15',
    headers={'User-Agent': UA}
)
soup = BeautifulSoup(resp.text, 'lxml')
nd = json.loads(soup.find('script', {'id': '__NEXT_DATA__'}).string)
fixture = nd['props']['pageProps']['oddsTables']
Path('tests/fixtures').mkdir(exist_ok=True)
Path('tests/fixtures/sbr_next_data_sample.json').write_text(json.dumps(fixture), encoding='utf-8')
print('Fixture saved:', len(fixture), 'tables')
"

# Run tests
python -m pytest tests/test_sbr_odds_parser.py -v
```

Expected: all tests pass including the fixture-based test.

---

## Step 7 — Test fetch with limit-dates 5

```bash
python sbr_mlb_odds_fetcher.py --limit-dates 5 --sleep-seconds 1
```

**Manual verification checklist:**
- [ ] `outputs/sbr_mlb_odds/cache/` contains 5 `.html` files
- [ ] `sbr_moneyline_odds.csv` has rows with valid team names and American odds
- [ ] `sbr_moneyline_game_consensus.csv` has `home_no_vig_avg` values between 0.40 and 0.75
- [ ] `sbr_unmatched_games.csv` is small (only Athletics edge cases expected)
- [ ] No crash or empty rows

---

## Step 8 — Full 2023-2025 backfill

```bash
python sbr_mlb_odds_fetcher.py --years 2023,2024,2025 --sleep-seconds 3
```

Expected output: ~627 dates × ~13 games × 6 books ≈ ~49,000 book rows, ~8,100 consensus rows.
Estimated time: ~627 × 3s = ~31 minutes. Run overnight or while at desk.

---

## Step 9 — Run market validation

```bash
python sbr_moneyline_core_validation.py
```

Review `outputs/sbr_moneyline_core_validation/moneyline_core_market_validation.md` before drawing any conclusions.

---

## Quality Checks

- [ ] Every file path in the plan exists or is created by the plan
- [ ] Team mapping covers all 30 teams (including Athletics/OAK/ATH split)
- [ ] No-vig computation sums to 1.0 (verified by tests)
- [ ] American odds conversion handles both positive and negative inputs
- [ ] Fetcher caches HTML — reruns are fast
- [ ] Validation report uses observe-only language
- [ ] No trades, no paper entries, no model changes
- [ ] ML Core v1 threshold (0.40) is not modified anywhere
- [ ] All tests pass before proceeding to full backfill

---

## Execution Modes

**Inline Execution (recommended for this plan):** 10 steps, mostly sequential. Complete in current session.
Steps 1–6 are code + tests (< 30 min). Step 8 is a long network fetch (run in background).

**Sequence:**
```
Step 1: mkdir sbr + __init__.py  (1 min)
Step 2: sbr/odds_parser.py       (5 min)
Step 3: fixture + test file      (3 min)
Step 6: save fixture, run tests  (2 min + network)
Step 4: sbr_mlb_odds_fetcher.py  (5 min)
Step 7: test --limit-dates 5     (2 min + network)
Step 5: sbr_moneyline_core_validation.py  (5 min)
Step 8: full backfill (run in background, ~31 min)
Step 9: run validation           (< 1 min)
```
