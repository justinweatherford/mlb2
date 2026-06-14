"""
mlb/candidate_generator.py — Observation-only live candidate generation.

Generates candidate_events rows from DB state. No paper positions are opened
and no trades are placed. All inserted candidates default to eligible_for_paper=0.

Candidate types:
  full_game_total_extreme_reprice_watch  — full-game total moved sharply after scoring
  f5_total_overreaction_fade_watch       — early scoring overpriced the F5 market
  trailing_team_total_lag_watch          — trailing team's team total may be underpriced
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from mlb.candidates import upsert_candidate_event
from mlb.guardrails import check_all
from mlb.price_utils import compute_price_baseline

log = logging.getLogger(__name__)


# ── Diagnostic result type ────────────────────────────────────────────────────

@dataclass
class GameDiag:
    """Diagnostics for one game's candidate-generation pass."""
    ids: list[int] = field(default_factory=list)
    rules_evaluated: int = 0     # times check_all() was called
    blocked: int = 0             # guardrail-blocked insertions
    dedupe_skipped: int = 0      # same-setup rows suppressed by dedup

    # Keyed by reason string; counts pre-insertion skips (nothing written to DB).
    skip_reasons: dict[str, int] = field(default_factory=dict)

    # ── List-compatible interface so existing callers work unchanged ──────────
    def __iter__(self):
        return iter(self.ids)

    def __len__(self) -> int:
        return len(self.ids)

    def __bool__(self) -> bool:
        return bool(self.ids)

    def __getitem__(self, i):
        return self.ids[i]

    def __eq__(self, other):
        if isinstance(other, list):
            return self.ids == other
        return NotImplemented


# ── Trigger thresholds ────────────────────────────────────────────────────────

# Minimum cents the full-game mid-price must move from open (or neutral 50) to trigger
_REPRICE_TRIGGER_CENTS  = 8

# F5 market mid must exceed this to suggest overpricing after early scoring
_F5_OVER_MID_THRESHOLD  = 55

# Minimum run deficit for trailing-team-total watch
_TRAILING_RUN_THRESHOLD = 2

# Maximum inning to still generate an F5 candidate (handled by guardrail too, belt+suspenders)
_F5_MAX_INNING          = 4

# Maximum inning to generate a trailing-team-total candidate
_TRAILING_MAX_INNING    = 6


# ── Scoring constants ─────────────────────────────────────────────────────────
# All scores are 0–100 (higher = stronger observation signal).

# market_mismatch_score:
#   Points = min(100, abs(mid - open_price) * _REPRICE_PTS_PER_CENT)
#   25c of movement from open → 100 pts; 8c (trigger) → 32 pts
_REPRICE_PTS_PER_CENT = 4.0

# baseball_support_score adjustments applied to a 50-pt neutral baseline:
#   fluky events (error, WP, PB) boost confidence that the move may fade
#   hard contact (HR, barrel-like) penalises confidence in a fade
_FLUKY_RUN_BOOST     = 20    # error / wild-pitch / passed-ball scoring play
_FLUKY_WALK_BOOST    = 10    # walk-heavy scoring play
_HARD_CONTACT_PENALTY = 25   # home run or barrel-like contact in scoring play

# execution_quality_score: linear interpolation
#   2c spread → 100 pts (perfect)
#   12c spread → 0 pts (at hard-block threshold)
_EXEC_PERFECT_SPREAD = 2
_EXEC_ZERO_SPREAD    = 12

# risk_blocker_score: additive risk flags (higher = riskier)
#   Home run in scoring play: strong evidence against fade
#   Spread in warn zone (8–12c): liquidity risk
_RISK_HOME_RUN       = 30
_RISK_WIDE_SPREAD    = 15

# overall_watch_score = weighted average (weights must sum to 1.0):
#   risk contributes as (100 - risk) so higher risk → lower watch score
_W_MISMATCH   = 0.30
_W_BASEBALL   = 0.30
_W_EXECUTION  = 0.25
_W_RISK       = 0.15


# ── Public API ────────────────────────────────────────────────────────────────

def generate_candidates_for_game(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
) -> GameDiag:
    """
    Scan DB state for game_pk/game_id and generate observation candidates.

    Returns a GameDiag whose .ids lists newly-inserted candidate_event IDs.
    GameDiag is list-compatible (supports len/iter/bool/index) so existing
    callers that treat the return as list[int] work unchanged.
    """
    diag          = GameDiag()
    gs            = _latest_game_state(conn, game_pk)
    scoring_plays = _recent_scoring_plays(conn, game_pk)

    for fn in (
        _try_full_game_total_watch,
        _try_f5_fade_watch,
        _try_trailing_team_total_watch,
    ):
        try:
            cid, skip_reason, guardrail_blocked, is_new = fn(conn, game_pk, game_id, gs, scoring_plays)
            if skip_reason is not None:
                diag.skip_reasons[skip_reason] = diag.skip_reasons.get(skip_reason, 0) + 1
            else:
                diag.rules_evaluated += 1
                if cid is not None:
                    if is_new:
                        diag.ids.append(cid)
                        if guardrail_blocked:
                            diag.blocked += 1
                    else:
                        diag.dedupe_skipped += 1
        except Exception as exc:
            log.error("%s error game_pk=%s: %s", fn.__name__, game_pk, exc)

    return diag


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _score_market_mismatch(
    yes_bid: int, yes_ask: int, open_price: Optional[int],
) -> float:
    """0–100 pts for how far the market mid has moved from the open price."""
    mid = (yes_bid + yes_ask) / 2.0
    if open_price is None:
        return 50.0  # neutral: no baseline to compare
    move = abs(mid - open_price)
    return min(100.0, round(move * _REPRICE_PTS_PER_CENT, 1))


def _score_baseball_support(scoring_plays: list[sqlite3.Row]) -> float:
    """0–100 pts for how well baseball context supports a price-fade observation."""
    score = 50.0  # neutral baseline
    for play in scoring_plays:
        etype = (play["event_type"] or "").lower()
        if play["is_home_run"]:
            # Hard contact: market move may be justified; lower fade confidence
            score -= _HARD_CONTACT_PENALTY
        elif any(tok in etype for tok in ("error", "wild_pitch", "passed_ball")):
            # Fluky / non-repeatable event: fade candidate strengthens
            score += _FLUKY_RUN_BOOST
        elif any(tok in etype for tok in ("walk", "base_on_balls", "intent_walk")):
            # Walk-driven rally: somewhat fluky
            score += _FLUKY_WALK_BOOST
    return max(0.0, min(100.0, round(score, 1)))


def _score_execution_quality(spread: int) -> float:
    """0–100 pts for bid-ask spread quality (linear between perfect and hard-block thresholds)."""
    if spread <= _EXEC_PERFECT_SPREAD:
        return 100.0
    if spread >= _EXEC_ZERO_SPREAD:
        return 0.0
    ratio = (spread - _EXEC_PERFECT_SPREAD) / (_EXEC_ZERO_SPREAD - _EXEC_PERFECT_SPREAD)
    return round(100.0 * (1.0 - ratio), 1)


def _score_risk(scoring_plays: list[sqlite3.Row], spread: int) -> float:
    """0–100 risk score; higher = more risk factors present."""
    risk = 0.0
    if any(p["is_home_run"] for p in scoring_plays):
        risk += _RISK_HOME_RUN  # market move may be structurally justified
    if spread > 8:              # in the warn zone (8–12c)
        risk += _RISK_WIDE_SPREAD
    return min(100.0, risk)


def _overall_watch_score(
    mismatch: float, baseball: float, execution: float, risk: float,
) -> float:
    """Weighted composite watch score (0–100)."""
    return round(
        _W_MISMATCH  * mismatch
        + _W_BASEBALL  * baseball
        + _W_EXECUTION * execution
        + _W_RISK      * (100.0 - risk),
        1,
    )


def _build_confidence_json(
    mismatch: float, baseball: float, execution: float, risk: float, overall: float,
) -> str:
    return json.dumps({
        "market_mismatch":    mismatch,
        "baseball_support":   baseball,
        "execution_quality":  execution,
        "risk_blocker":       risk,
        "overall_watch":      overall,
    })


# ── DB query helpers ──────────────────────────────────────────────────────────

def _latest_game_state(conn: sqlite3.Connection, game_pk: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM mlb_game_states WHERE game_pk = ? ORDER BY checked_at DESC LIMIT 1",
        (game_pk,),
    ).fetchone()


def _recent_scoring_plays(
    conn: sqlite3.Connection,
    game_pk: int,
    limit: int = 10,
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM mlb_play_events "
        "WHERE game_pk = ? AND is_scoring_play = 1 "
        "ORDER BY at_bat_index DESC LIMIT ?",
        (game_pk, limit),
    ).fetchall()


def _early_scoring_plays(
    conn: sqlite3.Connection,
    game_pk: int,
    max_inning: int = 3,
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM mlb_play_events "
        "WHERE game_pk = ? AND is_scoring_play = 1 AND inning <= ? "
        "ORDER BY at_bat_index DESC",
        (game_pk, max_inning),
    ).fetchall()


def _best_market(
    conn: sqlite3.Connection,
    game_id: str,
    market_type: str,
) -> Optional[sqlite3.Row]:
    """Most-recently-updated market with clear semantics for this game/type."""
    return conn.execute(
        "SELECT * FROM kalshi_markets "
        "WHERE game_id = ? AND market_type = ? AND is_semantics_clear = 1 "
        "ORDER BY updated_at DESC LIMIT 1",
        (game_id, market_type),
    ).fetchone()


def _best_team_total_market(
    conn: sqlite3.Connection,
    game_id: str,
    team_abbr: str,
) -> Optional[sqlite3.Row]:
    """Clear-semantics team_total market for the specified team."""
    return conn.execute(
        "SELECT * FROM kalshi_markets "
        "WHERE game_id = ? AND market_type = 'team_total' "
        "  AND is_semantics_clear = 1 AND selected_team_abbr = ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (game_id, team_abbr),
    ).fetchone()


def _get_team_abbrs(
    conn: sqlite3.Connection, game_pk: int,
) -> tuple[Optional[str], Optional[str]]:
    """Return (away_abbr, home_abbr) for the game, or (None, None)."""
    row = conn.execute(
        "SELECT away_abbr, home_abbr FROM mlb_games WHERE game_pk = ?", (game_pk,)
    ).fetchone()
    if row is None:
        return None, None
    return row["away_abbr"], row["home_abbr"]


# ── Candidate type A: full_game_total_extreme_reprice_watch ──────────────────

def _try_full_game_total_watch(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    gs: Optional[sqlite3.Row],
    scoring_plays: list[sqlite3.Row],
) -> tuple[Optional[int], Optional[str], bool, bool]:
    """
    Trigger: scoring occurred AND full-game total mid-price has repriced
    >= _REPRICE_TRIGGER_CENTS above the open price (or neutral 50 if no open).

    Returns (candidate_id, skip_reason, guardrail_blocked, is_new).
    skip_reason is None when check_all() was reached; candidate_id is None only
    on pre-insertion skips (skip_reason will be set).
    """
    if not scoring_plays:
        return None, "no_scoring_plays", False, False

    market = _best_market(conn, game_id, "full_game_total")
    if market is None:
        return None, "no_market", False, False

    yes_bid = market["yes_bid_cents"]
    yes_ask = market["yes_ask_cents"]
    if yes_bid is None or yes_ask is None:
        return None, "missing_bid_ask", False, False

    open_price = market["game_open_price_cents"]
    mid = (yes_bid + yes_ask) / 2.0
    baseline = open_price if open_price is not None else 50
    move = mid - baseline

    if move < _REPRICE_TRIGGER_CENTS:
        return None, "no_trigger_condition", False, False

    # Pull game state fields
    inning      = gs["inning"]       if gs else None
    half_inning = gs["inning_half"]  if gs else None
    outs        = gs["outs"]         if gs else None
    runners     = gs["runner_state"] if gs else None
    away_score  = gs["away_score"]   if gs else None
    home_score  = gs["home_score"]   if gs else None

    gr = check_all(
        market=market,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=game_pk,
        game_id=game_id,
        inning=inning,
        half_inning=half_inning,
        outs=outs,
        runners_state=runners,
        settlement_horizon=market["settlement_horizon"] or "unknown",
        market_ticker=market["market_ticker"],
        conn=conn,
    )

    spread    = yes_ask - yes_bid
    mismatch  = _score_market_mismatch(yes_bid, yes_ask, open_price)
    baseball  = _score_baseball_support(scoring_plays)
    execution = _score_execution_quality(spread)
    risk      = _score_risk(scoring_plays, spread)
    overall   = _overall_watch_score(mismatch, baseball, execution, risk)
    baseline  = compute_price_baseline(market)

    trigger_desc = (
        f"Full-game mid repriced {move:+.0f}c from "
        f"{'open ' + str(open_price) + 'c' if open_price else 'neutral 50c'} "
        f"after {len(scoring_plays)} scoring play(s)"
    )

    cid, is_new = upsert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=game_pk,
        game_id=game_id,
        market_ticker=market["market_ticker"],
        event_ticker=market["event_ticker"],
        market_type="full_game_total",
        settlement_horizon=market["settlement_horizon"] or "unknown",
        line_value=market["line_value"],
        side="NO",  # fading the over: short YES / long NO
        inning=inning,
        half_inning=half_inning,
        outs=outs,
        score_away=away_score,
        score_home=home_score,
        runners_state=runners,
        entry_yes_bid=yes_bid,
        entry_yes_ask=yes_ask,
        spread_cents=spread,
        market_mismatch_score=mismatch,
        baseball_support_score=baseball,
        execution_quality_score=execution,
        risk_blocker_score=risk,
        overall_watch_score=overall,
        trigger_event_type="full_game_total_reprice",
        trigger_description=trigger_desc,
        guardrails_json=gr.guardrails_json,
        blocked_reason=gr.blocked_reason,
        eligible_for_paper=0,
        status="observed_only" if gr.passed else "blocked",
        confidence_breakdown_json=_build_confidence_json(
            mismatch, baseball, execution, risk, overall
        ),
        **baseline,
    )
    return cid, None, not gr.passed, is_new


# ── Candidate type B: f5_total_overreaction_fade_watch ───────────────────────

def _try_f5_fade_watch(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    gs: Optional[sqlite3.Row],
    scoring_plays: list[sqlite3.Row],
) -> tuple[Optional[int], Optional[str], bool, bool]:
    """
    Trigger: early scoring (innings 1-3) AND F5 over market mid > _F5_OVER_MID_THRESHOLD.

    Observation rationale: early multi-run innings can push the F5 over price above
    its fair value; this watches for a potential fade before inning 4/5 cutoff.
    """
    inning = gs["inning"] if gs else None
    if inning is not None and inning > _F5_MAX_INNING:
        return None, "inning_too_late", False, False

    early_plays = _early_scoring_plays(conn, game_pk)
    if not early_plays:
        return None, "no_early_scoring", False, False

    market = _best_market(conn, game_id, "f5_total")
    if market is None:
        return None, "no_market", False, False

    # Only trigger on f5_over_yes markets (overreaction fade targets the over)
    if (market["contract_direction"] or "").lower() != "f5_over_yes":
        return None, "wrong_direction", False, False

    yes_bid = market["yes_bid_cents"]
    yes_ask = market["yes_ask_cents"]
    if yes_bid is None or yes_ask is None:
        return None, "missing_bid_ask", False, False

    mid = (yes_bid + yes_ask) / 2.0
    if mid < _F5_OVER_MID_THRESHOLD:
        return None, "no_trigger_condition", False, False

    half_inning = gs["inning_half"]  if gs else None
    outs        = gs["outs"]         if gs else None
    runners     = gs["runner_state"] if gs else None
    away_score  = gs["away_score"]   if gs else None
    home_score  = gs["home_score"]   if gs else None

    gr = check_all(
        market=market,
        candidate_type="f5_total_overreaction_fade_watch",
        game_pk=game_pk,
        game_id=game_id,
        inning=inning,
        half_inning=half_inning,
        outs=outs,
        runners_state=runners,
        settlement_horizon=market["settlement_horizon"] or "unknown",
        market_ticker=market["market_ticker"],
        conn=conn,
    )

    spread    = yes_ask - yes_bid
    mismatch  = _score_market_mismatch(yes_bid, yes_ask, market["game_open_price_cents"])
    baseball  = _score_baseball_support(early_plays)
    execution = _score_execution_quality(spread)
    risk      = _score_risk(early_plays, spread)
    overall   = _overall_watch_score(mismatch, baseball, execution, risk)
    baseline  = compute_price_baseline(market)

    trigger_desc = (
        f"F5 over mid={mid:.0f}c after {len(early_plays)} early-inning scoring play(s); "
        f"inning {inning}"
    )

    cid, is_new = upsert_candidate_event(
        conn,
        candidate_type="f5_total_overreaction_fade_watch",
        game_pk=game_pk,
        game_id=game_id,
        market_ticker=market["market_ticker"],
        event_ticker=market["event_ticker"],
        market_type="f5_total",
        settlement_horizon=market["settlement_horizon"] or "unknown",
        line_value=market["line_value"],
        side="NO",  # fading F5 over: long NO on f5_over_yes
        inning=inning,
        half_inning=half_inning,
        outs=outs,
        score_away=away_score,
        score_home=home_score,
        runners_state=runners,
        entry_yes_bid=yes_bid,
        entry_yes_ask=yes_ask,
        spread_cents=spread,
        market_mismatch_score=mismatch,
        baseball_support_score=baseball,
        execution_quality_score=execution,
        risk_blocker_score=risk,
        overall_watch_score=overall,
        trigger_event_type="f5_overreaction",
        trigger_description=trigger_desc,
        guardrails_json=gr.guardrails_json,
        blocked_reason=gr.blocked_reason,
        eligible_for_paper=0,
        status="observed_only" if gr.passed else "blocked",
        confidence_breakdown_json=_build_confidence_json(
            mismatch, baseball, execution, risk, overall
        ),
        **baseline,
    )
    return cid, None, not gr.passed, is_new


# ── Candidate type C: trailing_team_total_lag_watch ─────────────────────────

def _try_trailing_team_total_watch(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    gs: Optional[sqlite3.Row],
    scoring_plays: list[sqlite3.Row],
) -> tuple[Optional[int], Optional[str], bool, bool]:
    """
    Trigger: one team trails by >= _TRAILING_RUN_THRESHOLD in innings 1–6 AND
    their team-total over market exists with clear semantics.

    Observation rationale: the trailing team's market may lag behind score
    movements, leaving their team total underpriced relative to expected
    late-game scoring.
    """
    if gs is None:
        return None, "no_game_state", False, False

    inning     = gs["inning"] or 1
    away_score = gs["away_score"] or 0
    home_score = gs["home_score"] or 0

    if inning > _TRAILING_MAX_INNING:
        return None, "inning_too_late", False, False

    away_abbr, home_abbr = _get_team_abbrs(conn, game_pk)

    deficit_away = home_score - away_score   # away team is trailing by this many
    deficit_home = away_score - home_score   # home team is trailing by this many

    if deficit_away >= _TRAILING_RUN_THRESHOLD and away_abbr:
        trailing_abbr  = away_abbr
        trailing_score = away_score
        leading_score  = home_score
    elif deficit_home >= _TRAILING_RUN_THRESHOLD and home_abbr:
        trailing_abbr  = home_abbr
        trailing_score = home_score
        leading_score  = away_score
    else:
        return None, "no_trailing_team", False, False

    market = _best_team_total_market(conn, game_id, trailing_abbr)
    if market is None:
        return None, "no_market", False, False

    # Only team_total_over_yes markets — we're watching for underpriced team total
    if (market["contract_direction"] or "").lower() != "team_total_over_yes":
        return None, "wrong_direction", False, False

    yes_bid = market["yes_bid_cents"]
    yes_ask = market["yes_ask_cents"]
    if yes_bid is None or yes_ask is None:
        return None, "missing_bid_ask", False, False

    half_inning = gs["inning_half"]  if gs else None
    outs        = gs["outs"]         if gs else None
    runners     = gs["runner_state"] if gs else None

    gr = check_all(
        market=market,
        candidate_type="trailing_team_total_lag_watch",
        game_pk=game_pk,
        game_id=game_id,
        inning=inning,
        half_inning=half_inning,
        outs=outs,
        runners_state=runners,
        settlement_horizon=market["settlement_horizon"] or "unknown",
        market_ticker=market["market_ticker"],
        conn=conn,
    )

    spread    = yes_ask - yes_bid
    mismatch  = _score_market_mismatch(yes_bid, yes_ask, market["game_open_price_cents"])
    baseball  = _score_baseball_support(scoring_plays)
    execution = _score_execution_quality(spread)
    risk      = _score_risk(scoring_plays, spread)
    overall   = _overall_watch_score(mismatch, baseball, execution, risk)
    baseline  = compute_price_baseline(market)

    trigger_desc = (
        f"{trailing_abbr} trails {leading_score}-{trailing_score} in inning {inning}; "
        f"team total may be lagging"
    )

    cid, is_new = upsert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_pk=game_pk,
        game_id=game_id,
        market_ticker=market["market_ticker"],
        event_ticker=market["event_ticker"],
        market_type="team_total",
        settlement_horizon=market["settlement_horizon"] or "unknown",
        selected_team_abbr=trailing_abbr,
        line_value=market["line_value"],
        side="YES",  # watching for value on trailing team's total over
        inning=inning,
        half_inning=half_inning,
        outs=outs,
        score_away=gs["away_score"],
        score_home=gs["home_score"],
        runners_state=runners,
        entry_yes_bid=yes_bid,
        entry_yes_ask=yes_ask,
        spread_cents=spread,
        market_mismatch_score=mismatch,
        baseball_support_score=baseball,
        execution_quality_score=execution,
        risk_blocker_score=risk,
        overall_watch_score=overall,
        trigger_event_type="trailing_team_total_lag",
        trigger_description=trigger_desc,
        guardrails_json=gr.guardrails_json,
        blocked_reason=gr.blocked_reason,
        eligible_for_paper=0,
        status="observed_only" if gr.passed else "blocked",
        confidence_breakdown_json=_build_confidence_json(
            mismatch, baseball, execution, risk, overall
        ),
        **baseline,
    )
    return cid, None, not gr.passed, is_new
