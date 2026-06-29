import argparse
import csv
import os
import sqlite3
from statistics import mean


SUPPORTED_TYPES = {
    "full_game_total",
    "team_total",
    "f5_total",
    "moneyline",
    "spread_run_line",
    "f5_spread",
}


def safe_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def side_entry_price(candidate):
    side = (candidate.get("side") or "YES").upper()

    expected_fill = safe_int(candidate.get("expected_fill_price"))
    if expected_fill is not None:
        return expected_fill

    yes_bid = safe_int(candidate.get("entry_yes_bid"))
    yes_ask = safe_int(candidate.get("entry_yes_ask"))

    if side == "NO":
        if yes_bid is not None:
            return 100 - yes_bid
        if yes_bid is not None and yes_ask is not None:
            return 100 - ((yes_bid + yes_ask) / 2)
        return None

    if yes_ask is not None:
        return yes_ask

    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / 2

    if yes_bid is not None:
        return yes_bid

    return None


def table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def get_candidates(conn, date):
    rows = conn.execute(
        """
        SELECT *
        FROM candidate_events
        WHERE DATE(created_at) = ?
        ORDER BY created_at ASC, id ASC
        """,
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_market(conn, ticker):
    if not ticker or not table_exists(conn, "kalshi_markets"):
        return None

    row = conn.execute(
        """
        SELECT *
        FROM kalshi_markets
        WHERE market_ticker = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()

    return dict(row) if row else None


def get_game(conn, candidate, date):
    game_pk = candidate.get("game_pk")
    game_id = candidate.get("game_id")

    if game_pk:
        row = conn.execute(
            """
            SELECT *
            FROM mlb_games
            WHERE game_pk = ?
            LIMIT 1
            """,
            (game_pk,),
        ).fetchone()
        if row:
            return dict(row)

    if game_id:
        row = conn.execute(
            """
            SELECT *
            FROM mlb_games
            WHERE game_id = ?
              AND game_date = ?
            ORDER BY is_final DESC, last_checked_at DESC
            LIMIT 1
            """,
            (game_id, date),
        ).fetchone()
        if row:
            return dict(row)

        row = conn.execute(
            """
            SELECT *
            FROM mlb_games
            WHERE game_id = ?
            ORDER BY game_date DESC, is_final DESC, last_checked_at DESC
            LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        if row:
            return dict(row)

    return None


def get_f5_score(conn, game_pk):
    if not game_pk:
        return None

    rows = conn.execute(
        """
        SELECT inning, away_runs, home_runs
        FROM mlb_inning_scores
        WHERE game_pk = ?
          AND inning BETWEEN 1 AND 5
        ORDER BY inning ASC
        """,
        (game_pk,),
    ).fetchall()

    if not rows:
        return None

    innings_seen = {safe_int(r["inning"]) for r in rows}
    away = sum(safe_int(r["away_runs"]) or 0 for r in rows)
    home = sum(safe_int(r["home_runs"]) or 0 for r in rows)

    return {
        "f5_away_score": away,
        "f5_home_score": home,
        "f5_total": away + home,
        "f5_innings_seen": ",".join(str(i) for i in sorted(innings_seen) if i is not None),
        "f5_has_all_5": all(i in innings_seen for i in range(1, 6)),
    }


def latest_market_inferred_settlement(conn, ticker):
    if not ticker:
        return None

    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, mid_cents, last_price, snapped_at, source
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()

    if not row:
        return None

    d = dict(row)
    prices = [
        safe_int(d.get("yes_bid")),
        safe_int(d.get("yes_ask")),
        safe_int(d.get("mid_cents")),
        safe_int(d.get("last_price")),
    ]
    prices = [p for p in prices if p is not None]

    if not prices:
        return {
            "market_inferred_yes": None,
            "market_inferred_reason": "no_recent_price",
            "market_inferred_at": d.get("snapped_at"),
            "market_inferred_source": d.get("source"),
        }

    avg_price = mean(prices)

    if avg_price >= 98:
        yes_value = 100
        reason = "latest_market_price_near_100"
    elif avg_price <= 2:
        yes_value = 0
        reason = "latest_market_price_near_0"
    else:
        yes_value = None
        reason = "latest_market_price_not_settled"

    return {
        "market_inferred_yes": yes_value,
        "market_inferred_reason": reason,
        "market_inferred_avg_price": round(avg_price, 3),
        "market_inferred_at": d.get("snapped_at"),
        "market_inferred_source": d.get("source"),
    }


def selected_team(candidate, market):
    return (
        candidate.get("selected_team_abbr")
        or (market or {}).get("selected_team_abbr")
        or None
    )


def line_value(candidate, market):
    return safe_float(candidate.get("line_value")) if candidate.get("line_value") is not None else safe_float((market or {}).get("line_value"))


def spread_value(candidate, market):
    market_spread = safe_float((market or {}).get("spread_value"))
    if market_spread is not None:
        return market_spread

    return safe_float(candidate.get("line_value"))


def team_score(team, game):
    if not team or not game:
        return None

    team = team.upper()

    if team == str(game.get("away_abbr") or "").upper():
        return safe_int(game.get("final_away_score"))

    if team == str(game.get("home_abbr") or "").upper():
        return safe_int(game.get("final_home_score"))

    return None


def opponent_score(team, game):
    if not team or not game:
        return None

    team = team.upper()

    if team == str(game.get("away_abbr") or "").upper():
        return safe_int(game.get("final_home_score"))

    if team == str(game.get("home_abbr") or "").upper():
        return safe_int(game.get("final_away_score"))

    return None


def f5_team_score(team, game, f5):
    if not team or not game or not f5:
        return None

    team = team.upper()

    if team == str(game.get("away_abbr") or "").upper():
        return safe_int(f5.get("f5_away_score"))

    if team == str(game.get("home_abbr") or "").upper():
        return safe_int(f5.get("f5_home_score"))

    return None


def f5_opponent_score(team, game, f5):
    if not team or not game or not f5:
        return None

    team = team.upper()

    if team == str(game.get("away_abbr") or "").upper():
        return safe_int(f5.get("f5_home_score"))

    if team == str(game.get("home_abbr") or "").upper():
        return safe_int(f5.get("f5_away_score"))

    return None


def total_yes_wins(total_runs, line, yes_means):
    if total_runs is None or line is None:
        return None, "missing_total_or_line"

    text = (yes_means or "").lower()

    # Most Kalshi total markets are binary no-push thresholds.
    # For whole-number line_value, treat YES as total >= line unless metadata says otherwise.
    if "under" in text:
        return total_runs < line, "yes_means_under_score_derived"

    if "less" in text:
        return total_runs < line, "yes_means_less_score_derived"

    if "over" in text or "more" in text or "at_least" in text or "or_more" in text:
        if float(line).is_integer():
            return total_runs >= line, "yes_means_over_or_at_least_score_derived"
        return total_runs > line, "yes_means_over_score_derived"

    if float(line).is_integer():
        return total_runs >= line, "default_total_ge_line_score_derived"

    return total_runs > line, "default_total_gt_line_score_derived"


def settle_candidate(candidate, game, market, f5, market_inferred):
    market_type = candidate.get("market_type") or (market or {}).get("market_type")
    horizon = candidate.get("settlement_horizon") or (market or {}).get("settlement_horizon")
    side = (candidate.get("side") or "YES").upper()
    yes_means = (market or {}).get("yes_means")
    team = selected_team(candidate, market)
    line = line_value(candidate, market)
    spread = spread_value(candidate, market)

    if market_type not in SUPPORTED_TYPES:
        return {
            "settlement_status": "unsupported_market_type",
            "settlement_reason": f"unsupported market_type={market_type}",
            "settled_yes_value": None,
        }

    if not game:
        return {
            "settlement_status": "missing_game",
            "settlement_reason": "no matching mlb_games row",
            "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
        }

    if safe_int(game.get("is_final")) != 1:
        return {
            "settlement_status": "game_not_final",
            "settlement_reason": f"mlb_games status={game.get('status')}",
            "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
        }

    final_away = safe_int(game.get("final_away_score"))
    final_home = safe_int(game.get("final_home_score"))
    final_total = safe_int(game.get("final_total"))

    yes_wins = None
    reason = None

    if market_type == "full_game_total":
        yes_wins, reason = total_yes_wins(final_total, line, yes_means)

    elif market_type == "team_total":
        runs = team_score(team, game)
        yes_wins, reason = total_yes_wins(runs, line, yes_means)
        reason = f"team_total_{reason}"

    elif market_type == "f5_total":
        if not f5 or not f5.get("f5_has_all_5"):
            return {
                "settlement_status": "missing_f5_score",
                "settlement_reason": "missing complete innings 1-5 in mlb_inning_scores",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        yes_wins, reason = total_yes_wins(f5.get("f5_total"), line, yes_means)
        reason = f"f5_total_{reason}"

    elif market_type == "moneyline":
        if not team:
            return {
                "settlement_status": "missing_selected_team",
                "settlement_reason": "moneyline requires selected_team_abbr",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        runs = team_score(team, game)
        opp = opponent_score(team, game)

        if runs is None or opp is None:
            return {
                "settlement_status": "missing_team_score",
                "settlement_reason": "could not map selected team to final score",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        yes_wins = runs > opp
        reason = "moneyline_score_derived"

    elif market_type == "spread_run_line":
        if not team or spread is None:
            return {
                "settlement_status": "missing_spread_semantics",
                "settlement_reason": "spread requires selected_team_abbr and spread_value/line_value",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        runs = team_score(team, game)
        opp = opponent_score(team, game)

        if runs is None or opp is None:
            return {
                "settlement_status": "missing_team_score",
                "settlement_reason": "could not map selected team to final score",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        margin = runs - opp
        yes_wins = (margin + spread) > 0
        reason = "spread_score_derived_margin_plus_spread_gt_0"

    elif market_type == "f5_spread":
        if not f5 or not f5.get("f5_has_all_5"):
            return {
                "settlement_status": "missing_f5_score",
                "settlement_reason": "missing complete innings 1-5 in mlb_inning_scores",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        if not team or spread is None:
            return {
                "settlement_status": "missing_spread_semantics",
                "settlement_reason": "f5 spread requires selected_team_abbr and spread_value/line_value",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        runs = f5_team_score(team, game, f5)
        opp = f5_opponent_score(team, game, f5)

        if runs is None or opp is None:
            return {
                "settlement_status": "missing_f5_team_score",
                "settlement_reason": "could not map selected team to f5 score",
                "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
            }

        margin = runs - opp
        yes_wins = (margin + spread) > 0
        reason = "f5_spread_score_derived_margin_plus_spread_gt_0"

    if yes_wins is None:
        return {
            "settlement_status": "unsettled_or_unknown",
            "settlement_reason": reason or "could not derive settlement",
            "settled_yes_value": market_inferred.get("market_inferred_yes") if market_inferred else None,
        }

    return {
        "settlement_status": "score_derived",
        "settlement_reason": reason,
        "settled_yes_value": 100 if yes_wins else 0,
    }


def analyze_candidate(conn, candidate, date):
    market = get_market(conn, candidate.get("market_ticker"))
    game = get_game(conn, candidate, date)

    game_pk = candidate.get("game_pk") or (game or {}).get("game_pk")
    f5 = get_f5_score(conn, game_pk)
    market_inferred = latest_market_inferred_settlement(conn, candidate.get("market_ticker"))

    settlement = settle_candidate(candidate, game, market, f5, market_inferred)

    side = (candidate.get("side") or "YES").upper()
    settled_yes = settlement.get("settled_yes_value")

    settled_side = None
    if settled_yes is not None:
        settled_side = settled_yes if side == "YES" else 100 - settled_yes

    entry_price = side_entry_price(candidate)
    pnl = None
    roi = None

    if settled_side is not None and entry_price is not None:
        pnl = settled_side - entry_price
        roi = pnl / entry_price if entry_price else None

    row = {
        "candidate_id": candidate.get("id"),
        "candidate_type": candidate.get("candidate_type"),
        "game_id": candidate.get("game_id"),
        "game_pk": game_pk,
        "market_ticker": candidate.get("market_ticker"),
        "event_ticker": candidate.get("event_ticker"),
        "market_type": candidate.get("market_type"),
        "settlement_horizon": candidate.get("settlement_horizon"),
        "side": side,
        "line_value": candidate.get("line_value"),
        "selected_team_abbr": selected_team(candidate, market),
        "status": candidate.get("status"),
        "blocked_reason": candidate.get("blocked_reason"),
        "eligible_for_paper": candidate.get("eligible_for_paper"),
        "created_at": candidate.get("created_at"),
        "inning": candidate.get("inning"),
        "half_inning": candidate.get("half_inning"),
        "outs": candidate.get("outs"),
        "score_away_at_candidate": candidate.get("score_away"),
        "score_home_at_candidate": candidate.get("score_home"),
        "entry_yes_bid": candidate.get("entry_yes_bid"),
        "entry_yes_ask": candidate.get("entry_yes_ask"),
        "entry_side_price": entry_price,
        "spread_cents": candidate.get("spread_cents"),
        "overall_watch_score": candidate.get("overall_watch_score"),
        "trigger_event_type": candidate.get("trigger_event_type"),
        "trigger_description": candidate.get("trigger_description"),
        "final_away_score": (game or {}).get("final_away_score"),
        "final_home_score": (game or {}).get("final_home_score"),
        "final_total": (game or {}).get("final_total"),
        "game_status": (game or {}).get("status"),
        "game_is_final": (game or {}).get("is_final"),
        "f5_away_score": (f5 or {}).get("f5_away_score"),
        "f5_home_score": (f5 or {}).get("f5_home_score"),
        "f5_total": (f5 or {}).get("f5_total"),
        "f5_innings_seen": (f5 or {}).get("f5_innings_seen"),
        "f5_has_all_5": (f5 or {}).get("f5_has_all_5"),
        "market_yes_means": (market or {}).get("yes_means"),
        "market_no_means": (market or {}).get("no_means"),
        "market_contract_direction": (market or {}).get("contract_direction"),
        "market_semantics_confidence": (market or {}).get("semantics_confidence"),
        "market_is_semantics_clear": (market or {}).get("is_semantics_clear"),
        "market_spread_value": (market or {}).get("spread_value"),
        "market_line_value": (market or {}).get("line_value"),
        "settlement_status": settlement.get("settlement_status"),
        "settlement_reason": settlement.get("settlement_reason"),
        "settled_yes_value": settled_yes,
        "settled_side_value": settled_side,
        "hold_to_settle_win": 1 if settled_side == 100 else 0 if settled_side == 0 else None,
        "hold_to_settle_pnl_cents": pnl,
        "hold_to_settle_roi": round(roi, 4) if roi is not None else None,
        "market_inferred_yes": (market_inferred or {}).get("market_inferred_yes"),
        "market_inferred_reason": (market_inferred or {}).get("market_inferred_reason"),
        "market_inferred_avg_price": (market_inferred or {}).get("market_inferred_avg_price"),
        "market_inferred_at": (market_inferred or {}).get("market_inferred_at"),
        "market_inferred_source": (market_inferred or {}).get("market_inferred_source"),
    }

    return row


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return

    fieldnames = list(rows[0].keys())

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows, key_field, output_path):
    groups = {}
    for row in rows:
        key = row.get(key_field) or "(blank)"
        groups.setdefault(key, []).append(row)

    summary = []

    for key, items in groups.items():
        settled = [r for r in items if r.get("settled_side_value") is not None]
        wins = [r for r in settled if r.get("hold_to_settle_win") == 1]
        losses = [r for r in settled if r.get("hold_to_settle_win") == 0]

        pnls = [
            safe_float(r.get("hold_to_settle_pnl_cents"))
            for r in settled
            if safe_float(r.get("hold_to_settle_pnl_cents")) is not None
        ]

        entry_prices = [
            safe_float(r.get("entry_side_price"))
            for r in settled
            if safe_float(r.get("entry_side_price")) is not None
        ]

        total_risk = sum(entry_prices) if entry_prices else None
        total_pnl = sum(pnls) if pnls else None

        summary.append(
            {
                key_field: key,
                "count": len(items),
                "settled_count": len(settled),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(settled), 4) if settled else None,
                "avg_pnl_cents": round(mean(pnls), 3) if pnls else None,
                "total_pnl_cents": round(total_pnl, 3) if total_pnl is not None else None,
                "total_risk_cents": round(total_risk, 3) if total_risk is not None else None,
                "roi_on_risk": round(total_pnl / total_risk, 4) if total_pnl is not None and total_risk else None,
            }
        )

    summary.sort(key=lambda r: r["count"], reverse=True)
    write_csv(output_path, summary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", default="kalshi_mlb.db")
    args = parser.parse_args()

    out_dir = os.path.join("outputs", "candidate_settlement", args.date)
    os.makedirs(out_dir, exist_ok=True)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    candidates = get_candidates(conn, args.date)
    print(f"Loaded {len(candidates)} candidates from {args.date}")

    rows = []
    for i, candidate in enumerate(candidates, start=1):
        rows.append(analyze_candidate(conn, candidate, args.date))

        if i % 100 == 0:
            print(f"  analyzed {i}/{len(candidates)}")

    write_csv(os.path.join(out_dir, "candidate_settlement_outcomes.csv"), rows)
    summarize(rows, "candidate_type", os.path.join(out_dir, "summary_by_candidate_type.csv"))
    summarize(rows, "market_type", os.path.join(out_dir, "summary_by_market_type.csv"))
    summarize(rows, "blocked_reason", os.path.join(out_dir, "summary_by_blocked_reason.csv"))
    summarize(rows, "settlement_status", os.path.join(out_dir, "summary_by_settlement_status.csv"))
    summarize(rows, "status", os.path.join(out_dir, "summary_by_status.csv"))

    print()
    print("WROTE:", out_dir)
    print("  candidate_settlement_outcomes.csv")
    print("  summary_by_candidate_type.csv")
    print("  summary_by_market_type.csv")
    print("  summary_by_blocked_reason.csv")
    print("  summary_by_settlement_status.csv")
    print("  summary_by_status.csv")


if __name__ == "__main__":
    main()