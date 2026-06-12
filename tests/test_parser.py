"""
Parser tests — includes the real pasted sample from the user so we validate
against actual feed data, not invented fixtures.
"""
import pytest
from datetime import datetime

from parser.common import parse_header, extract_kv, is_game_state_message, is_totals_message
from parser.game_state_parser import parse_game_state
from parser.totals_parser import parse_totals
from parser.router import route_message
from models import ParsedGameState, ParsedTotalsUpdate

# ---------------------------------------------------------------------------
# Real sample from the user's Discord paste (newlines stripped by Discord)
# ---------------------------------------------------------------------------
REAL_PASTE = (
    "Run: HOU @ LAA 2-3 (B10) -- Jose Siri singles on a sharp line drive to left fielder "
    "Joey Loperfido. Nick Madrigal scores. Donovan Walton to 3rd @here"
    "⚾ HOU @ LAA — 2-3  (B10)"
    "Score2-3InningB10Kalshi YESHOU 0c LAA 99cOuts0Count0-2Runners1B • 3B"
    "ScoredNick MadrigalKalshi lead+2.93 sPitchFF · 97.8mph · zone 3"
    "HitEV 104.2 · LA 11.0 · dist 193ft · line drive"
    "PlayJose Siri singles on a sharp line drive to left fielder Joey Loperfido. "
    "Nick Madrigal scores. Donovan Walton to 3rd."
    "⚾ HOU @ LAA — 2-3  (B10)"
    "Over  5.5 : —/1¢       o-2¢"
    "Over  6.5 : —/1¢       o-2¢"
    "Over  7.5 : —/1¢       o-2¢"
    "Over  8.5 : —/1¢       o-2¢"
    "Over  9.5 : —/1¢       o-2¢"
    "Over 10.5 : —/1¢       o-2¢"
    "Over 11.5 : —/1¢       o-2¢"
    "Over 12.5 : —/1¢       o-2¢"
    "Over 13.5 : —       "
    "Over 14.5 : —       "
    "gamePk 824022 • KXMLBGAME-26JUN102138HOULAA-HOU•Today at 12:09 AM"
)

# Isolated game-state and totals blocks for focused tests
GAME_STATE_FLATTENED = (
    "⚾ HOU @ LAA — 2-3  (B10)"
    "Score2-3InningB10Kalshi YESHOU 0c LAA 99cOuts0Count0-2Runners1B • 3B"
    "ScoredNick MadrigalKalshi lead+2.93 sPitchFF · 97.8mph · zone 3"
    "HitEV 104.2 · LA 11.0 · dist 193ft · line drive"
    "PlayJose Siri singles on a sharp line drive to left fielder Joey Loperfido. "
    "Nick Madrigal scores. Donovan Walton to 3rd."
)

TOTALS_FLATTENED = (
    "⚾ HOU @ LAA — 2-3  (B10)"
    "Over  5.5 : —/1¢       o-2¢"
    "Over  6.5 : —/1¢       o-2¢"
    "Over  8.5 : —/1¢       o-2¢"
    "Over 13.5 : —       "
    "gamePk 824022 • KXMLBGAME-26JUN102138HOULAA-HOU"
)

# Newline-separated format (as received by a live Discord bot)
GAME_STATE_NEWLINES = """⚾ HOU @ LAA — 2-3  (B10)
Score
2-3
Inning
B10
Kalshi YES
HOU 0c LAA 99c
Outs
0
Count
0-2
Runners
1B • 3B
Scored
Nick Madrigal
Kalshi lead
+2.93 s
Pitch
FF · 97.8mph · zone 3
Hit
EV 104.2 · LA 11.0 · dist 193ft · line drive
Play
Jose Siri singles on a sharp line drive to left fielder Joey Loperfido. Nick Madrigal scores."""

NOW = datetime.utcnow()


# ---------------------------------------------------------------------------
# Header parser
# ---------------------------------------------------------------------------

def test_parse_header_standard():
    h = parse_header("⚾ HOU @ LAA — 2-3  (B10)")
    assert h["away_team"] == "HOU"
    assert h["home_team"] == "LAA"
    assert h["away_score"] == 2
    assert h["home_score"] == 3
    assert h["inning_half"] == "B"
    assert h["inning_number"] == 10
    assert h["game_id"] == "HOU@LAA"


def test_parse_header_top_inning():
    h = parse_header("⚾ NYY @ BOS — 0-0  (T1)")
    assert h["inning_half"] == "T"
    assert h["inning_number"] == 1


def test_parse_header_invalid_raises():
    with pytest.raises(ValueError):
        parse_header("not a valid header")


# ---------------------------------------------------------------------------
# Message type detection
# ---------------------------------------------------------------------------

def test_is_game_state():
    assert is_game_state_message(GAME_STATE_FLATTENED) is True
    assert is_totals_message(GAME_STATE_FLATTENED) is False


def test_is_totals():
    assert is_totals_message(TOTALS_FLATTENED) is True


# ---------------------------------------------------------------------------
# KV extractor
# ---------------------------------------------------------------------------

def test_extract_kv_flattened():
    h = parse_header(GAME_STATE_FLATTENED)
    body = GAME_STATE_FLATTENED[h["_header_end"]:]
    kv = extract_kv(body)
    assert kv["Score"] == "2-3"
    assert kv["Inning"] == "B10"
    assert "HOU 0c LAA 99c" in kv["Kalshi YES"]
    assert kv["Outs"] == "0"
    assert kv["Count"] == "0-2"
    assert "1B" in kv["Runners"]
    assert kv["Scored"] == "Nick Madrigal"
    assert "+2.93 s" in kv["Kalshi lead"]
    assert "97.8mph" in kv["Pitch"]
    assert "104.2" in kv["Hit"]
    assert "singles" in kv["Play"]


def test_extract_kv_newlines():
    h = parse_header(GAME_STATE_NEWLINES)
    body = GAME_STATE_NEWLINES[h["_header_end"]:]
    kv = extract_kv(body)
    assert kv["Score"] == "2-3"
    assert kv["Outs"] == "0"
    assert kv["Scored"] == "Nick Madrigal"


# ---------------------------------------------------------------------------
# Game state parser — flattened
# ---------------------------------------------------------------------------

def test_game_state_flattened_core_fields():
    gs = parse_game_state(GAME_STATE_FLATTENED, NOW)
    assert gs.game_id == "HOU@LAA"
    assert gs.away_score == 2
    assert gs.home_score == 3
    assert gs.inning_half == "B"
    assert gs.inning_number == 10
    assert gs.outs == 0
    assert gs.count == "0-2"


def test_game_state_flattened_runners():
    gs = parse_game_state(GAME_STATE_FLATTENED, NOW)
    assert "1B" in gs.runners
    assert "3B" in gs.runners


def test_game_state_flattened_kalshi():
    gs = parse_game_state(GAME_STATE_FLATTENED, NOW)
    assert gs.kalshi_yes_prices == {"HOU": 0, "LAA": 99}
    assert gs.kalshi_lead_seconds == pytest.approx(2.93)


def test_game_state_flattened_pitch():
    gs = parse_game_state(GAME_STATE_FLATTENED, NOW)
    assert gs.pitch_type == "FF"
    assert gs.pitch_velocity == pytest.approx(97.8)
    assert gs.pitch_zone == 3


def test_game_state_flattened_hit():
    gs = parse_game_state(GAME_STATE_FLATTENED, NOW)
    assert gs.exit_velocity == pytest.approx(104.2)
    assert gs.launch_angle == pytest.approx(11.0)
    assert gs.hit_distance == pytest.approx(193.0)
    assert gs.hit_type == "line drive"


def test_game_state_flattened_play():
    gs = parse_game_state(GAME_STATE_FLATTENED, NOW)
    assert gs.scored_player == "Nick Madrigal"
    assert "singles" in gs.play_description


def test_game_state_newlines():
    gs = parse_game_state(GAME_STATE_NEWLINES, NOW)
    assert gs.away_score == 2
    assert gs.home_score == 3
    assert gs.kalshi_yes_prices == {"HOU": 0, "LAA": 99}
    assert gs.scored_player == "Nick Madrigal"


# ---------------------------------------------------------------------------
# Totals parser
# ---------------------------------------------------------------------------

def test_totals_flattened_line_count():
    tu = parse_totals(TOTALS_FLATTENED, NOW)
    # Lines 5.5, 6.5, 8.5, 13.5, 14.5 — but we only have 5.5/6.5/8.5/13.5 in fixture
    assert len(tu.totals_lines) >= 3


def test_totals_flattened_header():
    tu = parse_totals(TOTALS_FLATTENED, NOW)
    assert tu.game_id == "HOU@LAA"
    assert tu.away_score == 2
    assert tu.home_score == 3


def test_totals_line_prices():
    tu = parse_totals(TOTALS_FLATTENED, NOW)
    line_55 = next(tl for tl in tu.totals_lines if tl.line == 5.5)
    # Over  5.5 : —/1¢       o-2¢
    assert line_55.over_bid_cents is None    # em dash = no bid
    assert line_55.over_ask_cents == 1
    assert line_55.movement_side == "o"
    assert line_55.movement_delta_cents == -2
    assert line_55.yes_price_cents == 1      # derived from over_ask
    assert line_55.price_confidence == "ask_only"


def test_totals_line_no_price():
    tu = parse_totals(TOTALS_FLATTENED, NOW)
    line_135 = next((tl for tl in tu.totals_lines if tl.line == 13.5), None)
    assert line_135 is not None
    assert line_135.over_bid_cents is None
    assert line_135.over_ask_cents is None
    assert line_135.yes_price_cents is None
    assert line_135.price_confidence == "no_price"


def test_totals_full_bid_ask():
    raw = (
        "⚾ HOU @ LAA — 5-3  (B9)"
        "Over  8.5 : 52/63¢ o+21¢"
        "Over 11.5 : 1/3¢ u+10¢"
    )
    tu = parse_totals(raw, NOW)

    line_85 = next(tl for tl in tu.totals_lines if tl.line == 8.5)
    assert line_85.over_bid_cents == 52
    assert line_85.over_ask_cents == 63
    assert line_85.movement_side == "o"
    assert line_85.movement_delta_cents == 21
    assert line_85.yes_price_cents == 63     # derived from over_ask
    assert line_85.price_confidence == "full"
    assert "52/63" in line_85.raw_price_text

    line_115 = next(tl for tl in tu.totals_lines if tl.line == 11.5)
    assert line_115.over_bid_cents == 1
    assert line_115.over_ask_cents == 3
    assert line_115.movement_side == "u"
    assert line_115.movement_delta_cents == 10
    assert line_115.price_confidence == "full"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def test_router_detects_game_state():
    result = route_message(GAME_STATE_FLATTENED, NOW)
    assert isinstance(result, ParsedGameState)


def test_router_detects_totals():
    result = route_message(TOTALS_FLATTENED, NOW)
    assert isinstance(result, ParsedTotalsUpdate)


def test_router_returns_none_for_noise():
    result = route_message("hello world nothing to see here", NOW)
    assert result is None


def test_router_returns_none_for_empty():
    assert route_message("", NOW) is None
