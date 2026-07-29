"""Stage 3: aggregate whole_market_rows/window_df into the remaining output CSVs
and print an inventory/handoff-stats JSON summary for the markdown writer.

Exploratory research only -- see whole_market_exploration.py docstring for constraints.
"""
from __future__ import annotations

import json
import pickle

import numpy as np
import pandas as pd

from whole_market_exploration import CACHE_PATH, OUT_DIR, TIMING_WINDOWS, PRICE_BUCKETS

STAGE2_PATH = CACHE_PATH.parent / "whole_market_stage2.pkl"

CONTEXT_FIELDS = [
    "home_away",
    "team_strength_bucket",
    "offense_form_bucket",
    "opponent_run_prevention_bucket",
    "starter_xfip_gap_bucket",
    "opponent_starter_kbb_bucket",
    "opponent_starter_xfip_bucket",
    "context_favorite_underdog",
]

WINDOW_ORDER = [w[0] for w in TIMING_WINDOWS]


def sample_warning(n: int) -> str:
    if n < 10:
        return "very_low_n_lt_10"
    if n < 25:
        return "low_n_lt_25"
    return ""


def calibration_table(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    out_rows = []
    for key, sub in df.groupby(group_cols):
        if not isinstance(key, tuple):
            key = (key,)
        priced = sub
        graded = sub[sub["graded"] == True]  # noqa: E712
        n_priced = len(priced)
        n_graded = len(graded)
        avg_fill = pd.to_numeric(priced["last_price_cents"], errors="coerce").mean()
        implied_breakeven = avg_fill
        hit_rate = None
        gap = None
        total_pl = None
        if n_graded > 0:
            wins = (graded["outcome"] == "WIN").sum()
            hit_rate = 100.0 * wins / n_graded
            gap = hit_rate - implied_breakeven if pd.notna(implied_breakeven) else None
            total_pl = pd.to_numeric(graded["pl_cents"], errors="coerce").sum()
        avg_spread = pd.to_numeric(priced["avg_spread_cents"], errors="coerce").mean()
        row = dict(zip(group_cols, key))
        row.update(
            {
                "priced_row_count": n_priced,
                "graded_count": n_graded,
                "avg_fill_price_cents": round(avg_fill, 2) if pd.notna(avg_fill) else "",
                "implied_breakeven_pct": round(implied_breakeven, 2) if pd.notna(implied_breakeven) else "",
                "actual_hit_rate_pct": round(hit_rate, 2) if hit_rate is not None else "",
                "hit_rate_minus_breakeven_pp": round(gap, 2) if gap is not None else "",
                "total_pl_cents_per_1_contract": round(total_pl, 1) if total_pl is not None else "",
                "avg_spread_cents": round(avg_spread, 2) if pd.notna(avg_spread) else "",
                "sample_warning": sample_warning(n_graded),
            }
        )
        out_rows.append(row)
    return pd.DataFrame(out_rows)


def build_market_type_calibration(rows_df: pd.DataFrame):
    priced = rows_df[rows_df["price_bucket"] != ""]
    priced = priced.copy()
    priced["price_bucket"] = pd.Categorical(priced["price_bucket"], categories=[p[0] for p in PRICE_BUCKETS], ordered=True)
    out = calibration_table(priced, ["market_type_bucket", "side", "price_bucket"])
    out = out.sort_values(["market_type_bucket", "side", "price_bucket"])
    out.to_csv(OUT_DIR / "market_type_calibration.csv", index=False)
    return out


def build_price_bucket_calibration(rows_df: pd.DataFrame):
    priced = rows_df[rows_df["price_bucket"] != ""].copy()
    priced["price_bucket"] = pd.Categorical(priced["price_bucket"], categories=[p[0] for p in PRICE_BUCKETS], ordered=True)
    out = calibration_table(priced, ["side", "price_bucket"])
    out = out.sort_values(["side", "price_bucket"])
    out.to_csv(OUT_DIR / "price_bucket_calibration.csv", index=False)
    return out


def build_pregame_timing_drift(window_df: pd.DataFrame):
    if window_df.empty:
        pd.DataFrame().to_csv(OUT_DIR / "pregame_timing_drift.csv", index=False)
        return pd.DataFrame()

    ticker_bucket = window_df.drop_duplicates("market_ticker").set_index("market_ticker")["market_type_bucket"]

    pivot = window_df.pivot_table(index="market_ticker", columns="window", values="yes_ask", aggfunc="last")
    yeswin = window_df.drop_duplicates("market_ticker").set_index("market_ticker")["yes_win"]

    move_records = []
    for i in range(1, len(WINDOW_ORDER)):
        prev_w, cur_w = WINDOW_ORDER[i - 1], WINDOW_ORDER[i]
        if prev_w not in pivot.columns or cur_w not in pivot.columns:
            continue
        both = pivot[[prev_w, cur_w]].dropna()
        diff = both[cur_w] - both[prev_w]
        for ticker, d in diff.items():
            move_records.append({"market_ticker": ticker, "window": cur_w, "move_from_prev": d})
    move_df = pd.DataFrame(move_records)

    out_rows = []
    for (mtb, window), sub in window_df.groupby(["market_type_bucket", "window"]):
        n_markets = sub["market_ticker"].nunique()
        avg_spread = pd.to_numeric(sub["spread_cents"], errors="coerce").mean()
        avg_price = pd.to_numeric(sub["yes_ask"], errors="coerce").mean()

        sub_move = move_df[(move_df["window"] == window) & (move_df["market_ticker"].isin(sub["market_ticker"]))]
        avg_move_prev = sub_move["move_from_prev"].mean() if not sub_move.empty else None

        gradable = sub.dropna(subset=["yes_win"])
        toward_winner_vals = []
        for _, r in gradable.iterrows():
            mv = move_df[(move_df["market_ticker"] == r["market_ticker"]) & (move_df["window"] == window)]
            if mv.empty:
                continue
            m = mv["move_from_prev"].iloc[0]
            toward_winner_vals.append(m if r["yes_win"] else -m)
        avg_toward_winner = float(np.mean(toward_winner_vals)) if toward_winner_vals else None

        pl_vals = []
        for _, r in gradable.iterrows():
            if pd.isna(r["yes_ask"]):
                continue
            pl_vals.append((100 - r["yes_ask"]) if r["yes_win"] else -r["yes_ask"])
        total_pl = float(np.sum(pl_vals)) if pl_vals else None

        out_rows.append(
            {
                "market_type_bucket": mtb,
                "window": window,
                "available_markets": n_markets,
                "avg_spread_cents": round(avg_spread, 2) if pd.notna(avg_spread) else "",
                "avg_price_cents": round(avg_price, 2) if pd.notna(avg_price) else "",
                "avg_price_move_from_previous_window": round(avg_move_prev, 2) if avg_move_prev is not None and pd.notna(avg_move_prev) else "",
                "avg_move_toward_eventual_winner": round(avg_toward_winner, 2) if avg_toward_winner is not None else "",
                "pl_cents_using_latest_snapshot_in_window": round(total_pl, 1) if total_pl is not None else "",
                "graded_count": len(pl_vals),
                "notes": sample_warning(len(pl_vals)),
            }
        )
    out = pd.DataFrame(out_rows)
    out["window"] = pd.Categorical(out["window"], categories=WINDOW_ORDER, ordered=True)
    out = out.sort_values(["market_type_bucket", "window"])
    out.to_csv(OUT_DIR / "pregame_timing_drift.csv", index=False)
    return out


def build_brain_market_disagreement(rows_df: pd.DataFrame):
    sub = rows_df[rows_df["brain_score"] != ""].copy()
    sub["brain_score"] = pd.to_numeric(sub["brain_score"], errors="coerce")
    sub["calibrated_probability"] = pd.to_numeric(sub["calibrated_probability"], errors="coerce")
    sub["last_price_cents"] = pd.to_numeric(sub["last_price_cents"], errors="coerce")

    def bucket(r):
        cp = r["calibrated_probability"]
        px = r["last_price_cents"]
        if pd.isna(cp) or pd.isna(px):
            return ""
        brain_high = cp >= 0.5
        market_high = px >= 50
        if brain_high and market_high:
            return "brain_high_market_high"
        if brain_high and not market_high:
            return "brain_high_market_low"
        if not brain_high and market_high:
            return "brain_low_market_high"
        return "brain_low_market_low"

    sub["gap_cents"] = (sub["calibrated_probability"] * 100) - sub["last_price_cents"]
    sub["bucket"] = sub.apply(bucket, axis=1)

    cols = [
        "game_date", "game_id", "subject_team", "market_type", "threshold", "side",
        "brain_score", "calibrated_probability", "last_price_cents", "gap_cents",
        "bucket", "graded", "outcome", "pl_cents",
    ]
    sub[cols].sort_values("gap_cents", key=lambda s: s.abs(), ascending=False).to_csv(
        OUT_DIR / "brain_market_disagreement.csv", index=False
    )
    return sub


def build_game_context_market_trends(rows_df: pd.DataFrame):
    ml_yes = rows_df[(rows_df["market_type"] == "moneyline") & (rows_df["side"] == "YES") & (rows_df["subject_team"] != "")].copy()
    ml_yes["last_price_cents"] = pd.to_numeric(ml_yes["last_price_cents"], errors="coerce")
    ml_yes["pl_cents"] = pd.to_numeric(ml_yes["pl_cents"], errors="coerce")

    out_rows = []
    for field in CONTEXT_FIELDS:
        for val, sub in ml_yes.groupby(field):
            if val == "" or pd.isna(val):
                continue
            graded = sub[sub["graded"] == True]  # noqa: E712
            n = len(sub)
            n_graded = len(graded)
            avg_price = sub["last_price_cents"].mean()
            hit_rate = 100.0 * (graded["outcome"] == "WIN").sum() / n_graded if n_graded else None
            total_pl = graded["pl_cents"].sum() if n_graded else None
            out_rows.append(
                {
                    "context_field": field,
                    "bucket_value": val,
                    "row_count": n,
                    "graded_count": n_graded,
                    "avg_moneyline_price_cents": round(avg_price, 2) if pd.notna(avg_price) else "",
                    "actual_hit_rate_pct": round(hit_rate, 2) if hit_rate is not None else "",
                    "total_pl_cents": round(total_pl, 1) if total_pl is not None else "",
                    "sample_warning": sample_warning(n_graded),
                }
            )
    out = pd.DataFrame(out_rows)
    out.to_csv(OUT_DIR / "game_context_market_trends.csv", index=False)
    return out


def build_liquidity_spread_diagnostics(window_df: pd.DataFrame):
    if window_df.empty:
        pd.DataFrame().to_csv(OUT_DIR / "liquidity_spread_diagnostics.csv", index=False)
        return pd.DataFrame()
    out_rows = []
    for (mtb, gdate, window), sub in window_df.groupby(["market_type_bucket", "game_date", "window"]):
        spreads = pd.to_numeric(sub["spread_cents"], errors="coerce").dropna()
        n = len(sub)
        out_rows.append(
            {
                "market_type_bucket": mtb,
                "game_date": gdate,
                "window": window,
                "avg_spread_cents": round(spreads.mean(), 2) if not spreads.empty else "",
                "median_spread_cents": round(spreads.median(), 2) if not spreads.empty else "",
                "pct_spread_le_3c": round(100.0 * (spreads <= 3).sum() / len(spreads), 1) if not spreads.empty else "",
                "pct_spread_le_5c": round(100.0 * (spreads <= 5).sum() / len(spreads), 1) if not spreads.empty else "",
                "pct_spread_gt_8c": round(100.0 * (spreads > 8).sum() / len(spreads), 1) if not spreads.empty else "",
                "empty_book_rate_pct": round(100.0 * sub["empty_book"].sum() / n, 1) if n else "",
                "one_sided_rate_pct": round(100.0 * sub["one_sided"].sum() / n, 1) if n else "",
                "snapshot_count": n,
                "market_count": sub["market_ticker"].nunique(),
            }
        )
    out = pd.DataFrame(out_rows)
    out["window"] = pd.Categorical(out["window"], categories=WINDOW_ORDER, ordered=True)
    out = out.sort_values(["market_type_bucket", "game_date", "window"])
    out.to_csv(OUT_DIR / "liquidity_spread_diagnostics.csv", index=False)
    return out


def build_interesting_rows(rows_df: pd.DataFrame, brain_df: pd.DataFrame):
    def mkrow(r, reason, notes=""):
        return {
            "date": r.get("game_date", ""),
            "matchup": r.get("game_id", ""),
            "team": r.get("subject_team", ""),
            "market_type": r.get("market_type", ""),
            "lane_or_score": r.get("brain_score", ""),
            "brain_score": r.get("brain_score", ""),
            "fill_price_cents": r.get("last_price_cents", ""),
            "final_result": r.get("outcome", ""),
            "pl_cents": r.get("pl_cents", ""),
            "reason_flagged": reason,
            "notes": notes,
        }

    picks = []

    if not brain_df.empty:
        bd = brain_df.dropna(subset=["gap_cents"]).copy()
        for _, r in bd.reindex(bd["gap_cents"].abs().sort_values(ascending=False).index[:8]).iterrows():
            picks.append(mkrow(r, "biggest_brain_market_disagreement", f"gap={r['gap_cents']:.1f}c"))

        hi_conf_losses = bd[(bd["calibrated_probability"] >= 0.55) & (bd["outcome"] == "LOSE")]
        for _, r in hi_conf_losses.sort_values("calibrated_probability", ascending=False).head(5).iterrows():
            picks.append(mkrow(r, "highest_confidence_brain_row_that_lost"))

        lo_conf_wins = bd[(bd["calibrated_probability"] <= 0.45) & (bd["outcome"] == "WIN")]
        for _, r in lo_conf_wins.sort_values("calibrated_probability", ascending=True).head(5).iterrows():
            picks.append(mkrow(r, "low_confidence_row_that_won"))

    graded = rows_df[rows_df["graded"] == True].copy()  # noqa: E712
    graded["pl_cents"] = pd.to_numeric(graded["pl_cents"], errors="coerce")
    graded["last_price_cents"] = pd.to_numeric(graded["last_price_cents"], errors="coerce")
    graded["price_move_cents"] = pd.to_numeric(graded["price_move_cents"], errors="coerce")

    for _, r in graded.sort_values("pl_cents", ascending=False).head(6).iterrows():
        picks.append(mkrow(r, "largest_positive_pl"))
    for _, r in graded.sort_values("pl_cents", ascending=True).head(6).iterrows():
        picks.append(mkrow(r, "largest_negative_pl"))

    movers = graded.dropna(subset=["price_move_cents"])
    for _, r in movers.reindex(movers["price_move_cents"].abs().sort_values(ascending=False).index[:6]).iterrows():
        picks.append(mkrow(r, "biggest_pregame_price_move", f"move={r['price_move_cents']:.1f}c"))

    exp_fav_lost = graded[(graded["side"] == "YES") & (graded["last_price_cents"] >= 70) & (graded["outcome"] == "LOSE")]
    for _, r in exp_fav_lost.sort_values("last_price_cents", ascending=False).head(6).iterrows():
        picks.append(mkrow(r, "expensive_favorite_that_lost"))

    cheap_won = graded[(graded["last_price_cents"] <= 25) & (graded["outcome"] == "WIN")]
    for _, r in cheap_won.sort_values("last_price_cents", ascending=True).head(6).iterrows():
        picks.append(mkrow(r, "cheap_side_that_won"))

    stale = rows_df[(rows_df["exclusion_reason"] == "") & (pd.to_numeric(rows_df["avg_spread_cents"], errors="coerce") > 15)]
    for _, r in stale.head(5).iterrows():
        picks.append(mkrow(r, "suspicious_wide_spread"))

    out = pd.DataFrame(picks)
    out.to_csv(OUT_DIR / "interesting_rows_to_review.csv", index=False)
    return out


def main():
    with open(STAGE2_PATH, "rb") as f:
        stage2 = pickle.load(f)
    rows_df = stage2["rows_df"]
    window_df = stage2["window_df"]
    games = stage2["games"]
    doubleheader_pks = stage2["doubleheader_pks"]
    no_match_log = stage2["no_match_log"]
    matches = stage2["matches"]
    cards = stage2["cards"]

    mtc = build_market_type_calibration(rows_df)
    pbc = build_price_bucket_calibration(rows_df)
    ptd = build_pregame_timing_drift(window_df)
    bmd = build_brain_market_disagreement(rows_df)
    gcmt = build_game_context_market_trends(rows_df)
    lsd = build_liquidity_spread_diagnostics(window_df)
    irr = build_interesting_rows(rows_df, bmd)

    summary = {
        "n_games": int(games["game_pk"].nunique()),
        "n_doubleheader_games_excluded": len(doubleheader_pks),
        "n_team_game_rows_in_cards": int(len(cards)),
        "n_matched_ticker_game_rows": int(len(matches)),
        "n_games_no_ticker_match_any_type": len(no_match_log),
        "market_type_counts": matches["market_type"].value_counts().to_dict(),
        "n_whole_market_rows": int(len(rows_df)),
        "n_valid_price_rows": int((rows_df["price_bucket"] != "").sum()),
        "n_empty_book_rows": int(rows_df["empty_book"].sum()),
        "n_one_sided_rows": int(rows_df["one_sided"].sum()),
        "n_excluded_no_pregame_snapshot": int((rows_df["exclusion_reason"] == "no_pregame_snapshot").sum()),
        "n_graded_rows": int(rows_df["graded"].sum()),
        "exclusion_reason_counts": rows_df["exclusion_reason"].value_counts().to_dict(),
    }
    with open(OUT_DIR / "_stage3_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
