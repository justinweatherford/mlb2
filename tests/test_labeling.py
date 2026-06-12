"""
Tests for the historical labeler.

Critical invariant: updates from game A must never contaminate labels for game B,
even when the transcript interleaves multiple live games.
"""
import pytest

from models import LabelSource, GameTimelineStatus
from labeling.game_grouper import extract_tagged_chunks, build_groups
from labeling.historical_labeler import build_timelines


# ---------------------------------------------------------------------------
# Helpers — build minimal parseable flattened Discord messages
# ---------------------------------------------------------------------------

def _gs(away, home, away_score, home_score, half, inning, game_pk=None, ticker=None):
    """
    Minimal flattened game-state message in the Discord feed format.
    - With game_pk: appends "gamePk XXXXX [• TICKER]" footer (normal case).
    - ticker-only (no game_pk): appends the raw ticker string as footer so
      the _TICKER_RE fallback can extract it.
    """
    hdr  = f"{away} @ {home} — {away_score}-{home_score}  ({half}{inning})"
    body = (
        f"Score{away_score}-{home_score}"
        f"Inning{half}{inning}"
        f"Kalshi YES{away} 50c {home} 50c"
        f"Outs1Count1-1Runners"
    )
    if game_pk:
        footer = f"gamePk {game_pk}"
        if ticker:
            footer += f" • {ticker}"
    elif ticker:
        footer = " " + ticker    # space ensures word-boundary detection works
    else:
        footer = ""
    return f"⚾ {hdr}{body}{footer}"


def _totals(away, home, away_score, home_score, half, inning,
            yes_cents=45, line=8.5, game_pk=None, ticker=None):
    """Minimal flattened totals message."""
    hdr    = f"{away} @ {home} — {away_score}-{home_score}  ({half}{inning})"
    prices = f"Over {line} : {yes_cents}¢/{100 - yes_cents}¢       o-{100 - yes_cents}¢"
    footer = ""
    if game_pk:
        footer = f"gamePk {game_pk}"
        if ticker:
            footer += f" • {ticker}"
    return f"⚾ {hdr}{prices}{footer}"


# ---------------------------------------------------------------------------
# extract_tagged_chunks
# ---------------------------------------------------------------------------

def test_extract_preserves_game_pk():
    msg = _gs("HOU", "LAA", 2, 3, "B", 10, game_pk="824022")
    chunks = extract_tagged_chunks(msg)
    assert len(chunks) == 1
    assert chunks[0].game_pk == "824022"


def test_extract_preserves_ticker():
    msg = _gs("HOU", "LAA", 2, 3, "B", 10,
              game_pk="824022", ticker="KXMLBGAME-26JUN102138HOULAA-HOU")
    chunks = extract_tagged_chunks(msg)
    assert chunks[0].ticker == "KXMLBGAME-26JUN102138HOULAA-HOU"


def test_extract_strips_footer_from_raw():
    msg = _gs("HOU", "LAA", 2, 3, "B", 10, game_pk="824022")
    chunks = extract_tagged_chunks(msg)
    assert "gamePk" not in chunks[0].raw


def test_extract_no_footer_chunk():
    msg = _gs("HOU", "LAA", 2, 3, "B", 10)   # no gamePk
    chunks = extract_tagged_chunks(msg)
    assert len(chunks) == 1
    assert chunks[0].game_pk is None


# ---------------------------------------------------------------------------
# build_groups — two-pass reconciliation
# ---------------------------------------------------------------------------

def test_groups_separate_distinct_games():
    """Two different games must produce two separate groups."""
    transcript = (
        _gs("MIL", "ATH", 2, 1, "T", 5, game_pk="111111") +
        _gs("CHC", "COL", 3, 5, "B", 9, game_pk="222222")
    )
    groups = build_groups(transcript)
    assert len(groups) == 2


def test_gs_chunk_promoted_to_pk_group():
    """
    A game-state chunk (no footer) followed by a totals chunk (has footer)
    for the same game must be merged into the pk-keyed group.
    """
    gs_chunk     = _gs("HOU", "LAA", 2, 3, "B", 10)               # no gamePk
    totals_chunk = _totals("HOU", "LAA", 2, 3, "B", 10,
                           game_pk="824022")                        # has gamePk

    transcript = gs_chunk + totals_chunk
    groups = build_groups(transcript)

    # Should be exactly ONE group (not two)
    assert len(groups) == 1
    key = list(groups.keys())[0]
    assert key == "pk:824022"
    # Both the game-state and totals updates are in the group
    assert len(groups[key]["updates"]) == 2


def test_fallback_to_game_id_when_no_pk():
    """When no gamePk is present, group key falls back to game_id."""
    transcript = (
        _gs("NYY", "BOS", 1, 2, "T", 6) +
        _gs("NYY", "BOS", 1, 3, "B", 6)
    )
    groups = build_groups(transcript)
    assert len(groups) == 1
    key = list(groups.keys())[0]
    assert key.startswith("game:")


def test_ticker_fallback():
    """Ticker is used as secondary key when gamePk absent."""
    msg = _gs("NYY", "BOS", 1, 2, "T", 6,
              game_pk=None, ticker="KXMLBGAME-TICKER123")
    groups = build_groups(msg)
    key = list(groups.keys())[0]
    assert key == "ticker:KXMLBGAME-TICKER123"


# ---------------------------------------------------------------------------
# CROSS-GAME CONTAMINATION TEST (core requirement)
# ---------------------------------------------------------------------------

def test_cross_game_contamination_blocked():
    """
    Transcript: MIL @ ATH (partial, T5) followed by CHC @ COL (terminal, B9
    with COL leading).

    The labeler must NOT use CHC @ COL's final score as MIL @ ATH's label.
    MIL @ ATH has no terminal update → UNRESOLVED / final_total=None.
    CHC @ COL has a terminal update → COMPLETE with its own final score.
    """
    # MIL @ ATH — top of 5th, early game, NOT terminal
    mil_msg = (
        _gs("MIL", "ATH", 2, 1, "T", 5) +           # no gamePk on gs chunk
        _totals("MIL", "ATH", 2, 1, "T", 5,
                game_pk="111111")                     # gamePk on totals chunk
    )

    # CHC @ COL — bottom of 9th, COL leads 5-3 → terminal (home win)
    chc_msg = (
        _gs("CHC", "COL", 3, 5, "B", 9) +
        _totals("CHC", "COL", 3, 5, "B", 9,
                game_pk="222222")
    )

    transcript = mil_msg + chc_msg
    timelines = build_timelines(transcript)

    assert len(timelines) == 2

    mil = next(t for t in timelines if t.game_id == "MIL@ATH")
    chc = next(t for t in timelines if t.game_id == "CHC@COL")

    # ── MIL @ ATH assertions ────────────────────────────────────────────────
    # T5 is not terminal — must be unresolved, NOT labeled from CHC data
    assert mil.label_source == LabelSource.UNRESOLVED, (
        "MIL@ATH has no terminal update and must not borrow CHC@COL's result"
    )
    assert mil.timeline_status == GameTimelineStatus.PARTIAL
    assert mil.final_total is None, (
        "MIL@ATH final_total must be None; CHC score 3+5=8 must not bleed in"
    )
    assert mil.final_away_score is None
    assert mil.final_home_score is None

    # ── CHC @ COL assertions ────────────────────────────────────────────────
    assert chc.label_source == LabelSource.TRANSCRIPT_FINAL
    assert chc.timeline_status == GameTimelineStatus.COMPLETE
    assert chc.final_total == 8   # 3 + 5
    assert chc.final_away_score == 3
    assert chc.final_home_score == 5
    assert chc.label_confidence >= 0.9


def test_same_teams_different_games_not_merged():
    """Two games with the same teams but different gamePks stay separate."""
    game1 = _gs("NYY", "BOS", 2, 1, "B", 9,
                game_pk="111111")   # terminal
    game2 = _gs("NYY", "BOS", 0, 0, "T", 3,
                game_pk="999999")   # early

    timelines = build_timelines(game1 + game2)
    assert len(timelines) == 2


# ---------------------------------------------------------------------------
# Timeline status and label confidence
# ---------------------------------------------------------------------------

def test_terminal_b9_home_leads_is_complete():
    msg = _gs("NYY", "BOS", 2, 4, "B", 9, game_pk="555555")
    timelines = build_timelines(msg)
    assert len(timelines) == 1
    t = timelines[0]
    assert t.timeline_status == GameTimelineStatus.COMPLETE
    assert t.label_source == LabelSource.TRANSCRIPT_FINAL
    assert t.label_confidence >= 0.90
    assert t.final_total == 6


def test_early_inning_is_partial():
    msg = _gs("NYY", "BOS", 1, 0, "T", 3, game_pk="555555")
    timelines = build_timelines(msg)
    t = timelines[0]
    assert t.timeline_status == GameTimelineStatus.PARTIAL
    assert t.label_source == LabelSource.UNRESOLVED
    assert t.final_total is None


def test_extra_innings_marked_complete():
    msg = _gs("NYY", "BOS", 3, 3, "B", 10, game_pk="555555")
    timelines = build_timelines(msg)
    t = timelines[0]
    assert t.timeline_status == GameTimelineStatus.COMPLETE
    assert t.label_confidence >= 0.80


def test_totals_only_group_is_terminal_only():
    """A group with only totals updates (no game-state) is TERMINAL_ONLY."""
    msg = _totals("HOU", "LAA", 5, 3, "B", 9, game_pk="777777")
    timelines = build_timelines(msg)
    t = timelines[0]
    assert t.timeline_status == GameTimelineStatus.TERMINAL_ONLY
    assert t.label_source == LabelSource.UNRESOLVED
    assert t.final_total is None


def test_multiple_updates_same_game_uses_last():
    """The last game-state (in transcript order) is used as the candidate final."""
    early = _gs("HOU", "LAA", 1, 0, "T", 3)        # early, no pk
    late  = _gs("HOU", "LAA", 2, 5, "B", 9,
                game_pk="824022")                   # terminal
    timelines = build_timelines(early + late)
    assert len(timelines) == 1
    t = timelines[0]
    assert t.timeline_status == GameTimelineStatus.COMPLETE
    assert t.final_total == 7   # 2+5, from the B9 update


def test_unresolved_game_has_no_final_score():
    """Partial game must expose None for all score fields."""
    msg = _gs("MIL", "ATH", 0, 0, "T", 1, game_pk="111111")
    t = build_timelines(msg)[0]
    assert t.final_total is None
    assert t.final_away_score is None
    assert t.final_home_score is None
    assert t.label_confidence == 0.0
