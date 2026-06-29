"""
sbr/odds_parser.py -- Pure stateless utilities for SportsbookReview MLB odds parsing.

No I/O. All functions take dicts/strings, return dicts/strings/floats.
"""
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

# ── Team name normalization ────────────────────────────────────────────────────

_TEAM_NORMALIZE: dict[str, str] = {
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
    "Athletics": "Athletics",
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
    "Athletics": "ATH",
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

_ABBR_TO_FULL: dict[str, str] = {v: k for k, v in _FULL_TO_ABBR.items()}


def normalize_team_name(name: str) -> str:
    return _TEAM_NORMALIZE.get(name, name)


def team_full_to_abbr(full_name: str) -> Optional[str]:
    return _FULL_TO_ABBR.get(normalize_team_name(full_name))


def team_abbr_to_full(abbr: str) -> Optional[str]:
    return _ABBR_TO_FULL.get(abbr)


# ── Odds conversion ────────────────────────────────────────────────────────────

def american_to_implied(odds: int) -> float:
    if odds >= 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def no_vig_normalize(home_implied: float, away_implied: float) -> tuple[float, float]:
    total = home_implied + away_implied
    if total <= 0:
        return 0.5, 0.5
    return home_implied / total, away_implied / total


def implied_to_american(prob: float) -> int:
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
    """Parse SBR __NEXT_DATA__ embedded JSON. Returns one dict per game/sportsbook pair."""
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

            away_imp_c = american_to_implied(int(away_curr)) if away_curr is not None else None
            home_imp_c = american_to_implied(int(home_curr)) if home_curr is not None else None
            if away_imp_c is not None and home_imp_c is not None:
                home_nv_c, away_nv_c = no_vig_normalize(home_imp_c, away_imp_c)
            else:
                home_nv_c = away_nv_c = None

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
    """Compute consensus no-vig probabilities from all sportsbook rows for one game."""
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
