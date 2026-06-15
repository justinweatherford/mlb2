"""
mlb/venue_metadata.py — MLB venue registry for weather auto-fetch.

Keyed by home_abbr (canonical abbreviations used in the local DB).
Includes lat/lon for Open-Meteo queries, elevation_ft for WRE scoring,
roof_type for dome detection, and tz for local→UTC game time conversion.

No TAKE labels. No order placement. No candidate generation changes.
"""
from __future__ import annotations

from typing import Optional

# ── Canonical venue data (30 MLB teams + common aliases) ─────────────────────
# roof_type: outdoor / dome / retractable / unknown

MLB_VENUE_BY_ABBR: dict[str, dict] = {
    # AL East
    "NYY": {"venue_name": "Yankee Stadium",           "lat": 40.8296,  "lon": -73.9262,  "elevation_ft": 55,   "roof_type": "outdoor",     "tz": "America/New_York"},
    "BOS": {"venue_name": "Fenway Park",               "lat": 42.3467,  "lon": -71.0972,  "elevation_ft": 20,   "roof_type": "outdoor",     "tz": "America/New_York"},
    "TBR": {"venue_name": "Tropicana Field",           "lat": 27.7683,  "lon": -82.6534,  "elevation_ft": 15,   "roof_type": "dome",        "tz": "America/New_York"},
    "TOR": {"venue_name": "Rogers Centre",             "lat": 43.6414,  "lon": -79.3894,  "elevation_ft": 300,  "roof_type": "dome",        "tz": "America/Toronto"},
    "BAL": {"venue_name": "Camden Yards",              "lat": 39.2838,  "lon": -76.6216,  "elevation_ft": 21,   "roof_type": "outdoor",     "tz": "America/New_York"},
    # AL Central
    "CHW": {"venue_name": "Guaranteed Rate Field",     "lat": 41.8300,  "lon": -87.6339,  "elevation_ft": 595,  "roof_type": "outdoor",     "tz": "America/Chicago"},
    "CLE": {"venue_name": "Progressive Field",         "lat": 41.4962,  "lon": -81.6852,  "elevation_ft": 650,  "roof_type": "outdoor",     "tz": "America/New_York"},
    "DET": {"venue_name": "Comerica Park",             "lat": 42.3390,  "lon": -83.0485,  "elevation_ft": 585,  "roof_type": "outdoor",     "tz": "America/Detroit"},
    "KCR": {"venue_name": "Kauffman Stadium",          "lat": 39.0517,  "lon": -94.4803,  "elevation_ft": 750,  "roof_type": "outdoor",     "tz": "America/Chicago"},
    "MIN": {"venue_name": "Target Field",              "lat": 44.9817,  "lon": -93.2786,  "elevation_ft": 815,  "roof_type": "outdoor",     "tz": "America/Chicago"},
    # AL West
    "HOU": {"venue_name": "Minute Maid Park",          "lat": 29.7572,  "lon": -95.3552,  "elevation_ft": 22,   "roof_type": "retractable", "tz": "America/Chicago"},
    "LAA": {"venue_name": "Angel Stadium",             "lat": 33.8003,  "lon": -117.8827, "elevation_ft": 160,  "roof_type": "outdoor",     "tz": "America/Los_Angeles"},
    "OAK": {"venue_name": "Oakland Coliseum",          "lat": 37.7516,  "lon": -122.2005, "elevation_ft": 25,   "roof_type": "outdoor",     "tz": "America/Los_Angeles"},
    "SEA": {"venue_name": "T-Mobile Park",             "lat": 47.5914,  "lon": -122.3325, "elevation_ft": 17,   "roof_type": "retractable", "tz": "America/Los_Angeles"},
    "TEX": {"venue_name": "Globe Life Field",          "lat": 32.7473,  "lon": -97.0845,  "elevation_ft": 571,  "roof_type": "dome",        "tz": "America/Chicago"},
    # NL East
    "ATL": {"venue_name": "Truist Park",               "lat": 33.8903,  "lon": -84.4677,  "elevation_ft": 1050, "roof_type": "outdoor",     "tz": "America/New_York"},
    "MIA": {"venue_name": "loanDepot park",            "lat": 25.7781,  "lon": -80.2196,  "elevation_ft": 6,    "roof_type": "retractable", "tz": "America/New_York"},
    "NYM": {"venue_name": "Citi Field",                "lat": 40.7571,  "lon": -73.8458,  "elevation_ft": 20,   "roof_type": "outdoor",     "tz": "America/New_York"},
    "PHI": {"venue_name": "Citizens Bank Park",        "lat": 39.9057,  "lon": -75.1665,  "elevation_ft": 40,   "roof_type": "outdoor",     "tz": "America/New_York"},
    "WSH": {"venue_name": "Nationals Park",            "lat": 38.8730,  "lon": -77.0074,  "elevation_ft": 0,    "roof_type": "outdoor",     "tz": "America/New_York"},
    # NL Central
    "CHC": {"venue_name": "Wrigley Field",             "lat": 41.9484,  "lon": -87.6553,  "elevation_ft": 595,  "roof_type": "outdoor",     "tz": "America/Chicago"},
    "CIN": {"venue_name": "Great American Ball Park",  "lat": 39.0975,  "lon": -84.5069,  "elevation_ft": 490,  "roof_type": "outdoor",     "tz": "America/New_York"},
    "MIL": {"venue_name": "American Family Field",     "lat": 43.0280,  "lon": -87.9712,  "elevation_ft": 860,  "roof_type": "retractable", "tz": "America/Chicago"},
    "PIT": {"venue_name": "PNC Park",                  "lat": 40.4469,  "lon": -80.0057,  "elevation_ft": 730,  "roof_type": "outdoor",     "tz": "America/New_York"},
    "STL": {"venue_name": "Busch Stadium",             "lat": 38.6226,  "lon": -90.1928,  "elevation_ft": 465,  "roof_type": "outdoor",     "tz": "America/Chicago"},
    # NL West
    "ARI": {"venue_name": "Chase Field",               "lat": 33.4453,  "lon": -112.0667, "elevation_ft": 1082, "roof_type": "retractable", "tz": "America/Phoenix"},
    "COL": {"venue_name": "Coors Field",               "lat": 39.7559,  "lon": -104.9942, "elevation_ft": 5200, "roof_type": "outdoor",     "tz": "America/Denver"},
    "LAD": {"venue_name": "Dodger Stadium",            "lat": 34.0739,  "lon": -118.2400, "elevation_ft": 512,  "roof_type": "outdoor",     "tz": "America/Los_Angeles"},
    "SDP": {"venue_name": "Petco Park",                "lat": 32.7076,  "lon": -117.1570, "elevation_ft": 20,   "roof_type": "outdoor",     "tz": "America/Los_Angeles"},
    "SFG": {"venue_name": "Oracle Park",               "lat": 37.7786,  "lon": -122.3893, "elevation_ft": 0,    "roof_type": "outdoor",     "tz": "America/Los_Angeles"},
}

# ── Common abbreviation aliases ────────────────────────────────────────────────
# Maps alternate abbreviations → canonical abbreviation in MLB_VENUE_BY_ABBR

TEAM_ABBR_ALIASES: dict[str, str] = {
    "CWS": "CHW",   # Chicago White Sox
    "TB":  "TBR",   # Tampa Bay Rays
    "SF":  "SFG",   # San Francisco Giants
    "KC":  "KCR",   # Kansas City Royals
    "AZ":  "ARI",   # Arizona Diamondbacks
    "SD":  "SDP",   # San Diego Padres
    "WSH": "WSH",   # Washington Nationals (some sources use WSH; point to itself)
    "WAS": "WSH",   # Washington Nationals alternate
    "WSN": "WSH",   # Washington Nationals (Statcast/Fangraphs abbreviation)
    "ATH": "OAK",   # Athletics (relocated branding)
    "LA":  "LAD",   # Los Angeles Dodgers
}


# ── Lookup function ────────────────────────────────────────────────────────────

def resolve_venue(home_abbr: Optional[str]) -> Optional[dict]:
    """
    Return venue metadata for home_abbr, resolving aliases.
    Returns None for unknown or empty abbreviations.

    No TAKE labels. No order placement.
    """
    if not home_abbr:
        return None
    # Try direct lookup first
    meta = MLB_VENUE_BY_ABBR.get(home_abbr)
    if meta is not None:
        return meta
    # Try alias
    canonical = TEAM_ABBR_ALIASES.get(home_abbr)
    if canonical:
        return MLB_VENUE_BY_ABBR.get(canonical)
    return None
