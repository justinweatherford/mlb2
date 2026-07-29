"""Recurring watchlist tracker built from whole-market exploration outputs.

Watchlisting / monitoring only. Does NOT change model logic, thresholds, or
lanes; does NOT promote any lane; does NOT create trades, paper trades,
Discord alerts, or write to live Slate Monitor outputs. Uses
mlb_games.game_start_time_utc as the only pregame clock (never ticker HHMM
-- confirmed elsewhere to be Eastern local, not UTC). Doubleheader-ambiguous
games are excluded, not guessed at.

This script does not re-derive fills or grading itself. It reads what
whole_market_exploration*.py already computed and graded
(whole_market_rows.csv, plus kalshi_mlb.db for final scores and the
reconstructed pregame cards for the full team-game universe) and rolls that
into six recurring watchlist categories, so the same categories can be
re-checked as more dates are added to a future whole_market_exploration run.

Each run also appends a fixed set of "key" watchlist cells (the ones flagged as
worth tracking in kalshi_market_watchlist_summary.md) as one row per cell to
outputs/kalshi_market_watchlist/watchlist_history.csv (append-only, never
overwritten) and rewrites watchlist_history_summary.md comparing the latest
run to the immediately-previous run for each tracked cell. This is a
monitoring log only -- it does not feed back into model logic, thresholds,
lanes, trades, paper trades, Discord alerts, or live Slate Monitor outputs.

Run: python whole_market_watchlist_tracker.py
  --source-dir PATH   override the whole_market_exploration output dir
                       (defaults to the newest whole_market_exploration_*
                       dir under outputs/kalshi_import_reconstruction_audit)
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH = "kalshi_mlb.db"
AUDIT_DIR = Path("outputs/kalshi_import_reconstruction_audit")
CARDS_DIR = Path("outputs/reconstructed_pregame_cards")
CONTEXT_CSV = Path("outputs/historical_team_context_preview_v2/historical_team_context_2026_clean.csv")
OUT_DIR = Path("outputs/kalshi_market_watchlist")

EXPENSIVE_YES_BUCKETS = ["56-65c", "66-80c", "81-99c"]
MOVE_MAGNITUDE_EDGES = [
    ("flat", 0.0, 0.0),
    ("small_lt5c", 0.0, 5.0),
    ("medium_5_10c", 5.0, 10.0),
    ("big_gt10c", 10.0, float("inf")),
]
RUN_DIFF_EVEN_THRESHOLD = 0.3  # runs/game gap within +-0.3 counts as "roughly even"

TEAM5_NO_SCORE_BINS = [
    ("0.50+", 0.50, float("inf")),
    ("0.40-0.50", 0.40, 0.50),
    ("0.30-0.40", 0.30, 0.40),
    ("0.20-0.30", 0.20, 0.30),
    ("0.10-0.20", 0.10, 0.20),
    ("0.00-0.10", -float("inf"), 0.10),
]
TEAM5_NO_CANDIDATE_THRESHOLD = 0.40  # matches team_runs_5plus_no_logic_audit.py lane rule


def sample_warning(n: int) -> str:
    if n < 10:
        return "very_low_n_lt_10"
    if n < 25:
        return "low_n_lt_25"
    return ""


def score_bin(score) -> str:
    if pd.isna(score):
        return ""
    for label, lo, hi in TEAM5_NO_SCORE_BINS:
        if score >= lo and (score < hi or hi == float("inf")):
            return label
    return ""


def move_bucket(move) -> str:
    if pd.isna(move):
        return ""
    m = abs(move)
    if m == 0:
        return "flat"
    if m < 5:
        return "small_lt5c"
    if m <= 10:
        return "medium_5_10c"
    return "big_gt10c"


def find_latest_source_dir() -> Path:
    candidates = sorted(AUDIT_DIR.glob("whole_market_exploration_*"))
    if not candidates:
        raise SystemExit(f"No whole_market_exploration_* directory found under {AUDIT_DIR}")
    return candidates[-1]


def summarize(sub: pd.DataFrame, price_col: str = "last_price_cents") -> dict:
    n = len(sub)
    graded = sub[sub["graded"] == True]  # noqa: E712
    n_graded = len(graded)
    avg_price = sub[price_col].mean() if price_col in sub.columns else np.nan
    hit = 100.0 * (graded["outcome"] == "WIN").sum() / n_graded if n_graded else np.nan
    breakeven = avg_price
    gap = (hit - breakeven) if pd.notna(hit) and pd.notna(breakeven) else np.nan
    pl = graded["pl_cents"].sum() if n_graded else np.nan
    avg_spread = sub["avg_spread_cents"].mean() if "avg_spread_cents" in sub.columns else np.nan
    avg_age = sub["last_snap_minutes_before"].mean() if "last_snap_minutes_before" in sub.columns else np.nan
    avg_brain = sub["brain_score"].mean() if "brain_score" in sub.columns else np.nan
    return {
        "n": n,
        "n_graded": n_graded,
        "hit_rate_pct": round(hit, 2) if pd.notna(hit) else None,
        "breakeven_pct": round(breakeven, 2) if pd.notna(breakeven) else None,
        "hit_minus_breakeven_pp": round(gap, 2) if pd.notna(gap) else None,
        "pl_cents": round(pl, 1) if pd.notna(pl) else None,
        "avg_price_cents": round(avg_price, 2) if pd.notna(avg_price) else None,
        "avg_spread_cents": round(avg_spread, 2) if pd.notna(avg_spread) else None,
        "avg_snapshot_age_minutes": round(avg_age, 1) if pd.notna(avg_age) else None,
        "avg_brain_score": round(avg_brain, 4) if pd.notna(avg_brain) else None,
        "sample_warning": sample_warning(n_graded),
    }


ROW = {
    "category": None, "dimension_type": None, "dimension_value": None,
    "market_type": None, "side": None, "team": None, "game_date": None,
    "candidate_status": None, "score_bin": None,
    "n": None, "n_graded": None, "hit_rate_pct": None, "breakeven_pct": None,
    "hit_minus_breakeven_pp": None, "pl_cents": None, "avg_price_cents": None,
    "avg_spread_cents": None, "avg_snapshot_age_minutes": None, "avg_brain_score": None,
    "market_implied_probability_pct": None, "actual_minus_implied_pp": None,
    "pl_buy_early_cents": None, "pl_buy_late_cents": None, "pl_fade_cents": None,
    "pct_moved_toward_winner": None, "sample_warning": None,
}


def mk_row(**kwargs) -> dict:
    r = dict(ROW)
    r.update(kwargs)
    return r


# ---------------------------------------------------------------------------
# Category 1: high-priced YES danger zone
# ---------------------------------------------------------------------------
def build_expensive_yes(df: pd.DataFrame) -> list[dict]:
    yes = df[(df["side"] == "YES") & (df["price_bucket"].isin(EXPENSIVE_YES_BUCKETS))].copy()
    rows: list[dict] = []
    for bucket in EXPENSIVE_YES_BUCKETS:
        base = yes[yes["price_bucket"] == bucket]
        rows.append(mk_row(category="expensive_yes", dimension_type="overall",
                            dimension_value=bucket, market_type="ALL", side="YES",
                            **summarize(base)))
        for mt, sub in base.groupby("market_type_bucket"):
            if len(sub) == 0:
                continue
            rows.append(mk_row(category="expensive_yes", dimension_type="market_type",
                                dimension_value=bucket, market_type=mt, side="YES",
                                **summarize(sub)))
        for team, sub in base.groupby("subject_team"):
            if len(sub) == 0:
                continue
            rows.append(mk_row(category="expensive_yes", dimension_type="team",
                                dimension_value=bucket, market_type="ALL", side="YES",
                                team=team, **summarize(sub)))
        for gd, sub in base.groupby("game_date"):
            if len(sub) == 0:
                continue
            rows.append(mk_row(category="expensive_yes", dimension_type="date",
                                dimension_value=bucket, market_type="ALL", side="YES",
                                game_date=gd, **summarize(sub)))
    return rows


# ---------------------------------------------------------------------------
# Category 2: team_runs_5plus_no watch
# ---------------------------------------------------------------------------
def build_team5_no(df: pd.DataFrame, mlb_games: pd.DataFrame) -> tuple[list[dict], pd.DataFrame]:
    t5 = df[(df["market_type"] == "team_total") & (df["threshold"] == 5.0) & (df["side"] == "NO")].copy()
    t5["score_bin"] = t5["brain_score"].apply(score_bin)
    t5["candidate_status"] = np.where(
        t5["brain_score"] >= TEAM5_NO_CANDIDATE_THRESHOLD, "official_candidate", "borderline_non_candidate"
    )
    t5["candidate_status"] = np.where(t5["brain_score"].isna(), "no_score", t5["candidate_status"])

    scores = mlb_games.rename(columns={"game_pk": "game_pk"})
    t5 = t5.merge(
        scores[["game_pk", "away_abbr_final", "home_abbr_final", "final_away_score", "final_home_score"]],
        on="game_pk", how="left",
    )
    t5["final_team_runs"] = np.where(
        t5["subject_team"] == t5["away_abbr_final"], t5["final_away_score"],
        np.where(t5["subject_team"] == t5["home_abbr_final"], t5["final_home_score"], np.nan),
    )
    t5 = t5.drop(columns=["away_abbr_final", "home_abbr_final", "final_away_score", "final_home_score"])

    rows: list[dict] = []
    rows.append(mk_row(category="team_runs_5plus_no", dimension_type="overall",
                        dimension_value="all_team5_no", market_type="team_total", side="NO",
                        **summarize(t5)))
    for status, sub in t5.groupby("candidate_status"):
        rows.append(mk_row(category="team_runs_5plus_no", dimension_type="candidate_status",
                            dimension_value=status, market_type="team_total", side="NO",
                            candidate_status=status, **summarize(sub)))
    for bucket in [b for b, *_ in TEAM5_NO_SCORE_BINS]:
        sub = t5[t5["score_bin"] == bucket]
        if len(sub) == 0:
            continue
        rows.append(mk_row(category="team_runs_5plus_no", dimension_type="score_band",
                            dimension_value=bucket, market_type="team_total", side="NO",
                            score_bin=bucket, **summarize(sub)))
    for pb, sub in t5.groupby("price_bucket"):
        if len(sub) == 0 or pb == "":
            continue
        rows.append(mk_row(category="team_runs_5plus_no", dimension_type="no_ask_price_bucket",
                            dimension_value=pb, market_type="team_total", side="NO",
                            **summarize(sub)))
    for ob, sub in t5.groupby("opponent_starter_kbb_bucket"):
        if len(sub) == 0 or pd.isna(ob) or ob == "":
            continue
        rows.append(mk_row(category="team_runs_5plus_no", dimension_type="opponent_starter_kbb_bucket",
                            dimension_value=ob, market_type="team_total", side="NO",
                            **summarize(sub)))
    for ob, sub in t5.groupby("opponent_starter_xfip_bucket"):
        if len(sub) == 0 or pd.isna(ob) or ob == "":
            continue
        rows.append(mk_row(category="team_runs_5plus_no", dimension_type="opponent_starter_xfip_bucket",
                            dimension_value=ob, market_type="team_total", side="NO",
                            **summarize(sub)))
    for of, sub in t5.groupby("offense_form_bucket"):
        if len(sub) == 0 or pd.isna(of) or of == "":
            continue
        rows.append(mk_row(category="team_runs_5plus_no", dimension_type="team_offense_context",
                            dimension_value=of, market_type="team_total", side="NO",
                            **summarize(sub)))
    return rows, t5


# ---------------------------------------------------------------------------
# Category 3: run-differential favorite/underdog context
# ---------------------------------------------------------------------------
def load_run_diff_gap(window_start: str, window_end: str) -> pd.DataFrame:
    ctx = pd.read_csv(CONTEXT_CSV)
    ctx = ctx[(ctx["game_date"] >= window_start) & (ctx["game_date"] <= window_end)]
    m = ctx.merge(
        ctx, left_on=["game_pk", "opponent_abbr"], right_on=["game_pk", "team_abbr"],
        suffixes=("", "_opp"),
    )
    m["run_diff_gap"] = m["season_run_diff_pg_before_game"] - m["season_run_diff_pg_before_game_opp"]

    def bucket(gap):
        if pd.isna(gap):
            return ""
        if gap > RUN_DIFF_EVEN_THRESHOLD:
            return "stronger_team_vs_weaker_team"
        if gap < -RUN_DIFF_EVEN_THRESHOLD:
            return "weaker_team_vs_stronger_team"
        return "roughly_even_matchup"

    m["run_diff_bucket"] = m["run_diff_gap"].apply(bucket)
    return m[["game_pk", "team_abbr", "run_diff_gap", "run_diff_bucket"]].rename(
        columns={"team_abbr": "subject_team"}
    )


def build_run_diff_context(df: pd.DataFrame, window_start: str, window_end: str) -> list[dict]:
    gap = load_run_diff_gap(window_start, window_end)
    merged = df.merge(gap, on=["game_pk", "subject_team"], how="left")
    yes = merged[(merged["side"] == "YES") & (merged["run_diff_bucket"] != "")].copy()

    rows: list[dict] = []
    for (mt, bucket), sub in yes.groupby(["market_type_bucket", "run_diff_bucket"]):
        if len(sub) == 0:
            continue
        s = summarize(sub)
        implied = s["avg_price_cents"]
        hit = s["hit_rate_pct"]
        actual_minus_implied = round(hit - implied, 2) if (hit is not None and implied is not None) else None
        rows.append(mk_row(category="run_differential_context", dimension_type="market_type_x_bucket",
                            dimension_value=bucket, market_type=mt, side="YES",
                            market_implied_probability_pct=implied,
                            actual_minus_implied_pp=actual_minus_implied,
                            **s))
    for bucket, sub in yes.groupby("run_diff_bucket"):
        if len(sub) == 0:
            continue
        s = summarize(sub)
        implied = s["avg_price_cents"]
        hit = s["hit_rate_pct"]
        actual_minus_implied = round(hit - implied, 2) if (hit is not None and implied is not None) else None
        rows.append(mk_row(category="run_differential_context", dimension_type="overall",
                            dimension_value=bucket, market_type="ALL", side="YES",
                            market_implied_probability_pct=implied,
                            actual_minus_implied_pp=actual_minus_implied,
                            **s))
    return rows


# ---------------------------------------------------------------------------
# Category 4: brain-market disagreement
# ---------------------------------------------------------------------------
def build_brain_market_gap(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    sub = df[df["brain_score"].notna() & df["calibrated_probability"].notna() & df["last_price_cents"].notna()].copy()
    sub["gap_cents"] = (sub["calibrated_probability"] * 100) - sub["last_price_cents"]

    def bucket(r):
        brain_high = r["calibrated_probability"] >= 0.5
        market_high = r["last_price_cents"] >= 50
        if brain_high and market_high:
            return "brain_high_market_high"
        if brain_high and not market_high:
            return "brain_high_market_low"
        if not brain_high and market_high:
            return "brain_low_market_high"
        return "brain_low_market_low"

    sub["bucket"] = sub.apply(bucket, axis=1)

    detail = sub[[
        "game_date", "game_id", "subject_team", "market_type", "threshold", "side",
        "brain_score", "calibrated_probability", "last_price_cents", "gap_cents", "bucket",
        "graded", "outcome", "pl_cents",
    ]].rename(columns={"last_price_cents": "market_implied_probability_cents"}).sort_values(
        "gap_cents", key=lambda s: s.abs(), ascending=False
    )

    rows: list[dict] = []
    for bucket_name, bsub in sub.groupby("bucket"):
        rows.append(mk_row(category="brain_market_disagreement", dimension_type="bucket",
                            dimension_value=bucket_name, market_type="ALL", side="ALL",
                            **summarize(bsub, price_col="last_price_cents")))
    return detail, rows


# ---------------------------------------------------------------------------
# Category 5: market drift
# ---------------------------------------------------------------------------
def build_market_drift(df: pd.DataFrame) -> list[dict]:
    yes = df[(df["side"] == "YES") & (df["graded"] == True) & df["price_move_cents"].notna()].copy()  # noqa: E712
    no_rows = df[df["side"] == "NO"][["market_ticker", "last_price_cents", "graded", "pl_cents"]].rename(
        columns={"last_price_cents": "no_last_price_cents", "pl_cents": "no_pl_cents", "graded": "no_graded"}
    )
    yes = yes.merge(no_rows, on="market_ticker", how="left")
    yes["move_bucket"] = yes["price_move_cents"].apply(move_bucket)
    yes["direction"] = np.where(
        yes["price_move_cents"] > 0, "moved_up",
        np.where(yes["price_move_cents"] < 0, "moved_down", "flat"),
    )
    yes["pl_buy_early_cents"] = np.where(
        yes["outcome"] == "WIN", 100 - yes["first_price_cents"], -yes["first_price_cents"]
    )
    yes["pl_buy_early_cents"] = np.where(yes["first_price_cents"].isna(), np.nan, yes["pl_buy_early_cents"])

    def agg(sub: pd.DataFrame) -> dict:
        s = summarize(sub)
        n = len(sub)
        pl_early = sub["pl_buy_early_cents"].sum() if n else None
        pl_late = sub["pl_cents"].sum() if n else None
        fade = sub[sub["no_graded"] == True]  # noqa: E712
        pl_fade = fade["no_pl_cents"].sum() if len(fade) else None
        toward = sub["moved_toward_winner"].dropna()
        pct_toward = 100.0 * (toward == True).sum() / len(toward) if len(toward) else None  # noqa: E712
        s.update({
            "pl_buy_early_cents": round(pl_early, 1) if pl_early is not None and pd.notna(pl_early) else None,
            "pl_buy_late_cents": round(pl_late, 1) if pl_late is not None and pd.notna(pl_late) else None,
            "pl_fade_cents": round(pl_fade, 1) if pl_fade is not None and pd.notna(pl_fade) else None,
            "pct_moved_toward_winner": round(pct_toward, 2) if pct_toward is not None else None,
        })
        return s

    rows: list[dict] = []
    for bucket, sub in yes.groupby("move_bucket"):
        if bucket == "" or len(sub) == 0:
            continue
        rows.append(mk_row(category="market_drift", dimension_type="magnitude",
                            dimension_value=bucket, market_type="ALL", side="YES", **agg(sub)))
    for direction, sub in yes.groupby("direction"):
        if len(sub) == 0:
            continue
        rows.append(mk_row(category="market_drift", dimension_type="direction",
                            dimension_value=direction, market_type="ALL", side="YES", **agg(sub)))
    for (mt, bucket), sub in yes.groupby(["market_type_bucket", "move_bucket"]):
        if bucket == "" or len(sub) == 0:
            continue
        rows.append(mk_row(category="market_drift", dimension_type="magnitude_by_market_type",
                            dimension_value=bucket, market_type=mt, side="YES", **agg(sub)))
    return rows


# ---------------------------------------------------------------------------
# Category 6: team coverage gaps
# ---------------------------------------------------------------------------
def build_coverage_gaps(df: pd.DataFrame, mlb_games: pd.DataFrame, window_start: str, window_end: str) -> pd.DataFrame:
    card_rows = []
    for d in sorted(CARDS_DIR.iterdir()):
        if not d.is_dir():
            continue
        if not (window_start <= d.name <= window_end):
            continue
        f = d / "pregame_identifier_cards.csv"
        if not f.exists():
            continue
        cards = pd.read_csv(f, usecols=["game_pk", "game_date", "game_id", "team", "opponent", "home_away"])
        card_rows.append(cards)
    universe = pd.concat(card_rows, ignore_index=True) if card_rows else pd.DataFrame(
        columns=["game_pk", "game_date", "game_id", "team", "opponent", "home_away"]
    )

    dh_counts = mlb_games.groupby(["game_date", "away_abbr_final", "home_abbr_final"]).size()
    dh_pairs = set(dh_counts[dh_counts > 1].index)
    games_idx = mlb_games.set_index("game_pk")[["game_date", "away_abbr_final", "home_abbr_final"]]

    wm_game_pks = set(df["game_pk"].dropna().unique())
    per_team_rows = df.groupby(["game_pk", "subject_team"]).agg(
        n_rows=("market_ticker", "size"),
        n_with_snapshot=("n_pregame_snapshots", lambda s: (pd.to_numeric(s, errors="coerce").fillna(0) > 0).sum()),
        n_graded=("graded", lambda s: (s == True).sum()),  # noqa: E712
        n_empty_book=("empty_book", lambda s: (s == True).sum()),  # noqa: E712
    ).reset_index()

    def classify(row):
        gpk = row["game_pk"]
        team = row["team"]
        gd, away, home = games_idx.loc[gpk] if gpk in games_idx.index else (row["game_date"], None, None)
        if (gd, away, home) in dh_pairs:
            return "doubleheader_ambiguous_excluded"
        if gpk not in wm_game_pks:
            return "no_kalshi_ticker_match"
        pt = per_team_rows[(per_team_rows["game_pk"] == gpk) & (per_team_rows["subject_team"] == team)]
        if len(pt) == 0:
            return "no_kalshi_ticker_match"
        pt = pt.iloc[0]
        if pt["n_graded"] > 0:
            return "ok_has_usable_price"
        if pt["n_with_snapshot"] == 0:
            return "no_pregame_snapshot"
        if pt["n_empty_book"] > 0:
            return "empty_book_only"
        return "no_usable_price_other"

    universe["coverage_gap_type"] = universe.apply(classify, axis=1)
    universe["team_or_opponent_is_wsn"] = (universe["team"] == "WSN") | (universe["opponent"] == "WSN")
    return universe.sort_values(["coverage_gap_type", "game_date", "game_id"])


# ---------------------------------------------------------------------------
# Watchlist history (append-only, one row per key cell per run)
# ---------------------------------------------------------------------------
HISTORY_CSV = OUT_DIR / "watchlist_history.csv"
HISTORY_SUMMARY_MD = OUT_DIR / "watchlist_history_summary.md"

HISTORY_COLUMNS = [
    "run_timestamp", "source_date_start", "source_date_end", "source_row_count",
    "category", "bucket_name", "market_type", "rows", "graded_rows",
    "actual_hit_rate", "implied_breakeven", "actual_minus_implied_pp",
    "pnl_cents", "avg_fill_price", "avg_spread", "notes",
]

# Fixed set of "key" cells to track every run. Each spec locates one row in
# bucket_df by (category, dimension_type, dimension_value[, market_type]).
KEY_BUCKET_SPECS = [
    dict(bucket_name="team_totals_roughly_even_matchup", category="run_differential_context",
         dimension_type="market_type_x_bucket", dimension_value="roughly_even_matchup",
         market_type="team totals"),
    dict(bucket_name="expensive_yes_56_65c", category="expensive_yes",
         dimension_type="overall", dimension_value="56-65c"),
    dict(bucket_name="expensive_yes_66_80c", category="expensive_yes",
         dimension_type="overall", dimension_value="66-80c"),
    dict(bucket_name="expensive_yes_81_99c", category="expensive_yes",
         dimension_type="overall", dimension_value="81-99c"),
    dict(bucket_name="team_runs_5plus_no_official_threshold", category="team_runs_5plus_no",
         dimension_type="candidate_status", dimension_value="official_candidate"),
    dict(bucket_name="team_runs_5plus_no_below_threshold_watch", category="team_runs_5plus_no",
         dimension_type="candidate_status", dimension_value="borderline_non_candidate"),
    dict(bucket_name="brain_high_market_high", category="brain_market_disagreement",
         dimension_type="bucket", dimension_value="brain_high_market_high"),
    dict(bucket_name="brain_high_market_low", category="brain_market_disagreement",
         dimension_type="bucket", dimension_value="brain_high_market_low"),
]


def _history_pnl_row(spec: dict, bucket_df: pd.DataFrame, run_timestamp: str,
                      window_start: str, window_end: str, source_row_count: int) -> dict:
    match = bucket_df[
        (bucket_df["category"] == spec["category"])
        & (bucket_df["dimension_type"] == spec["dimension_type"])
        & (bucket_df["dimension_value"] == spec["dimension_value"])
    ]
    if "market_type" in spec:
        match = match[match["market_type"] == spec["market_type"]]

    base = dict(
        run_timestamp=run_timestamp, source_date_start=window_start, source_date_end=window_end,
        source_row_count=source_row_count, category=spec["category"], bucket_name=spec["bucket_name"],
    )
    if len(match) == 0:
        base.update(
            market_type=spec.get("market_type", ""), rows=0, graded_rows=0,
            actual_hit_rate=None, implied_breakeven=None, actual_minus_implied_pp=None,
            pnl_cents=None, avg_fill_price=None, avg_spread=None,
            notes="no rows matched this bucket in this run window (bucket defined but empty)",
        )
        return base

    r = match.iloc[0]
    implied = r["breakeven_pct"] if pd.notna(r.get("breakeven_pct")) else r.get("market_implied_probability_pct")
    gap = r["hit_minus_breakeven_pp"] if pd.notna(r.get("hit_minus_breakeven_pp")) else r.get("actual_minus_implied_pp")
    base.update(
        market_type=r.get("market_type", ""), rows=r.get("n"), graded_rows=r.get("n_graded"),
        actual_hit_rate=r.get("hit_rate_pct"), implied_breakeven=implied,
        actual_minus_implied_pp=gap, pnl_cents=r.get("pl_cents"),
        avg_fill_price=r.get("avg_price_cents"), avg_spread=r.get("avg_spread_cents"),
        notes=r.get("sample_warning") or "",
    )
    return base


def _history_coverage_row(bucket_name: str, category: str, sub: pd.DataFrame, notes: str,
                           run_timestamp: str, window_start: str, window_end: str,
                           source_row_count: int) -> dict:
    return dict(
        run_timestamp=run_timestamp, source_date_start=window_start, source_date_end=window_end,
        source_row_count=source_row_count, category=category, bucket_name=bucket_name,
        market_type="", rows=len(sub), graded_rows=None, actual_hit_rate=None,
        implied_breakeven=None, actual_minus_implied_pp=None, pnl_cents=None,
        avg_fill_price=None, avg_spread=None, notes=notes,
    )


def select_key_watchlist_cells(bucket_df: pd.DataFrame, coverage: pd.DataFrame, run_timestamp: str,
                                window_start: str, window_end: str, source_row_count: int) -> pd.DataFrame:
    rows = [
        _history_pnl_row(spec, bucket_df, run_timestamp, window_start, window_end, source_row_count)
        for spec in KEY_BUCKET_SPECS
    ]

    wsh = coverage[
        (coverage["coverage_gap_type"] == "no_kalshi_ticker_match") & (coverage["team_or_opponent_is_wsn"] == True)  # noqa: E712
    ]
    wsh_games = wsh["game_pk"].nunique() if len(wsh) else 0
    rows.append(_history_coverage_row(
        "wsh_no_coverage_games", "team_coverage_gaps", wsh,
        f"{wsh_games} games / {len(wsh)} team-game rows with zero Kalshi ticker match, WSN involved",
        run_timestamp, window_start, window_end, source_row_count,
    ))

    partial = coverage[coverage["coverage_gap_type"] == "no_pregame_snapshot"]
    partial_games = partial["game_pk"].nunique() if len(partial) else 0
    partial_dates = ",".join(sorted(partial["game_date"].unique())) if len(partial) else ""
    rows.append(_history_coverage_row(
        "partial_collection_day_gaps", "team_coverage_gaps", partial,
        f"{partial_games} games / {len(partial)} team-game rows with zero pregame snapshots"
        + (f"; dates: {partial_dates}" if partial_dates else ""),
        run_timestamp, window_start, window_end, source_row_count,
    ))

    return pd.DataFrame(rows)[HISTORY_COLUMNS]


def append_history(key_cells: pd.DataFrame) -> pd.DataFrame:
    write_header = not HISTORY_CSV.exists()
    key_cells.to_csv(HISTORY_CSV, mode="a", header=write_header, index=False)
    return pd.read_csv(HISTORY_CSV)


def _fmt(v, digits=2, pct=False, cents=False):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    if cents:
        return f"{v:,.0f}c"
    if pct:
        return f"{v:.{digits}f}%"
    return f"{v:.{digits}f}"


def _blank(v, default="—"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    s = str(v).strip()
    return s if s and s.lower() != "nan" else default


def write_history_summary(history: pd.DataFrame, run_timestamp: str) -> None:
    run_ts_sorted = sorted(history["run_timestamp"].unique())
    latest_ts = run_ts_sorted[-1]
    assert latest_ts == run_timestamp
    latest = history[history["run_timestamp"] == latest_ts]
    n_runs = len(run_ts_sorted)

    lines = []
    lines.append("# Watchlist History Summary")
    lines.append("")
    lines.append(
        "Lightweight append-only comparison of the key watchlist cells across runs of "
        "`whole_market_watchlist_tracker.py`. Source data: `watchlist_history.csv`. "
        "Monitoring only -- no model, threshold, or lane logic is read from or written "
        "to this file."
    )
    lines.append("")
    lines.append(f"Latest run: `{latest_ts}` ({n_runs} run{'s' if n_runs != 1 else ''} recorded total). "
                  f"Window: {latest['source_date_start'].iloc[0]} -> {latest['source_date_end'].iloc[0]} "
                  f"({int(latest['source_row_count'].iloc[0]):,} source rows).")
    lines.append("")

    if n_runs == 1:
        lines.append("## First run -- baseline established")
        lines.append("")
        lines.append("No prior run exists yet, so there is nothing to compare against. "
                      "This run's values are the baseline future runs will be compared to.")
        lines.append("")
        lines.append("| bucket | market type | rows | graded | hit rate | implied breakeven | gap (pp) | P/L (cents) | notes |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for _, r in latest.iterrows():
            lines.append(
                f"| {r['bucket_name']} | {_blank(r['market_type'])} | {int(r['rows']) if pd.notna(r['rows']) else 0} | "
                f"{int(r['graded_rows']) if pd.notna(r['graded_rows']) else '—'} | "
                f"{_fmt(r['actual_hit_rate'], pct=True) if pd.notna(r['actual_hit_rate']) else '—'} | "
                f"{_fmt(r['implied_breakeven'], pct=True) if pd.notna(r['implied_breakeven']) else '—'} | "
                f"{_fmt(r['actual_minus_implied_pp']) if pd.notna(r['actual_minus_implied_pp']) else '—'} | "
                f"{_fmt(r['pnl_cents'], cents=True) if pd.notna(r['pnl_cents']) else '—'} | "
                f"{_blank(r['notes'], '')} |"
            )
    else:
        previous_ts = run_ts_sorted[-2]
        previous = history[history["run_timestamp"] == previous_ts]
        first_ts = run_ts_sorted[0]
        lines.append(f"## Latest run vs. previous run (`{previous_ts}` -> `{latest_ts}`)")
        lines.append("")
        lines.append("| bucket | rows (prev→latest) | hit rate (prev→latest) | gap pp (prev→latest) | P/L cents (prev→latest, Δ) |")
        lines.append("|---|---|---|---|---|")
        for bucket_name in [s["bucket_name"] for s in KEY_BUCKET_SPECS] + [
            "wsh_no_coverage_games", "partial_collection_day_gaps"
        ]:
            lr = latest[latest["bucket_name"] == bucket_name]
            pr = previous[previous["bucket_name"] == bucket_name]
            if len(lr) == 0:
                continue
            lr = lr.iloc[0]
            if len(pr) == 0:
                lines.append(f"| {bucket_name} | —→{int(lr['rows']) if pd.notna(lr['rows']) else 0} | "
                              f"no prior data | no prior data | no prior data |")
                continue
            pr = pr.iloc[0]
            row_str = f"{int(pr['rows']) if pd.notna(pr['rows']) else 0}→{int(lr['rows']) if pd.notna(lr['rows']) else 0}"
            if pd.notna(pr["actual_hit_rate"]) and pd.notna(lr["actual_hit_rate"]):
                hit_str = f"{_fmt(pr['actual_hit_rate'], pct=True)}→{_fmt(lr['actual_hit_rate'], pct=True)}"
            else:
                hit_str = "n/a"
            if pd.notna(pr["actual_minus_implied_pp"]) and pd.notna(lr["actual_minus_implied_pp"]):
                gap_str = f"{_fmt(pr['actual_minus_implied_pp'])}→{_fmt(lr['actual_minus_implied_pp'])}"
            else:
                gap_str = "n/a"
            if pd.notna(pr["pnl_cents"]) and pd.notna(lr["pnl_cents"]):
                delta = lr["pnl_cents"] - pr["pnl_cents"]
                pnl_str = f"{_fmt(pr['pnl_cents'], cents=True)}→{_fmt(lr['pnl_cents'], cents=True)} (Δ{delta:+,.0f}c)"
            else:
                pnl_str = "n/a"
            lines.append(f"| {bucket_name} | {row_str} | {hit_str} | {gap_str} | {pnl_str} |")
        if n_runs >= 3:
            lines.append("")
            lines.append(f"## Trend since first run (`{first_ts}` -> `{latest_ts}`, {n_runs} runs)")
            lines.append("")
            lines.append("| bucket | rows (first→latest) | P/L cents (first→latest, Δ) |")
            lines.append("|---|---|---|")
            first = history[history["run_timestamp"] == first_ts]
            for bucket_name in [s["bucket_name"] for s in KEY_BUCKET_SPECS] + [
                "wsh_no_coverage_games", "partial_collection_day_gaps"
            ]:
                lr = latest[latest["bucket_name"] == bucket_name]
                fr = first[first["bucket_name"] == bucket_name]
                if len(lr) == 0 or len(fr) == 0:
                    continue
                lr, fr = lr.iloc[0], fr.iloc[0]
                row_str = f"{int(fr['rows']) if pd.notna(fr['rows']) else 0}→{int(lr['rows']) if pd.notna(lr['rows']) else 0}"
                if pd.notna(fr["pnl_cents"]) and pd.notna(lr["pnl_cents"]):
                    delta = lr["pnl_cents"] - fr["pnl_cents"]
                    pnl_str = f"{_fmt(fr['pnl_cents'], cents=True)}→{_fmt(lr['pnl_cents'], cents=True)} (Δ{delta:+,.0f}c)"
                else:
                    pnl_str = "n/a"
                lines.append(f"| {bucket_name} | {row_str} | {pnl_str} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Verdict: \"Watchlist only; no model changes.\"** This history log tracks how the "
                  "same fixed set of cells moves as more dates are collected. It does not by itself "
                  "justify a threshold, lane, or model change regardless of what the deltas above show.")
    lines.append("")

    HISTORY_SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_mlb_games(window_start: str, window_end: str) -> pd.DataFrame:
    con = sqlite3.connect(DB_PATH)
    q = """
        SELECT game_pk, game_date, away_abbr AS away_abbr_final, home_abbr AS home_abbr_final,
               final_away_score, final_home_score
        FROM mlb_games
        WHERE game_date BETWEEN ? AND ?
    """
    out = pd.read_sql_query(q, con, params=[window_start, window_end])
    con.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path, default=None,
                     help="whole_market_exploration output dir (defaults to newest found)")
    args = ap.parse_args()

    source_dir = args.source_dir or find_latest_source_dir()
    rows_csv = source_dir / "whole_market_rows.csv"
    if not rows_csv.exists():
        raise SystemExit(f"{rows_csv} not found -- run whole_market_exploration*.py first")

    df = pd.read_csv(rows_csv, low_memory=False)
    for col in ["brain_score", "calibrated_probability", "last_price_cents", "first_price_cents",
                "price_move_cents", "avg_spread_cents", "last_snap_minutes_before", "pl_cents",
                "n_pregame_snapshots", "threshold", "game_pk"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["graded"] = df["graded"].astype(str).map({"True": True, "False": False}).fillna(False)
    df["empty_book"] = df["empty_book"].astype(str).map({"True": True, "False": False}).fillna(False)
    df["moved_toward_winner"] = df["moved_toward_winner"].map(
        lambda v: True if str(v) == "True" else (False if str(v) == "False" else np.nan)
    )

    window_start = str(df["game_date"].min())
    window_end = str(df["game_date"].max())
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    mlb_games = load_mlb_games(window_start, window_end)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_bucket_rows: list[dict] = []
    all_bucket_rows += build_expensive_yes(df)
    team5_bucket_rows, team5_detail = build_team5_no(df, mlb_games)
    all_bucket_rows += team5_bucket_rows
    all_bucket_rows += build_run_diff_context(df, window_start, window_end)
    brain_gap_detail, brain_gap_bucket_rows = build_brain_market_gap(df)
    all_bucket_rows += brain_gap_bucket_rows
    all_bucket_rows += build_market_drift(df)

    bucket_df = pd.DataFrame(all_bucket_rows)
    bucket_df.insert(0, "generated_at", generated_at)
    bucket_df.insert(1, "window_start", window_start)
    bucket_df.insert(2, "window_end", window_end)
    bucket_df.to_csv(OUT_DIR / "watchlist_bucket_tracking.csv", index=False)

    price_perf = bucket_df[bucket_df["category"] == "expensive_yes"].copy()
    price_perf.to_csv(OUT_DIR / "watchlist_price_bucket_performance.csv", index=False)

    brain_gap_detail.insert(0, "generated_at", generated_at)
    brain_gap_detail.insert(1, "window_start", window_start)
    brain_gap_detail.insert(2, "window_end", window_end)
    brain_gap_detail.to_csv(OUT_DIR / "watchlist_brain_market_gap.csv", index=False)

    coverage = build_coverage_gaps(df, mlb_games, window_start, window_end)
    coverage.insert(0, "generated_at", generated_at)
    coverage.insert(1, "window_start", window_start)
    coverage.insert(2, "window_end", window_end)
    coverage.to_csv(OUT_DIR / "watchlist_team_coverage_gaps.csv", index=False)

    # Row-level drill-down: expensive-YES rows + all team5-NO rows, tagged by watchlist category.
    yes_expensive = df[(df["side"] == "YES") & (df["price_bucket"].isin(EXPENSIVE_YES_BUCKETS))].copy()
    yes_expensive["watchlist_category"] = "expensive_yes"
    team5_detail = team5_detail.copy()
    team5_detail["watchlist_category"] = "team_runs_5plus_no"
    row_cols = [
        "watchlist_category", "game_date", "game_id", "game_pk", "away_abbr", "home_abbr",
        "subject_team", "market_type", "market_type_bucket", "threshold", "side", "market_ticker",
        "price_bucket", "last_price_cents", "avg_spread_cents", "last_snap_minutes_before",
        "n_pregame_snapshots", "graded", "outcome", "pl_cents", "brain_score", "calibrated_probability",
        "candidate_status", "score_bin", "final_team_runs", "context_favorite_underdog",
        "opponent_starter_kbb_bucket", "opponent_starter_xfip_bucket", "offense_form_bucket",
    ]
    for c in row_cols:
        if c not in yes_expensive.columns:
            yes_expensive[c] = None
        if c not in team5_detail.columns:
            team5_detail[c] = None
    watchlist_rows = pd.concat([yes_expensive[row_cols], team5_detail[row_cols]], ignore_index=True)
    watchlist_rows.insert(0, "generated_at", generated_at)
    watchlist_rows.insert(1, "window_start", window_start)
    watchlist_rows.insert(2, "window_end", window_end)
    watchlist_rows.to_csv(OUT_DIR / "kalshi_market_watchlist_rows.csv", index=False)

    key_cells = select_key_watchlist_cells(
        bucket_df, coverage, generated_at, window_start, window_end, len(df)
    )
    history = append_history(key_cells)
    write_history_summary(history, generated_at)

    return {
        "window_start": window_start,
        "window_end": window_end,
        "generated_at": generated_at,
        "source_dir": str(source_dir),
        "bucket_df": bucket_df,
        "brain_gap_detail": brain_gap_detail,
        "coverage": coverage,
        "watchlist_rows": watchlist_rows,
        "key_cells": key_cells,
        "history": history,
        "df": df,
    }


if __name__ == "__main__":
    result = main()
    print(f"Window: {result['window_start']} -> {result['window_end']}")
    print(f"Source: {result['source_dir']}")
    print(f"Wrote outputs to: {OUT_DIR}")
    print(f"Appended {len(result['key_cells'])} key cells to watchlist_history.csv "
          f"({result['history']['run_timestamp'].nunique()} run(s) recorded total)")
