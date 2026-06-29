import argparse
import csv
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from statistics import mean


HORIZONS_SECONDS = [60, 180, 300, 600, 1200]


def parse_dt(value):
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def dt_to_sql(dt):
    return dt.astimezone(timezone.utc).isoformat()


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


def yes_mid_from_bid_ask(yes_bid, yes_ask, fallback_mid=None, fallback_last=None):
    yes_bid = safe_int(yes_bid)
    yes_ask = safe_int(yes_ask)
    fallback_mid = safe_int(fallback_mid)
    fallback_last = safe_int(fallback_last)

    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / 2

    if fallback_mid is not None:
        return fallback_mid

    if fallback_last is not None:
        return fallback_last

    if yes_bid is not None:
        return yes_bid

    if yes_ask is not None:
        return yes_ask

    return None


def side_price_from_yes_mid(side, yes_mid):
    if yes_mid is None:
        return None

    side_text = (side or "YES").upper()

    if side_text == "NO":
        return 100 - yes_mid

    return yes_mid


def side_entry_price(candidate):
    side = (candidate.get("side") or "YES").upper()

    entry_yes_bid = safe_int(candidate.get("entry_yes_bid"))
    entry_yes_ask = safe_int(candidate.get("entry_yes_ask"))
    expected_fill = safe_int(candidate.get("expected_fill_price"))

    if expected_fill is not None:
        return expected_fill

    # Estimated taker fill.
    # Buying YES pays YES ask.
    # Buying NO pays NO ask, which is approximately 100 - YES bid.
    if side == "NO":
        if entry_yes_bid is not None:
            return 100 - entry_yes_bid

        entry_yes_mid = yes_mid_from_bid_ask(entry_yes_bid, entry_yes_ask)
        if entry_yes_mid is not None:
            return 100 - entry_yes_mid

        return None

    if entry_yes_ask is not None:
        return entry_yes_ask

    return yes_mid_from_bid_ask(entry_yes_bid, entry_yes_ask)


def snapshot_side_price(side, snap):
    yes_mid = yes_mid_from_bid_ask(
        snap.get("yes_bid"),
        snap.get("yes_ask"),
        snap.get("mid_cents"),
        snap.get("last_price"),
    )
    return side_price_from_yes_mid(side, yes_mid)


def first_snapshot_at_or_after(snaps, target_dt):
    for snap in snaps:
        snap_dt = snap["_dt"]
        if snap_dt is not None and snap_dt >= target_dt:
            return snap
    return None


def first_snapshot_at_or_before(snaps, target_dt):
    best = None
    for snap in snaps:
        snap_dt = snap["_dt"]
        if snap_dt is not None and snap_dt <= target_dt:
            best = snap
        if snap_dt is not None and snap_dt > target_dt:
            break
    return best


def load_snapshots_for_candidate(conn, ticker, start_dt, end_dt):
    if not ticker:
        return []

    rows = conn.execute(
        """
        SELECT
            id,
            market_ticker,
            snapped_at,
            source,
            market_type,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            mid_cents,
            last_price,
            spread_cents,
            volume,
            open_interest,
            home_team,
            away_team,
            event_ticker
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at >= ?
          AND snapped_at <= ?
          AND source IN ('rest_batch', 'ws_ticker', 'ws_orderbook', 'focused_watch', 'rest_poll')
        ORDER BY snapped_at ASC
        """,
        (ticker, dt_to_sql(start_dt), dt_to_sql(end_dt)),
    ).fetchall()

    snaps = []
    for row in rows:
        d = dict(row)
        d["_dt"] = parse_dt(d.get("snapped_at"))
        snaps.append(d)

    return snaps


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


def summarize(rows, key_field, output_path):
    groups = {}

    for row in rows:
        key = row.get(key_field) or "(blank)"
        groups.setdefault(key, []).append(row)

    summary = []

    for key, items in groups.items():
        def values(field):
            vals = []
            for item in items:
                value = safe_float(item.get(field))
                if value is not None:
                    vals.append(value)
            return vals

        delta_1m = values("delta_1m")
        delta_3m = values("delta_3m")
        delta_5m = values("delta_5m")
        delta_10m = values("delta_10m")
        mfe_10m = values("mfe_10m")
        mae_10m = values("mae_10m")

        def favorable_rate(field):
            vals = values(field)
            if not vals:
                return None
            return round(sum(1 for v in vals if v > 0) / len(vals), 4)

        summary.append(
            {
                key_field: key,
                "count": len(items),
                "with_1m": len(delta_1m),
                "with_5m": len(delta_5m),
                "avg_delta_1m": round(mean(delta_1m), 3) if delta_1m else None,
                "avg_delta_3m": round(mean(delta_3m), 3) if delta_3m else None,
                "avg_delta_5m": round(mean(delta_5m), 3) if delta_5m else None,
                "avg_delta_10m": round(mean(delta_10m), 3) if delta_10m else None,
                "favorable_rate_1m": favorable_rate("delta_1m"),
                "favorable_rate_5m": favorable_rate("delta_5m"),
                "avg_mfe_10m": round(mean(mfe_10m), 3) if mfe_10m else None,
                "avg_mae_10m": round(mean(mae_10m), 3) if mae_10m else None,
            }
        )

    summary.sort(key=lambda r: r["count"], reverse=True)

    write_csv(output_path, summary)


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


def analyze_candidate(conn, candidate):
    created_at = parse_dt(candidate.get("created_at"))
    ticker = candidate.get("market_ticker")
    side = candidate.get("side") or "YES"

    output = {
        "candidate_id": candidate.get("id"),
        "candidate_type": candidate.get("candidate_type"),
        "game_id": candidate.get("game_id"),
        "game_pk": candidate.get("game_pk"),
        "market_ticker": ticker,
        "event_ticker": candidate.get("event_ticker"),
        "market_type": candidate.get("market_type"),
        "derivative_type": candidate.get("derivative_type"),
        "read_type": candidate.get("read_type"),
        "side": side,
        "line_value": candidate.get("line_value"),
        "status": candidate.get("status"),
        "blocked_reason": candidate.get("blocked_reason"),
        "eligible_for_paper": candidate.get("eligible_for_paper"),
        "created_at": candidate.get("created_at"),
        "first_seen_at": candidate.get("first_seen_at"),
        "last_seen_at": candidate.get("last_seen_at"),
        "seen_count": candidate.get("seen_count"),
        "inning": candidate.get("inning"),
        "half_inning": candidate.get("half_inning"),
        "outs": candidate.get("outs"),
        "score_away": candidate.get("score_away"),
        "score_home": candidate.get("score_home"),
        "runners_state": candidate.get("runners_state"),
        "entry_yes_bid": candidate.get("entry_yes_bid"),
        "entry_yes_ask": candidate.get("entry_yes_ask"),
        "spread_cents": candidate.get("spread_cents"),
        "overall_watch_score": candidate.get("overall_watch_score"),
        "baseball_support_score": candidate.get("baseball_support_score"),
        "execution_quality_score": candidate.get("execution_quality_score"),
        "risk_blocker_score": candidate.get("risk_blocker_score"),
        "trigger_event_type": candidate.get("trigger_event_type"),
        "trigger_description": candidate.get("trigger_description"),
    }

    if created_at is None or not ticker:
        output.update(
            {
                "analysis_status": "missing_created_at_or_ticker",
                "entry_side_price": None,
                "pre_side_price": None,
                "snapshots_found": 0,
                "delta_1m": None,
                "delta_3m": None,
                "delta_5m": None,
                "delta_10m": None,
                "delta_20m": None,
                "mfe_10m": None,
                "mae_10m": None,
                "mfe_20m": None,
                "mae_20m": None,
            }
        )
        return output

    start_dt = created_at - timedelta(minutes=2)
    end_dt = created_at + timedelta(minutes=25)
    snaps = load_snapshots_for_candidate(conn, ticker, start_dt, end_dt)

    entry_price = side_entry_price(candidate)

    pre_snap = first_snapshot_at_or_before(snaps, created_at)
    pre_side_price = snapshot_side_price(side, pre_snap) if pre_snap else None

    if entry_price is None:
        entry_price = pre_side_price

    output["analysis_status"] = "ok" if snaps else "no_snapshots_found"
    output["snapshots_found"] = len(snaps)
    output["entry_side_price"] = entry_price
    output["pre_side_price"] = pre_side_price
    output["pre_snapshot_at"] = pre_snap.get("snapped_at") if pre_snap else None
    output["pre_snapshot_source"] = pre_snap.get("source") if pre_snap else None

    future_prices = []

    for seconds in HORIZONS_SECONDS:
        target_dt = created_at + timedelta(seconds=seconds)
        snap = first_snapshot_at_or_after(snaps, target_dt)
        label = f"{int(seconds / 60)}m"

        side_price = snapshot_side_price(side, snap) if snap else None
        delta = side_price - entry_price if side_price is not None and entry_price is not None else None

        output[f"side_price_{label}"] = side_price
        output[f"delta_{label}"] = delta
        output[f"snapshot_at_{label}"] = snap.get("snapped_at") if snap else None
        output[f"source_{label}"] = snap.get("source") if snap else None

    for snap in snaps:
        snap_dt = snap.get("_dt")
        if snap_dt is None or snap_dt < created_at:
            continue

        minutes_after = (snap_dt - created_at).total_seconds() / 60
        side_price = snapshot_side_price(side, snap)

        if side_price is None:
            continue

        future_prices.append((minutes_after, side_price))

    def excursion(max_minutes, mode):
        prices = [p for m, p in future_prices if m <= max_minutes]
        if not prices or entry_price is None:
            return None

        deltas = [p - entry_price for p in prices]

        if mode == "max":
            return max(deltas)

        return min(deltas)

    output["mfe_10m"] = excursion(10, "max")
    output["mae_10m"] = excursion(10, "min")
    output["mfe_20m"] = excursion(20, "max")
    output["mae_20m"] = excursion(20, "min")

    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", default="kalshi_mlb.db")
    args = parser.parse_args()

    out_dir = os.path.join("outputs", "candidate_outcomes", args.date)
    os.makedirs(out_dir, exist_ok=True)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    candidates = get_candidates(conn, args.date)
    print(f"Loaded {len(candidates)} candidates from {args.date}")

    rows = []

    for i, candidate in enumerate(candidates, start=1):
        rows.append(analyze_candidate(conn, candidate))

        if i % 100 == 0:
            print(f"  analyzed {i}/{len(candidates)}")

    outcome_path = os.path.join(out_dir, "candidate_outcomes.csv")
    write_csv(outcome_path, rows)

    summarize(rows, "candidate_type", os.path.join(out_dir, "summary_by_candidate_type.csv"))
    summarize(rows, "blocked_reason", os.path.join(out_dir, "summary_by_blocked_reason.csv"))
    summarize(rows, "market_type", os.path.join(out_dir, "summary_by_market_type.csv"))
    summarize(rows, "status", os.path.join(out_dir, "summary_by_status.csv"))

    print()
    print("WROTE:", out_dir)
    print("  candidate_outcomes.csv")
    print("  summary_by_candidate_type.csv")
    print("  summary_by_blocked_reason.csv")
    print("  summary_by_market_type.csv")
    print("  summary_by_status.csv")


if __name__ == "__main__":
    main()