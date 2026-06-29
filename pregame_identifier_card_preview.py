"""
pregame_identifier_card_preview.py

Read-only, output-only research script.
Combines the validated identifier brain (pregame_feature_family_lift_preview + BO/BD from
beans_offense_defense_lift_preview) into per-team-game scored slate cards with multiple lanes:

    side_score               -- positive = lean toward this team winning
    side_fade_score          -- positive = lean toward fading this team
    side_pick                -- lean / fade / neutral at threshold 0.20 / 0.15
    team_runs_4plus_score    -- positive = team likely to score 4+
    team_runs_5plus_no_score -- positive = team unlikely to score 5+ (NO direction only)
    team_f5_runs_2plus_score -- positive = team likely to score 2+ in F5
    full_total_avoid_score   -- positive = total likely to stay under 9
    live_watch_score         -- positive = game likely to produce live rebound action
    avoid_score              -- positive = multiple strong negative signals
    top_positive_reasons / top_negative_reasons

Two scoring models (select with --model):
    ff_only        [DEFAULT] FF features only. Matches prior baselines.
    ff_plus_beans  Comparison: FF + BO/BD combined. Research-only until it beats baselines.

BO/BD diagnosis (2026-06-20): 117 of 500 rules survived filtering — not junk.
However BO/BD did not improve top-line validation (runs5+ NO dropped -1.7%).
Root cause: (a) redundant signal with FF features on side/scoring lanes;
(b) BO/BD's strongest team_runs_5plus signal is positive-direction, which degrades the NO lane.
FF-only remains the default model. Positive BO/BD rules are always excluded from
team_runs_5plus_no_score computation regardless of model.

Validation: leave-one-season-out + chronological (train 2023-2024, test 2025).
Baseline targets from pregame_combined_identifier_score_preview (chronological 2025):
    winner @0.20      57.9%
    runs4+ @0.15      59.4%
    runs5+ NO @0.20   65.1%
    f5_2+  @0.20      61.3%

Prohibited from positive-predict-yes lane outputs (known to invert out-of-season):
    team_runs_5plus YES
    team_early_deficit_scored_next2 YES

No lookahead. No market prices. Baseball-truth classification only.
"""
import argparse
import csv
import importlib.util
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path("outputs") / "pregame_identifier_card_preview"
FF_SCRIPT = Path("pregame_feature_family_lift_preview.py")
BEANS_SCRIPT = Path("beans_offense_defense_lift_preview.py")

MODEL_FF_ONLY = "ff_only"
MODEL_FF_PLUS_BEANS = "ff_plus_beans"

# ── Outcomes ───────────────────────────────────────────────────────────────────

TARGET_OUTCOMES = [
    "team_won",
    "team_runs_4plus",
    "team_runs_5plus",
    "game_total_9plus",
    "f5_total_4plus",
    "team_f5_runs_2plus",
    "team_early_deficit_tied_or_led_later",
    "team_early_deficit_scored_next2",
    "opponent_blew_early_small_lead",
]

# These positive directions invert badly out-of-season — exclude from YES-side card lanes.
PROHIBITED_POS_PREDICT = {"team_runs_5plus", "team_early_deficit_scored_next2"}

# ── Card lane thresholds ───────────────────────────────────────────────────────

CARD_THRESHOLDS = {
    "side_score": 0.20,
    "side_fade_score": 0.15,
    "team_runs_4plus_score": 0.15,
    "team_runs_5plus_no_score": 0.20,
    "team_f5_runs_2plus_score": 0.20,
    "full_total_avoid_score": 0.06,
    "full_game_over_score": 0.20,
    "live_watch_score": 0.04,
    "avoid_score": 0.10,
}

# ── Prior baselines to beat ────────────────────────────────────────────────────
# From pregame_combined_identifier_score_preview, chronological 2023+2024 → 2025

PRIOR_BASELINES = {
    ("team_won",         "positive_predict_yes", 0.20): 0.579,
    ("team_runs_4plus",  "positive_predict_yes", 0.15): 0.594,
    ("team_runs_5plus",  "negative_predict_no",  0.20): 0.651,
    ("team_f5_runs_2plus", "positive_predict_yes", 0.20): 0.613,
}

# ── BO/BD feature additions ────────────────────────────────────────────────────

BEANS_FEATURE_FAMILIES: dict[str, list[str]] = {
    "beans_offense": [
        "BO_bucket",
        "BO_vs_opponent_BD_gap_bucket",
    ],
    "beans_defense": [
        "BD_bucket",
    ],
    "beans_combo_tags": [
        "BO_plus_weak_BD_tag",
        "avoid_low_BO_strong_BD_tag",
        "strong_BO_clean_BD_tag",
    ],
    "beans_bullpen": [
        "bullpen_outs_last_2d_bucket",
        "starter_short_outing_previous_game",
        "bullpen_heavy_previous_game",
    ],
}

BEANS_TWO_FEATURE_COMBOS: list[tuple[str, str]] = [
    ("BO_bucket", "opponent_strength_bucket"),
    ("BD_bucket", "l10_rpg_bucket"),
    ("avoid_low_BO_strong_BD_tag", "opponent_strength_bucket"),
    ("BO_plus_weak_BD_tag", "team_strength_bucket"),
]

BEANS_FILL_DEFAULTS: dict[str, Any] = {
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

# ── Helpers ────────────────────────────────────────────────────────────────────

def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        s = str(value).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        return float(s)
    except Exception:
        return None


def as_int(value: Any) -> int | None:
    f = as_float(value)
    return None if f is None else int(round(f))


def rate(num: float, den: float) -> float | None:
    return None if not den else round(num / den, 4)


def pct(v: float | None) -> str:
    return "NA" if v is None else f"{v * 100:.1f}%"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
        seen: set[str] = set()
        for r in rows:
            for k in r:
                if k not in seen:
                    keys.append(k)
                    seen.add(k)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Module loading ─────────────────────────────────────────────────────────────

def load_module(script_path: Path, name: str):
    if not script_path.exists():
        raise FileNotFoundError(
            f"Required script not found: {script_path}\n"
            "Run this from the repo root alongside the reference scripts."
        )
    spec = importlib.util.spec_from_file_location(name, script_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── Season row building ────────────────────────────────────────────────────────

def build_season_rows(
    conn: sqlite3.Connection,
    season: str,
    args: argparse.Namespace,
    ff,
    beans,
) -> tuple[list[dict], dict]:
    """Build merged FF + BO/BD rows for one season. No lookahead."""
    ff_rows, meta = ff.build_rows_for_season(conn, str(season), args.rolling_games, args.rolling_starts)
    beans_rows, _bm = beans.build_rows(conn, str(season))

    beans_index: dict[tuple[str, str], dict] = {
        (str(r["game_pk"]), str(r["team"])): r for r in beans_rows
    }

    merged: list[dict] = []
    skipped = 0
    for row in ff_rows:
        key = (str(row["game_pk"]), str(row["team"]))
        br = beans_index.get(key)
        if br is None:
            skipped += 1
            for field, default in BEANS_FILL_DEFAULTS.items():
                row.setdefault(field, default)
        else:
            for field in BEANS_FILL_DEFAULTS:
                row[field] = br.get(field, BEANS_FILL_DEFAULTS[field])
        merged.append(row)

    if skipped:
        print(f"  {season}: {skipped:,} FF rows had no beans match (filled with defaults)")

    meta["beans_rows"] = len(beans_rows)
    meta["merged_rows"] = len(merged)
    meta["beans_skipped"] = skipped
    return merged, meta


# ── Feature families (extended) ────────────────────────────────────────────────

def get_all_feature_families(ff) -> dict[str, list[str]]:
    combined = dict(ff.FEATURE_FAMILIES)
    combined.update(BEANS_FEATURE_FAMILIES)
    return combined


def get_all_two_feature_combos(ff) -> list[tuple[str, str]]:
    return list(ff.TWO_FEATURE_COMBOS) + BEANS_TWO_FEATURE_COMBOS


# ── Rule training ──────────────────────────────────────────────────────────────

def _summarize_single(rows: list[dict], feature: str, family: str, min_count: int) -> list[dict]:
    base: dict[tuple, list[int]] = defaultdict(list)
    group: dict[tuple, list[int]] = defaultdict(list)
    for r in rows:
        season = str(r["season"])
        value = str(r[feature] if r.get(feature) not in {None, ""} else "missing")
        for outcome in TARGET_OUTCOMES:
            val = as_int(r.get(outcome))
            if val is None:
                continue
            base[(season, outcome)].append(val)
            group[(season, family, feature, value, outcome)].append(val)
    out = []
    for (season, fam, feat, value, outcome), vals in group.items():
        if len(vals) < min_count:
            continue
        b = base[(season, outcome)]
        br = rate(sum(b), len(b))
        fr = rate(sum(vals), len(vals))
        out.append({
            "season": season, "rule_type": "single",
            "family": fam, "feature": feat, "combo": "",
            "feature_value": value, "outcome": outcome,
            "count": len(vals), "feature_rate": fr, "baseline_rate": br,
            "lift": round(fr - br, 4) if fr is not None and br is not None else None,
        })
    return out


def _summarize_combo(rows: list[dict], feat_a: str, feat_b: str, min_count: int) -> list[dict]:
    base: dict[tuple, list[int]] = defaultdict(list)
    group: dict[tuple, list[int]] = defaultdict(list)
    combo_name = f"{feat_a}+{feat_b}"
    for r in rows:
        season = str(r["season"])
        va = str(r.get(feat_a) if r.get(feat_a) not in {None, ""} else "missing")
        vb = str(r.get(feat_b) if r.get(feat_b) not in {None, ""} else "missing")
        value = f"{va}__{vb}"
        for outcome in TARGET_OUTCOMES:
            val = as_int(r.get(outcome))
            if val is None:
                continue
            base[(season, outcome)].append(val)
            group[(season, combo_name, value, outcome)].append(val)
    out = []
    for (season, combo, value, outcome), vals in group.items():
        if len(vals) < min_count:
            continue
        b = base[(season, outcome)]
        br = rate(sum(b), len(b))
        fr = rate(sum(vals), len(vals))
        out.append({
            "season": season, "rule_type": "combo",
            "family": "combo", "feature": "", "combo": combo,
            "feature_value": value, "outcome": outcome,
            "count": len(vals), "feature_rate": fr, "baseline_rate": br,
            "lift": round(fr - br, 4) if fr is not None and br is not None else None,
        })
    return out


def build_rules(
    train_rows: list[dict],
    feature_families: dict[str, list[str]],
    two_feature_combos: list[tuple[str, str]],
    min_count: int,
    min_abs_lift: float,
    require_same_sign: bool,
) -> list[dict]:
    season_lift: list[dict] = []
    for family, features in feature_families.items():
        for feat in features:
            season_lift.extend(_summarize_single(train_rows, feat, family, min_count))
    for a, b in two_feature_combos:
        season_lift.extend(_summarize_combo(train_rows, a, b, min_count))

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for r in season_lift:
        if r["rule_type"] == "single":
            key = ("single", r["family"], r["feature"], "", r["feature_value"], r["outcome"])
        else:
            key = ("combo", "combo", "", r["combo"], r["feature_value"], r["outcome"])
        grouped[key].append(r)

    rules: list[dict] = []
    for key, rs in grouped.items():
        lifts = [as_float(r["lift"]) for r in rs if as_float(r["lift"]) is not None]
        if not lifts:
            continue
        seasons = sorted(set(str(r["season"]) for r in rs))
        counts = [as_int(r["count"]) or 0 for r in rs]
        if len(seasons) < 2:
            continue
        avg_lift = sum(lifts) / len(lifts)
        if abs(avg_lift) < min_abs_lift:
            continue
        if require_same_sign:
            if avg_lift > 0 and min(lifts) < 0:
                continue
            if avg_lift < 0 and max(lifts) > 0:
                continue
        rule_type, family, feature, combo, feature_value, outcome = key
        rules.append({
            "rule_type": rule_type, "family": family,
            "feature": feature, "combo": combo,
            "feature_value": feature_value, "outcome": outcome,
            "train_seasons": ",".join(seasons), "season_count": len(seasons),
            "total_count": sum(counts), "min_season_count": min(counts),
            "avg_lift": round(avg_lift, 4),
            "min_lift": round(min(lifts), 4),
            "max_lift": round(max(lifts), 4),
            "avg_feature_rate": round(
                sum(as_float(r["feature_rate"]) or 0 for r in rs) / len(rs), 4
            ),
            "avg_baseline_rate": round(
                sum(as_float(r["baseline_rate"]) or 0 for r in rs) / len(rs), 4
            ),
        })
    rules.sort(key=lambda r: (-abs(as_float(r["avg_lift"]) or 0), -r["total_count"], r["outcome"]))
    return rules


# ── Scoring ────────────────────────────────────────────────────────────────────

_BEANS_FEATURE_SET: set[str] = {f for feats in BEANS_FEATURE_FAMILIES.values() for f in feats}
_BEANS_COMBO_SET: set[str] = {f"{a}+{b}" for a, b in BEANS_TWO_FEATURE_COMBOS}


def _is_beans_rule(rule: dict) -> bool:
    """True if this rule originates from a BO/BD feature family."""
    if rule.get("family") in BEANS_FEATURE_FAMILIES:
        return True
    if rule.get("feature") in _BEANS_FEATURE_SET:
        return True
    combo = rule.get("combo", "")
    return bool(combo and (combo in _BEANS_COMBO_SET or any(f in combo for f in _BEANS_FEATURE_SET)))


def _rule_matches(row: dict, rule: dict) -> bool:
    if rule["rule_type"] == "single":
        val = row.get(rule["feature"])
        return str(val if val not in {None, ""} else "missing") == rule["feature_value"]
    combo = rule["combo"]
    try:
        a, b = combo.split("+", 1)
    except ValueError:
        return False
    va = str(row.get(a) if row.get(a) not in {None, ""} else "missing")
    vb = str(row.get(b) if row.get(b) not in {None, ""} else "missing")
    return f"{va}__{vb}" == rule["feature_value"]


def _rule_label(rule: dict) -> str:
    name = rule["feature"] if rule["rule_type"] == "single" else rule["combo"]
    return f"{name}={rule['feature_value']}({rule['avg_lift']:+.3f})"


def score_rows(
    test_rows: list[dict],
    rules: list[dict],
    max_rules_per_side: int,
) -> tuple[list[dict], list[dict]]:
    """Return (outcome_rows, card_rows).

    outcome_rows: one per (team-game, outcome) — used for threshold validation.
    card_rows:    one per team-game — the actual card with lane scores.
    """
    rules_by_outcome: dict[str, list[dict]] = defaultdict(list)
    for r in rules:
        rules_by_outcome[r["outcome"]].append(r)

    outcome_rows: list[dict] = []
    card_rows: list[dict] = []

    for row in test_rows:
        row_base = {
            "season": row["season"],
            "game_pk": row["game_pk"],
            "game_date": row["game_date"],
            "game_id": row["game_id"],
            "team": row["team"],
            "opponent": row["opponent"],
            "home_away": row["home_away"],
        }

        # Per-outcome scoring
        outcome_scores: dict[str, dict] = {}
        for outcome in TARGET_OUTCOMES:
            actual = as_int(row.get(outcome))
            if actual is None:
                continue
            pos_rules = []
            neg_rules = []
            for rule in rules_by_outcome.get(outcome, []):
                if not _rule_matches(row, rule):
                    continue
                lift = as_float(rule["avg_lift"]) or 0.0
                if lift > 0:
                    pos_rules.append(rule)
                elif lift < 0:
                    neg_rules.append(rule)

            pos_rules = sorted(pos_rules, key=lambda r: -(as_float(r["avg_lift"]) or 0))[:max_rules_per_side]
            neg_rules = sorted(neg_rules, key=lambda r: (as_float(r["avg_lift"]) or 0))[:max_rules_per_side]

            # Safety rail: BO/BD's strongest signal for team_runs_5plus is positive-direction,
            # which degrades the NO lane. Exclude positive BO/BD rules from this outcome
            # regardless of model so the runs5+ NO score stays clean.
            if outcome == "team_runs_5plus":
                pos_rules = [r for r in pos_rules if not _is_beans_rule(r)]

            pos_score = sum(as_float(r["avg_lift"]) or 0 for r in pos_rules)
            neg_score = sum(as_float(r["avg_lift"]) or 0 for r in neg_rules)
            net = pos_score + neg_score

            outcome_scores[outcome] = {
                "actual": actual,
                "net_score": round(net, 4),
                "positive_score": round(pos_score, 4),
                "negative_score": round(neg_score, 4),
                "top_pos": " | ".join(_rule_label(r) for r in pos_rules[:3]),
                "top_neg": " | ".join(_rule_label(r) for r in neg_rules[:3]),
            }

        def net(o: str) -> float:
            return as_float(outcome_scores.get(o, {}).get("net_score")) or 0.0

        # ── Lane scores ────────────────────────────────────────────────────────

        side_net = net("team_won")
        side_score = max(0.0, side_net)
        side_fade_score = max(0.0, -side_net)

        runs4_score = max(0.0, net("team_runs_4plus"))
        # 5+ NO direction only (YES direction is prohibited)
        runs5_no_score = max(0.0, -net("team_runs_5plus"))

        f5_score = max(0.0, net("team_f5_runs_2plus"))
        total_avoid_score = max(0.0, -net("game_total_9plus"))
        game_over_score   = max(0.0,  net("game_total_9plus"))

        # Live watch: average positive direction of both comeback outcomes
        lw1 = max(0.0, net("team_early_deficit_tied_or_led_later"))
        lw2 = max(0.0, net("opponent_blew_early_small_lead"))
        live_watch_score = (lw1 + lw2) / 2.0

        # Avoid: average of all active negative components
        avoid_parts = [
            max(0.0, -side_net),
            max(0.0, -net("team_runs_4plus")),
            max(0.0, -net("team_runs_5plus")),
            max(0.0, -net("team_f5_runs_2plus")),
        ]
        active_avoid = [x for x in avoid_parts if x > 0]
        avoid_score = sum(active_avoid) / len(active_avoid) if active_avoid else 0.0

        # Top reasons: collect from outcomes with meaningful signal
        pos_reasons: list[str] = []
        neg_reasons: list[str] = []
        for outcome, data in outcome_scores.items():
            ns = data["net_score"]
            if ns >= 0.04 and data["top_pos"]:
                for piece in data["top_pos"].split(" | ")[:2]:
                    pos_reasons.append(f"[{outcome}] {piece}")
            elif ns <= -0.04 and data["top_neg"]:
                for piece in data["top_neg"].split(" | ")[:2]:
                    neg_reasons.append(f"[{outcome}] {piece}")

        # Outcome-level rows for threshold validation
        for outcome, data in outcome_scores.items():
            outcome_rows.append({
                **row_base,
                "outcome": outcome,
                "actual_outcome": data["actual"],
                "positive_score": data["positive_score"],
                "negative_score": data["negative_score"],
                "net_score": data["net_score"],
                "top_positive_rules": data["top_pos"],
                "top_negative_rules": data["top_neg"],
            })

        # Card row (one per team-game)
        card_rows.append({
            **row_base,
            "model_version": "",  # stamped by caller (run_loo / run_chronological)
            "side_score": round(side_score, 4),
            "side_fade_score": round(side_fade_score, 4),
            "side_pick": (
                "lean" if side_score >= CARD_THRESHOLDS["side_score"]
                else ("fade" if side_fade_score >= CARD_THRESHOLDS["side_fade_score"]
                      else "neutral")
            ),
            "team_runs_4plus_score": round(runs4_score, 4),
            "team_runs_5plus_no_score": round(runs5_no_score, 4),
            "team_f5_runs_2plus_score": round(f5_score, 4),
            "full_total_avoid_score": round(total_avoid_score, 4),
            "full_game_over_score": round(game_over_score, 4),
            "live_watch_score": round(live_watch_score, 4),
            "avoid_score": round(avoid_score, 4),
            "top_positive_reasons": " | ".join(pos_reasons[:5]),
            "top_negative_reasons": " | ".join(neg_reasons[:5]),
            # BO/BD context fields — always populated from merged row for human inspection
            # regardless of which scoring model is active
            "bo_bucket": str(row.get("BO_bucket") or "missing"),
            "bd_bucket": str(row.get("BD_bucket") or "missing"),
            "bo_plus_weak_bd_tag": str(row.get("BO_plus_weak_BD_tag") or "missing"),
            "avoid_low_bo_strong_bd_tag": str(row.get("avoid_low_BO_strong_BD_tag") or "missing"),
            # raw actuals for outcome-level card validation
            "actual_team_won": outcome_scores.get("team_won", {}).get("actual"),
            "actual_team_runs_4plus": outcome_scores.get("team_runs_4plus", {}).get("actual"),
            "actual_team_runs_5plus": outcome_scores.get("team_runs_5plus", {}).get("actual"),
            "actual_team_f5_runs_2plus": outcome_scores.get("team_f5_runs_2plus", {}).get("actual"),
            "actual_game_total_9plus": outcome_scores.get("game_total_9plus", {}).get("actual"),
            "actual_lw_tied_or_led": outcome_scores.get("team_early_deficit_tied_or_led_later", {}).get("actual"),
        })

    return outcome_rows, card_rows


# ── Threshold validation (same logic as pregame_combined_identifier_score_preview) ────

def threshold_summary(scored_rows: list[dict], thresholds: list[float], mode_name: str) -> list[dict]:
    out: list[dict] = []
    base_by_outcome: dict[str, list[int]] = defaultdict(list)
    for r in scored_rows:
        base_by_outcome[r["outcome"]].append(as_int(r["actual_outcome"]) or 0)

    for outcome in TARGET_OUTCOMES:
        rows = [r for r in scored_rows if r["outcome"] == outcome]
        if not rows:
            continue
        actual_vals = [as_int(r["actual_outcome"]) or 0 for r in rows]
        yes_rate = sum(actual_vals) / len(actual_vals) if actual_vals else None
        baseline_success = max(yes_rate, 1 - yes_rate) if yes_rate is not None else None

        for threshold in thresholds:
            pos = [r for r in rows if (as_float(r["net_score"]) or 0) >= threshold]
            neg = [r for r in rows if (as_float(r["net_score"]) or 0) <= -threshold]

            for side, subset in [("positive_predict_yes", pos), ("negative_predict_no", neg)]:
                if not subset:
                    continue
                correct = sum(
                    1 if (as_int(r["actual_outcome"]) or 0) == (1 if side == "positive_predict_yes" else 0) else 0
                    for r in subset
                )
                success = correct / len(subset)
                out.append({
                    "mode": mode_name,
                    "outcome": outcome,
                    "score_side": side,
                    "threshold": threshold,
                    "count": len(subset),
                    "correct": correct,
                    "success_rate": round(success, 4),
                    "actual_yes_rate": round(yes_rate, 4) if yes_rate is not None else None,
                    "majority_baseline": round(baseline_success, 4) if baseline_success is not None else None,
                    "lift_vs_baseline": round(success - baseline_success, 4) if baseline_success is not None else None,
                    "avg_net_score": round(
                        sum(as_float(r["net_score"]) or 0 for r in subset) / len(subset), 4
                    ),
                    "prior_baseline": PRIOR_BASELINES.get((outcome, side, threshold)),
                    "beats_prior": (
                        (success > PRIOR_BASELINES[(outcome, side, threshold)])
                        if (outcome, side, threshold) in PRIOR_BASELINES else None
                    ),
                })

    out.sort(key=lambda r: (r["mode"], r["outcome"], r["score_side"], r["threshold"]))
    return out


def game_winner_summary(scored_rows: list[dict], thresholds: list[float], mode_name: str) -> list[dict]:
    rows = [r for r in scored_rows if r["outcome"] == "team_won"]
    by_game: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_game[(r["season"], r["game_pk"])].append(r)

    out: list[dict] = []
    for threshold in thresholds:
        picks: list[dict] = []
        for key, sides in by_game.items():
            if len(sides) < 2:
                continue
            sides = sorted(sides, key=lambda r: as_float(r["net_score"]) or 0, reverse=True)
            top, other = sides[0], sides[1]
            top_score = as_float(top["net_score"]) or 0
            other_score = as_float(other["net_score"]) or 0
            if top_score < threshold:
                continue
            if top_score - other_score < threshold / 2:
                continue
            picks.append(top)
        if not picks:
            continue
        correct = sum(as_int(p["actual_outcome"]) or 0 for p in picks)
        prior = PRIOR_BASELINES.get(("team_won", "positive_predict_yes", threshold))
        success = rate(correct, len(picks))
        out.append({
            "mode": mode_name,
            "threshold": threshold,
            "picks": len(picks),
            "correct": correct,
            "success_rate": success,
            "home_pick_rate": rate(sum(1 for p in picks if p["home_away"] == "home"), len(picks)),
            "avg_net_score": round(
                sum(as_float(p["net_score"]) or 0 for p in picks) / len(picks), 4
            ),
            "prior_baseline": prior,
            "beats_prior": ((success or 0) > prior if prior else None),
        })
    return out


# ── Card-level outputs ─────────────────────────────────────────────────────────

def build_game_cards(card_rows: list[dict]) -> list[dict]:
    """One row per game comparing home vs away lanes."""
    by_game: dict[tuple, dict] = {}
    for r in card_rows:
        key = (r["season"], r["game_pk"])
        side = r["home_away"]
        if key not in by_game:
            by_game[key] = {"home": None, "away": None, "mode": r.get("validation_mode", "")}
        by_game[key][side] = r

    game_cards: list[dict] = []
    for (season, game_pk), sides in by_game.items():
        home = sides.get("home")
        away = sides.get("away")
        if not home or not away:
            continue

        home_side = as_float(home["side_score"]) or 0.0
        away_side = as_float(away["side_score"]) or 0.0
        home_fade = as_float(home["side_fade_score"]) or 0.0
        away_fade = as_float(away["side_fade_score"]) or 0.0

        if home_side >= CARD_THRESHOLDS["side_score"] and home_side > away_side:
            game_pick = home["team"]
            game_confidence = home_side - away_side
        elif away_side >= CARD_THRESHOLDS["side_score"] and away_side > home_side:
            game_pick = away["team"]
            game_confidence = away_side - home_side
        else:
            game_pick = "no_pick"
            game_confidence = abs(home_side - away_side)

        # Total-related scores are the same for both sides (same game), take max for avoid/over
        total_avoid = max(
            as_float(home["full_total_avoid_score"]) or 0.0,
            as_float(away["full_total_avoid_score"]) or 0.0,
        )
        game_over = max(
            as_float(home["full_game_over_score"]) or 0.0,
            as_float(away["full_game_over_score"]) or 0.0,
        )
        live_watch = max(
            as_float(home["live_watch_score"]) or 0.0,
            as_float(away["live_watch_score"]) or 0.0,
        )

        game_cards.append({
            "validation_mode": sides["mode"],
            "season": season,
            "game_pk": game_pk,
            "game_date": home["game_date"],
            "game_id": home["game_id"],
            "home_team": home["team"],
            "away_team": away["team"],
            "game_pick": game_pick,
            "game_side_confidence": round(game_confidence, 4),
            "home_side_score": round(home_side, 4),
            "away_side_score": round(away_side, 4),
            "home_side_fade": round(home_fade, 4),
            "away_side_fade": round(away_fade, 4),
            "home_runs4_score": round(as_float(home["team_runs_4plus_score"]) or 0, 4),
            "away_runs4_score": round(as_float(away["team_runs_4plus_score"]) or 0, 4),
            "home_runs5no_score": round(as_float(home["team_runs_5plus_no_score"]) or 0, 4),
            "away_runs5no_score": round(as_float(away["team_runs_5plus_no_score"]) or 0, 4),
            "home_f5_score": round(as_float(home["team_f5_runs_2plus_score"]) or 0, 4),
            "away_f5_score": round(as_float(away["team_f5_runs_2plus_score"]) or 0, 4),
            "full_total_avoid_score": round(total_avoid, 4),
            "full_game_over_score": round(game_over, 4),
            "live_watch_score": round(live_watch, 4),
            "home_avoid_score": round(as_float(home["avoid_score"]) or 0, 4),
            "away_avoid_score": round(as_float(away["avoid_score"]) or 0, 4),
            "home_top_positive": home.get("top_positive_reasons", ""),
            "home_top_negative": home.get("top_negative_reasons", ""),
            "away_top_positive": away.get("top_positive_reasons", ""),
            "away_top_negative": away.get("top_negative_reasons", ""),
            # actuals
            "actual_home_won": home.get("actual_team_won"),
            "actual_away_won": away.get("actual_team_won"),
            "actual_game_total_9plus": home.get("actual_game_total_9plus"),
        })

    game_cards.sort(key=lambda r: (r["validation_mode"], r["game_date"], r["game_pk"]))
    return game_cards


# ── Validation run helpers ─────────────────────────────────────────────────────

def run_loo(
    all_rows: list[dict],
    seasons: list[str],
    feature_families: dict,
    two_feature_combos: list,
    args: argparse.Namespace,
    model_version: str = MODEL_FF_ONLY,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    all_outcome_rows: list[dict] = []
    all_card_rows: list[dict] = []
    all_thresh: list[dict] = []
    all_gw: list[dict] = []

    for test_season in seasons:
        train = [r for r in all_rows if str(r["season"]) != str(test_season)]
        test = [r for r in all_rows if str(r["season"]) == str(test_season)]
        rules = build_rules(
            train, feature_families, two_feature_combos,
            args.min_count, args.min_abs_lift, not args.allow_mixed_sign_rules,
        )
        mode = f"loo_test_{test_season}"
        o_rows, c_rows = score_rows(test, rules, args.max_rules_per_side)
        for r in o_rows:
            r["validation_mode"] = mode
            r["model_version"] = model_version
        for r in c_rows:
            r["validation_mode"] = mode
            r["model_version"] = model_version
        all_outcome_rows.extend(o_rows)
        all_card_rows.extend(c_rows)
        thresh = threshold_summary(o_rows, args.thresholds, mode)
        gw = game_winner_summary(o_rows, args.thresholds, mode)
        for r in thresh:
            r["model_version"] = model_version
        for r in gw:
            r["model_version"] = model_version
        all_thresh.extend(thresh)
        all_gw.extend(gw)

    return all_outcome_rows, all_card_rows, all_thresh, all_gw


def run_chronological(
    all_rows: list[dict],
    feature_families: dict,
    two_feature_combos: list,
    args: argparse.Namespace,
    model_version: str = MODEL_FF_ONLY,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    train = [r for r in all_rows if str(r["season"]) in {"2023", "2024"}]
    test = [r for r in all_rows if str(r["season"]) == "2025"]
    if not train or not test:
        return [], [], [], []

    rules = build_rules(
        train, feature_families, two_feature_combos,
        args.min_count, args.min_abs_lift, not args.allow_mixed_sign_rules,
    )
    mode = "chronological_2023+2024_to_2025"
    o_rows, c_rows = score_rows(test, rules, args.max_rules_per_side)
    for r in o_rows:
        r["validation_mode"] = mode
        r["model_version"] = model_version
    for r in c_rows:
        r["validation_mode"] = mode
        r["model_version"] = model_version

    thresh = threshold_summary(o_rows, args.thresholds, mode)
    gw = game_winner_summary(o_rows, args.thresholds, mode)
    for r in thresh:
        r["model_version"] = model_version
    for r in gw:
        r["model_version"] = model_version
    return o_rows, c_rows, thresh, gw


# ── Baseline comparison table ──────────────────────────────────────────────────

def build_baseline_comparison(
    threshold_rows: list[dict],
    winner_rows: list[dict],
    model_version: str = MODEL_FF_ONLY,
) -> list[dict]:
    chrono_mode = "chronological_2023+2024_to_2025"
    out: list[dict] = []

    for (outcome, side, threshold), prior in PRIOR_BASELINES.items():
        match = next(
            (r for r in threshold_rows
             if r.get("mode") == chrono_mode
             and r.get("outcome") == outcome
             and r.get("score_side") == side
             and abs((as_float(r.get("threshold")) or 0) - threshold) < 0.001),
            None,
        )
        new_rate = as_float(match.get("success_rate")) if match else None
        new_count = as_int(match.get("count")) if match else None
        out.append({
            "model_version": model_version,
            "outcome": outcome,
            "score_side": side,
            "threshold": threshold,
            "prior_baseline": prior,
            "new_success_rate": new_rate,
            "new_count": new_count,
            "improvement": round((new_rate or 0) - prior, 4) if new_rate is not None else None,
            "beats_prior": new_rate > prior if new_rate is not None else None,
            "status": (
                "IMPROVED" if new_rate is not None and new_rate > prior
                else "BELOW" if new_rate is not None
                else "NO_DATA"
            ),
        })

    # Add game winner picks at 0.20
    gw_match = next(
        (r for r in winner_rows
         if r.get("mode") == chrono_mode and abs((as_float(r.get("threshold")) or 0) - 0.20) < 0.001),
        None,
    )
    if gw_match:
        new_rate = as_float(gw_match.get("success_rate"))
        prior = 0.579
        out.append({
            "model_version": model_version,
            "outcome": "game_winner_pick",
            "score_side": "positive_predict_yes",
            "threshold": 0.20,
            "prior_baseline": prior,
            "new_success_rate": new_rate,
            "new_count": as_int(gw_match.get("picks")),
            "improvement": round((new_rate or 0) - prior, 4) if new_rate is not None else None,
            "beats_prior": new_rate > prior if new_rate is not None else None,
            "status": (
                "IMPROVED" if new_rate is not None and new_rate > prior
                else "BELOW" if new_rate is not None
                else "NO_DATA"
            ),
        })

    return out


# ── Filtered output CSVs ───────────────────────────────────────────────────────

def write_filter_csv(path: Path, card_rows: list[dict], score_col: str, threshold: float) -> list[dict]:
    filtered = [
        r for r in card_rows
        if (as_float(r.get(score_col)) or 0.0) >= threshold
    ]
    filtered.sort(key=lambda r: -(as_float(r.get(score_col)) or 0.0))
    write_csv(path, filtered)
    return filtered


# ── Markdown summary ───────────────────────────────────────────────────────────

def build_summary_md(
    health_rows: list[dict],
    args: argparse.Namespace,
    threshold_rows: list[dict],
    winner_rows: list[dict],
    comparison: list[dict],
    card_row_counts: dict[str, int],
    model_version: str = MODEL_FF_ONLY,
) -> str:
    md: list[str] = []
    md.append("# Pregame Identifier Card Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append(f"Model: {model_version}")
    md.append("")
    md.append("## No-Lookahead / Contamination Guardrail")
    md.append("")
    md.append("- Feature rows use the same no-lookahead rolling framework as `pregame_feature_family_lift_preview.py`.")
    md.append("- BO/BD features from `beans_offense_defense_lift_preview.py` are merged by (game_pk, team); no current-game data.")
    md.append("- Composite rules for each holdout season are trained only on other seasons.")
    md.append("- Chronological validation trains on 2023-2024, tests on 2025 only.")
    md.append("- No Kalshi/Vegas prices. This is baseball-truth classification, not EV.")
    md.append("- Prohibited from positive-predict-yes: `team_runs_5plus`, `team_early_deficit_scored_next2`.")
    md.append("")
    md.append("## Input Health")
    md.append("")
    for h in health_rows:
        md.append(
            f"- {h['season']}: games {h['final_games_loaded']:,}, "
            f"FF rows {h['team_game_rows']:,}, merged {h['merged_rows']:,}, "
            f"beans skipped {h['beans_skipped']}, starters {h['starter_lines']:,}, "
            f"xFIP constant {h['xfip_constant']}"
        )
    md.append("")
    md.append("## Rule Settings")
    md.append("")
    md.append(f"- Min count per season bucket: {args.min_count}")
    md.append(f"- Min abs lift to include rule: {args.min_abs_lift}")
    md.append(f"- Require same sign across training seasons: {not args.allow_mixed_sign_rules}")
    md.append(f"- Max matched positive/negative rules per row: {args.max_rules_per_side}")
    md.append("")
    md.append("## Baseline Comparison (chronological 2023+2024 → 2025)")
    md.append("")
    md.append("Prior baselines are from `pregame_combined_identifier_score_preview` (FF features only, no BO/BD).")
    md.append("")
    for r in comparison:
        status = r["status"]
        sign = "+" if (r.get("improvement") or 0) >= 0 else ""
        new_pct = pct(as_float(r.get("new_success_rate")))
        improvement = f"{sign}{pct(as_float(r.get('improvement')))}" if r.get("improvement") is not None else "NA"
        md.append(
            f"- [{status}] {r['outcome']} / {r['score_side']} @{r['threshold']}: "
            f"prior {pct(r['prior_baseline'])}, new {new_pct}, "
            f"delta {improvement}, count {r.get('new_count') or 'NA'}"
        )
    md.append("")
    md.append("## Game Winner Picks (chronological 2025)")
    md.append("")
    chrono_gw = [r for r in winner_rows if r.get("mode") == "chronological_2023+2024_to_2025"]
    chrono_gw = sorted(chrono_gw, key=lambda r: -(as_float(r.get("success_rate")) or 0))
    for r in chrono_gw[:10]:
        prior = PRIOR_BASELINES.get(("team_won", "positive_predict_yes", as_float(r.get("threshold")) or 0))
        prior_str = pct(prior) if prior else "NA"
        beats = "IMPROVED" if (r.get("beats_prior")) else ("BELOW" if prior else "")
        md.append(
            f"- [{beats}] @{r['threshold']}: picks {r['picks']:,}, "
            f"success {pct(as_float(r.get('success_rate')))}, prior {prior_str}, "
            f"home pick rate {pct(as_float(r.get('home_pick_rate')))}"
        )
    md.append("")
    md.append("## Card Filter Output Counts (chronological 2025)")
    md.append("")
    for lane, count in card_row_counts.items():
        threshold = CARD_THRESHOLDS.get(lane, "?")
        md.append(f"- {lane} @{threshold}: {count:,} team-game rows")
    md.append("")
    md.append("## BO/BD Integration Diagnosis (finalized 2026-06-20)")
    md.append("")
    md.append("- **BO/BD is not junk**: 117 of 500 rules cleared min_abs_lift=0.04 and same-sign filter across training seasons.")
    md.append("- **BO/BD did not improve top-line validation**: team_won -0.1%, team_runs_5plus NO -1.7%, team_f5_runs_2plus -0.7%.")
    md.append("- **Root cause — redundancy**: BO/BD combo features (BO_bucket+opponent_strength) capture the same signal as FF features (l10_rpg+opponent_strength). Where they agree, they add no new picks; where they disagree, they add noise.")
    md.append("- **Root cause — lane conflict**: BO/BD's strongest surviving team_runs_5plus signal is positive-direction (14 pos rules vs 10 neg, top lift +0.148). This competes against the NO lane and explains the -1.7% drop.")
    md.append("- **Decision**: FF-only is the default model (`--model ff_only`). `--model ff_plus_beans` is available for comparison only.")
    md.append("- **Safety rail**: Positive BO/BD rules are always excluded from `team_runs_5plus_no_score` computation regardless of model.")
    md.append("- **BO/BD context**: Raw BO_bucket, BD_bucket, and tag values are included in every card row for human inspection. BO/BD reason strings appear in card outputs when running `ff_plus_beans` mode.")
    md.append("")
    md.append("## Architecture Notes")
    md.append("")
    md.append("- This script is a classifier with multiple lanes, not a single predictor.")
    md.append(f"- Active scoring model: `{model_version}`. BO/BD fields are always in card output for human context.")
    md.append("- Market EV comparison (Kalshi bid/ask/depth/line movement) is the next step, not included here.")
    md.append("- F5 total 4+ positive and team_runs_5+ YES are labeled research-only and excluded from lane outputs.")
    md.append("")
    md.append("## Files Written")
    md.append("")
    for name in [
        "pregame_identifier_card_summary.md",
        "input_health.csv",
        "pregame_identifier_cards.csv",
        "pregame_side_leans.csv",
        "pregame_side_fades.csv",
        "team_scoring_watchlist.csv",
        "team_5plus_avoid_list.csv",
        "team_f5_scoring_watchlist.csv",
        "live_watchlist.csv",
        "full_avoid_list.csv",
        "validation_summary.csv",
        "game_winner_pick_summary.csv",
        "baseline_comparison.csv",
        "pregame_game_cards.csv",
    ]:
        md.append(f"- {name}")

    return "\n".join(md)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pregame identifier card preview: combined brain -> scored slate cards."
    )
    parser.add_argument("--seasons", nargs="+", default=["2023", "2024", "2025"])
    parser.add_argument("--db", default="kalshi_mlb.db")
    parser.add_argument("--rolling-games", type=int, default=10)
    parser.add_argument("--rolling-starts", type=int, default=8)
    parser.add_argument("--min-count", type=int, default=100)
    parser.add_argument("--min-abs-lift", type=float, default=0.04)
    parser.add_argument("--thresholds", nargs="*", type=float,
                        default=[0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20])
    parser.add_argument("--max-rules-per-side", type=int, default=12)
    parser.add_argument("--allow-mixed-sign-rules", action="store_true")
    parser.add_argument("--write-outcome-rows", action="store_true",
                        help="Also write all_outcome_scored_rows.csv (large).")
    parser.add_argument(
        "--model",
        choices=[MODEL_FF_ONLY, MODEL_FF_PLUS_BEANS],
        default=MODEL_FF_ONLY,
        help=(
            f"Scoring model. '{MODEL_FF_ONLY}' (default): FF features only, matches prior baselines. "
            f"'{MODEL_FF_PLUS_BEANS}': FF + BO/BD combined, comparison-only until it beats baselines."
        ),
    )
    args = parser.parse_args()
    model_version = args.model

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading reference modules...")
    ff = load_module(FF_SCRIPT, "ff")
    beans = load_module(BEANS_SCRIPT, "beans")

    conn = sqlite3.connect(args.db)
    all_rows: list[dict] = []
    health_rows: list[dict] = []

    print("Building season rows (FF + BO/BD merge)...")
    for season in args.seasons:
        rows, meta = build_season_rows(conn, season, args, ff, beans)
        all_rows.extend(rows)
        health_rows.append(meta)
        print(
            f"  {season}: games={meta['final_games_loaded']:,}, "
            f"merged_rows={meta['merged_rows']:,}, "
            f"starters={meta['starter_lines']:,}, "
            f"beans_skipped={meta['beans_skipped']}"
        )

    write_csv(OUT_DIR / "input_health.csv", health_rows)

    if model_version == MODEL_FF_PLUS_BEANS:
        feature_families = get_all_feature_families(ff)
        two_feature_combos = get_all_two_feature_combos(ff)
        print(f"Model: {MODEL_FF_PLUS_BEANS} (research comparison — not the default)")
    else:
        feature_families = dict(ff.FEATURE_FAMILIES)
        two_feature_combos = list(ff.TWO_FEATURE_COMBOS)
        print(f"Model: {MODEL_FF_ONLY} (default)")

    print(f"Feature families: {list(feature_families.keys())}")
    print(f"Two-feature combos: {len(two_feature_combos)}")

    print("Running leave-one-season-out validation...")
    loo_o, loo_c, loo_thresh, loo_gw = run_loo(
        all_rows, args.seasons, feature_families, two_feature_combos, args,
        model_version=model_version,
    )

    chrono_o: list[dict] = []
    chrono_c: list[dict] = []
    chrono_thresh: list[dict] = []
    chrono_gw: list[dict] = []

    if set(map(str, args.seasons)) >= {"2023", "2024", "2025"}:
        print("Running chronological validation (train 2023-2024, test 2025)...")
        chrono_o, chrono_c, chrono_thresh, chrono_gw = run_chronological(
            all_rows, feature_families, two_feature_combos, args,
            model_version=model_version,
        )

    all_outcome_rows = loo_o + chrono_o
    all_card_rows = loo_c + chrono_c
    all_thresh = loo_thresh + chrono_thresh
    all_gw = loo_gw + chrono_gw

    print(f"Outcome rows: {len(all_outcome_rows):,} | Card rows: {len(all_card_rows):,}")

    # Game-level cards
    game_cards = build_game_cards(all_card_rows)

    # Baseline comparison
    comparison = build_baseline_comparison(all_thresh, all_gw, model_version=model_version)

    # Write validation outputs
    write_csv(OUT_DIR / "validation_summary.csv", all_thresh)
    write_csv(OUT_DIR / "game_winner_pick_summary.csv", all_gw)
    write_csv(OUT_DIR / "baseline_comparison.csv", comparison)
    write_csv(OUT_DIR / "pregame_game_cards.csv", game_cards)

    if args.write_outcome_rows:
        write_csv(OUT_DIR / "all_outcome_scored_rows.csv", all_outcome_rows)

    # Filter outputs (all card rows combined, including LOO + chrono)
    write_csv(OUT_DIR / "pregame_identifier_cards.csv", all_card_rows)

    # Focused filter lists — use chronological 2025 card rows as the primary reference
    chrono_cards = [r for r in all_card_rows if r.get("validation_mode") == "chronological_2023+2024_to_2025"]
    filter_target = chrono_cards if chrono_cards else all_card_rows
    print(f"Filter outputs targeting: {len(filter_target):,} chrono-2025 card rows")

    filter_counts: dict[str, int] = {}

    for fname, col, label in [
        ("pregame_side_leans.csv",       "side_score",              "side_score"),
        ("pregame_side_fades.csv",       "side_fade_score",         "side_fade_score"),
        ("team_scoring_watchlist.csv",   "team_runs_4plus_score",   "team_runs_4plus_score"),
        ("team_5plus_avoid_list.csv",    "team_runs_5plus_no_score","team_runs_5plus_no_score"),
        ("team_f5_scoring_watchlist.csv","team_f5_runs_2plus_score","team_f5_runs_2plus_score"),
        ("live_watchlist.csv",           "live_watch_score",        "live_watch_score"),
        ("full_avoid_list.csv",          "avoid_score",             "avoid_score"),
    ]:
        rows = write_filter_csv(OUT_DIR / fname, filter_target, col, CARD_THRESHOLDS[label])
        filter_counts[label] = len(rows)
        print(f"  {fname}: {len(rows):,} rows @ threshold {CARD_THRESHOLDS[label]}")

    # Summary markdown
    md_text = build_summary_md(health_rows, args, all_thresh, all_gw, comparison, filter_counts, model_version=model_version)
    (OUT_DIR / "pregame_identifier_card_summary.md").write_text(md_text, encoding="utf-8")

    # Print baseline comparison to console
    print("\n--- Baseline Comparison (chronological 2023+2024 to 2025) ---")
    for r in comparison:
        new_pct = pct(as_float(r.get("new_success_rate")))
        improvement = r.get("improvement")
        sign = "+" if (improvement or 0) >= 0 else ""
        print(
            f"  [{r['status']}] {r['outcome']} / {r['score_side']} @{r['threshold']}: "
            f"prior={pct(r['prior_baseline'])}, new={new_pct}, "
            f"delta={sign}{pct(improvement)}, n={r.get('new_count','NA')}"
        )

    print(f"\nWROTE: {OUT_DIR}")
    print(f"Summary: {OUT_DIR / 'pregame_identifier_card_summary.md'}")


if __name__ == "__main__":
    main()
