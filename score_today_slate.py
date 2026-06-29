"""
score_today_slate.py

Read-only. Scores today's scheduled MLB games using the pregame brain and writes rows
with game_date=<slate_date> into the pregame_identifier_card_preview output CSVs so the
Slate Monitor can display them.

No lookahead: only uses features computable before game time — rolling team stats from
completed games and team context from historical_team_context_2026_clean.csv. Starter
features are omitted (no starters available for unplayed games).

Prerequisite: run historical_team_context_preview_v2.py --season 2026 first.

Usage:
  python score_today_slate.py [--date YYYY-MM-DD] [--db DB]
"""

import argparse
import csv
import importlib.util
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

FF_SCRIPT = Path("pregame_feature_family_lift_preview.py")
BEANS_SCRIPT = Path("beans_offense_defense_lift_preview.py")
CP_SCRIPT = Path("pregame_identifier_card_preview.py")
OUT_DIR = Path("outputs") / "pregame_identifier_card_preview"
CTX_2026 = Path("outputs") / "historical_team_context_preview_v2" / "historical_team_context_2026_clean.csv"

# Minimum current-season starts before we prefer current-season stats over prior-season fallback.
# Below this threshold, prior-season data (if available) gives more stable features.
_MIN_CURR_STARTS = 3


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    seen: dict = {}
    for r in rows:
        for k in r:
            seen[k] = None
    fieldnames = list(seen)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _merge(existing: list[dict], new_rows: list[dict], slate_date: str) -> list[dict]:
    kept = [r for r in existing if r.get("game_date") != slate_date]
    return kept + new_rows


def _score_live_row(feat_row: dict, rules_by_outcome: dict, max_rules_per_side: int, cp) -> dict:
    """Score a feature row for an unplayed game (no outcome actuals required)."""
    outcome_scores: dict[str, dict] = {}
    for outcome in cp.TARGET_OUTCOMES:
        pos_rules, neg_rules = [], []
        for rule in rules_by_outcome.get(outcome, []):
            if not cp._rule_matches(feat_row, rule):
                continue
            lift = cp.as_float(rule["avg_lift"]) or 0.0
            (pos_rules if lift > 0 else neg_rules if lift < 0 else []).append(rule)

        pos_rules = sorted(pos_rules, key=lambda r: -(cp.as_float(r["avg_lift"]) or 0))[:max_rules_per_side]
        neg_rules = sorted(neg_rules, key=lambda r: (cp.as_float(r["avg_lift"]) or 0))[:max_rules_per_side]

        if outcome == "team_runs_5plus":
            pos_rules = [r for r in pos_rules if not cp._is_beans_rule(r)]

        pos_score = sum(cp.as_float(r["avg_lift"]) or 0 for r in pos_rules)
        neg_score = sum(cp.as_float(r["avg_lift"]) or 0 for r in neg_rules)
        net = pos_score + neg_score
        outcome_scores[outcome] = {
            "net_score": round(net, 4),
            "top_pos": " | ".join(cp._rule_label(r) for r in pos_rules[:3]),
            "top_neg": " | ".join(cp._rule_label(r) for r in neg_rules[:3]),
        }

    def net(o: str) -> float:
        return cp.as_float(outcome_scores.get(o, {}).get("net_score")) or 0.0

    side_net = net("team_won")
    side_score = max(0.0, side_net)
    side_fade_score = max(0.0, -side_net)
    runs4_score = max(0.0, net("team_runs_4plus"))
    runs5_no_score = max(0.0, -net("team_runs_5plus"))
    f5_score = max(0.0, net("team_f5_runs_2plus"))
    total_avoid_score = max(0.0, -net("game_total_9plus"))
    game_over_score   = max(0.0,  net("game_total_9plus"))
    live_watch_score = (max(0.0, net("team_early_deficit_tied_or_led_later")) + max(0.0, net("opponent_blew_early_small_lead"))) / 2.0
    avoid_parts = [max(0.0, -side_net), max(0.0, -net("team_runs_4plus")), max(0.0, -net("team_runs_5plus")), max(0.0, -net("team_f5_runs_2plus"))]
    active_avoid = [x for x in avoid_parts if x > 0]
    avoid_score = sum(active_avoid) / len(active_avoid) if active_avoid else 0.0

    pos_reasons, neg_reasons = [], []
    for outcome, data in outcome_scores.items():
        ns = data["net_score"]
        if ns >= 0.04 and data["top_pos"]:
            for piece in data["top_pos"].split(" | ")[:2]:
                pos_reasons.append(f"[{outcome}] {piece}")
        elif ns <= -0.04 and data["top_neg"]:
            for piece in data["top_neg"].split(" | ")[:2]:
                neg_reasons.append(f"[{outcome}] {piece}")

    thresholds = cp.CARD_THRESHOLDS
    side_pick = (
        "lean" if side_score >= thresholds["side_score"]
        else "fade" if side_fade_score >= thresholds["side_fade_score"]
        else "neutral"
    )

    return {
        **feat_row,
        "model_version": "ff_only",
        "side_score": round(side_score, 4),
        "side_fade_score": round(side_fade_score, 4),
        "side_pick": side_pick,
        "team_runs_4plus_score": round(runs4_score, 4),
        "team_runs_5plus_no_score": round(runs5_no_score, 4),
        "team_f5_runs_2plus_score": round(f5_score, 4),
        "full_total_avoid_score": round(total_avoid_score, 4),
        "full_game_over_score": round(game_over_score, 4),
        "live_watch_score": round(live_watch_score, 4),
        "avoid_score": round(avoid_score, 4),
        "top_positive_reasons": " | ".join(pos_reasons[:5]),
        "top_negative_reasons": " | ".join(neg_reasons[:5]),
        "bo_bucket": "missing",
        "bd_bucket": "missing",
        "bo_plus_weak_bd_tag": "missing",
        "avoid_low_bo_strong_bd_tag": "missing",
        "actual_team_won": "",
        "actual_team_runs_4plus": "",
        "actual_team_runs_5plus": "",
        "actual_team_f5_runs_2plus": "",
        "actual_game_total_9plus": "",
        "actual_lw_tied_or_led": "",
        "validation_mode": "live_slate",
    }


def _build_feature_row(game: tuple, team: str, opponent: str, is_home: bool,
                       team_hist: dict, ff, latest_ctx: dict, slate_date: str,
                       starter_hist: dict | None = None, lhr: float = 0.11,
                       xfip_const: float = 0.0,
                       starter_hist_prev: dict | None = None, lhr_prev: float = 0.11,
                       xfip_const_prev: float = 0.0,
                       pp_by_game: dict | None = None) -> dict:
    team_roll = ff.team_context_from_history(list(team_hist.get(team, [])))
    opp_roll = ff.team_context_from_history(list(team_hist.get(opponent, [])))
    team_ctx = latest_ctx.get(team)
    opp_ctx = latest_ctx.get(opponent)

    game_pk = game[0]
    pp = (pp_by_game or {}).get(game_pk, {})
    if is_home:
        own_pp_id, own_pp_name = pp.get("home_id"), pp.get("home_name")
        opp_pp_id, opp_pp_name = pp.get("away_id"), pp.get("away_name")
    else:
        own_pp_id, own_pp_name = pp.get("away_id"), pp.get("away_name")
        opp_pp_id, opp_pp_name = pp.get("home_id"), pp.get("home_name")

    def _lookup_sh(sh, pitcher_id, pitcher_name):
        """Return (hist, key, name) from a starter_hist dict. hist may be empty."""
        if pitcher_id:
            key = f"id:{pitcher_id}"
            hist = list(sh.get(key, []))
            if hist:
                return hist, key, pitcher_name or key
        if pitcher_name:
            key = ff.normalize_pitcher_key("", pitcher_name)
            hist = list(sh.get(key, []))
            if hist:
                return hist, key, pitcher_name
        key = f"id:{pitcher_id}" if pitcher_id else ff.normalize_pitcher_key("", pitcher_name) if pitcher_name else ""
        return [], key, pitcher_name or ""

    def _pitcher_hist(pitcher_id, pitcher_name):
        """
        Return (hist, key, name, source, _lhr, _xfip_const).
        source is 'current_season', 'prior_season_fallback', or 'missing'.
        Uses prior-season data when current-season sample is below _MIN_CURR_STARTS.
        """
        curr_hist, curr_key, curr_name = _lookup_sh(starter_hist or {}, pitcher_id, pitcher_name)

        if len(curr_hist) >= _MIN_CURR_STARTS:
            return curr_hist, curr_key, curr_name, "current_season", lhr, xfip_const

        prev_hist, _, prev_name = _lookup_sh(starter_hist_prev or {}, pitcher_id, pitcher_name)
        if prev_hist:
            # Prefer the current key (id-based if available) so bucketed rules resolve correctly
            key = curr_key or ff.normalize_pitcher_key("", pitcher_name) if pitcher_name else ""
            return prev_hist, key, curr_name or prev_name, "prior_season_fallback", lhr_prev, xfip_const_prev

        # Current season exists but below threshold — use as small sample
        if curr_hist:
            return curr_hist, curr_key, curr_name, "current_season", lhr, xfip_const

        return [], curr_key, pitcher_name or "", "missing", lhr, xfip_const

    own_hist, own_key, own_name, own_source, own_lhr, own_xc = _pitcher_hist(own_pp_id, own_pp_name)
    opp_hist, opp_key, opp_name, opp_source, opp_lhr, opp_xc = _pitcher_hist(opp_pp_id, opp_pp_name)

    starter_pre = ff.starter_context_from_history(own_hist, own_lhr, own_xc)
    opp_starter_pre = ff.starter_context_from_history(opp_hist, opp_lhr, opp_xc)

    team_strength = ff.ctx_float(team_ctx, "team_strength_proxy")
    opp_strength = ff.ctx_float(opp_ctx, "team_strength_proxy")
    offense_form = ff.ctx_float(team_ctx, "offense_form_proxy")
    opp_run_prev = ff.ctx_float(opp_ctx, "run_prevention_proxy")
    strength_gap = (team_strength - opp_strength) if team_strength is not None and opp_strength is not None else None

    f5_style = "missing"
    team_f5 = ff.as_float(team_roll.get("l10_f5_rpg"))
    team_post5 = ff.as_float(team_roll.get("l10_post5_rpg"))
    if team_f5 is not None and team_post5 is not None:
        if team_f5 >= 2.5 and team_post5 >= 2.0:
            f5_style = "early_and_late_scoring"
        elif team_f5 >= 2.5:
            f5_style = "early_scoring"
        elif team_post5 >= 2.0:
            f5_style = "late_scoring"
        else:
            f5_style = "low_scoring_profile"

    tag_home_scoring_spot = is_home and ff.bucket_rpg(ff.as_float(team_roll.get("l10_rpg"))) in {"high_4_5_5_5", "very_high_5_5_plus"}
    tag_strong_offense_vs_weak_opp = (
        ff.bucket_rate(ff.as_float(team_roll.get("l10_scored4_rate"))) in {"high_60_75", "very_high_75_plus"}
        and ff.bucket_rate(ff.as_float(opp_roll.get("l10_allowed4_rate"))) in {"high_60_75", "very_high_75_plus"}
    )
    tag_strong_offense_vs_vulnerable_starter = (
        ff.bucket_rate(ff.as_float(team_roll.get("l10_scored4_rate"))) in {"high_60_75", "very_high_75_plus"}
        and ff.bucket_ra9(ff.as_float(opp_starter_pre.get("starter_ra9"))) in {"bad_5_0_6_0", "very_bad_6_plus"}
    )
    tag_weak_leader_fade_watch = (
        ff.bucket_rating(opp_strength) in {"lt_40", "40_45"}
        and ff.bucket_rate(ff.as_float(team_roll.get("l10_scored4_rate"))) in {"high_60_75", "very_high_75_plus"}
    )
    tag_live_rebound_watch = (
        is_home
        and ff.bucket_rating(team_strength) in {"50_55", "55_60", "60_plus"}
        and ff.bucket_rating(opp_strength) in {"lt_40", "40_45", "45_50"}
    )
    tag_low_run_environment_risk = (
        ff.bucket_rate(ff.as_float(team_roll.get("l10_scored2minus_rate"))) in {"high_60_75", "very_high_75_plus"}
        or ff.bucket_rate(ff.as_float(opp_roll.get("l10_allowed2minus_rate"))) in {"high_60_75", "very_high_75_plus"}
    )
    tag_short_leash_bullpen_exposure = (
        ff.bucket_ip(ff.as_float(opp_starter_pre.get("starter_ip_per_start"))) in {"short_lt_4_3", "below_avg_4_3_5_0"}
        and ff.bucket_rpg(ff.as_float(opp_roll.get("l10_post5_allowed_pg"))) in {"high_4_5_5_5", "very_high_5_5_plus"}
    )

    away_abbr = ff.norm_team(game[2])
    home_abbr = ff.norm_team(game[3])

    return {
        "season": "2026",
        "game_pk": str(game[0]),
        "game_date": slate_date,
        "game_id": f"{away_abbr}@{home_abbr}",
        "team": team,
        "opponent": opponent,
        "home_away": "home" if is_home else "away",
        "team_strength": team_strength,
        "opponent_strength": opp_strength,
        "team_strength_gap": strength_gap,
        "offense_form": offense_form,
        "opponent_run_prevention": opp_run_prev,
        "team_context_confidence": ff.context_confidence(team_ctx),
        "opponent_context_confidence": ff.context_confidence(opp_ctx),
        **{f"team_{k}": v for k, v in team_roll.items()},
        **{f"opponent_{k}": v for k, v in opp_roll.items()},
        "starter_key": own_key,
        "starter_name": own_name,
        "opponent_starter_key": opp_key,
        "opponent_starter_name": opp_name,
        **{f"starter_{k}": v for k, v in starter_pre.items()},
        **{f"opponent_{k}": v for k, v in opp_starter_pre.items()},
        "starter_xfip_gap": (
            ff.as_float(opp_starter_pre.get("starter_xfip")) - ff.as_float(starter_pre.get("starter_xfip"))
            if starter_pre.get("starter_xfip") is not None and opp_starter_pre.get("starter_xfip") is not None
            else None
        ),
        "starter_quality_gap": (
            ff.as_float(opp_starter_pre.get("starter_ra9")) - ff.as_float(starter_pre.get("starter_ra9"))
            if starter_pre.get("starter_ra9") is not None and opp_starter_pre.get("starter_ra9") is not None
            else None
        ),
        "team_strength_bucket": ff.bucket_rating(team_strength),
        "opponent_strength_bucket": ff.bucket_rating(opp_strength),
        "team_strength_gap_bucket": ff.bucket_gap(strength_gap),
        "offense_form_bucket": ff.bucket_rating(offense_form),
        "opponent_run_prevention_bucket": ff.bucket_rating(opp_run_prev),
        "l10_rpg_bucket": ff.bucket_rpg(ff.as_float(team_roll.get("l10_rpg"))),
        "l10_scored4_rate_bucket": ff.bucket_rate(ff.as_float(team_roll.get("l10_scored4_rate"))),
        "l10_scored5_rate_bucket": ff.bucket_rate(ff.as_float(team_roll.get("l10_scored5_rate"))),
        "l10_scored2minus_rate_bucket": ff.bucket_rate(ff.as_float(team_roll.get("l10_scored2minus_rate"))),
        "opponent_l10_allowed4_rate_bucket": ff.bucket_rate(ff.as_float(opp_roll.get("l10_allowed4_rate"))),
        "opponent_l10_allowed5_rate_bucket": ff.bucket_rate(ff.as_float(opp_roll.get("l10_allowed5_rate"))),
        "opponent_l10_allowed2minus_rate_bucket": ff.bucket_rate(ff.as_float(opp_roll.get("l10_allowed2minus_rate"))),
        "team_l10_f5_rpg_bucket": ff.bucket_rpg(ff.as_float(team_roll.get("l10_f5_rpg"))),
        "team_l10_post5_rpg_bucket": ff.bucket_rpg(ff.as_float(team_roll.get("l10_post5_rpg"))),
        "opponent_l10_f5_allowed_bucket": ff.bucket_rpg(ff.as_float(opp_roll.get("l10_f5_allowed_pg"))),
        "opponent_l10_post5_allowed_bucket": ff.bucket_rpg(ff.as_float(opp_roll.get("l10_post5_allowed_pg"))),
        "f5_style_bucket": f5_style,
        "starter_confidence": starter_pre.get("starter_confidence"),
        "opponent_starter_confidence": opp_starter_pre.get("starter_confidence"),
        "opponent_starter_ra9_bucket": ff.bucket_ra9(ff.as_float(opp_starter_pre.get("starter_ra9"))),
        "opponent_starter_ip_bucket": ff.bucket_ip(ff.as_float(opp_starter_pre.get("starter_ip_per_start"))),
        "opponent_starter_kbb_bucket": ff.bucket_kbb(ff.as_float(opp_starter_pre.get("starter_kbb_pct"))),
        "opponent_starter_xfip_bucket": ff.bucket_xfip(ff.as_float(opp_starter_pre.get("starter_xfip"))),
        "starter_xfip_gap_bucket": ff.bucket_gap(
            ff.as_float(opp_starter_pre.get("starter_xfip")) - ff.as_float(starter_pre.get("starter_xfip"))
            if starter_pre.get("starter_xfip") is not None and opp_starter_pre.get("starter_xfip") is not None
            else None
        ),
        "starter_quality_gap_bucket": ff.bucket_gap(
            ff.as_float(opp_starter_pre.get("starter_ra9")) - ff.as_float(starter_pre.get("starter_ra9"))
            if starter_pre.get("starter_ra9") is not None and opp_starter_pre.get("starter_ra9") is not None
            else None
        ),
        "opponent_starter_bad_start_rate_bucket": ff.bucket_rate(ff.as_float(opp_starter_pre.get("starter_bad_start_rate"))),
        "opponent_starter_blowup_rate_bucket": ff.bucket_rate(ff.as_float(opp_starter_pre.get("starter_blowup_rate"))),
        "opponent_starter_early_exit_rate_bucket": ff.bucket_rate(ff.as_float(opp_starter_pre.get("starter_early_exit_rate"))),
        "opponent_starter_ra_std_bucket": ff.bucket_std(ff.as_float(opp_starter_pre.get("starter_ra_std"))),
        "starter_feature_source": own_source,
        "opponent_starter_feature_source": opp_source,
        "starter_starts_used": len(own_hist),
        "opponent_starter_starts_used": len(opp_hist),
        "starter_innings_used": round(sum(h.get("outs", 0) for h in own_hist) / 3, 1) if own_hist else 0,
        "opponent_starter_innings_used": round(sum(h.get("outs", 0) for h in opp_hist) / 3, 1) if opp_hist else 0,
        "starter_feature_as_of_date": slate_date,
        "tag_home_scoring_spot": "yes" if tag_home_scoring_spot else "no",
        "tag_strong_offense_vs_weak_opp": "yes" if tag_strong_offense_vs_weak_opp else "no",
        "tag_strong_offense_vs_vulnerable_starter": "yes" if tag_strong_offense_vs_vulnerable_starter else "no",
        "tag_weak_leader_fade_watch": "yes" if tag_weak_leader_fade_watch else "no",
        "tag_live_rebound_watch": "yes" if tag_live_rebound_watch else "no",
        "tag_low_run_environment_risk": "yes" if tag_low_run_environment_risk else "no",
        "tag_short_leash_bullpen_exposure": "yes" if tag_short_leash_bullpen_exposure else "no",
        "BO_bucket": "missing",
        "BO_vs_opponent_BD_gap_bucket": "missing",
        "BD_bucket": "missing",
        "BO_plus_weak_BD_tag": "missing",
        "avoid_low_BO_strong_BD_tag": "missing",
        "strong_BO_clean_BD_tag": "missing",
        "bullpen_outs_last_2d_bucket": "missing",
        "starter_short_outing_previous_game": "missing",
        "bullpen_heavy_previous_game": "missing",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score today's MLB slate with the pregame brain. Read-only output only."
    )
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--db", default="kalshi_mlb.db")
    parser.add_argument("--rolling-games", type=int, default=10)
    parser.add_argument("--rolling-starts", type=int, default=8)
    parser.add_argument("--min-count", type=int, default=100)
    parser.add_argument("--min-abs-lift", type=float, default=0.04)
    parser.add_argument("--max-rules-per-side", type=int, default=12)
    args = parser.parse_args()
    args.allow_mixed_sign_rules = False  # match pregame_identifier_card_preview default

    slate_date = args.date or date.today().isoformat()
    print(f"Scoring slate: {slate_date}")

    cp = _load_module(CP_SCRIPT, "cp")
    ff = _load_module(FF_SCRIPT, "ff")
    beans = _load_module(BEANS_SCRIPT, "beans")

    conn = sqlite3.connect(args.db)

    # Train rules on 2023-2025 (same training set as chronological validation)
    print("Training rules on 2023-2025...")
    train_rows: list[dict] = []
    for season in ["2023", "2024", "2025"]:
        rows, meta = cp.build_season_rows(conn, season, args, ff, beans)
        train_rows.extend(rows)
        print(f"  {season}: {meta['merged_rows']:,} rows")

    feature_families = dict(ff.FEATURE_FAMILIES)
    two_feature_combos = list(ff.TWO_FEATURE_COMBOS)
    rules = cp.build_rules(
        train_rows, feature_families, two_feature_combos,
        args.min_count, args.min_abs_lift, not args.allow_mixed_sign_rules,
    )
    print(f"  Rules trained: {len(rules)}")

    rules_by_outcome: dict[str, list] = defaultdict(list)
    for r in rules:
        rules_by_outcome[r["outcome"]].append(r)

    # Build current-season rolling state (no lookahead — only completed games)
    print("Building 2026 rolling state...")
    team_hist, starter_hist, lhr, xfip_const = ff.build_final_state(
        conn, "2026", args.rolling_games, args.rolling_starts
    )
    print(f"  Teams with rolling history: {len(team_hist)}")
    print(f"  Pitchers with 2026 start history: {len(starter_hist)}")

    # Build prior-season starter history for cross-season fallback.
    # Used only when a pitcher has < {_MIN_CURR_STARTS} current-season starts.
    # All 2025 games are completed → no lookahead risk.
    print("Building 2025 starter fallback state...")
    _, starter_hist_prev, lhr_prev, xfip_const_prev = ff.build_final_state(
        conn, "2025", args.rolling_games, args.rolling_starts
    )
    print(f"  Pitchers with 2025 fallback history: {len(starter_hist_prev)}")

    # Latest context per team from 2026 context CSV (use most recent game entry per team)
    latest_ctx: dict[str, dict] = {}
    if CTX_2026.exists():
        for row in ff.read_csv_rows(CTX_2026):
            abbr = ff.norm_team(row.get("team_abbr") or "")
            if abbr:
                latest_ctx[abbr] = row
        print(f"  Context loaded for {len(latest_ctx)} teams")
    else:
        print(f"  WARNING: {CTX_2026} not found — run historical_team_context_preview_v2.py --season 2026 first")

    # Load today's scheduled (unplayed) games
    schedule = conn.execute(
        "SELECT game_pk, game_date, away_abbr, home_abbr, game_start_time_utc "
        "FROM mlb_games WHERE game_date = ? AND final_away_score IS NULL "
        "ORDER BY COALESCE(game_start_time_utc, ''), game_pk",
        [slate_date],
    ).fetchall()
    print(f"  Scheduled games: {len(schedule)}")

    if not schedule:
        print("No unplayed games found for this date. Nothing to score.")
        return

    # Load probable pitchers for today's games (stored by seed_tonight / fetch_and_store_schedule)
    pp_by_game: dict = {}
    try:
        for row in conn.execute(
            "SELECT game_pk, away_probable_pitcher_id, away_probable_pitcher_name, "
            "home_probable_pitcher_id, home_probable_pitcher_name "
            "FROM mlb_games WHERE game_date = ?",
            [slate_date],
        ).fetchall():
            pp_by_game[row[0]] = {
                "away_id": row[1], "away_name": row[2],
                "home_id": row[3], "home_name": row[4],
            }
    except Exception:
        pass  # column not yet migrated — will fall back to empty history

    pp_found = sum(1 for v in pp_by_game.values() if v.get("away_id") or v.get("home_id"))
    print(f"  Probable pitchers found: {pp_found}/{len(pp_by_game)} games have at least one")

    # Build feature rows and score
    card_rows: list[dict] = []
    for game in schedule:
        away = ff.norm_team(game[2])
        home = ff.norm_team(game[3])
        for team, opponent, is_home in [(away, home, False), (home, away, True)]:
            feat = _build_feature_row(
                game, team, opponent, is_home, team_hist, ff, latest_ctx, slate_date,
                starter_hist=starter_hist, lhr=lhr, xfip_const=xfip_const,
                starter_hist_prev=starter_hist_prev, lhr_prev=lhr_prev, xfip_const_prev=xfip_const_prev,
                pp_by_game=pp_by_game,
            )
            card = _score_live_row(feat, rules_by_outcome, args.max_rules_per_side, cp)
            card_rows.append(card)

    print(f"  Scored {len(card_rows)} team-game rows")

    # Merge into existing output CSVs (deduplicated by game_date)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cards_path = OUT_DIR / "pregame_identifier_cards.csv"
    merged_cards = _merge(_read_csv(cards_path), card_rows, slate_date)
    _write_csv(cards_path, merged_cards)
    print(f"  pregame_identifier_cards.csv: {len(merged_cards)} total rows ({len(card_rows)} for {slate_date})")

    filter_specs = [
        ("pregame_side_leans.csv",        "side_score",               cp.CARD_THRESHOLDS["side_score"]),
        ("pregame_side_fades.csv",         "side_fade_score",          cp.CARD_THRESHOLDS["side_fade_score"]),
        ("team_scoring_watchlist.csv",     "team_runs_4plus_score",    cp.CARD_THRESHOLDS["team_runs_4plus_score"]),
        ("team_5plus_avoid_list.csv",      "team_runs_5plus_no_score", cp.CARD_THRESHOLDS["team_runs_5plus_no_score"]),
        ("team_f5_scoring_watchlist.csv",  "team_f5_runs_2plus_score", cp.CARD_THRESHOLDS["team_f5_runs_2plus_score"]),
        ("live_watchlist.csv",             "live_watch_score",         cp.CARD_THRESHOLDS["live_watch_score"]),
        ("full_avoid_list.csv",            "avoid_score",              cp.CARD_THRESHOLDS["avoid_score"]),
    ]
    for fname, score_col, threshold in filter_specs:
        path = OUT_DIR / fname
        existing_f = _read_csv(path)
        today_q = sorted(
            [r for r in card_rows if (cp.as_float(r.get(score_col)) or 0.0) >= threshold],
            key=lambda r: -(cp.as_float(r.get(score_col)) or 0.0),
        )
        merged_f = [r for r in existing_f if r.get("game_date") != slate_date] + today_q
        _write_csv(path, merged_f)
        print(f"  {fname}: {len(today_q)} rows for {slate_date} (threshold {threshold})")

    starters_populated = sum(
        1 for r in card_rows
        if r.get("starter_key") or r.get("opponent_starter_key")
    )
    fallback_used = sum(
        1 for r in card_rows
        if r.get("starter_feature_source") == "prior_season_fallback"
        or r.get("opponent_starter_feature_source") == "prior_season_fallback"
    )
    print(f"\nDone. Slate Monitor will now show {len(card_rows)} team rows for {slate_date}.")
    print(f"Starter features: {starters_populated}/{len(card_rows)} rows have at least one starter identified.")
    print(f"Prior-season fallback used: {fallback_used}/{len(card_rows)} rows (starters with <{_MIN_CURR_STARTS} current-season starts).")
    if starters_populated == 0:
        print("  Note: no probable pitchers found — re-run seed_tonight.py then retry.")


if __name__ == "__main__":
    main()
