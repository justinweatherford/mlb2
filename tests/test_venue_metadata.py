"""
tests/test_venue_metadata.py — TDD for mlb/venue_metadata.py

Tests written BEFORE implementation.

No TAKE labels. No order placement. Context/evidence only.

Groups:
  TestCompleteness       — all 30 MLB teams present (canonical abbrs)
  TestAliasNormalization — CWS/CHW, TB/TBR, SF/SFG, KC/KCR, AZ/ARI, SD/SDP, WSH/WAS, ATH/OAK
  TestLatLon             — lat/lon present and plausible for all venues
  TestRoofType           — dome teams correctly marked
  TestTimezone           — tz field present and valid
  TestElevation          — elevation_ft present; Coors Field high
  TestVenueName          — venue_name field present
  TestResolveVenue       — resolve_venue() with alias normalization
  TestNoTakeLabels       — no trade terms
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mlb.venue_metadata import (
    MLB_VENUE_BY_ABBR,
    TEAM_ABBR_ALIASES,
    resolve_venue,
)

# Canonical 30-team abbreviations (primary abbrs used in DB)
CANONICAL_30 = {
    "NYY", "NYM", "BOS", "PHI", "ATL", "MIA", "WSH", "PIT", "CIN",
    "CHC", "CHW", "STL", "MIL", "MIN", "KCR", "DET", "CLE", "COL",
    "ARI", "SDP", "LAD", "LAA", "SEA", "OAK", "SFG", "HOU", "TEX",
    "TBR", "BAL", "TOR",
}

# Known dome/closed roof venues
DOME_TEAMS = {"TEX", "TBR", "TOR"}

# Retractable roof teams
RETRACTABLE_TEAMS = {"SEA", "HOU", "MIL", "MIA", "ARI"}


# ─────────────────────────────────────────────────────────────────────────────
# TestCompleteness
# ─────────────────────────────────────────────────────────────────────────────

class TestCompleteness:
    def test_all_30_canonical_abbrs_present(self):
        missing = CANONICAL_30 - set(MLB_VENUE_BY_ABBR.keys())
        assert missing == set(), f"Missing team abbrs: {missing}"

    def test_mlb_venue_by_abbr_is_dict(self):
        assert isinstance(MLB_VENUE_BY_ABBR, dict)

    def test_at_least_30_entries(self):
        # Including aliases
        assert len(MLB_VENUE_BY_ABBR) >= 30

    def test_each_entry_is_dict(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert isinstance(meta, dict), f"{abbr} value is not a dict"


# ─────────────────────────────────────────────────────────────────────────────
# TestAliasNormalization
# ─────────────────────────────────────────────────────────────────────────────

class TestAliasNormalization:
    def test_team_abbr_aliases_is_dict(self):
        assert isinstance(TEAM_ABBR_ALIASES, dict)

    def test_cws_alias_to_chw(self):
        assert TEAM_ABBR_ALIASES.get("CWS") == "CHW"

    def test_tb_alias_to_tbr(self):
        assert TEAM_ABBR_ALIASES.get("TB") == "TBR"

    def test_sf_alias_to_sfg(self):
        assert TEAM_ABBR_ALIASES.get("SF") == "SFG"

    def test_kc_alias_to_kcr(self):
        assert TEAM_ABBR_ALIASES.get("KC") == "KCR"

    def test_az_alias_to_ari(self):
        assert TEAM_ABBR_ALIASES.get("AZ") == "ARI"

    def test_sd_alias_to_sdp(self):
        assert TEAM_ABBR_ALIASES.get("SD") == "SDP"

    def test_wsh_alias_to_was_or_wsh(self):
        # WSH used by some sources → should resolve to WSH canonical
        alias_target = TEAM_ABBR_ALIASES.get("WSH")
        if alias_target:
            assert alias_target in MLB_VENUE_BY_ABBR
        else:
            assert "WSH" in MLB_VENUE_BY_ABBR

    def test_ath_alias_to_oak(self):
        assert TEAM_ABBR_ALIASES.get("ATH") == "OAK"

    def test_aliases_resolve_in_main_dict(self):
        for alias, canonical in TEAM_ABBR_ALIASES.items():
            assert canonical in MLB_VENUE_BY_ABBR, \
                f"Alias {alias}→{canonical} but {canonical} not in MLB_VENUE_BY_ABBR"


# ─────────────────────────────────────────────────────────────────────────────
# TestLatLon
# ─────────────────────────────────────────────────────────────────────────────

class TestLatLon:
    def test_all_entries_have_lat(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert "lat" in meta, f"{abbr} missing lat"
            assert meta["lat"] is not None, f"{abbr} lat is None"

    def test_all_entries_have_lon(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert "lon" in meta, f"{abbr} missing lon"
            assert meta["lon"] is not None, f"{abbr} lon is None"

    def test_us_venues_lat_in_range(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            if abbr == "TOR":
                continue  # Toronto is in Canada
            assert 25.0 < meta["lat"] < 50.0, \
                f"{abbr} lat={meta['lat']} out of US range"

    def test_us_venues_lon_in_range(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            if abbr == "TOR":
                continue
            assert -125.0 < meta["lon"] < -66.0, \
                f"{abbr} lon={meta['lon']} out of US range"

    def test_yankee_stadium_lat(self):
        assert abs(MLB_VENUE_BY_ABBR["NYY"]["lat"] - 40.8296) < 0.1

    def test_coors_field_lon(self):
        assert abs(MLB_VENUE_BY_ABBR["COL"]["lon"] - (-104.9942)) < 0.1

    def test_dodger_stadium_lat(self):
        assert abs(MLB_VENUE_BY_ABBR["LAD"]["lat"] - 34.07) < 0.1


# ─────────────────────────────────────────────────────────────────────────────
# TestRoofType
# ─────────────────────────────────────────────────────────────────────────────

class TestRoofType:
    def test_all_entries_have_roof_type(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert "roof_type" in meta, f"{abbr} missing roof_type"

    def test_roof_type_valid_values(self):
        valid = {"outdoor", "dome", "retractable", "unknown"}
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert meta["roof_type"] in valid, \
                f"{abbr} roof_type={meta['roof_type']} not in {valid}"

    def test_tex_is_dome(self):
        assert MLB_VENUE_BY_ABBR["TEX"]["roof_type"] == "dome"

    def test_tbr_is_dome(self):
        assert MLB_VENUE_BY_ABBR["TBR"]["roof_type"] == "dome"

    def test_tor_is_dome(self):
        assert MLB_VENUE_BY_ABBR["TOR"]["roof_type"] == "dome"

    def test_sea_is_retractable(self):
        assert MLB_VENUE_BY_ABBR["SEA"]["roof_type"] == "retractable"

    def test_hou_is_retractable(self):
        assert MLB_VENUE_BY_ABBR["HOU"]["roof_type"] == "retractable"

    def test_nyy_is_outdoor(self):
        assert MLB_VENUE_BY_ABBR["NYY"]["roof_type"] == "outdoor"

    def test_col_is_outdoor(self):
        assert MLB_VENUE_BY_ABBR["COL"]["roof_type"] == "outdoor"


# ─────────────────────────────────────────────────────────────────────────────
# TestTimezone
# ─────────────────────────────────────────────────────────────────────────────

class TestTimezone:
    def test_all_entries_have_tz(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert "tz" in meta, f"{abbr} missing tz"
            assert meta["tz"] is not None, f"{abbr} tz is None"

    def test_tz_is_string(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert isinstance(meta["tz"], str), f"{abbr} tz is not string"

    def test_nyy_is_eastern(self):
        assert "New_York" in MLB_VENUE_BY_ABBR["NYY"]["tz"]

    def test_lad_is_pacific(self):
        assert "Los_Angeles" in MLB_VENUE_BY_ABBR["LAD"]["tz"]

    def test_chc_is_central(self):
        assert "Chicago" in MLB_VENUE_BY_ABBR["CHC"]["tz"]

    def test_col_is_mountain(self):
        assert "Denver" in MLB_VENUE_BY_ABBR["COL"]["tz"]

    def test_tz_parseable_by_zoneinfo(self):
        from zoneinfo import ZoneInfo
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            try:
                ZoneInfo(meta["tz"])
            except Exception as e:
                raise AssertionError(f"{abbr} tz={meta['tz']} not parseable: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# TestElevation
# ─────────────────────────────────────────────────────────────────────────────

class TestElevation:
    def test_all_entries_have_elevation_ft(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert "elevation_ft" in meta, f"{abbr} missing elevation_ft"

    def test_elevation_ft_is_numeric(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert isinstance(meta["elevation_ft"], (int, float)), \
                f"{abbr} elevation_ft is not numeric"

    def test_elevation_ft_non_negative(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert meta["elevation_ft"] >= 0, \
                f"{abbr} elevation_ft={meta['elevation_ft']} is negative"

    def test_coors_field_high_elevation(self):
        assert MLB_VENUE_BY_ABBR["COL"]["elevation_ft"] >= 5000

    def test_chase_field_moderate_elevation(self):
        assert MLB_VENUE_BY_ABBR["ARI"]["elevation_ft"] > 1000


# ─────────────────────────────────────────────────────────────────────────────
# TestVenueName
# ─────────────────────────────────────────────────────────────────────────────

class TestVenueName:
    def test_all_entries_have_venue_name(self):
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            assert "venue_name" in meta, f"{abbr} missing venue_name"
            assert meta["venue_name"], f"{abbr} venue_name is empty"

    def test_nyy_venue_name(self):
        assert "Yankee" in MLB_VENUE_BY_ABBR["NYY"]["venue_name"]

    def test_col_venue_name(self):
        assert "Coors" in MLB_VENUE_BY_ABBR["COL"]["venue_name"]

    def test_tbr_venue_name(self):
        assert "Tropicana" in MLB_VENUE_BY_ABBR["TBR"]["venue_name"]


# ─────────────────────────────────────────────────────────────────────────────
# TestResolveVenue
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveVenue:
    def test_resolve_canonical_abbr(self):
        result = resolve_venue("NYY")
        assert result is not None
        assert "lat" in result

    def test_resolve_cws_alias(self):
        result = resolve_venue("CWS")
        assert result is not None
        assert result["venue_name"] == MLB_VENUE_BY_ABBR["CHW"]["venue_name"]

    def test_resolve_tb_alias(self):
        result = resolve_venue("TB")
        assert result is not None
        assert result["roof_type"] == "dome"

    def test_resolve_sf_alias(self):
        result = resolve_venue("SF")
        assert result is not None
        assert "Oracle" in result["venue_name"] or "Park" in result["venue_name"]

    def test_resolve_kc_alias(self):
        result = resolve_venue("KC")
        assert result is not None
        assert "Kauffman" in result["venue_name"]

    def test_resolve_az_alias(self):
        result = resolve_venue("AZ")
        assert result is not None
        assert result["roof_type"] == "retractable"

    def test_resolve_sd_alias(self):
        result = resolve_venue("SD")
        assert result is not None
        assert "Petco" in result["venue_name"]

    def test_resolve_ath_alias(self):
        result = resolve_venue("ATH")
        assert result is not None

    def test_resolve_unknown_returns_none(self):
        assert resolve_venue("XYZ") is None

    def test_resolve_empty_returns_none(self):
        assert resolve_venue("") is None

    def test_resolve_none_returns_none(self):
        assert resolve_venue(None) is None

    def test_resolve_returns_dict_with_lat_lon(self):
        result = resolve_venue("BOS")
        assert result is not None
        assert "lat" in result and "lon" in result

    def test_resolve_case_insensitive_not_required(self):
        # Lowercase should NOT match (DB abbrs are uppercase)
        result = resolve_venue("nyy")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# TestNoTakeLabels
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTakeLabels:
    def test_no_order_placement_in_source(self):
        import mlb.venue_metadata as m
        src = inspect.getsource(m)
        assert "place_order" not in src.lower()
        assert "execute_trade" not in src.lower()

    def test_venue_data_no_trade_keys(self):
        trade_keys = {"take", "buy", "sell", "order", "execute"}
        for abbr, meta in MLB_VENUE_BY_ABBR.items():
            for key in meta:
                assert key.lower() not in trade_keys


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    sys.exit(result.returncode)
