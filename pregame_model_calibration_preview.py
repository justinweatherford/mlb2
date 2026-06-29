import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_IN_DIR = Path("outputs") / "pregame_matchup_profile_preview"
DEFAULT_OUT_DIR = Path("outputs") / "pregame_model_calibration_preview"


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


def read_csv_rows(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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


def summarize(rows: list[dict], group_cols: list[str]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        key = tuple(str(r.get(c) if r.get(c) not in {None, ""} else "missing") for c in group_cols)
        groups[key].append(r)

    out = []
    for key, rs in groups.items():
        row = {c: v for c, v in zip(group_cols, key)}
        row["count"] = len(rs)
        row["correct"] = sum(as_int(r.get("correct")) or 0 for r in rs)
        row["success_rate"] = rate(row["correct"], row["count"])
        row["avg_model_edge"] = round(sum(abs(as_float(r.get("model_edge")) or 0) for r in rs) / len(rs), 3) if rs else None

        yes_rows = [r for r in rs if as_int(r.get("predicted_outcome")) == 1]
        no_rows = [r for r in rs if as_int(r.get("predicted_outcome")) == 0]
        row["predicted_yes_count"] = len(yes_rows)
        row["predicted_no_count"] = len(no_rows)
        row["predicted_yes_success_rate"] = rate(sum(as_int(r.get("correct")) or 0 for r in yes_rows), len(yes_rows))
        row["predicted_no_success_rate"] = rate(sum(as_int(r.get("correct")) or 0 for r in no_rows), len(no_rows))

        actual_yes_vals = [as_int(r.get("actual_outcome")) for r in rs if as_int(r.get("actual_outcome")) is not None]
        row["actual_yes_rate"] = rate(sum(actual_yes_vals), len(actual_yes_vals)) if actual_yes_vals else None

        out.append(row)

    out.sort(key=lambda r: (str([r.get(c) for c in group_cols]), -(r.get("count") or 0)))
    return out


def majority_baseline_for_prediction_type(pred_rows: list[dict]) -> dict:
    by_type = defaultdict(list)
    for r in pred_rows:
        by_type[r.get("prediction_type")].append(r)

    out = {}
    for pred_type, rows in by_type.items():
        actual_vals = [as_int(r.get("actual_outcome")) for r in rows if as_int(r.get("actual_outcome")) is not None]

        if pred_type == "winner":
            # Winner rows have actual_outcome = whether model winner won, so majority class baseline is not meaningful.
            # Use home team baseline from game profiles separately.
            continue

        if not actual_vals:
            continue

        yes_rate = sum(actual_vals) / len(actual_vals)
        baseline_yes = 1 if yes_rate >= 0.5 else 0
        baseline_success = yes_rate if baseline_yes == 1 else (1 - yes_rate)
        out[pred_type] = {
            "prediction_type": pred_type,
            "baseline_type": "majority_actual_outcome",
            "actual_yes_rate": round(yes_rate, 4),
            "baseline_prediction": baseline_yes,
            "baseline_success_rate": round(baseline_success, 4),
            "sample_count": len(actual_vals),
        }
    return out


def home_team_baseline(game_rows: list[dict]) -> dict:
    vals = []
    for r in game_rows:
        home = r.get("home")
        actual = r.get("actual_winner")
        if home and actual:
            vals.append(1 if home == actual else 0)
    return {
        "prediction_type": "winner",
        "baseline_type": "always_pick_home_team",
        "actual_yes_rate": None,
        "baseline_prediction": "home",
        "baseline_success_rate": rate(sum(vals), len(vals)),
        "sample_count": len(vals),
    }


def add_baseline_lift(summary_rows: list[dict], baseline_rows: list[dict]) -> list[dict]:
    baseline_by_type = {r["prediction_type"]: r for r in baseline_rows}
    out = []
    for r in summary_rows:
        pred_type = r.get("prediction_type")
        b = baseline_by_type.get(pred_type)
        row = dict(r)
        if b:
            row["baseline_type"] = b.get("baseline_type")
            row["baseline_success_rate"] = b.get("baseline_success_rate")
            sr = as_float(row.get("success_rate"))
            br = as_float(row.get("baseline_success_rate"))
            row["lift_vs_baseline"] = round(sr - br, 4) if sr is not None and br is not None else None
        else:
            row["baseline_type"] = ""
            row["baseline_success_rate"] = None
            row["lift_vs_baseline"] = None
        out.append(row)
    return out


def threshold_sensitivity(pred_rows: list[dict], thresholds: list[float]) -> list[dict]:
    out = []
    by_type = defaultdict(list)
    for r in pred_rows:
        by_type[r.get("prediction_type")].append(r)

    for pred_type, rows in by_type.items():
        for threshold in thresholds:
            subset = [r for r in rows if abs(as_float(r.get("model_edge")) or 0) >= threshold]
            if not subset:
                continue

            yes_subset = [r for r in subset if as_int(r.get("predicted_outcome")) == 1]
            no_subset = [r for r in subset if as_int(r.get("predicted_outcome")) == 0]

            out.append({
                "prediction_type": pred_type,
                "min_model_edge": threshold,
                "count": len(subset),
                "correct": sum(as_int(r.get("correct")) or 0 for r in subset),
                "success_rate": rate(sum(as_int(r.get("correct")) or 0 for r in subset), len(subset)),
                "predicted_yes_count": len(yes_subset),
                "predicted_yes_success_rate": rate(sum(as_int(r.get("correct")) or 0 for r in yes_subset), len(yes_subset)),
                "predicted_no_count": len(no_subset),
                "predicted_no_success_rate": rate(sum(as_int(r.get("correct")) or 0 for r in no_subset), len(no_subset)),
            })

    out.sort(key=lambda r: (r["prediction_type"], r["min_model_edge"]))
    return out


def season_stability(rows: list[dict]) -> list[dict]:
    base = summarize(rows, ["season", "prediction_type"])
    grouped = defaultdict(list)
    for r in base:
        grouped[r["prediction_type"]].append(r)

    out = []
    for pred_type, rs in grouped.items():
        if len(rs) < 2:
            continue
        rates = [as_float(r.get("success_rate")) for r in rs if as_float(r.get("success_rate")) is not None]
        if not rates:
            continue
        out.append({
            "prediction_type": pred_type,
            "seasons_seen": ",".join(sorted(str(r["season"]) for r in rs)),
            "season_count": len(rs),
            "total_count": sum(as_int(r.get("count")) or 0 for r in rs),
            "avg_success_rate": round(sum(rates) / len(rates), 4),
            "min_success_rate": min(rates),
            "max_success_rate": max(rates),
            "success_rate_range": round(max(rates) - min(rates), 4),
            "stability_label": "stable" if len(rs) >= 3 and (max(rates) - min(rates)) <= 0.04 else "variable",
        })

    out.sort(key=lambda r: (r["stability_label"], r["prediction_type"]))
    return out


def wrong_reason_lift(pred_rows: list[dict]) -> list[dict]:
    misses = [r for r in pred_rows if as_int(r.get("correct")) == 0]
    groups = defaultdict(list)
    for r in misses:
        groups[(r.get("prediction_type"), r.get("wrong_reason") or "missing")].append(r)

    out = []
    for (pred_type, reason), rs in groups.items():
        out.append({
            "prediction_type": pred_type,
            "wrong_reason": reason,
            "miss_count": len(rs),
            "avg_model_edge": round(sum(abs(as_float(r.get("model_edge")) or 0) for r in rs) / len(rs), 3),
            "high_confidence_misses": sum(1 for r in rs if r.get("confidence_label") == "high"),
            "medium_confidence_misses": sum(1 for r in rs if r.get("confidence_label") == "medium"),
            "low_confidence_misses": sum(1 for r in rs if r.get("confidence_label") == "low"),
        })
    out.sort(key=lambda r: (r["prediction_type"], -r["miss_count"]))
    return out


def yes_no_base_rates(pred_rows: list[dict]) -> list[dict]:
    out = []
    for pred_type, rows in defaultdict(list, {}).items():
        pass

    by_type = defaultdict(list)
    for r in pred_rows:
        by_type[r.get("prediction_type")].append(r)

    for pred_type, rows in by_type.items():
        for predicted in (1, 0):
            subset = [r for r in rows if as_int(r.get("predicted_outcome")) == predicted]
            if not subset:
                continue
            actual_yes_vals = [as_int(r.get("actual_outcome")) for r in subset if as_int(r.get("actual_outcome")) is not None]
            out.append({
                "prediction_type": pred_type,
                "predicted_outcome": predicted,
                "count": len(subset),
                "correct": sum(as_int(r.get("correct")) or 0 for r in subset),
                "success_rate": rate(sum(as_int(r.get("correct")) or 0 for r in subset), len(subset)),
                "actual_yes_rate_within_bucket": rate(sum(actual_yes_vals), len(actual_yes_vals)) if actual_yes_vals else None,
                "avg_model_edge": round(sum(abs(as_float(r.get("model_edge")) or 0) for r in subset) / len(subset), 3),
            })
    out.sort(key=lambda r: (r["prediction_type"], -r["predicted_outcome"]))
    return out


def confidence_lift(rows: list[dict], baseline_rows: list[dict]) -> list[dict]:
    base = summarize(rows, ["prediction_type", "confidence_label"])
    return add_baseline_lift(base, baseline_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate pregame model predictions against base rates and thresholds.")
    parser.add_argument("--input-dir", default=str(DEFAULT_IN_DIR), help="Input directory from pregame_matchup_profile_preview.py")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory.")
    parser.add_argument("--thresholds", nargs="*", type=float, default=[0, 0.25, 0.5, 0.75, 1, 2, 3, 5, 7, 10, 15], help="Model-edge thresholds.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pred_path = input_dir / "pregame_prediction_rows.csv"
    game_path = input_dir / "pregame_game_profiles.csv"

    if not pred_path.exists():
        raise FileNotFoundError(f"Missing {pred_path}")
    if not game_path.exists():
        raise FileNotFoundError(f"Missing {game_path}")

    pred_rows = read_csv_rows(pred_path)
    game_rows = read_csv_rows(game_path)

    baseline_map = majority_baseline_for_prediction_type(pred_rows)
    baseline_rows = list(baseline_map.values())
    baseline_rows.append(home_team_baseline(game_rows))
    baseline_rows.sort(key=lambda r: r["prediction_type"])
    write_csv(out_dir / "base_rate_vs_model_baselines.csv", baseline_rows)

    by_type = summarize(pred_rows, ["prediction_type"])
    by_type_lift = add_baseline_lift(by_type, baseline_rows)
    write_csv(out_dir / "base_rate_vs_model_summary.csv", by_type_lift)

    yes_no = yes_no_base_rates(pred_rows)
    write_csv(out_dir / "yes_no_split_by_prediction_type.csv", yes_no)

    conf = confidence_lift(pred_rows, baseline_rows)
    write_csv(out_dir / "confidence_lift_summary.csv", conf)

    season = season_stability(pred_rows)
    write_csv(out_dir / "season_stability_by_prediction_type.csv", season)

    thresholds = threshold_sensitivity(pred_rows, args.thresholds)
    write_csv(out_dir / "threshold_sensitivity.csv", thresholds)

    wrong = wrong_reason_lift(pred_rows)
    write_csv(out_dir / "wrong_reason_calibration.csv", wrong)

    high_conf_misses = [r for r in pred_rows if as_int(r.get("correct")) == 0 and r.get("confidence_label") == "high"]
    high_conf_misses.sort(key=lambda r: (r.get("prediction_type", ""), -(as_float(r.get("model_edge")) or 0)))
    write_csv(out_dir / "high_confidence_misses_review.csv", high_conf_misses)

    # recommendations
    recs = []
    for r in by_type_lift:
        sr = as_float(r.get("success_rate"))
        br = as_float(r.get("baseline_success_rate"))
        lift = as_float(r.get("lift_vs_baseline"))
        count = as_int(r.get("count")) or 0
        label = "hold"
        if lift is not None and lift >= 0.04 and count >= 500:
            label = "promising"
        elif lift is not None and lift <= 0.00:
            label = "not_beating_baseline"
        elif sr is not None and sr >= 0.58:
            label = "watch_thresholds"
        recs.append({
            "prediction_type": r.get("prediction_type"),
            "count": count,
            "success_rate": sr,
            "baseline_success_rate": br,
            "lift_vs_baseline": lift,
            "recommendation": label,
        })
    write_csv(out_dir / "calibration_recommendations.csv", recs)

    md = []
    md.append("# Pregame Model Calibration Preview")
    md.append("")
    md.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    md.append("")
    md.append(f"- Input dir: `{input_dir}`")
    md.append(f"- Prediction rows: {len(pred_rows):,}")
    md.append(f"- Game profile rows: {len(game_rows):,}")
    md.append("")
    md.append("## Baseline Comparison")
    md.append("")
    for r in by_type_lift:
        md.append(
            f"- {r['prediction_type']}: model {pct(as_float(r.get('success_rate')))}, "
            f"baseline {pct(as_float(r.get('baseline_success_rate')))}, "
            f"lift {pct(as_float(r.get('lift_vs_baseline')))}, count {r['count']}"
        )
    md.append("")
    md.append("## YES/NO Split")
    md.append("")
    for r in yes_no:
        md.append(
            f"- {r['prediction_type']} predicted {r['predicted_outcome']}: "
            f"{r['correct']}/{r['count']} correct, success {pct(as_float(r.get('success_rate')))}, "
            f"actual yes rate {pct(as_float(r.get('actual_yes_rate_within_bucket')))}"
        )
    md.append("")
    md.append("## Season Stability")
    md.append("")
    for r in season:
        md.append(
            f"- {r['prediction_type']}: avg {pct(as_float(r.get('avg_success_rate')))}, "
            f"range {pct(as_float(r.get('success_rate_range')))}, {r['stability_label']}"
        )
    md.append("")
    md.append("## Initial Recommendations")
    md.append("")
    for r in recs:
        md.append(
            f"- {r['prediction_type']}: {r['recommendation']} "
            f"(model {pct(as_float(r.get('success_rate')))}, baseline {pct(as_float(r.get('baseline_success_rate')))}, "
            f"lift {pct(as_float(r.get('lift_vs_baseline')))}, count {r['count']})"
        )
    md.append("")
    md.append("## How To Use This")
    md.append("")
    md.append("- Promote only prediction types that beat a simple baseline and remain stable by season.")
    md.append("- Treat high raw success with low/no lift as a calibration issue, not a model edge.")
    md.append("- Use threshold sensitivity to tighten or loosen future pregame candidate filters.")
    md.append("- Use wrong-reason calibration to decide the next feature to add, likely starter/pitcher context.")
    md.append("")
    md.append("## Files Written")
    md.append("")
    for name in [
        "calibration_summary.md",
        "base_rate_vs_model_baselines.csv",
        "base_rate_vs_model_summary.csv",
        "yes_no_split_by_prediction_type.csv",
        "confidence_lift_summary.csv",
        "season_stability_by_prediction_type.csv",
        "threshold_sensitivity.csv",
        "wrong_reason_calibration.csv",
        "high_confidence_misses_review.csv",
        "calibration_recommendations.csv",
    ]:
        md.append(f"- {name}")

    (out_dir / "calibration_summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"WROTE: {out_dir}")
    print(f"  {out_dir / 'calibration_summary.md'}")
    print(f"Prediction rows: {len(pred_rows):,}")
    print("Baseline lift by type:")
    for r in by_type_lift:
        print(
            f"  {r['prediction_type']}: model={pct(as_float(r.get('success_rate')))} "
            f"baseline={pct(as_float(r.get('baseline_success_rate')))} "
            f"lift={pct(as_float(r.get('lift_vs_baseline')))}"
        )


if __name__ == "__main__":
    main()
