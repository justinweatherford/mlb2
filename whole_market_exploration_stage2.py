"""Stage 2: build all whole-market exploration output CSVs from the stage1 pickle.

Reads outputs/reconstructed_pregame_cards/<date>/pregame_identifier_cards.csv for
context + brain scores, outputs/historical_team_context_preview_v2/
historical_team_context_2026_clean.csv for refreshed team context (favorite/underdog
proxy), and outputs/pregame_probability_calibration/latest_calibration_bins.csv for
calibrated probabilities -- joins all of this to the stage1 ticker/snapshot data.

Exploratory research only -- see whole_market_exploration.py docstring for constraints.
"""
from __future__ import annotations

import csv
import pickle
import re
from pathlib import Path

import numpy as np
import pandas as pd

from whole_market_exploration import (
    CACHE_PATH, OUT_DIR, CARDS_DIR, MARKET_TYPE_BUCKET, TIMING_WINDOWS,
    PRICE_BUCKETS, DATE_START, DATE_END,
)

STAGE1_PATH = CACHE_PATH.parent / "whole_market_stage1.pkl"
CONTEXT_CSV = Path("outputs/historical_team_context_preview_v2/historical_team_context_2026_clean.csv")
CALIB_CSV = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")

DATES = [f"2026-07-{d:02d}" for d in range(19, 27)]

STRIKE_RE_TEAM_NUM = re.compile(r"^([A-Z]+)(\d+)$")


def parse_strike(strike: str, away: str, home: str):
    """Return (subject_team_or_None, threshold_int_or_None, is_tie)."""
    if strike == "TIE":
        return None, None, True
    if strike.isdigit():
        return None, int(strike), False
    m = STRIKE_RE_TEAM_NUM.match(strike)
    if m:
        return m.group(1), int(m.group(2)), False
    if strike in (away, home):
        return strike, None, False
    return None, None, False


def load_cards() -> pd.DataFrame:
    frames = []
    for d in DATES:
        p = CARDS_DIR / d / "pregame_identifier_cards.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p, dtype={"game_pk": "Int64"})
        frames.append(df)
    cards = pd.concat(frames, ignore_index=True)
    return cards


def load_refreshed_context() -> pd.DataFrame:
    df = pd.read_csv(CONTEXT_CSV, dtype={"game_pk": "Int64"})
    df = df[(df["game_date"] >= DATE_START) & (df["game_date"] <= DATE_END)]
    return df


def load_calibration() -> pd.DataFrame:
    df = pd.read_csv(CALIB_CSV)
    return df


def calibrated_prob(calib: pd.DataFrame, lane: str, score) -> float | None:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return None
    sub = calib[calib["lane"] == lane]
    for _, row in sub.iterrows():
        lo = row["min_score"]
        hi = row["max_score"]
        lo = -np.inf if pd.isna(lo) else lo
        hi = np.inf if pd.isna(hi) else hi
        if lo <= score < hi or (np.isinf(hi) and score >= lo):
            return row["conservative_probability"]
    return None


def build_calib_lookup(calib: pd.DataFrame) -> dict:
    """lane -> sorted list of (lo, hi, prob) for fast repeated lookups."""
    out = {}
    for lane, sub in calib.groupby("lane"):
        bins = []
        for _, row in sub.iterrows():
            lo = row["min_score"]
            hi = row["max_score"]
            lo = -np.inf if pd.isna(lo) else lo
            hi = np.inf if pd.isna(hi) else hi
            bins.append((lo, hi, row["conservative_probability"]))
        out[lane] = bins
    return out


def calib_lookup(bins_by_lane: dict, lane: str, score):
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return None
    bins = bins_by_lane.get(lane)
    if not bins:
        return None
    for lo, hi, prob in bins:
        if lo <= score < hi:
            return prob
    return None


def price_bucket_of(price):
    if price is None or (isinstance(price, float) and np.isnan(price)):
        return None
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= price <= hi:
            return name
    return None


def window_of(minutes_before):
    for name, lo, hi in TIMING_WINDOWS:
        if lo <= minutes_before < hi:
            return name
    return None


def sample_warning(n: int) -> str:
    if n < 10:
        return "very_low_n_lt_10"
    if n < 25:
        return "low_n_lt_25"
    return ""


def main():
    print("loading stage1 pickle...")
    with open(STAGE1_PATH, "rb") as f:
        stage1 = pickle.load(f)
    games = stage1["games"]
    doubleheader_pks = stage1["doubleheader_pks"]
    f5_map = stage1["f5_map"]
    matches = stage1["matches"]
    no_match_log = stage1["no_match_log"]
    snap_cache = stage1["snap_cache"]

    def strip_tz(s):
        if hasattr(s, "dt") and s.dt.tz is not None:
            return s.dt.tz_localize(None)
        return s

    games["game_start_time_utc"] = strip_tz(games["game_start_time_utc"])
    matches["game_start_time_utc"] = strip_tz(matches["game_start_time_utc"])
    for t, df in snap_cache.items():
        if not df.empty and df["snapped_at"].dt.tz is not None:
            df["snapped_at"] = df["snapped_at"].dt.tz_localize(None)

    print("loading cards / context / calibration...")
    cards = load_cards()
    context = load_refreshed_context()
    calib = load_calibration()
    calib_bins = build_calib_lookup(calib)

    cards_idx = cards.set_index(["game_pk", "team"])
    context_idx = context.set_index(["game_pk", "team_abbr"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []          # whole_market_rows.csv
    window_rows = []   # long-format per-ticker-per-window rows, used for timing + liquidity CSVs

    n_no_pregame = 0
    n_empty_book = 0
    n_one_sided = 0

    for m in matches.itertuples(index=False):
        game_start = m.game_start_time_utc
        snaps = snap_cache.get(m.market_ticker)
        subject_team, threshold, is_tie = parse_strike(m.strike, m.away_abbr, m.home_abbr)

        # ---- grading ----
        exclusion_reason = ""
        yes_win = None
        if m.market_type in ("moneyline",):
            if m.final_away_score is None or m.final_home_score is None:
                exclusion_reason = "no_final_score"
            else:
                subj_final = m.final_away_score if subject_team == m.away_abbr else m.final_home_score
                opp_final = m.final_home_score if subject_team == m.away_abbr else m.final_away_score
                yes_win = subj_final > opp_final
        elif m.market_type == "team_total":
            if m.final_away_score is None or m.final_home_score is None:
                exclusion_reason = "no_final_score"
            else:
                team_final = m.final_away_score if subject_team == m.away_abbr else m.final_home_score
                yes_win = team_final >= threshold
        elif m.market_type == "full_game_total":
            if m.final_total is None:
                exclusion_reason = "no_final_score"
            else:
                yes_win = m.final_total >= threshold
        elif m.market_type in ("f5_total", "f5_winner", "f5_spread"):
            f5 = f5_map.get(m.game_pk)
            if f5 is None or f5[2] < 5:
                exclusion_reason = "incomplete_f5_data"
            else:
                f5_away, f5_home, _ = f5
                if m.market_type == "f5_total":
                    yes_win = (f5_away + f5_home) >= threshold
                elif m.market_type == "f5_winner":
                    if is_tie:
                        yes_win = f5_away == f5_home
                    else:
                        subj_f5 = f5_away if subject_team == m.away_abbr else f5_home
                        opp_f5 = f5_home if subject_team == m.away_abbr else f5_away
                        yes_win = subj_f5 > opp_f5
                else:  # f5_spread
                    subj_f5 = f5_away if subject_team == m.away_abbr else f5_home
                    opp_f5 = f5_home if subject_team == m.away_abbr else f5_away
                    yes_win = (subj_f5 - opp_f5) >= threshold
        elif m.market_type == "spread_run_line":
            if m.final_away_score is None or m.final_home_score is None:
                exclusion_reason = "no_final_score"
            else:
                team_final = m.final_away_score if subject_team == m.away_abbr else m.final_home_score
                opp_final = m.final_home_score if subject_team == m.away_abbr else m.final_away_score
                yes_win = (team_final - opp_final) >= threshold

        # ---- pregame snapshot slice ----
        if snaps is None or snaps.empty:
            exclusion_reason = exclusion_reason or "no_pregame_snapshot"
            pregame = snaps.iloc[0:0] if snaps is not None else pd.DataFrame()
        else:
            pregame = snaps[snaps["snapped_at"] < game_start]
            if pregame.empty:
                exclusion_reason = exclusion_reason or "no_pregame_snapshot"

        if pregame.empty:
            n_no_pregame += 1

        card_row = cards_idx.loc[(m.game_pk, subject_team)] if subject_team is not None and (m.game_pk, subject_team) in cards_idx.index else None
        if isinstance(card_row, pd.DataFrame):
            card_row = card_row.iloc[0]
        ctx_row = context_idx.loc[(m.game_pk, subject_team)] if subject_team is not None and (m.game_pk, subject_team) in context_idx.index else None
        if isinstance(ctx_row, pd.DataFrame):
            ctx_row = ctx_row.iloc[0]
        opp_abbr = None
        if subject_team is not None:
            opp_abbr = m.home_abbr if subject_team == m.away_abbr else m.away_abbr
        opp_ctx_row = context_idx.loc[(m.game_pk, opp_abbr)] if opp_abbr is not None and (m.game_pk, opp_abbr) in context_idx.index else None
        if isinstance(opp_ctx_row, pd.DataFrame):
            opp_ctx_row = opp_ctx_row.iloc[0]

        favorite_underdog = ""
        if ctx_row is not None and opp_ctx_row is not None:
            gap = ctx_row["season_run_diff_pg_before_game"] - opp_ctx_row["season_run_diff_pg_before_game"]
            if pd.notna(gap):
                favorite_underdog = "favorite" if gap > 0 else ("underdog" if gap < 0 else "even")

        for window_name, lo, hi in TIMING_WINDOWS:
            if pregame.empty:
                continue
            mb = (game_start - pregame["snapped_at"]).dt.total_seconds() / 60.0
            in_win = pregame[(mb >= lo) & (mb < hi)]
            if in_win.empty:
                continue
            last = in_win.iloc[-1]
            window_rows.append(
                {
                    "market_ticker": m.market_ticker,
                    "market_type": m.market_type,
                    "market_type_bucket": MARKET_TYPE_BUCKET.get(m.market_type, "other"),
                    "game_date": m.game_date,
                    "window": window_name,
                    "yes_ask": last["yes_ask"],
                    "no_ask": last["no_ask"],
                    "spread_cents": last["spread_cents"],
                    "empty_book": pd.isna(last["yes_bid"]) and pd.isna(last["yes_ask"]) and pd.isna(last["no_bid"]) and pd.isna(last["no_ask"]),
                    "one_sided": bool((pd.notna(last["yes_ask"]) or pd.notna(last["yes_bid"])) != (pd.notna(last["no_ask"]) or pd.notna(last["no_bid"]))),
                    "yes_win": yes_win,
                }
            )

        for side in ("YES", "NO"):
            excl = exclusion_reason
            first_price = last_price = None
            first_mb = last_mb = None
            avg_spread = last_spread = None
            empty_book = one_sided = False
            n_pregame = 0
            price_move = None
            if not pregame.empty and not excl:
                col = "yes_ask" if side == "YES" else "no_ask"
                n_pregame = len(pregame)
                first_row = pregame.iloc[0]
                last_row = pregame.iloc[-1]
                first_price = first_row[col]
                last_price = last_row[col]
                first_mb = (game_start - first_row["snapped_at"]).total_seconds() / 60.0
                last_mb = (game_start - last_row["snapped_at"]).total_seconds() / 60.0
                avg_spread = pregame["spread_cents"].mean()
                last_spread = last_row["spread_cents"]
                empty_book = bool(pd.isna(last_row["yes_bid"]) and pd.isna(last_row["yes_ask"]) and pd.isna(last_row["no_bid"]) and pd.isna(last_row["no_ask"]))
                yb = pd.notna(last_row["yes_bid"]) or pd.notna(last_row["yes_ask"])
                nb = pd.notna(last_row["no_bid"]) or pd.notna(last_row["no_ask"])
                one_sided = bool(yb != nb)
                if pd.notna(first_price) and pd.notna(last_price):
                    price_move = last_price - first_price
                if pd.isna(last_price):
                    excl = "empty_book_no_fill"

            graded = bool(excl == "" and last_price is not None and pd.notna(last_price) and yes_win is not None)
            outcome = ""
            pl_cents = None
            moved_toward_winner = None
            if graded:
                side_win = yes_win if side == "YES" else (not yes_win)
                outcome = "WIN" if side_win else "LOSE"
                pl_cents = (100 - last_price) if side_win else -last_price
                if price_move is not None:
                    if side_win:
                        moved_toward_winner = price_move > 0
                    else:
                        moved_toward_winner = price_move < 0

            brain_score = None
            calibrated_probability = None
            if subject_team is not None and card_row is not None:
                if m.market_type == "moneyline" and side == "YES":
                    brain_score = card_row.get("side_score")
                    calibrated_probability = calib_lookup(calib_bins, "side", brain_score)
                elif m.market_type == "team_total" and threshold == 4 and side == "YES":
                    brain_score = card_row.get("team_runs_4plus_score")
                    calibrated_probability = calib_lookup(calib_bins, "team_runs_4plus", brain_score)
                elif m.market_type == "team_total" and threshold == 5 and side == "NO":
                    brain_score = card_row.get("team_runs_5plus_no_score")
                    calibrated_probability = calib_lookup(calib_bins, "team_runs_5plus_no", brain_score)

            rows.append(
                {
                    "game_date": m.game_date,
                    "game_id": f"{m.away_abbr}@{m.home_abbr}",
                    "game_pk": m.game_pk,
                    "away_abbr": m.away_abbr,
                    "home_abbr": m.home_abbr,
                    "market_type": m.market_type,
                    "market_type_bucket": MARKET_TYPE_BUCKET.get(m.market_type, "other"),
                    "market_ticker": m.market_ticker,
                    "strike": m.strike,
                    "subject_team": subject_team or "",
                    "threshold": threshold if threshold is not None else "",
                    "side": side,
                    "n_pregame_snapshots": n_pregame,
                    "first_snap_minutes_before": round(first_mb, 1) if first_mb is not None else "",
                    "first_price_cents": first_price if first_price is not None else "",
                    "last_snap_minutes_before": round(last_mb, 1) if last_mb is not None else "",
                    "last_price_cents": last_price if last_price is not None else "",
                    "price_move_cents": price_move if price_move is not None else "",
                    "avg_spread_cents": round(avg_spread, 2) if avg_spread is not None and pd.notna(avg_spread) else "",
                    "last_spread_cents": last_spread if last_spread is not None and pd.notna(last_spread) else "",
                    "empty_book": empty_book,
                    "one_sided": one_sided,
                    "price_bucket": price_bucket_of(last_price) or "",
                    "graded": graded,
                    "outcome": outcome,
                    "pl_cents": pl_cents if pl_cents is not None else "",
                    "moved_toward_winner": moved_toward_winner if moved_toward_winner is not None else "",
                    "exclusion_reason": excl,
                    "brain_score": round(brain_score, 4) if brain_score is not None and pd.notna(brain_score) else "",
                    "calibrated_probability": calibrated_probability if calibrated_probability is not None else "",
                    "home_away": (card_row.get("home_away") if card_row is not None else "") or "",
                    "team_strength_bucket": (card_row.get("team_strength_bucket") if card_row is not None else "") or "",
                    "offense_form_bucket": (card_row.get("offense_form_bucket") if card_row is not None else "") or "",
                    "opponent_run_prevention_bucket": (card_row.get("opponent_run_prevention_bucket") if card_row is not None else "") or "",
                    "starter_xfip_gap_bucket": (card_row.get("starter_xfip_gap_bucket") if card_row is not None else "") or "",
                    "opponent_starter_kbb_bucket": (card_row.get("opponent_starter_kbb_bucket") if card_row is not None else "") or "",
                    "opponent_starter_xfip_bucket": (card_row.get("opponent_starter_xfip_bucket") if card_row is not None else "") or "",
                    "context_favorite_underdog": favorite_underdog,
                }
            )
            if empty_book:
                n_empty_book += 1
            if one_sided:
                n_one_sided += 1

    rows_df = pd.DataFrame(rows)
    window_df = pd.DataFrame(window_rows)

    print(f"whole_market_rows: {len(rows_df)} rows; no_pregame={n_no_pregame}; empty_book={n_empty_book}; one_sided={n_one_sided}")

    # persist intermediate for stage3 (report writer) to avoid recompute
    stage2_path = CACHE_PATH.parent / "whole_market_stage2.pkl"
    with open(stage2_path, "wb") as f:
        pickle.dump(
            {
                "rows_df": rows_df,
                "window_df": window_df,
                "games": games,
                "doubleheader_pks": doubleheader_pks,
                "no_match_log": no_match_log,
                "matches": matches,
                "cards": cards,
            },
            f,
        )
    print(f"stage2 saved to {stage2_path}")

    rows_df.to_csv(OUT_DIR / "whole_market_rows.csv", index=False)
    print("wrote whole_market_rows.csv")


if __name__ == "__main__":
    main()
