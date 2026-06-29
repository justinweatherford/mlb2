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
from mlb.derivatives import derive_candidate_metadata
from mlb.guardrails import check_all
from mlb.price_utils import compute_price_baseline
from mlb.team_context import get_team_context

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

    # Per-derivative-type breakdowns — keyed by derivative_type string.
    # derivative_skips: {derivative_type: {skip_reason: count}}
    # derivative_evaluated: {derivative_type: times check_all() was called}
    derivative_skips: dict = field(default_factory=dict)
    derivative_evaluated: dict = field(default_factory=dict)

    # Spread diagnostics — how many fg_spread/f5_spread markets exist for this game,
    # and the canonical reason why no spread Watch candidates are generated.
    spread_markets_discovered: int = 0
    spread_skip_reason: str = (
        "spread_direction_requires_manual_review: spread markets are visible in Bot Markets "
        "but generate no Watch candidates — YES/NO direction cannot be reliably derived "
        "from Kalshi metadata alone."
    )

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

# First-discovery baselines (baseline_quality="medium") are not confirmed opening
# prices — the market was captured for the first time at discovery, not at game
# open. Cap mismatch to avoid treating discovery-time price as a real baseline.
_FIRST_DISCOVERY_MISMATCH_CAP = 25.0

# Team Lag classification thresholds
# deficit >= this → "blowout", suppress the candidate
_TEAM_LAG_BLOWOUT_MARGIN          = 7
# baseball_support < this → insufficient signal, suppress
_TEAM_LAG_MIN_BASEBALL_SUPPORT    = 40.0

# F5 already-cleared: bid >= this (near-certainty) → market_effectively_settled
_F5_NEAR_SETTLED_BID              = 95

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

# Team-context signal thresholds — conservative ±5/±2/0 step per signal.
# High risk ratings (F5-Pit, BP) have inverted polarity: higher = more risk = worse.
_TC_STRONG_THRESHOLD = 15   # |rating − 50| ≥ 15 → ±5 pts
_TC_MILD_THRESHOLD   = 7    # |rating − 50| ≥ 7  → ±2 pts
_TC_STRONG_STEP      = 5.0
_TC_MILD_STEP        = 2.0
_TC_MAX_ADJ          = 15.0  # combined team-context adj clamped to ±15


# ── Public API ────────────────────────────────────────────────────────────────

def generate_candidates_for_game(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    slate_date: Optional[str] = None,
) -> GameDiag:
    """
    Scan DB state for game_pk/game_id and generate observation candidates.

    Returns a GameDiag whose .ids lists newly-inserted candidate_event IDs.
    GameDiag is list-compatible (supports len/iter/bool/index) so existing
    callers that treat the return as list[int] work unchanged.

    slate_date: if provided, skip games whose game_date != slate_date.
                Prevents stale is_final=0 games from prior dates being processed.
    """
    _fn_derivative_type = [
        (_try_full_game_total_watch,    "fg_total"),
        (_try_f5_fade_watch,            "f5_total"),
        (_try_trailing_team_total_watch, "team_total"),
    ]

    diag = GameDiag()

    # Skip final games: stale game state + date-unfiltered _best_market() would
    # pair a finished game's score with today's market ticker.
    game_row = conn.execute(
        "SELECT is_final, game_date FROM mlb_games WHERE game_pk = ?", (game_pk,)
    ).fetchone()
    if game_row and game_row["is_final"]:
        return diag
    game_date = game_row["game_date"] if game_row else None
    # Belt-and-suspenders: skip games from other dates even if is_final=0.
    # Primary guard is in run_one_cycle; this catches direct calls with wrong date.
    if slate_date and game_date and game_date != slate_date:
        diag.skip_reasons["wrong_game_date"] = diag.skip_reasons.get("wrong_game_date", 0) + 1
        return diag
    season = (game_date[:4] if game_date else "2026")

    gs            = _latest_game_state(conn, game_pk)
    scoring_plays = _recent_scoring_plays(conn, game_pk)

    # Count spread markets that exist but are structurally blocked from Watch candidates.
    diag.spread_markets_discovered = _count_spread_markets(conn, game_id)

    for fn, deriv_type in _fn_derivative_type:
        try:
            cid, skip_reason, guardrail_blocked, is_new = fn(conn, game_pk, game_id, gs, scoring_plays, season)
            if skip_reason is not None:
                diag.skip_reasons[skip_reason] = diag.skip_reasons.get(skip_reason, 0) + 1
                dt_skips = diag.derivative_skips.setdefault(deriv_type, {})
                dt_skips[skip_reason] = dt_skips.get(skip_reason, 0) + 1
            else:
                diag.rules_evaluated += 1
                diag.derivative_evaluated[deriv_type] = (
                    diag.derivative_evaluated.get(deriv_type, 0) + 1
                )
                if cid is not None:
                    if is_new:
                        # Stamp trigger_game_date on newly inserted candidates.
                        if game_date:
                            conn.execute(
                                "UPDATE candidate_events SET trigger_game_date=? WHERE id=? AND trigger_game_date IS NULL",
                                (game_date, cid),
                            )
                            conn.commit()
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
    baseline_quality: Optional[str] = None,
) -> float:
    """0–100 pts for how far the market mid has moved from the open price.

    Returns neutral 50 when the baseline is low-quality (backfilled_current)
    or missing — we don't want to overtrust a fake opening delta.

    First-discovery baselines (quality="medium") are capped at
    _FIRST_DISCOVERY_MISMATCH_CAP: the "open price" was the price at first
    market discovery, not a true game-open price, so large deltas are
    artifacts of discovery timing rather than real market moves.
    """
    if open_price is None or baseline_quality in ("none", "low"):
        return 50.0  # neutral: no reliable baseline to compare
    mid = (yes_bid + yes_ask) / 2.0
    move = abs(mid - open_price)
    raw = min(100.0, round(move * _REPRICE_PTS_PER_CENT, 1))
    if baseline_quality == "medium":
        # baseline_unverified_first_discovery: cap to avoid inflated mismatch
        return min(raw, _FIRST_DISCOVERY_MISMATCH_CAP)
    return raw


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


def _ctx_get(ctx: Optional[dict], field: str) -> Optional[float]:
    """Safely extract a float field from a team-context dict."""
    if ctx is None:
        return None
    return ctx.get(field)


def _fetch_team_ctx(
    conn: sqlite3.Connection, team_abbr: Optional[str], season: str,
) -> Optional[dict]:
    """Return the team context row dict, or None when not found."""
    if not team_abbr:
        return None
    return get_team_context(team_abbr, season, conn)


def _score_baseball_support_full(
    scoring_plays: list[sqlite3.Row],
    *,
    candidate_type: str,
    away_ctx: Optional[dict],
    home_ctx: Optional[dict],
    selected_team_abbr: Optional[str] = None,
    away_abbr: Optional[str] = None,
    home_abbr: Optional[str] = None,
) -> tuple[float, dict]:
    """
    Return (final_baseball_support_score, detail_dict) combining play-event logic
    with a conservative team-context adjustment layer.

    Team context is an *adjustment*, not the main model.  Each signal contributes
    ±5 (strong) / ±2 (mild) / 0 (neutral/missing); the total is clamped to ±15.

    Risk ratings (f5_pitching_risk_rating, bullpen_risk_rating) are inverted:
    higher value = more risk = bad for the team that owns it.  When the OPPONENT'S
    bullpen risk is high it SUPPORTS a YES-side trailing-team candidate.
    """
    play_event_score = _score_baseball_support(scoring_plays)
    play_event_adj   = round(play_event_score - 50.0, 1)

    # Build signals: (rating | None, supports_thesis, human-readable label)
    # "supports_thesis" means: high value of this rating is GOOD for the candidate direction.
    signals: list[tuple[Optional[float], bool, str]] = []

    a = away_abbr or "away"
    h = home_abbr or "home"

    if candidate_type == "trailing_team_total_lag_watch":
        # YES side: watching for the trailing team to score more.
        # Determine opponent from selected_team_abbr.
        if selected_team_abbr and away_abbr and home_abbr:
            sel_ctx = away_ctx if selected_team_abbr == away_abbr else home_ctx
            opp_ctx = home_ctx if selected_team_abbr == away_abbr else away_ctx
            opp_lbl = h      if selected_team_abbr == away_abbr else a
        else:
            sel_ctx = opp_ctx = None
            opp_lbl = "opp"
        sel_lbl = selected_team_abbr or "sel"
        signals = [
            (_ctx_get(sel_ctx, "offense_rating"),       True,  f"{sel_lbl}_offense"),
            (_ctx_get(opp_ctx, "defense_pitching_rating"), False, f"{opp_lbl}_defense"),
            # Opponent's high BP risk means their bullpen gives up more runs → supports YES
            (_ctx_get(opp_ctx, "bullpen_risk_rating"),  True,  f"{opp_lbl}_bp_risk"),
        ]

    elif candidate_type == "full_game_total_extreme_reprice_watch":
        # NO side: fading the over — high offense / bullpen risk contradicts, high defense supports.
        signals = [
            (_ctx_get(away_ctx, "offense_rating"),          False, f"{a}_offense"),
            (_ctx_get(home_ctx, "offense_rating"),          False, f"{h}_offense"),
            (_ctx_get(away_ctx, "defense_pitching_rating"), True,  f"{a}_defense"),
            (_ctx_get(home_ctx, "defense_pitching_rating"), True,  f"{h}_defense"),
            # High bullpen risk = more late runs = contradicts the fade
            (_ctx_get(away_ctx, "bullpen_risk_rating"),     False, f"{a}_bp_risk"),
            (_ctx_get(home_ctx, "bullpen_risk_rating"),     False, f"{h}_bp_risk"),
        ]

    elif candidate_type == "f5_total_overreaction_fade_watch":
        # NO side: fading F5 over — high F5 offense or bad starters both contradict the fade.
        signals = [
            (_ctx_get(away_ctx, "f5_offense_rating"),         False, f"{a}_f5_offense"),
            (_ctx_get(home_ctx, "f5_offense_rating"),         False, f"{h}_f5_offense"),
            # High starter risk = bad starters = more early runs = contradicts fade
            (_ctx_get(away_ctx, "f5_pitching_risk_rating"),   False, f"{a}_f5_pit_risk"),
            (_ctx_get(home_ctx, "f5_pitching_risk_rating"),   False, f"{h}_f5_pit_risk"),
        ]

    # Compute per-signal step contributions.
    total_tc_adj = 0.0
    support_reasons: list[str] = []
    contradiction_reasons: list[str] = []
    missing_context_reasons: list[str] = []

    for rating, supports, label in signals:
        if rating is None:
            missing_context_reasons.append(f"{label}=missing")
            continue
        dev = rating - 50.0
        if abs(dev) >= _TC_STRONG_THRESHOLD:
            step = _TC_STRONG_STEP
        elif abs(dev) >= _TC_MILD_THRESHOLD:
            step = _TC_MILD_STEP
        else:
            # Within neutral band — no contribution.
            continue
        direction_supports = (dev > 0) == supports
        contrib = step if direction_supports else -step
        total_tc_adj += contrib
        reason_str = f"{label}={rating:.0f}"
        if direction_supports:
            support_reasons.append(reason_str)
        else:
            contradiction_reasons.append(reason_str)

    tc_adj = max(-_TC_MAX_ADJ, min(_TC_MAX_ADJ, total_tc_adj))
    final  = max(0.0, min(100.0, round(50.0 + play_event_adj + tc_adj, 1)))

    detail = {
        "baseball_support_base":      50.0,
        "play_event_adjustment":      play_event_adj,
        "team_context_adjustment":    round(tc_adj, 1),
        "final_baseball_support_score": final,
        "support_reasons":            support_reasons,
        "contradiction_reasons":      contradiction_reasons,
        "missing_context_reasons":    missing_context_reasons,
    }
    return final, detail


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


def _classify_team_lag_watch(
    *,
    deficit_runs: int,
    baseball_support: float,
    mismatch: float,
    runners_state: Optional[str],
    recent_scoring: bool,
) -> tuple[Optional[str], str]:
    """Classify a trailing_team_total_lag_watch signal quality.

    Returns (blocked_reason_or_None, label) where label is one of:
      "watch"    — real pressure + adequate scores, surface as actionable
      "observe"  — early trailing but no active pressure; record but deprioritise
      "suppress" — blowout / low support; block with a specific reason

    Called only after guardrails pass (never overrides rally_still_active etc.).
    """
    # Hard suppression: blowout margin
    if deficit_runs >= _TEAM_LAG_BLOWOUT_MARGIN:
        return "team_lag_blowout", "suppress"

    # Hard suppression: insufficient baseball support signal
    if baseball_support < _TEAM_LAG_MIN_BASEBALL_SUPPORT:
        return "team_lag_insufficient_baseball_support", "suppress"

    # "observe" when no active pressure exists
    _runners = (runners_state or "").strip().lower()
    _has_runners = bool(_runners) and _runners not in ("", "empty", "bases_empty", "---")
    if not _has_runners and not recent_scoring:
        return "team_lag_observe_only", "observe"

    return None, "watch"


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


def _count_spread_markets(conn: sqlite3.Connection, game_id: str) -> int:
    """Count fg_spread + f5_spread markets that exist for the game.

    These markets are always is_semantics_clear=False (see kalshi/semantics.py),
    so they can never be selected by _best_market() for Watch candidates.
    The count surfaces in GameDiag.spread_markets_discovered for diagnostics.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM kalshi_markets "
        "WHERE game_id = ? AND market_type IN ('spread_run_line', 'f5_spread')",
        (game_id,),
    ).fetchone()[0]


# ── Candidate type A: full_game_total_extreme_reprice_watch ──────────────────

def _try_full_game_total_watch(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    gs: Optional[sqlite3.Row],
    scoring_plays: list[sqlite3.Row],
    season: str = "2026",
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

    away_abbr, home_abbr = _get_team_abbrs(conn, game_pk)
    away_ctx  = _fetch_team_ctx(conn, away_abbr, season)
    home_ctx  = _fetch_team_ctx(conn, home_abbr, season)

    baseline         = compute_price_baseline(market)
    derivative_meta  = derive_candidate_metadata("full_game_total_extreme_reprice_watch")
    spread    = yes_ask - yes_bid
    mismatch  = _score_market_mismatch(yes_bid, yes_ask, open_price,
                                        baseline["baseline_quality"])
    baseball, baseball_detail = _score_baseball_support_full(
        scoring_plays,
        candidate_type="full_game_total_extreme_reprice_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr=away_abbr,
        home_abbr=home_abbr,
    )
    execution = _score_execution_quality(spread)
    risk      = _score_risk(scoring_plays, spread)
    overall   = _overall_watch_score(mismatch, baseball, execution, risk)

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
        baseball_context_json=json.dumps(baseball_detail),
        **baseline,
        **derivative_meta,
    )
    return cid, None, not gr.passed, is_new


# ── Candidate type B: f5_total_overreaction_fade_watch ───────────────────────

def _try_f5_fade_watch(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    gs: Optional[sqlite3.Row],
    scoring_plays: list[sqlite3.Row],
    season: str = "2026",
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

    # F5 near-settled: YES bid is near certainty — over is effectively decided
    if yes_bid >= _F5_NEAR_SETTLED_BID:
        return None, "market_effectively_settled", False, False

    mid = (yes_bid + yes_ask) / 2.0
    if mid < _F5_OVER_MID_THRESHOLD:
        return None, "no_trigger_condition", False, False

    half_inning = gs["inning_half"]  if gs else None
    outs        = gs["outs"]         if gs else None
    runners     = gs["runner_state"] if gs else None
    away_score  = gs["away_score"]   if gs else None
    home_score  = gs["home_score"]   if gs else None

    # F5 already-cleared: current combined score has already exceeded the line
    _line = market["line_value"]
    if _line is not None and away_score is not None and home_score is not None:
        if (away_score + home_score) > _line:
            return None, "f5_total_already_cleared", False, False

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

    away_abbr, home_abbr = _get_team_abbrs(conn, game_pk)
    away_ctx  = _fetch_team_ctx(conn, away_abbr, season)
    home_ctx  = _fetch_team_ctx(conn, home_abbr, season)

    baseline         = compute_price_baseline(market)
    derivative_meta  = derive_candidate_metadata("f5_total_overreaction_fade_watch")
    spread    = yes_ask - yes_bid
    mismatch  = _score_market_mismatch(yes_bid, yes_ask, market["game_open_price_cents"],
                                        baseline["baseline_quality"])
    baseball, baseball_detail = _score_baseball_support_full(
        early_plays,
        candidate_type="f5_total_overreaction_fade_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr=away_abbr,
        home_abbr=home_abbr,
    )
    execution = _score_execution_quality(spread)
    risk      = _score_risk(early_plays, spread)
    overall   = _overall_watch_score(mismatch, baseball, execution, risk)

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
        baseball_context_json=json.dumps(baseball_detail),
        **baseline,
        **derivative_meta,
    )
    return cid, None, not gr.passed, is_new


# ── Candidate type C: trailing_team_total_lag_watch ─────────────────────────

def _try_trailing_team_total_watch(
    conn: sqlite3.Connection,
    game_pk: int,
    game_id: str,
    gs: Optional[sqlite3.Row],
    scoring_plays: list[sqlite3.Row],
    season: str = "2026",
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
        actual_deficit = deficit_away
    elif deficit_home >= _TRAILING_RUN_THRESHOLD and home_abbr:
        trailing_abbr  = home_abbr
        trailing_score = home_score
        leading_score  = away_score
        actual_deficit = deficit_home
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

    away_ctx = _fetch_team_ctx(conn, away_abbr, season)
    home_ctx = _fetch_team_ctx(conn, home_abbr, season)

    baseline         = compute_price_baseline(market)
    derivative_meta  = derive_candidate_metadata("trailing_team_total_lag_watch")
    spread    = yes_ask - yes_bid
    mismatch  = _score_market_mismatch(yes_bid, yes_ask, market["game_open_price_cents"],
                                        baseline["baseline_quality"])
    baseball, baseball_detail = _score_baseball_support_full(
        scoring_plays,
        candidate_type="trailing_team_total_lag_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        selected_team_abbr=trailing_abbr,
        away_abbr=away_abbr,
        home_abbr=home_abbr,
    )
    execution = _score_execution_quality(spread)
    risk      = _score_risk(scoring_plays, spread)
    overall   = _overall_watch_score(mismatch, baseball, execution, risk)

    trigger_desc = (
        f"{trailing_abbr} trails {leading_score}-{trailing_score} in inning {inning}; "
        f"team total may be lagging"
    )

    # Team Lag classification: only applied when guardrails already passed.
    # Never overrides rally_still_active or other hard guardrail blocks.
    if gr.passed:
        _lag_block, _lag_label = _classify_team_lag_watch(
            deficit_runs=actual_deficit,
            baseball_support=baseball,
            mismatch=mismatch,
            runners_state=runners,
            recent_scoring=bool(scoring_plays),
        )
        if _lag_block:
            effective_blocked_reason = _lag_block
            effective_status = "blocked"
        else:
            effective_blocked_reason = None
            effective_status = "observed_only"
    else:
        _lag_label = "guardrail_blocked"
        effective_blocked_reason = gr.blocked_reason
        effective_status = "blocked"

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
        blocked_reason=effective_blocked_reason,
        eligible_for_paper=0,
        status=effective_status,
        confidence_breakdown_json=_build_confidence_json(
            mismatch, baseball, execution, risk, overall
        ),
        baseball_context_json=json.dumps(baseball_detail),
        **baseline,
        **derivative_meta,
    )
    return cid, None, effective_status == "blocked", is_new
