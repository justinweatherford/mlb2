"""
mlb/candidates.py — Storage helpers for candidate_events.

Observation-only: nothing here opens paper positions or places real trades.
Candidates are surfaced for manual review; all eligibility flags default to
the safest values (eligible_for_paper=0, status='observed_only').
"""
import sqlite3
from datetime import datetime
from typing import Any, Optional


_VALID_STATUSES = frozenset({
    "observed_only",
    "blocked",
    "manual_review",
    "paper_eligible_later",
    "dismissed",
})

# mlb_games.status values that mean a game is currently in progress.
# Used by Current Setups mode to exclude final/preview candidates.
_LIVE_GAME_STATUSES_SQL = "('Live', 'In Progress', 'live', 'in_progress')"
_LIVE_GAME_FILTER = (
    f"game_pk IN (SELECT game_pk FROM mlb_games WHERE status IN {_LIVE_GAME_STATUSES_SQL})"
)


def _compute_dedupe_key(
    game_id: Optional[str],
    market_ticker: Optional[str],
    candidate_type: str,
    inning: Optional[int],
    half_inning: Optional[str],
    score_away: Optional[int],
    score_home: Optional[int],
    status: str,
    blocked_reason: Optional[str],
    entry_yes_bid: Optional[int],
    entry_yes_ask: Optional[int],
) -> str:
    """
    Stable key that identifies a 'same setup' candidate.
    Price is bucketed to 5-cent intervals so tiny tick moves don't create new rows.
    A new row IS created when inning, score state, status, or price bucket changes.
    """
    mid = ((entry_yes_bid or 0) + (entry_yes_ask or 0)) / 2.0 if (entry_yes_bid and entry_yes_ask) else 0.0
    price_bucket = int(round(mid / 5.0) * 5)
    return "|".join([
        game_id or "",
        market_ticker or "",
        candidate_type,
        str(inning) if inning is not None else "",
        half_inning or "",
        str(score_away) if score_away is not None else "",
        str(score_home) if score_home is not None else "",
        status,
        blocked_reason or "",
        str(price_bucket),
    ])


def _now() -> str:
    return datetime.now().isoformat()


def insert_candidate_event(
    conn: sqlite3.Connection,
    *,
    candidate_type: str,
    game_pk: Optional[int] = None,
    game_id: Optional[str] = None,
    market_ticker: Optional[str] = None,
    event_ticker: Optional[str] = None,
    market_type: Optional[str] = None,
    settlement_horizon: str = "unknown",
    selected_team_abbr: Optional[str] = None,
    line_value: Optional[float] = None,
    side: Optional[str] = None,
    decision_time: Optional[str] = None,
    available_data_cutoff: Optional[str] = None,
    mlb_play_event_id: Optional[str] = None,
    trigger_event_type: Optional[str] = None,
    trigger_description: Optional[str] = None,
    inning: Optional[int] = None,
    half_inning: Optional[str] = None,
    outs: Optional[int] = None,
    score_away: Optional[int] = None,
    score_home: Optional[int] = None,
    runners_state: Optional[str] = None,
    entry_yes_bid: Optional[int] = None,
    entry_yes_ask: Optional[int] = None,
    entry_no_bid: Optional[int] = None,
    entry_no_ask: Optional[int] = None,
    spread_cents: Optional[int] = None,
    expected_fill_price: Optional[int] = None,
    market_mismatch_score: Optional[float] = None,
    baseball_support_score: Optional[float] = None,
    execution_quality_score: Optional[float] = None,
    risk_blocker_score: Optional[float] = None,
    overall_watch_score: Optional[float] = None,
    confidence_breakdown_json: Optional[str] = None,
    baseball_context_json: Optional[str] = None,
    market_context_json: Optional[str] = None,
    guardrails_json: Optional[str] = None,
    blocked_reason: Optional[str] = None,
    eligible_for_paper: int = 0,
    status: str = "observed_only",
    opening_price_cents: Optional[int] = None,
    current_mid_price_cents: Optional[int] = None,
    price_delta_from_open_cents: Optional[int] = None,
    has_baseline_price: int = 0,
    implied_probability_open: Optional[float] = None,
    implied_probability_current: Optional[float] = None,
    baseline_explanation: Optional[str] = None,
    baseline_source: Optional[str] = None,
    baseline_quality: Optional[str] = None,
    derivative_type: Optional[str] = None,
    read_type: Optional[str] = None,
    selected_derivative_type: Optional[str] = None,
    derivative_rationale: Optional[str] = None,
    rejected_derivatives_json: Optional[str] = None,
    trigger_game_date: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    first_seen_at: Optional[str] = None,
    last_seen_at: Optional[str] = None,
    seen_count: int = 1,
) -> int:
    """Insert a candidate_events row. Returns the new row id."""
    now = _now()
    ts = first_seen_at or now
    cur = conn.execute(
        """
        INSERT INTO candidate_events (
            candidate_type, game_pk, game_id,
            market_ticker, event_ticker, market_type,
            settlement_horizon, selected_team_abbr,
            line_value, side, decision_time, available_data_cutoff,
            mlb_play_event_id, trigger_event_type, trigger_description,
            inning, half_inning, outs,
            score_away, score_home, runners_state,
            entry_yes_bid, entry_yes_ask, entry_no_bid, entry_no_ask,
            spread_cents, expected_fill_price,
            market_mismatch_score, baseball_support_score,
            execution_quality_score, risk_blocker_score, overall_watch_score,
            confidence_breakdown_json, baseball_context_json,
            market_context_json, guardrails_json,
            blocked_reason, eligible_for_paper, status,
            opening_price_cents, current_mid_price_cents, price_delta_from_open_cents,
            has_baseline_price, implied_probability_open, implied_probability_current,
            baseline_explanation, baseline_source, baseline_quality,
            derivative_type, read_type, selected_derivative_type,
            derivative_rationale, rejected_derivatives_json,
            trigger_game_date,
            dedupe_key, first_seen_at, last_seen_at, seen_count,
            created_at, updated_at
        ) VALUES (
            ?,?,?,  ?,?,?,  ?,?,  ?,?,?,?,
            ?,?,?,  ?,?,?,  ?,?,?,
            ?,?,?,?,  ?,?,
            ?,?,  ?,?,?,
            ?,?,  ?,?,
            ?,?,?,  ?,?,?,  ?,?,?,  ?,?,?,
            ?,?,?,?,?,
            ?,
            ?,?,?,?,  ?,?
        )
        """,
        (
            candidate_type, game_pk, game_id,
            market_ticker, event_ticker, market_type,
            settlement_horizon, selected_team_abbr,
            line_value, side, decision_time, available_data_cutoff,
            mlb_play_event_id, trigger_event_type, trigger_description,
            inning, half_inning, outs,
            score_away, score_home, runners_state,
            entry_yes_bid, entry_yes_ask, entry_no_bid, entry_no_ask,
            spread_cents, expected_fill_price,
            market_mismatch_score, baseball_support_score,
            execution_quality_score, risk_blocker_score, overall_watch_score,
            confidence_breakdown_json, baseball_context_json,
            market_context_json, guardrails_json,
            blocked_reason, eligible_for_paper, status,
            opening_price_cents, current_mid_price_cents, price_delta_from_open_cents,
            has_baseline_price, implied_probability_open, implied_probability_current,
            baseline_explanation, baseline_source, baseline_quality,
            derivative_type, read_type, selected_derivative_type,
            derivative_rationale, rejected_derivatives_json,
            trigger_game_date,
            dedupe_key, ts, last_seen_at or ts, seen_count,
            now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def upsert_candidate_event(
    conn: sqlite3.Connection,
    *,
    candidate_type: str,
    game_pk: Optional[int] = None,
    game_id: Optional[str] = None,
    market_ticker: Optional[str] = None,
    event_ticker: Optional[str] = None,
    market_type: Optional[str] = None,
    settlement_horizon: str = "unknown",
    selected_team_abbr: Optional[str] = None,
    line_value: Optional[float] = None,
    side: Optional[str] = None,
    decision_time: Optional[str] = None,
    available_data_cutoff: Optional[str] = None,
    mlb_play_event_id: Optional[str] = None,
    trigger_event_type: Optional[str] = None,
    trigger_description: Optional[str] = None,
    inning: Optional[int] = None,
    half_inning: Optional[str] = None,
    outs: Optional[int] = None,
    score_away: Optional[int] = None,
    score_home: Optional[int] = None,
    runners_state: Optional[str] = None,
    entry_yes_bid: Optional[int] = None,
    entry_yes_ask: Optional[int] = None,
    entry_no_bid: Optional[int] = None,
    entry_no_ask: Optional[int] = None,
    spread_cents: Optional[int] = None,
    expected_fill_price: Optional[int] = None,
    market_mismatch_score: Optional[float] = None,
    baseball_support_score: Optional[float] = None,
    execution_quality_score: Optional[float] = None,
    risk_blocker_score: Optional[float] = None,
    overall_watch_score: Optional[float] = None,
    confidence_breakdown_json: Optional[str] = None,
    baseball_context_json: Optional[str] = None,
    market_context_json: Optional[str] = None,
    guardrails_json: Optional[str] = None,
    blocked_reason: Optional[str] = None,
    eligible_for_paper: int = 0,
    status: str = "observed_only",
    opening_price_cents: Optional[int] = None,
    current_mid_price_cents: Optional[int] = None,
    price_delta_from_open_cents: Optional[int] = None,
    has_baseline_price: int = 0,
    implied_probability_open: Optional[float] = None,
    implied_probability_current: Optional[float] = None,
    baseline_explanation: Optional[str] = None,
    baseline_source: Optional[str] = None,
    baseline_quality: Optional[str] = None,
    derivative_type: Optional[str] = None,
    read_type: Optional[str] = None,
    selected_derivative_type: Optional[str] = None,
    derivative_rationale: Optional[str] = None,
    rejected_derivatives_json: Optional[str] = None,
    trigger_game_date: Optional[str] = None,
) -> tuple[int, bool]:
    """
    Insert or deduplicate a candidate_event.

    Returns (id, is_new):
      is_new=True  — a fresh row was inserted
      is_new=False — an existing same-setup row was found; last_seen_at and
                     seen_count were updated, no new row created

    Dedup key covers: game_id, market_ticker, candidate_type, inning,
    half_inning, score state, status, blocked_reason, and 5-cent price bucket.
    A new row is created whenever any of those change (e.g. score advances,
    inning flips, price moves ≥ 5¢ bucket boundary).
    """
    now = _now()
    today = now[:10]

    key = _compute_dedupe_key(
        game_id, market_ticker, candidate_type,
        inning, half_inning, score_away, score_home,
        status, blocked_reason, entry_yes_bid, entry_yes_ask,
    )

    existing = conn.execute(
        "SELECT id, seen_count FROM candidate_events "
        "WHERE dedupe_key = ? AND DATE(first_seen_at) = ? "
        "ORDER BY id DESC LIMIT 1",
        (key, today),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE candidate_events SET last_seen_at=?, seen_count=?, updated_at=? WHERE id=?",
            (now, existing["seen_count"] + 1, now, existing["id"]),
        )
        conn.commit()
        return existing["id"], False

    cid = insert_candidate_event(
        conn,
        candidate_type=candidate_type,
        game_pk=game_pk,
        game_id=game_id,
        market_ticker=market_ticker,
        event_ticker=event_ticker,
        market_type=market_type,
        settlement_horizon=settlement_horizon,
        selected_team_abbr=selected_team_abbr,
        line_value=line_value,
        side=side,
        decision_time=decision_time,
        available_data_cutoff=available_data_cutoff,
        mlb_play_event_id=mlb_play_event_id,
        trigger_event_type=trigger_event_type,
        trigger_description=trigger_description,
        inning=inning,
        half_inning=half_inning,
        outs=outs,
        score_away=score_away,
        score_home=score_home,
        runners_state=runners_state,
        entry_yes_bid=entry_yes_bid,
        entry_yes_ask=entry_yes_ask,
        entry_no_bid=entry_no_bid,
        entry_no_ask=entry_no_ask,
        spread_cents=spread_cents,
        expected_fill_price=expected_fill_price,
        market_mismatch_score=market_mismatch_score,
        baseball_support_score=baseball_support_score,
        execution_quality_score=execution_quality_score,
        risk_blocker_score=risk_blocker_score,
        overall_watch_score=overall_watch_score,
        confidence_breakdown_json=confidence_breakdown_json,
        baseball_context_json=baseball_context_json,
        market_context_json=market_context_json,
        guardrails_json=guardrails_json,
        blocked_reason=blocked_reason,
        eligible_for_paper=eligible_for_paper,
        status=status,
        opening_price_cents=opening_price_cents,
        current_mid_price_cents=current_mid_price_cents,
        price_delta_from_open_cents=price_delta_from_open_cents,
        has_baseline_price=has_baseline_price,
        implied_probability_open=implied_probability_open,
        implied_probability_current=implied_probability_current,
        baseline_explanation=baseline_explanation,
        baseline_source=baseline_source,
        baseline_quality=baseline_quality,
        derivative_type=derivative_type,
        read_type=read_type,
        selected_derivative_type=selected_derivative_type,
        derivative_rationale=derivative_rationale,
        rejected_derivatives_json=rejected_derivatives_json,
        trigger_game_date=trigger_game_date,
        dedupe_key=key,
        first_seen_at=now,
        last_seen_at=now,
        seen_count=1,
    )
    return cid, True


def _broad_setup_key(row: Any) -> str:
    """Broad grouping key for Current Setups view — ignores game-state changes.

    Groups by the setup identity (what we're watching) rather than the exact
    game state (score/inning/runners/status) at the moment of observation.
    """
    return "|".join([
        row["game_id"] or "",
        row["market_ticker"] or "",
        row["derivative_type"] or "",
        row["read_type"] or "",
        row["selected_derivative_type"] or "",
        row["candidate_type"] or "",
    ])


def list_candidate_events(
    conn: sqlite3.Connection,
    *,
    game_pk: Optional[int] = None,
    game_id: Optional[str] = None,
    candidate_type: Optional[str] = None,
    status: Optional[str] = None,
    eligible_for_paper: Optional[int] = None,
    exclude_blocked_reason: Optional[str] = "duplicate_candidate",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    live_games_only: bool = False,
    latest_unique: bool = False,
    current_setups: bool = False,
    limit: int = 100,
) -> list[Any]:
    """Return candidate_events rows newest-first. All filters are optional AND-combined.

    exclude_blocked_reason: suppress rows whose blocked_reason matches this value.
    Defaults to 'duplicate_candidate' — those rows are internal dedup artifacts
    that existed before dedup was moved into upsert_candidate_event.
    Pass None to see all rows including those.

    live_games_only: when True, restrict to candidates whose game_pk is linked to
    a currently live game in mlb_games (status in Live/In Progress).  Used by
    Current Setups mode so historical final/preview candidates are excluded.

    current_setups: when True, collapse rows by the broad setup key
    (game_id + market_ticker + derivative_type + read_type + selected_derivative_type
    + candidate_type), ignoring game-state changes (score/inning/status).
    Returns one latest row per setup with seen_count summed across all rows in
    the group.  Takes priority over latest_unique.

    latest_unique: when True, collapse rows with the same dedupe_key by returning
    only the most-recent row per key.  Rows with no dedupe_key are always returned.
    """
    where: list[str] = []
    filter_params: list[Any] = []

    if game_pk is not None:
        where.append("game_pk = ?")
        filter_params.append(game_pk)
    if game_id is not None:
        where.append("game_id = ?")
        filter_params.append(game_id)
    if candidate_type is not None:
        where.append("candidate_type = ?")
        filter_params.append(candidate_type)
    if status is not None:
        where.append("status = ?")
        filter_params.append(status)
    if eligible_for_paper is not None:
        where.append("eligible_for_paper = ?")
        filter_params.append(eligible_for_paper)
    if exclude_blocked_reason is not None:
        where.append("(blocked_reason IS NULL OR blocked_reason != ?)")
        filter_params.append(exclude_blocked_reason)
    if date_from is not None:
        where.append("DATE(created_at) >= ?")
        filter_params.append(date_from)
    if date_to is not None:
        where.append("DATE(created_at) <= ?")
        filter_params.append(date_to)
    if live_games_only:
        where.append(_LIVE_GAME_FILTER)

    clause = " WHERE " + " AND ".join(where) if where else ""

    if current_setups:
        # Fetch all matching rows ordered by latest last_seen_at first.
        # Deduplicate in Python by broad setup key, aggregating seen_count.
        all_rows = conn.execute(
            f"SELECT * FROM candidate_events{clause} "
            f"ORDER BY COALESCE(last_seen_at, created_at) DESC, id DESC",
            filter_params,
        ).fetchall()

        # First pass: aggregate seen_count and earliest first_seen_at per broad key.
        seen_counts: dict[str, int] = {}
        min_first_seen: dict[str, str] = {}
        for row in all_rows:
            key = _broad_setup_key(row)
            seen_counts[key] = seen_counts.get(key, 0) + (row["seen_count"] or 1)
            fsa = row["first_seen_at"] or row["created_at"] or ""
            if fsa and (key not in min_first_seen or fsa < min_first_seen[key]):
                min_first_seen[key] = fsa

        # Second pass: keep the latest row per broad key; inject aggregated count + earliest first_seen_at.
        seen_keys: set[str] = set()
        result: list[dict] = []
        for row in all_rows:
            key = _broad_setup_key(row)
            if key not in seen_keys:
                seen_keys.add(key)
                d = dict(row)
                d["seen_count"] = seen_counts[key]
                d["first_seen_at"] = min_first_seen.get(key, d.get("first_seen_at"))
                result.append(d)
            if len(result) >= limit:
                break

        return result

    if latest_unique:
        # Fetch all matching rows ordered newest-first; deduplicate in Python
        # keeping only the highest-id row per dedupe_key.
        all_rows = conn.execute(
            f"SELECT * FROM candidate_events{clause} ORDER BY id DESC",
            filter_params,
        ).fetchall()
        seen: set[str] = set()
        unique_result: list[sqlite3.Row] = []
        for row in all_rows:
            key = row["dedupe_key"] or str(row["id"])
            if key not in seen:
                seen.add(key)
                unique_result.append(row)
        return unique_result[:limit]

    return conn.execute(
        f"SELECT * FROM candidate_events{clause} ORDER BY created_at DESC LIMIT ?",
        filter_params + [limit],
    ).fetchall()


def get_candidate_event(
    conn: sqlite3.Connection,
    candidate_id: int,
) -> Optional[sqlite3.Row]:
    """Return a single candidate_events row by id, or None."""
    return conn.execute(
        "SELECT * FROM candidate_events WHERE id = ?", (candidate_id,)
    ).fetchone()


def update_candidate_status(
    conn: sqlite3.Connection,
    candidate_id: int,
    status: str,
) -> bool:
    """Update a candidate's status. Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE candidate_events SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), candidate_id),
    )
    conn.commit()
    return cur.rowcount > 0


def backfill_candidate_derivative_metadata(conn: sqlite3.Connection) -> dict:
    """
    Backfill derivative_type, read_type, selected_derivative_type,
    derivative_rationale, and rejected_derivatives_json for existing
    candidate_events rows where these fields are NULL or 'unknown'.

    Uses derive_candidate_metadata(candidate_type) to resolve known types.
    Rows whose candidate_type maps to 'unknown' are left unchanged.

    Returns a summary dict: {scanned, updated, skipped_unknown}.
    """
    from mlb.derivatives import derive_candidate_metadata  # avoid circular import

    rows = conn.execute(
        """
        SELECT id, candidate_type
        FROM candidate_events
        WHERE derivative_type IS NULL OR derivative_type = 'unknown'
        """
    ).fetchall()

    now = _now()
    updated = 0
    skipped = 0

    for row in rows:
        meta = derive_candidate_metadata(row["candidate_type"])
        if meta.get("derivative_type") == "unknown":
            skipped += 1
            continue
        conn.execute(
            """
            UPDATE candidate_events
            SET derivative_type          = ?,
                read_type                = ?,
                selected_derivative_type = ?,
                derivative_rationale     = ?,
                rejected_derivatives_json = ?,
                updated_at               = ?
            WHERE id = ?
            """,
            (
                meta["derivative_type"],
                meta["read_type"],
                meta["selected_derivative_type"],
                meta["derivative_rationale"],
                meta["rejected_derivatives_json"],
                now,
                row["id"],
            ),
        )
        updated += 1

    conn.commit()
    return {"scanned": len(rows), "updated": updated, "skipped_unknown": skipped}
