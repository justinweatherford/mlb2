import argparse
import csv
import os
from datetime import datetime
from statistics import mean


def safe_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value):
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def read_csv(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing input file: {path}")

    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def split_game_id(game_id):
    if not game_id or "@" not in game_id:
        return None, None

    away, home = game_id.split("@", 1)
    return away.strip().upper(), home.strip().upper()


def selected_team_score(row, prefix="candidate"):
    selected = (row.get("selected_team_abbr") or "").upper()
    game_id = row.get("game_id")
    away_abbr, home_abbr = split_game_id(game_id)

    if not selected or not away_abbr or not home_abbr:
        return None

    if prefix == "candidate":
        away_score = safe_int(row.get("score_away_at_candidate") or row.get("score_away"))
        home_score = safe_int(row.get("score_home_at_candidate") or row.get("score_home"))
    elif prefix == "final":
        away_score = safe_int(row.get("final_away_score"))
        home_score = safe_int(row.get("final_home_score"))
    elif prefix == "f5":
        away_score = safe_int(row.get("f5_away_score"))
        home_score = safe_int(row.get("f5_home_score"))
    else:
        return None

    if selected == away_abbr:
        return away_score

    if selected == home_abbr:
        return home_score

    return None


def selected_team_opponent_score(row, prefix="candidate"):
    selected = (row.get("selected_team_abbr") or "").upper()
    game_id = row.get("game_id")
    away_abbr, home_abbr = split_game_id(game_id)

    if not selected or not away_abbr or not home_abbr:
        return None

    if prefix == "candidate":
        away_score = safe_int(row.get("score_away_at_candidate") or row.get("score_away"))
        home_score = safe_int(row.get("score_home_at_candidate") or row.get("score_home"))
    elif prefix == "final":
        away_score = safe_int(row.get("final_away_score"))
        home_score = safe_int(row.get("final_home_score"))
    elif prefix == "f5":
        away_score = safe_int(row.get("f5_away_score"))
        home_score = safe_int(row.get("f5_home_score"))
    else:
        return None

    if selected == away_abbr:
        return home_score

    if selected == home_abbr:
        return away_score

    return None


def outs_elapsed(inning, half_inning, outs):
    inning = safe_int(inning)
    outs = safe_int(outs)

    if inning is None:
        return None

    if outs is None:
        outs = 0

    half = (half_inning or "").lower()
    completed_before_inning = max(inning - 1, 0) * 6

    if half.startswith("top"):
        return completed_before_inning + outs

    if half.startswith("bot") or half.startswith("bottom"):
        return completed_before_inning + 3 + outs

    return completed_before_inning + outs


def bucket_number(value, buckets, missing_label="missing"):
    value = safe_float(value)
    if value is None:
        return missing_label

    for upper, label in buckets:
        if value <= upper:
            return label

    return buckets[-1][1]


def bucket_runs_needed(value):
    value = safe_float(value)
    if value is None:
        return "missing"

    if value <= 0:
        return "already_cleared"
    if value <= 1:
        return "need_1"
    if value <= 2:
        return "need_2"
    if value <= 3:
        return "need_3"
    if value <= 4:
        return "need_4"
    if value <= 5:
        return "need_5"
    if value <= 6:
        return "need_6"
    return "need_7_plus"


def bucket_inning(inning):
    inning = safe_int(inning)
    if inning is None:
        return "missing"

    if inning <= 3:
        return "early_1_3"
    if inning <= 6:
        return "middle_4_6"
    if inning <= 9:
        return "late_7_9"
    return "extras"


def score_state(row):
    selected = (row.get("selected_team_abbr") or "").upper()
    if not selected:
        return "no_selected_team"

    team = selected_team_score(row, "candidate")
    opp = selected_team_opponent_score(row, "candidate")

    if team is None or opp is None:
        return "missing_score_state"

    diff = team - opp

    if diff > 0:
        return "selected_leading"
    if diff < 0:
        return "selected_trailing"
    return "tied"


def total_score_state(row):
    away = safe_int(row.get("score_away_at_candidate") or row.get("score_away"))
    home = safe_int(row.get("score_home_at_candidate") or row.get("score_home"))

    if away is None or home is None:
        return "missing_total_score"

    total = away + home

    if total <= 2:
        return "low_total_0_2"
    if total <= 5:
        return "medium_total_3_5"
    if total <= 8:
        return "high_total_6_8"
    return "very_high_total_9_plus"


def context_lane(row):
    market_type = row.get("market_type") or "unknown"
    candidate_type = row.get("candidate_type") or "unknown"
    runs_needed_team = row.get("runs_needed_team")
    runs_needed_total = row.get("runs_needed_total")
    inning_bucket = row.get("inning_bucket")
    score_bucket = row.get("score_state")

    if market_type == "team_total":
        return f"{candidate_type}|team_runs_{bucket_runs_needed(runs_needed_team)}|{inning_bucket}|{score_bucket}"

    if market_type in ("full_game_total", "f5_total"):
        return f"{candidate_type}|total_runs_{bucket_runs_needed(runs_needed_total)}|{inning_bucket}|{total_score_state(row)}"

    return f"{candidate_type}|{market_type}|{inning_bucket}|{score_bucket}"


def merge_rows(short_rows, settlement_rows):
    by_id = {}

    for row in short_rows:
        candidate_id = row.get("candidate_id")
        if candidate_id:
            by_id[candidate_id] = dict(row)

    merged = []

    for row in settlement_rows:
        candidate_id = row.get("candidate_id")
        base = by_id.get(candidate_id, {})
        combined = dict(base)

        for key, value in row.items():
            combined[key] = value

        merged.append(combined)

    return merged


def enrich(row):
    away_at_candidate = safe_int(row.get("score_away_at_candidate") or row.get("score_away"))
    home_at_candidate = safe_int(row.get("score_home_at_candidate") or row.get("score_home"))

    current_total = None
    if away_at_candidate is not None and home_at_candidate is not None:
        current_total = away_at_candidate + home_at_candidate

    final_away = safe_int(row.get("final_away_score"))
    final_home = safe_int(row.get("final_home_score"))

    final_total = safe_int(row.get("final_total"))
    if final_total is None and final_away is not None and final_home is not None:
        final_total = final_away + final_home

    line = safe_float(row.get("resolved_line_value") or row.get("line_value") or row.get("candidate_line_value"))

    current_team = selected_team_score(row, "candidate")
    final_team = selected_team_score(row, "final")
    current_opp = selected_team_opponent_score(row, "candidate")

    runs_needed_team = None
    if line is not None and current_team is not None:
        runs_needed_team = line - current_team

    runs_needed_total = None
    if line is not None and current_total is not None:
        runs_needed_total = line - current_total

    out_count = outs_elapsed(row.get("inning"), row.get("half_inning"), row.get("outs"))
    remaining_outs_full = None
    remaining_outs_f5 = None

    if out_count is not None:
        remaining_outs_full = max(54 - out_count, 0)
        remaining_outs_f5 = max(30 - out_count, 0)

    team_score_diff = None
    if current_team is not None and current_opp is not None:
        team_score_diff = current_team - current_opp

    row["current_total_runs"] = current_total
    row["final_total_runs"] = final_total
    row["current_team_score"] = current_team
    row["final_team_score"] = final_team
    row["current_opponent_score"] = current_opp
    row["runs_needed_team"] = runs_needed_team
    row["runs_needed_total"] = runs_needed_total
    row["team_score_diff"] = team_score_diff
    row["outs_elapsed"] = out_count
    row["remaining_outs_full"] = remaining_outs_full
    row["remaining_outs_f5"] = remaining_outs_f5
    row["inning_bucket"] = bucket_inning(row.get("inning"))
    row["score_state"] = score_state(row)
    row["total_score_state"] = total_score_state(row)
    row["runs_needed_team_bucket"] = bucket_runs_needed(runs_needed_team)
    row["runs_needed_total_bucket"] = bucket_runs_needed(runs_needed_total)
    row["baseball_support_bucket"] = bucket_number(
        row.get("baseball_support_score"),
        [
            (25, "support_0_25"),
            (50, "support_26_50"),
            (75, "support_51_75"),
            (100, "support_76_100"),
        ],
    )
    row["overall_watch_bucket"] = bucket_number(
        row.get("overall_watch_score"),
        [
            (25, "watch_0_25"),
            (50, "watch_26_50"),
            (75, "watch_51_75"),
            (100, "watch_76_100"),
        ],
    )
    row["spread_bucket"] = bucket_number(
        row.get("spread_cents"),
        [
            (2, "spread_0_2"),
            (5, "spread_3_5"),
            (10, "spread_6_10"),
            (20, "spread_11_20"),
            (999, "spread_21_plus"),
        ],
    )
    row["is_late_game"] = 1 if bucket_inning(row.get("inning")) in ("late_7_9", "extras") else 0
    row["is_blowout"] = 1 if team_score_diff is not None and abs(team_score_diff) >= 5 else 0
    row["context_lane"] = context_lane(row)

    return row


def pct(values, predicate):
    if not values:
        return None
    return round(sum(1 for v in values if predicate(v)) / len(values), 4)


def avg_numeric(rows, field):
    vals = []
    for row in rows:
        value = safe_float(row.get(field))
        if value is not None:
            vals.append(value)

    return round(mean(vals), 3) if vals else None


def sum_numeric(rows, field):
    vals = []
    for row in rows:
        value = safe_float(row.get(field))
        if value is not None:
            vals.append(value)

    return round(sum(vals), 3) if vals else None


def summarize(rows, key_fields):
    groups = {}

    for row in rows:
        key = tuple(row.get(k) or "missing" for k in key_fields)
        groups.setdefault(key, []).append(row)

    output = []

    for key, items in groups.items():
        settled = [r for r in items if r.get("settled_side_value") not in (None, "")]
        wins = [r for r in settled if safe_int(r.get("hold_to_settle_win")) == 1]
        losses = [r for r in settled if safe_int(r.get("hold_to_settle_win")) == 0]

        deltas_1m = [safe_float(r.get("delta_1m")) for r in items if safe_float(r.get("delta_1m")) is not None]
        deltas_3m = [safe_float(r.get("delta_3m")) for r in items if safe_float(r.get("delta_3m")) is not None]
        deltas_5m = [safe_float(r.get("delta_5m")) for r in items if safe_float(r.get("delta_5m")) is not None]
        deltas_10m = [safe_float(r.get("delta_10m")) for r in items if safe_float(r.get("delta_10m")) is not None]

        entry_prices = [safe_float(r.get("entry_side_price")) for r in settled if safe_float(r.get("entry_side_price")) is not None]
        pnls = [safe_float(r.get("hold_to_settle_pnl_cents")) for r in settled if safe_float(r.get("hold_to_settle_pnl_cents")) is not None]

        total_risk = sum(entry_prices) if entry_prices else None
        total_pnl = sum(pnls) if pnls else None

        row = {}
        for idx, field in enumerate(key_fields):
            row[field] = key[idx]

        row.update(
            {
                "count": len(items),
                "settled_count": len(settled),
                "wins": len(wins),
                "losses": len(losses),
                "hold_win_rate": round(len(wins) / len(settled), 4) if settled else None,
                "avg_hold_pnl_cents": round(mean(pnls), 3) if pnls else None,
                "total_hold_pnl_cents": round(total_pnl, 3) if total_pnl is not None else None,
                "total_risk_cents": round(total_risk, 3) if total_risk is not None else None,
                "hold_roi_on_risk": round(total_pnl / total_risk, 4) if total_pnl is not None and total_risk else None,
                "avg_delta_1m": round(mean(deltas_1m), 3) if deltas_1m else None,
                "avg_delta_3m": round(mean(deltas_3m), 3) if deltas_3m else None,
                "avg_delta_5m": round(mean(deltas_5m), 3) if deltas_5m else None,
                "avg_delta_10m": round(mean(deltas_10m), 3) if deltas_10m else None,
                "favorable_1m_rate": pct(deltas_1m, lambda v: v > 0),
                "favorable_5m_rate": pct(deltas_5m, lambda v: v > 0),
                "avg_mfe_10m": avg_numeric(items, "mfe_10m"),
                "avg_mae_10m": avg_numeric(items, "mae_10m"),
                "avg_baseball_support_score": avg_numeric(items, "baseball_support_score"),
                "avg_overall_watch_score": avg_numeric(items, "overall_watch_score"),
                "avg_runs_needed_team": avg_numeric(items, "runs_needed_team"),
                "avg_runs_needed_total": avg_numeric(items, "runs_needed_total"),
                "avg_spread_cents": avg_numeric(items, "spread_cents"),
            }
        )

        output.append(row)

    output.sort(key=lambda r: (r.get("count") or 0), reverse=True)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--candidate-outcomes",
        default=None,
        help="Path to candidate_outcomes.csv. Defaults to outputs/candidate_outcomes/<date>/candidate_outcomes.csv",
    )
    parser.add_argument(
        "--settlement-outcomes",
        default=None,
        help="Path to candidate_settlement_outcomes.csv. Defaults to outputs/candidate_settlement/<date>/candidate_settlement_outcomes.csv",
    )
    args = parser.parse_args()

    short_path = args.candidate_outcomes or os.path.join(
        "outputs",
        "candidate_outcomes",
        args.date,
        "candidate_outcomes.csv",
    )
    settlement_path = args.settlement_outcomes or os.path.join(
        "outputs",
        "candidate_settlement",
        args.date,
        "candidate_settlement_outcomes.csv",
    )

    out_dir = os.path.join("outputs", "baseball_context_performance", args.date)
    os.makedirs(out_dir, exist_ok=True)

    short_rows = read_csv(short_path)
    settlement_rows = read_csv(settlement_path)

    merged = merge_rows(short_rows, settlement_rows)
    enriched = [enrich(row) for row in merged]

    write_csv(os.path.join(out_dir, "context_enriched_candidates.csv"), enriched)

    reports = {
        "summary_by_candidate_type.csv": ["candidate_type"],
        "summary_by_market_type.csv": ["market_type"],
        "summary_by_baseball_support_bucket.csv": ["baseball_support_bucket"],
        "summary_by_overall_watch_bucket.csv": ["overall_watch_bucket"],
        "summary_by_runs_needed_team_bucket.csv": ["runs_needed_team_bucket"],
        "summary_by_runs_needed_total_bucket.csv": ["runs_needed_total_bucket"],
        "summary_by_inning_bucket.csv": ["inning_bucket"],
        "summary_by_score_state.csv": ["score_state"],
        "summary_by_total_score_state.csv": ["total_score_state"],
        "summary_by_spread_bucket.csv": ["spread_bucket"],
        "summary_by_candidate_type_and_runs_needed_team.csv": ["candidate_type", "runs_needed_team_bucket"],
        "summary_by_candidate_type_and_inning.csv": ["candidate_type", "inning_bucket"],
        "summary_by_candidate_type_and_score_state.csv": ["candidate_type", "score_state"],
        "summary_by_context_lane.csv": ["context_lane"],
        "summary_by_blocked_reason_and_context.csv": ["blocked_reason", "runs_needed_team_bucket"],
    }

    for filename, fields in reports.items():
        write_csv(os.path.join(out_dir, filename), summarize(enriched, fields))

    print(f"Loaded short-term rows: {len(short_rows)}")
    print(f"Loaded settlement rows: {len(settlement_rows)}")
    print(f"Merged/enriched rows: {len(enriched)}")
    print()
    print("WROTE:", out_dir)
    for filename in ["context_enriched_candidates.csv"] + list(reports.keys()):
        print(" ", filename)


if __name__ == "__main__":
    main()