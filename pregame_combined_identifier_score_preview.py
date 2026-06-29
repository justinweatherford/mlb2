import argparse
import csv
import importlib.util
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path("outputs") / "pregame_combined_identifier_score_preview"
FEATURE_SCRIPT = Path("pregame_feature_family_lift_preview.py")

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

SINGLE_RULE_KEY_COLS = ["family", "feature", "feature_value", "outcome"]
COMBO_RULE_KEY_COLS = ["combo", "feature_value", "outcome"]


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
    if f is None:
        return None
    return int(round(f))


def rate(num: float, den: float) -> float | None:
    if not den:
        return None
    return round(num / den, 4)


def pct(v: float | None) -> str:
    if v is None:
        return "NA"
    return f"{v * 100:.1f}%"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys = []
        seen = set()
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


def read_csv_rows(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_feature_module():
    if not FEATURE_SCRIPT.exists():
        raise FileNotFoundError(
            f"Missing {FEATURE_SCRIPT}. Put this script in the repo root beside pregame_feature_family_lift_preview.py."
        )
    spec = importlib.util.spec_from_file_location("ff", FEATURE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def summarize_feature_train(rows: list[dict], feature: str, family: str, min_count: int) -> list[dict]:
    base_by_season = defaultdict(list)
    group = defaultdict(list)

    for r in rows:
        season = str(r["season"])
        value = str(r.get(feature) if r.get(feature) not in {None, ""} else "missing")
        for outcome in TARGET_OUTCOMES:
            val = as_int(r.get(outcome))
            if val is None:
                continue
            base_by_season[(season, outcome)].append(val)
            group[(season, family, feature, value, outcome)].append(val)

    out = []
    for (season, fam, feat, value, outcome), vals in group.items():
        if len(vals) < min_count:
            continue
        base_vals = base_by_season[(season, outcome)]
        br = rate(sum(base_vals), len(base_vals))
        fr = rate(sum(vals), len(vals))
        out.append({
            "season": season,
            "rule_type": "single",
            "family": fam,
            "feature": feat,
            "combo": "",
            "feature_value": value,
            "outcome": outcome,
            "count": len(vals),
            "feature_rate": fr,
            "baseline_rate": br,
            "lift": round(fr - br, 4) if fr is not None and br is not None else None,
        })
    return out


def summarize_combo_train(rows: list[dict], feature_a: str, feature_b: str, min_count: int) -> list[dict]:
    base_by_season = defaultdict(list)
    group = defaultdict(list)
    combo_name = f"{feature_a}+{feature_b}"

    for r in rows:
        season = str(r["season"])
        va = str(r.get(feature_a) if r.get(feature_a) not in {None, ""} else "missing")
        vb = str(r.get(feature_b) if r.get(feature_b) not in {None, ""} else "missing")
        value = f"{va}__{vb}"
        for outcome in TARGET_OUTCOMES:
            val = as_int(r.get(outcome))
            if val is None:
                continue
            base_by_season[(season, outcome)].append(val)
            group[(season, combo_name, value, outcome)].append(val)

    out = []
    for (season, combo, value, outcome), vals in group.items():
        if len(vals) < min_count:
            continue
        base_vals = base_by_season[(season, outcome)]
        br = rate(sum(base_vals), len(base_vals))
        fr = rate(sum(vals), len(vals))
        out.append({
            "season": season,
            "rule_type": "combo",
            "family": "combo",
            "feature": "",
            "combo": combo,
            "feature_value": value,
            "outcome": outcome,
            "count": len(vals),
            "feature_rate": fr,
            "baseline_rate": br,
            "lift": round(fr - br, 4) if fr is not None and br is not None else None,
        })
    return out


def build_rules_from_training_rows(train_rows: list[dict], ff, min_count: int, min_abs_lift: float, require_same_sign: bool) -> list[dict]:
    season_lift_rows = []

    for family, features in ff.FEATURE_FAMILIES.items():
        for feat in features:
            season_lift_rows.extend(summarize_feature_train(train_rows, feat, family, min_count))

    for a, b in ff.TWO_FEATURE_COMBOS:
        season_lift_rows.extend(summarize_combo_train(train_rows, a, b, min_count))

    grouped = defaultdict(list)
    for r in season_lift_rows:
        if r["rule_type"] == "single":
            key = ("single", r["family"], r["feature"], "", r["feature_value"], r["outcome"])
        else:
            key = ("combo", "combo", "", r["combo"], r["feature_value"], r["outcome"])
        grouped[key].append(r)

    rules = []
    for key, rs in grouped.items():
        lifts = [as_float(r.get("lift")) for r in rs if as_float(r.get("lift")) is not None]
        if not lifts:
            continue

        seasons = sorted(set(str(r["season"]) for r in rs))
        counts = [as_int(r.get("count")) or 0 for r in rs]
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
            "rule_type": rule_type,
            "family": family,
            "feature": feature,
            "combo": combo,
            "feature_value": feature_value,
            "outcome": outcome,
            "train_seasons": ",".join(seasons),
            "season_count": len(seasons),
            "total_count": sum(counts),
            "min_season_count": min(counts),
            "avg_lift": round(avg_lift, 4),
            "min_lift": round(min(lifts), 4),
            "max_lift": round(max(lifts), 4),
            "avg_feature_rate": round(sum(as_float(r.get("feature_rate")) or 0 for r in rs) / len(rs), 4),
            "avg_baseline_rate": round(sum(as_float(r.get("baseline_rate")) or 0 for r in rs) / len(rs), 4),
        })

    rules.sort(key=lambda r: (-abs(as_float(r["avg_lift"]) or 0), -r["total_count"], r["outcome"]))
    return rules


def rule_matches(row: dict, rule: dict) -> bool:
    if rule["rule_type"] == "single":
        feature = rule["feature"]
        return str(row.get(feature) if row.get(feature) not in {None, ""} else "missing") == rule["feature_value"]

    combo = rule["combo"]
    try:
        a, b = combo.split("+", 1)
    except ValueError:
        return False
    va = str(row.get(a) if row.get(a) not in {None, ""} else "missing")
    vb = str(row.get(b) if row.get(b) not in {None, ""} else "missing")
    return f"{va}__{vb}" == rule["feature_value"]


def score_rows(test_rows: list[dict], rules: list[dict], max_rules_per_side: int = 12) -> tuple[list[dict], list[dict]]:
    rules_by_outcome = defaultdict(list)
    for r in rules:
        rules_by_outcome[r["outcome"]].append(r)

    scored = []
    examples = []

    for row in test_rows:
        base = {
            "season": row["season"],
            "game_pk": row["game_pk"],
            "game_date": row["game_date"],
            "game_id": row["game_id"],
            "team": row["team"],
            "opponent": row["opponent"],
            "home_away": row["home_away"],
        }

        for outcome in TARGET_OUTCOMES:
            actual = as_int(row.get(outcome))
            if actual is None:
                continue

            pos = []
            neg = []
            for rule in rules_by_outcome.get(outcome, []):
                if not rule_matches(row, rule):
                    continue
                lift = as_float(rule["avg_lift"]) or 0
                if lift > 0:
                    pos.append(rule)
                elif lift < 0:
                    neg.append(rule)

            pos = sorted(pos, key=lambda r: -(as_float(r["avg_lift"]) or 0))[:max_rules_per_side]
            neg = sorted(neg, key=lambda r: (as_float(r["avg_lift"]) or 0))[:max_rules_per_side]

            positive_score = sum(as_float(r["avg_lift"]) or 0 for r in pos)
            negative_score = sum(as_float(r["avg_lift"]) or 0 for r in neg)
            net_score = positive_score + negative_score

            scored_row = {
                **base,
                "outcome": outcome,
                "actual_outcome": actual,
                "positive_rule_count": len(pos),
                "negative_rule_count": len(neg),
                "positive_score": round(positive_score, 4),
                "negative_score": round(negative_score, 4),
                "net_score": round(net_score, 4),
                "top_positive_rules": " | ".join(rule_label(r) for r in pos[:5]),
                "top_negative_rules": " | ".join(rule_label(r) for r in neg[:5]),
            }
            scored.append(scored_row)

            if abs(net_score) >= 0.08 and len(examples) < 500:
                examples.append(scored_row)

    return scored, examples


def rule_label(rule: dict) -> str:
    name = rule["feature"] if rule["rule_type"] == "single" else rule["combo"]
    return f"{name}={rule['feature_value']}({rule['avg_lift']:+.3f})"


def threshold_summary(scored_rows: list[dict], thresholds: list[float], mode_name: str) -> list[dict]:
    out = []
    base_by_season_outcome = defaultdict(list)
    for r in scored_rows:
        base_by_season_outcome[(r["season"], r["outcome"])].append(as_int(r["actual_outcome"]) or 0)

    for outcome in TARGET_OUTCOMES:
        rows = [r for r in scored_rows if r["outcome"] == outcome]
        for threshold in thresholds:
            # Positive score means signal predicts YES/outcome happens.
            pos = [r for r in rows if (as_float(r["net_score"]) or 0) >= threshold]
            neg = [r for r in rows if (as_float(r["net_score"]) or 0) <= -threshold]

            for side, subset in [("positive_predict_yes", pos), ("negative_predict_no", neg)]:
                if not subset:
                    continue
                correct = 0
                for r in subset:
                    actual = as_int(r["actual_outcome"]) or 0
                    if side == "positive_predict_yes":
                        correct += 1 if actual == 1 else 0
                    else:
                        correct += 1 if actual == 0 else 0

                # baseline is majority class rate for same outcome/test rows
                actual_vals = [as_int(r["actual_outcome"]) or 0 for r in rows]
                actual_yes_rate = sum(actual_vals) / len(actual_vals) if actual_vals else None
                baseline_success = max(actual_yes_rate, 1 - actual_yes_rate) if actual_yes_rate is not None else None
                success = correct / len(subset)
                out.append({
                    "mode": mode_name,
                    "outcome": outcome,
                    "score_side": side,
                    "threshold": threshold,
                    "count": len(subset),
                    "correct": correct,
                    "success_rate": round(success, 4),
                    "actual_yes_rate_all_rows": round(actual_yes_rate, 4) if actual_yes_rate is not None else None,
                    "majority_baseline_success": round(baseline_success, 4) if baseline_success is not None else None,
                    "lift_vs_majority_baseline": round(success - baseline_success, 4) if baseline_success is not None else None,
                    "avg_net_score": round(sum(as_float(r["net_score"]) or 0 for r in subset) / len(subset), 4),
                    "avg_positive_score": round(sum(as_float(r["positive_score"]) or 0 for r in subset) / len(subset), 4),
                    "avg_negative_score": round(sum(as_float(r["negative_score"]) or 0 for r in subset) / len(subset), 4),
                })

    out.sort(key=lambda r: (r["mode"], r["outcome"], r["score_side"], r["threshold"]))
    return out


def game_winner_summary(scored_rows: list[dict], thresholds: list[float], mode_name: str) -> list[dict]:
    # Convert team_won scored rows into game-level picks by selecting highest net_score side.
    rows = [r for r in scored_rows if r["outcome"] == "team_won"]
    by_game = defaultdict(list)
    for r in rows:
        by_game[(r["season"], r["game_pk"])].append(r)

    out = []
    for threshold in thresholds:
        picks = []
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
        out.append({
            "mode": mode_name,
            "threshold": threshold,
            "picks": len(picks),
            "correct": correct,
            "success_rate": rate(correct, len(picks)),
            "avg_net_score": round(sum(as_float(p["net_score"]) or 0 for p in picks) / len(picks), 4),
            "home_pick_rate": rate(sum(1 for p in picks if p["home_away"] == "home"), len(picks)),
        })
    return out


def run_leave_one_season_out(all_rows: list[dict], seasons: list[str], ff, args) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    all_rules = []
    all_scored = []
    all_examples = []
    all_thresholds = []
    all_game_winner = []

    for test_season in seasons:
        train_rows = [r for r in all_rows if str(r["season"]) != str(test_season)]
        test_rows = [r for r in all_rows if str(r["season"]) == str(test_season)]
        rules = build_rules_from_training_rows(
            train_rows,
            ff,
            min_count=args.min_count,
            min_abs_lift=args.min_abs_lift,
            require_same_sign=not args.allow_mixed_sign_rules,
        )
        for r in rules:
            r["validation_mode"] = "leave_one_season_out"
            r["test_season"] = test_season
        scored, examples = score_rows(test_rows, rules, max_rules_per_side=args.max_rules_per_side)
        for r in scored:
            r["validation_mode"] = "leave_one_season_out"
            r["test_season"] = test_season
        for r in examples:
            r["validation_mode"] = "leave_one_season_out"
            r["test_season"] = test_season

        all_rules.extend(rules)
        all_scored.extend(scored)
        all_examples.extend(examples)
        all_thresholds.extend(threshold_summary(scored, args.thresholds, f"loo_test_{test_season}"))
        all_game_winner.extend(game_winner_summary(scored, args.thresholds, f"loo_test_{test_season}"))

    return all_rules, all_scored, all_examples, all_thresholds + all_game_winner


def run_chronological(all_rows: list[dict], train_seasons: list[str], test_season: str, ff, args) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    train_rows = [r for r in all_rows if str(r["season"]) in set(map(str, train_seasons))]
    test_rows = [r for r in all_rows if str(r["season"]) == str(test_season)]
    if not train_rows or not test_rows:
        return [], [], [], []

    rules = build_rules_from_training_rows(
        train_rows,
        ff,
        min_count=args.min_count,
        min_abs_lift=args.min_abs_lift,
        require_same_sign=not args.allow_mixed_sign_rules,
    )
    for r in rules:
        r["validation_mode"] = "chronological"
        r["test_season"] = test_season
        r["train_seasons"] = ",".join(map(str, train_seasons))

    scored, examples = score_rows(test_rows, rules, max_rules_per_side=args.max_rules_per_side)
    for r in scored:
        r["validation_mode"] = "chronological"
        r["test_season"] = test_season
    for r in examples:
        r["validation_mode"] = "chronological"
        r["test_season"] = test_season

    thresholds = threshold_summary(scored, args.thresholds, f"chronological_{'+'.join(train_seasons)}_to_{test_season}")
    thresholds.extend(game_winner_summary(scored, args.thresholds, f"chronological_{'+'.join(train_seasons)}_to_{test_season}"))

    return rules, scored, examples, thresholds


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine stable identifiers into composite scores and validate out-of-season.")
    parser.add_argument("--seasons", nargs="+", default=["2023", "2024", "2025"])
    parser.add_argument("--db", default="kalshi_mlb.db")
    parser.add_argument("--rolling-games", type=int, default=10)
    parser.add_argument("--rolling-starts", type=int, default=8)
    parser.add_argument("--min-count", type=int, default=100)
    parser.add_argument("--min-abs-lift", type=float, default=0.04)
    parser.add_argument("--thresholds", nargs="*", type=float, default=[0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20])
    parser.add_argument("--max-rules-per-side", type=int, default=12)
    parser.add_argument("--allow-mixed-sign-rules", action="store_true")
    parser.add_argument("--write-scored-rows", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ff = load_feature_module()

    conn = ff.sqlite3.connect(args.db) if hasattr(ff, "sqlite3") else None
    if conn is None:
        import sqlite3
        conn = sqlite3.connect(args.db)

    all_rows = []
    health_rows = []
    for season in args.seasons:
        rows, meta = ff.build_rows_for_season(conn, str(season), args.rolling_games, args.rolling_starts)
        all_rows.extend(rows)
        health_rows.append(meta)
        print(f"{season}: team-game rows={len(rows):,}, games={meta['final_games_loaded']:,}, starter_lines={meta['starter_lines']:,}")

    write_csv(OUT_DIR / "input_health.csv", health_rows)

    loo_rules, loo_scored, loo_examples, loo_summaries = run_leave_one_season_out(all_rows, args.seasons, ff, args)

    chronological_rules, chronological_scored, chronological_examples, chronological_summaries = [], [], [], []
    if set(map(str, args.seasons)) >= {"2023", "2024", "2025"}:
        chronological_rules, chronological_scored, chronological_examples, chronological_summaries = run_chronological(
            all_rows, ["2023", "2024"], "2025", ff, args
        )

    write_csv(OUT_DIR / "identifier_rules_by_holdout_season.csv", loo_rules + chronological_rules)
    write_csv(OUT_DIR / "threshold_validation_summary.csv", [r for r in loo_summaries + chronological_summaries if "outcome" in r])
    write_csv(OUT_DIR / "game_winner_pick_summary.csv", [r for r in loo_summaries + chronological_summaries if "picks" in r])
    write_csv(OUT_DIR / "matched_identifier_examples.csv", loo_examples + chronological_examples)

    if args.write_scored_rows:
        write_csv(OUT_DIR / "all_scored_team_outcomes.csv", loo_scored + chronological_scored)

    # Compact best summary
    threshold_rows = [r for r in loo_summaries + chronological_summaries if "outcome" in r]
    best_by_outcome = []
    by_outcome = defaultdict(list)
    for r in threshold_rows:
        if (as_int(r.get("count")) or 0) < 200:
            continue
        by_outcome[(r["mode"], r["outcome"], r["score_side"])].append(r)
    for key, rs in by_outcome.items():
        rs = sorted(rs, key=lambda r: (as_float(r.get("lift_vs_majority_baseline")) or -999), reverse=True)
        best_by_outcome.append(rs[0])
    best_by_outcome.sort(key=lambda r: (r["mode"], -(as_float(r.get("lift_vs_majority_baseline")) or -999), r["outcome"]))
    write_csv(OUT_DIR / "best_thresholds_by_outcome.csv", best_by_outcome)

    md = []
    md.append("# Pregame Combined Identifier Score Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append("## No-Lookahead / Contamination Guardrail")
    md.append("")
    md.append("- Base team-game feature rows are generated with the same no-lookahead rules as `pregame_feature_family_lift_preview.py`.")
    md.append("- Composite scoring rules for each holdout season are trained only on the other seasons.")
    md.append("- The holdout season is scored using rules learned outside that season, avoiding same-season lift leakage.")
    md.append("- A stricter chronological validation is also run when 2023, 2024, and 2025 are present: train 2023-2024, test 2025.")
    md.append("- No Kalshi/Vegas prices are used. This is still baseball-truth research, not EV.")
    md.append("")
    md.append("## Input Health")
    md.append("")
    for h in health_rows:
        md.append(
            f"- {h['season']}: games {h['final_games_loaded']:,}, team-game rows {h['team_game_rows']:,}, "
            f"starter lines {h['starter_lines']:,}, xFIP constant {h['xfip_constant']}"
        )
    md.append("")
    md.append("## Rule Settings")
    md.append("")
    md.append(f"- Min count per season bucket: {args.min_count}")
    md.append(f"- Min abs lift to include rule: {args.min_abs_lift}")
    md.append(f"- Require same sign across training seasons: {not args.allow_mixed_sign_rules}")
    md.append(f"- Max matched positive/negative rules per row: {args.max_rules_per_side}")
    md.append("")
    md.append("## Best Thresholds By Outcome")
    md.append("")
    for r in best_by_outcome[:40]:
        md.append(
            f"- {r['mode']} / {r['outcome']} / {r['score_side']} @ {r['threshold']}: "
            f"count {r['count']:,}, success {pct(as_float(r['success_rate']))}, "
            f"baseline {pct(as_float(r['majority_baseline_success']))}, "
            f"lift {pct(as_float(r['lift_vs_majority_baseline']))}, avg score {r['avg_net_score']}"
        )
    md.append("")
    md.append("## Game Winner Pick Summary")
    md.append("")
    gw = [r for r in loo_summaries + chronological_summaries if "picks" in r]
    gw = sorted(gw, key=lambda r: (r["mode"], -(as_float(r.get("success_rate")) or 0), -(as_int(r.get("picks")) or 0)))
    for r in gw[:30]:
        md.append(
            f"- {r['mode']} @ {r['threshold']}: picks {r['picks']:,}, "
            f"success {pct(as_float(r['success_rate']))}, home pick rate {pct(as_float(r.get('home_pick_rate')))}, "
            f"avg score {r['avg_net_score']}"
        )
    md.append("")
    md.append("## How To Read")
    md.append("")
    md.append("- `positive_predict_yes` means the combined identifier score says the outcome is more likely than baseline.")
    md.append("- `negative_predict_no` means the combined identifier score says the outcome is less likely than baseline.")
    md.append("- Meaningful improvement requires enough count, positive lift versus majority baseline, and stability in leave-one-season-out or chronological validation.")
    md.append("- If a threshold only works on tiny count, treat it as research-only.")
    md.append("")
    md.append("## Files Written")
    md.append("")
    for name in [
        "combined_score_summary.md",
        "input_health.csv",
        "identifier_rules_by_holdout_season.csv",
        "threshold_validation_summary.csv",
        "game_winner_pick_summary.csv",
        "best_thresholds_by_outcome.csv",
        "matched_identifier_examples.csv",
    ]:
        md.append(f"- {name}")

    (OUT_DIR / "combined_score_summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {OUT_DIR}")
    print(f"Rows scored: {len(loo_scored) + len(chronological_scored):,}")
    print(f"Rules: {len(loo_rules) + len(chronological_rules):,}")
    print(f"Summary: {OUT_DIR / 'combined_score_summary.md'}")


if __name__ == "__main__":
    main()
