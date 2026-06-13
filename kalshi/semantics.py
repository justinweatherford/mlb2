"""
kalshi/semantics.py — Conservative market semantics parser.

Parses contract direction, settlement horizon, and YES/NO meaning from
Kalshi market metadata (rules_primary, title, subtitle, market_type, ticker).

Conservative principles — enforced throughout:
  - Does NOT infer direction from signal names, strategy keywords, or any
    field that originates from this app's own trading logic.
  - Does NOT assume YES = Over by default for any market type.
  - Does NOT assume spread/team side unless the selected team is unambiguously
    identified from Kalshi metadata alone.
  - If any required semantic field is unclear, is_semantics_clear=False and
    needs_review_reason explains why.
  - Unclear semantics must block: auto-settlement, paper eligibility,
    live candidate eligibility, and real-trade recommendations.

Evidence hierarchy (confidence levels):
  1.0 — rules_primary contains explicit "resolves/settles YES if … over/under [N]"
  0.9 — market ticker last segment identifies the team (moneyline)
  0.8 — title/subtitle contains "over N" or "under N" (number required)
  0.0 — unclear; is_semantics_clear=False

contract_direction values:
  over_yes | under_yes               — full_game_total
  f5_over_yes | f5_under_yes         — f5_total
  team_total_over_yes                — team_total, direction=over
  team_total_under_yes               — team_total, direction=under
  moneyline_yes                      — moneyline, selected team wins
  spread_yes                         — never set (spreads always unclear)
  unknown                            — any ambiguous case
"""
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional


# ── Regex patterns — rules_primary (confidence 1.0) ──────────────────────────
# Require the explicit settlement sentence: "resolves/settles YES if … [keyword]".
# "exceed" is handled separately to avoid false-positive on "do not exceed".

# Matches non-negated directional words (safe — "over"/"more than" never appear negated)
_RULES_YES_OVER = re.compile(
    r'(?:resolves?|settles?)\s+yes\s+if\b[^.!?]*'
    r'\b(?:over|more\s+than|greater\s+than)\b',
    re.IGNORECASE,
)
# Matches "exceed" in a YES clause — combined with negation guard below
_RULES_YES_EXCEED = re.compile(
    r'(?:resolves?|settles?)\s+yes\s+if\b[^.!?]*\bexceeds?\b',
    re.IGNORECASE,
)
# Detects negation of "exceed" anywhere in the sentence
_RULES_NEG_EXCEED = re.compile(
    r'\b(?:not|do\s+not|does\s+not|cannot|can\s+not)\s+exceeds?\b',
    re.IGNORECASE,
)
_RULES_YES_UNDER = re.compile(
    r'(?:resolves?|settles?)\s+yes\s+if\b[^.!?]*'
    r'\b(?:under|fewer\s+than|less\s+than|not\s+exceed|do\s+not\s+exceed|does\s+not\s+exceed|at\s+most)\b',
    re.IGNORECASE,
)

# ── Regex patterns — title / subtitle (confidence 0.8) ───────────────────────
# Require "over/under [number]" — a bare keyword without a number is NOT enough.
# This blocks signal-name keywords like "under_candidate" (no number follows).

_TITLE_OVER_N  = re.compile(r'\bover\s+\d+(?:\.\d+)?\b',  re.IGNORECASE)
_TITLE_UNDER_N = re.compile(r'\bunder\s+\d+(?:\.\d+)?\b', re.IGNORECASE)

# ── Moneyline team detection ──────────────────────────────────────────────────
# From title: "[ABBR] wins" or "[ABBR] to win"
_TITLE_WINS_RE = re.compile(r'\b([A-Z]{2,4})\s+(?:wins?|to\s+win)\b', re.IGNORECASE)

# ── Settlement horizon lookup — derived from market_type, no text parsing ─────
_HORIZON_MAP: dict[str, str] = {
    "full_game_total":      "full_game",
    "spread_run_line":      "full_game",
    "moneyline":            "full_game",
    "team_total":           "full_game",
    "extra_innings":        "full_game",
    "f5_total":             "first_5",
    "f5_spread":            "first_5",
    "f5_winner":            "first_5",
    "player_hr":            "player_prop",
    "player_hrr":           "player_prop",
    "player_strikeouts":    "player_prop",
    "player_total_bases":   "player_prop",
    "player_hits":          "player_prop",
    "player_rbi":           "player_prop",
    "player_stolen_bases":  "player_prop",
}


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class MarketSemantics:
    settlement_horizon:  str           # full_game | first_5 | player_prop | unknown
    selected_team_abbr:  Optional[str] # for moneyline / team_total
    opponent_team_abbr:  Optional[str]
    spread_value:        Optional[float]
    yes_means:           str           # over | under | f5_over | f5_under |
                                       # team_total_over | team_total_under |
                                       # {ABBR}_wins | unknown
    no_means:            str           # inverse of yes_means
    contract_direction:  str           # see module docstring
    semantics_confidence: float        # 0.0 – 1.0
    is_semantics_clear:  bool
    needs_review_reason: Optional[str]


def _unclear(
    settlement_horizon: str,
    reason: str,
    spread_value: Optional[float] = None,
) -> MarketSemantics:
    return MarketSemantics(
        settlement_horizon=settlement_horizon,
        selected_team_abbr=None,
        opponent_team_abbr=None,
        spread_value=spread_value,
        yes_means="unknown",
        no_means="unknown",
        contract_direction="unknown",
        semantics_confidence=0.0,
        is_semantics_clear=False,
        needs_review_reason=reason,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_totals_direction(
    rules_primary: Optional[str],
    title: Optional[str],
    subtitle: Optional[str],
    is_f5: bool,
) -> tuple[str, str, str, float]:
    """
    Return (contract_direction, yes_means, no_means, confidence).
    Tries rules_primary first (confidence 1.0), then title/subtitle (0.8).
    Returns ("unknown", "unknown", "unknown", 0.0) when nothing is clear.
    """
    over_dir   = "f5_over_yes"   if is_f5 else "over_yes"
    under_dir  = "f5_under_yes"  if is_f5 else "under_yes"
    yes_over   = "f5_over"       if is_f5 else "over"
    yes_under  = "f5_under"      if is_f5 else "under"
    no_over    = "f5_under"      if is_f5 else "under"
    no_under   = "f5_over"       if is_f5 else "over"

    # ── rules_primary: look for explicit YES settlement condition ────────────
    rules = rules_primary or ""
    if rules.strip():
        # "exceed" only counts as over when not negated ("do not exceed" → under)
        over_basic  = bool(_RULES_YES_OVER.search(rules))
        exceed_hit  = bool(_RULES_YES_EXCEED.search(rules))
        neg_exceed  = bool(_RULES_NEG_EXCEED.search(rules))
        found_over  = over_basic or (exceed_hit and not neg_exceed)
        found_under = bool(_RULES_YES_UNDER.search(rules))

        if found_over and not found_under:
            return (over_dir, yes_over, no_over, 1.0)
        if found_under and not found_over:
            return (under_dir, yes_under, no_under, 1.0)
        if found_over and found_under:
            # Two distinct YES conditions — genuinely ambiguous.
            return ("unknown", "unknown", "unknown", 0.0)
        # No match in rules — fall through to title

    # ── title / subtitle: require "over N" or "under N" ─────────────────────
    for text in [title or "", subtitle or ""]:
        if not text.strip():
            continue
        has_over  = bool(_TITLE_OVER_N.search(text))
        has_under = bool(_TITLE_UNDER_N.search(text))
        if has_over and not has_under:
            return (over_dir, yes_over, no_over, 0.8)
        if has_under and not has_over:
            return (under_dir, yes_under, no_under, 0.8)
        if has_over and has_under:
            return ("unknown", "unknown", "unknown", 0.0)

    return ("unknown", "unknown", "unknown", 0.0)


def _find_team_abbr_in_text(
    text: str,
    away: Optional[str],
    home: Optional[str],
) -> Optional[str]:
    """
    Return the team abbreviation that appears in text.
    Returns None if zero or both team abbreviations appear.
    """
    text_u = text.upper()
    away_u = (away or "").upper().strip()
    home_u = (home or "").upper().strip()

    found_away = bool(away_u) and away_u in text_u
    found_home = bool(home_u) and home_u in text_u

    if found_away and not found_home:
        return away_u
    if found_home and not found_away:
        return home_u
    return None


def _parse_team_from_ticker(
    ticker: str,
    away: Optional[str],
    home: Optional[str],
) -> Optional[str]:
    """
    Parse ticker last dash-segment and match to a known team abbreviation.
    KXMLBGAME-26JUN121937NYYTOR-NYY → "NYY"
    Returns None if no match.
    """
    segs = ticker.split("-")
    if len(segs) < 2:
        return None
    candidate = segs[-1].upper().strip()
    away_u = (away or "").upper().strip()
    home_u = (home or "").upper().strip()
    if away_u and candidate == away_u:
        return away_u
    if home_u and candidate == home_u:
        return home_u
    return None


def _parse_moneyline_team(
    ticker: str,
    title: Optional[str],
    subtitle: Optional[str],
    away: Optional[str],
    home: Optional[str],
) -> tuple[Optional[str], float]:
    """
    Return (selected_team_abbr, confidence) for a moneyline market.
    Tries ticker last segment first (confidence 0.9), then title (0.9).
    Returns (None, 0.0) if the YES team cannot be identified unambiguously.
    """
    # Ticker last segment (most reliable Kalshi encoding)
    from_ticker = _parse_team_from_ticker(ticker, away, home)
    if from_ticker:
        return (from_ticker, 0.9)

    # Title: "[ABBR] wins" or "[ABBR] to win"
    for text in [title or "", subtitle or ""]:
        if not text.strip():
            continue
        matches = _TITLE_WINS_RE.findall(text)
        unique = {m.upper() for m in matches}

        away_u = (away or "").upper().strip()
        home_u = (home or "").upper().strip()

        # Filter to only known abbreviations
        known = unique & {away_u, home_u}
        if len(known) == 1:
            return (known.pop(), 0.9)

    return (None, 0.0)


def _parse_team_total_team(
    ticker: str,
    title: Optional[str],
    subtitle: Optional[str],
    rules_primary: Optional[str],
    away: Optional[str],
    home: Optional[str],
) -> Optional[str]:
    """
    Identify which team's total the market is tracking.
    Searches ticker last segment, then title, then rules_primary.
    Returns None if ambiguous.
    """
    # Try ticker last segment
    from_ticker = _parse_team_from_ticker(ticker, away, home)
    if from_ticker:
        return from_ticker

    # Try title, subtitle, rules_primary
    for text in [title or "", subtitle or "", rules_primary or ""]:
        found = _find_team_abbr_in_text(text, away, home)
        if found:
            return found

    return None


# ── Public API ────────────────────────────────────────────────────────────────

def parse_market_semantics(
    *,
    market_type: Optional[str],
    market_ticker: str,
    title: Optional[str],
    subtitle: Optional[str],
    rules_primary: Optional[str],
    away_team: Optional[str],
    home_team: Optional[str],
    line_value: Optional[float] = None,
) -> MarketSemantics:
    """
    Parse market semantics from Kalshi metadata.

    All inputs are treated as Kalshi-supplied fields only — no app-internal
    fields (signal_type, signal_subtype, strategy names) are consulted.

    Returns a MarketSemantics dataclass.  When is_semantics_clear=False,
    needs_review_reason will always be populated.
    """
    mtype = (market_type or "").lower().strip()
    horizon = _HORIZON_MAP.get(mtype, "unknown")

    away = (away_team or "").upper().strip() or None
    home = (home_team or "").upper().strip() or None

    # ── Spread markets — always unclear ──────────────────────────────────────
    if mtype in ("spread_run_line", "f5_spread"):
        return _unclear(
            horizon,
            reason="spread_direction_requires_manual_review: "
                   "YES/NO meaning for spreads cannot be reliably derived "
                   "from market metadata alone",
        )

    # ── Unsupported / unknown market types ───────────────────────────────────
    if mtype in ("unknown", "", "extra_innings", "run_first_inning",
                 "championship_futures", "f5_winner"):
        return _unclear(
            horizon,
            reason=f"market_type_not_supported_for_semantic_parsing: {mtype!r}",
        )

    # ── Player props — horizon only, direction is player-specific ────────────
    if horizon == "player_prop":
        return _unclear(
            horizon,
            reason="player_prop_direction_requires_player_context",
        )

    # ── Full-game and F5 totals ───────────────────────────────────────────────
    if mtype in ("full_game_total", "f5_total"):
        is_f5 = mtype == "f5_total"
        direction, yes_means, no_means, confidence = _parse_totals_direction(
            rules_primary, title, subtitle, is_f5
        )

        if direction == "unknown":
            return _unclear(
                horizon,
                reason="totals_direction_not_found_in_rules_or_title: "
                       "rules_primary absent or lacks explicit YES settlement condition; "
                       "title/subtitle absent or lacks 'over/under N' pattern",
            )

        return MarketSemantics(
            settlement_horizon=horizon,
            selected_team_abbr=None,
            opponent_team_abbr=None,
            spread_value=None,
            yes_means=yes_means,
            no_means=no_means,
            contract_direction=direction,
            semantics_confidence=confidence,
            is_semantics_clear=True,
            needs_review_reason=None,
        )

    # ── Team total ────────────────────────────────────────────────────────────
    if mtype == "team_total":
        is_f5 = False  # team_total is always full_game horizon for now
        direction, yes_means, no_means, confidence = _parse_totals_direction(
            rules_primary, title, subtitle, is_f5
        )

        if direction == "unknown":
            return _unclear(
                horizon,
                reason="team_total_direction_not_found_in_rules_or_title",
            )

        # Remap direction to team_total variants
        if direction == "over_yes":
            direction = "team_total_over_yes"
            yes_means = "team_total_over"
            no_means  = "team_total_under"
        elif direction == "under_yes":
            direction = "team_total_under_yes"
            yes_means = "team_total_under"
            no_means  = "team_total_over"

        # Identify which team is the subject
        team = _parse_team_total_team(
            market_ticker, title, subtitle, rules_primary, away, home
        )
        if team is None:
            return _unclear(
                horizon,
                reason="team_total_selected_team_not_identified: "
                       "could not determine unambiguously which team's "
                       "total the market tracks from ticker, title, or rules",
            )

        opponent = home if team == away else away

        return MarketSemantics(
            settlement_horizon=horizon,
            selected_team_abbr=team,
            opponent_team_abbr=opponent,
            spread_value=None,
            yes_means=yes_means,
            no_means=no_means,
            contract_direction=direction,
            semantics_confidence=confidence,
            is_semantics_clear=True,
            needs_review_reason=None,
        )

    # ── Moneyline ─────────────────────────────────────────────────────────────
    if mtype == "moneyline":
        selected, confidence = _parse_moneyline_team(
            market_ticker, title, subtitle, away, home
        )
        if selected is None:
            return _unclear(
                horizon,
                reason="moneyline_yes_team_not_identified: "
                       "could not determine which team is the YES contract "
                       "from ticker last segment or title '[ABBR] wins/to win' pattern",
            )

        opponent = home if selected == away else away
        yes_means = f"{selected}_wins"
        no_means  = f"{opponent}_wins_or_tie" if opponent else "opponent_wins_or_tie"

        return MarketSemantics(
            settlement_horizon=horizon,
            selected_team_abbr=selected,
            opponent_team_abbr=opponent,
            spread_value=None,
            yes_means=yes_means,
            no_means=no_means,
            contract_direction="moneyline_yes",
            semantics_confidence=confidence,
            is_semantics_clear=True,
            needs_review_reason=None,
        )

    # ── Fallback ──────────────────────────────────────────────────────────────
    return _unclear(
        horizon,
        reason=f"unhandled_market_type: {mtype!r}",
    )


def refresh_market_semantics(conn: sqlite3.Connection) -> dict:
    """
    Backfill semantics columns for all rows in kalshi_markets.
    Safe to call multiple times (idempotent — UPDATE always overwrites).

    Returns {"total": N, "updated_clear": N, "updated_unclear": N}.
    """
    rows = conn.execute("SELECT * FROM kalshi_markets").fetchall()
    clear = unclear = 0

    for row in rows:
        sem = parse_market_semantics(
            market_type=row["market_type"],
            market_ticker=row["market_ticker"] or "",
            title=row["title"],
            subtitle=row["subtitle"] if "subtitle" in row.keys() else None,
            rules_primary=row["rules_primary"] if "rules_primary" in row.keys() else None,
            away_team=row["away_team"],
            home_team=row["home_team"],
            line_value=row["line_value"],
        )
        conn.execute(
            """UPDATE kalshi_markets SET
               settlement_horizon   = ?,
               selected_team_abbr   = ?,
               opponent_team_abbr   = ?,
               yes_means            = ?,
               no_means             = ?,
               contract_direction   = ?,
               semantics_confidence = ?,
               is_semantics_clear   = ?,
               needs_review_reason  = ?
               WHERE id = ?""",
            (
                sem.settlement_horizon,
                sem.selected_team_abbr,
                sem.opponent_team_abbr,
                sem.yes_means,
                sem.no_means,
                sem.contract_direction,
                sem.semantics_confidence,
                int(sem.is_semantics_clear),
                sem.needs_review_reason,
                row["id"],
            ),
        )
        if sem.is_semantics_clear:
            clear += 1
        else:
            unclear += 1

    conn.commit()
    return {"total": clear + unclear, "updated_clear": clear, "updated_unclear": unclear}
