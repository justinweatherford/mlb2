"""
mlb/market_layer.py — Deterministic market layer classification.

classify_market_layer(market) -> dict with 5 keys:
  market_layer_status : discovered | supported | candidate_worthy |
                        blocked | noisy_ignored | unsupported | needs_review
  market_layer_reason : human-readable explanation
  supported_by_bot    : 0 | 1
  candidate_surface   : fg_spread | f5_spread | fg_total | f5_total |
                        team_total | fg_moneyline | f5_moneyline |
                        player_prop | unsupported | unknown
  is_noisy_market     : 0 | 1
"""

# Map from market_type (DB column) → candidate_surface label
MARKET_TYPE_TO_SURFACE: dict[str, str] = {
    "full_game_total":      "fg_total",
    "f5_total":             "f5_total",
    "team_total":           "team_total",
    "spread_run_line":      "fg_spread",
    "f5_spread":            "f5_spread",
    "moneyline":            "fg_moneyline",
    "f5_winner":            "f5_moneyline",
    "player_hr":            "player_prop",
    "player_hrr":           "player_prop",
    "player_strikeouts":    "player_prop",
    "player_total_bases":   "player_prop",
    "player_hits":          "player_prop",
    "player_rbi":           "player_prop",
    "player_stolen_bases":  "player_prop",
    "extra_innings":        "unsupported",
    "run_first_inning":     "unsupported",
    "championship_futures": "unsupported",
    "unknown":              "unknown",
}

# The 5 core surfaces that the bot evaluates for candidate_worthy classification
_CORE_SURFACES = frozenset({"fg_total", "f5_total", "team_total", "fg_spread", "f5_spread"})

# Surfaces the bot monitors but does not generate primary candidates from
_MONITORED_SURFACES = frozenset({"fg_moneyline", "f5_moneyline"})

# Player-prop market types — noisy, not useful for game-scoring derivative reads
_NOISY_TYPES = frozenset({
    "player_hr", "player_hrr", "player_strikeouts", "player_total_bases",
    "player_hits", "player_rbi", "player_stolen_bases",
})

# Types that are structurally outside the bot's scope
_UNSUPPORTED_TYPES = frozenset({"extra_innings", "run_first_inning", "championship_futures"})

# Spread ceiling that blocks candidate_worthy (mirrors guardrail in candidate generator)
_HARD_SPREAD_BLOCK_CENTS = 12


def classify_market_layer(market: dict) -> dict:
    """
    Pure, deterministic classifier. Accepts a plain dict or sqlite3.Row.
    Never raises — always returns a complete 5-key result dict.
    """
    mtype     = (market.get("market_type") or "unknown").strip()
    game_id   = market.get("game_id")
    sem_clear = int(market.get("is_semantics_clear") or 0)
    yes_bid   = market.get("yes_bid_cents")
    yes_ask   = market.get("yes_ask_cents")

    surface = MARKET_TYPE_TO_SURFACE.get(mtype, "unknown")

    # ── Player-prop markets (noisy, not game-derivative) ──────────────────────
    if mtype in _NOISY_TYPES:
        return {
            "market_layer_status": "noisy_ignored",
            "market_layer_reason": f"{mtype} is a player-prop surface, not a game-scoring derivative",
            "supported_by_bot": 0,
            "candidate_surface": "player_prop",
            "is_noisy_market": 1,
        }

    # ── Structurally unsupported types ────────────────────────────────────────
    if mtype in _UNSUPPORTED_TYPES:
        return {
            "market_layer_status": "unsupported",
            "market_layer_reason": f"{mtype} is not in the supported derivative surface set",
            "supported_by_bot": 0,
            "candidate_surface": "unsupported",
            "is_noisy_market": 0,
        }

    # ── Unknown market type — classifier failed to resolve ────────────────────
    if mtype == "unknown" or surface == "unknown":
        return {
            "market_layer_status": "needs_review",
            "market_layer_reason": "market_type is unknown — series prefix or regex classifier found no match",
            "supported_by_bot": 0,
            "candidate_surface": "unknown",
            "is_noisy_market": 0,
        }

    # ── Missing game_id — cannot route to game context ────────────────────────
    if not game_id:
        return {
            "market_layer_status": "needs_review",
            "market_layer_reason": "no game_id resolved — market cannot be routed to a game context",
            "supported_by_bot": 0,
            "candidate_surface": surface,
            "is_noisy_market": 0,
        }

    # ── Monitored surfaces (moneyline / F5 winner) ────────────────────────────
    if surface in _MONITORED_SURFACES:
        return {
            "market_layer_status": "supported",
            "market_layer_reason": f"{mtype} is monitored but is not a primary candidate surface",
            "supported_by_bot": 1,
            "candidate_surface": surface,
            "is_noisy_market": 0,
        }

    # ── Core 5 surfaces: full evaluation chain ────────────────────────────────
    if surface in _CORE_SURFACES:
        if not sem_clear:
            return {
                "market_layer_status": "needs_review",
                "market_layer_reason": "semantics unclear — contract direction not yet determined",
                "supported_by_bot": 1,
                "candidate_surface": surface,
                "is_noisy_market": 0,
            }

        if yes_bid is None or yes_ask is None:
            return {
                "market_layer_status": "blocked",
                "market_layer_reason": "no bid/ask price available — market not priceable",
                "supported_by_bot": 1,
                "candidate_surface": surface,
                "is_noisy_market": 0,
            }

        spread = yes_ask - yes_bid
        if spread > _HARD_SPREAD_BLOCK_CENTS:
            return {
                "market_layer_status": "blocked",
                "market_layer_reason": (
                    f"spread {spread}¢ exceeds {_HARD_SPREAD_BLOCK_CENTS}¢ hard-block threshold"
                ),
                "supported_by_bot": 1,
                "candidate_surface": surface,
                "is_noisy_market": 0,
            }

        return {
            "market_layer_status": "candidate_worthy",
            "market_layer_reason": (
                "core surface · clear semantics · game matched · priced within spread limit"
            ),
            "supported_by_bot": 1,
            "candidate_surface": surface,
            "is_noisy_market": 0,
        }

    # Fallback — surface is known but no evaluation rule matched
    return {
        "market_layer_status": "discovered",
        "market_layer_reason": f"surface={surface!r} has no evaluation rule",
        "supported_by_bot": 0,
        "candidate_surface": surface,
        "is_noisy_market": 0,
    }
