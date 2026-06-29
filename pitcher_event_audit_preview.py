import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "pitcher_event_audit_preview"

IGNORE_EVENTS = {
    "Mound Visit", "Game Advisory", "Batter Timeout",
    "Offensive Substitution", "Defensive Switch", "Pitching Substitution",
    "Pickoff 1B", "Pickoff 2B", "Pickoff 3B",
    "Caught Stealing 2B", "Caught Stealing 3B", "Caught Stealing Home",
    "Pickoff Caught Stealing 2B", "Wild Pitch",
}

def estimate_outs(event_type: str | None) -> int:
    e = event_type or ""
    if e in {"Strikeout Double Play", "Grounded Into DP", "Double Play", "Sac Fly Double Play"}:
        return 2
    if e in {
        "Strikeout", "Groundout", "Bunt Groundout", "Flyout", "Lineout",
        "Pop Out", "Bunt Pop Out", "Sac Fly", "Sac Bunt",
        "Forceout", "Fielders Choice Out",
    }:
        return 1
    return 0

def classify_batted_ball(event_type: str | None, description: str | None, is_home_run: int | None) -> str:
    e = (event_type or "").lower()
    d = (description or "").lower()

    if event_type in IGNORE_EVENTS:
        return "ignore"
    if "line drive" in d or "lineout" in e:
        return "line_drive"
    if "ground ball" in d or "groundout" in e or "grounded into" in e or "bunt groundout" in e:
        return "ground_ball"
    if "pop out" in e or "popup" in d or "pop up" in d or "pops out" in d or "bunt pop out" in e:
        return "popup"
    if "fly ball" in d or "flyout" in e or "sac fly" in e:
        return "fly_ball"
    if is_home_run:
        return "home_run_unclassified_contact"
    return "unclassified"

def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT
            game_pk, event_time, inning, inning_half, event_type,
            description, is_home_run, outs, pitcher_name
        FROM mlb_play_events
        WHERE pitcher_name IS NOT NULL
    """).fetchall()

    event_counts = Counter()
    pitcher_counts = Counter()
    class_counts = Counter()
    outs_compare = defaultdict(lambda: {"rows": 0, "stored_outs_sum": 0, "estimated_outs_sum": 0})
    examples = defaultdict(list)

    for r in rows:
        event_type = r["event_type"]
        description = r["description"]
        pitcher = r["pitcher_name"]
        stored_outs = r["outs"] or 0
        est_outs = estimate_outs(event_type)
        contact_class = classify_batted_ball(event_type, description, r["is_home_run"])

        event_counts[event_type or "NULL"] += 1
        pitcher_counts[pitcher] += 1
        class_counts[contact_class] += 1

        oc = outs_compare[event_type or "NULL"]
        oc["rows"] += 1
        oc["stored_outs_sum"] += stored_outs
        oc["estimated_outs_sum"] += est_outs

        if len(examples[contact_class]) < 25 and description:
            examples[contact_class].append({
                "classification": contact_class,
                "event_type": event_type,
                "description": description,
                "stored_outs": stored_outs,
                "estimated_outs": est_outs,
                "pitcher_name": pitcher,
            })

    write_csv(
        OUT_DIR / "event_type_counts.csv",
        [{"event_type": k, "count": v} for k, v in event_counts.most_common()],
        ["event_type", "count"],
    )

    write_csv(
        OUT_DIR / "pitcher_coverage.csv",
        [{"pitcher_name": k, "event_rows": v} for k, v in pitcher_counts.most_common()],
        ["pitcher_name", "event_rows"],
    )

    example_rows = []
    for group_rows in examples.values():
        example_rows.extend(group_rows)

    write_csv(
        OUT_DIR / "batted_ball_examples.csv",
        example_rows,
        ["classification", "event_type", "description", "stored_outs", "estimated_outs", "pitcher_name"],
    )

    outs_rows = []
    for event_type, data in sorted(outs_compare.items(), key=lambda x: x[1]["rows"], reverse=True):
        outs_rows.append({
            "event_type": event_type,
            "rows": data["rows"],
            "stored_outs_sum": data["stored_outs_sum"],
            "estimated_outs_sum": data["estimated_outs_sum"],
            "stored_minus_estimated": data["stored_outs_sum"] - data["estimated_outs_sum"],
        })

    write_csv(
        OUT_DIR / "outs_semantics_review.csv",
        outs_rows,
        ["event_type", "rows", "stored_outs_sum", "estimated_outs_sum", "stored_minus_estimated"],
    )

    summary_path = OUT_DIR / "classification_summary.md"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# Pitcher Event Audit Preview\n\n")
        f.write(f"Generated: {datetime.utcnow().isoformat()} UTC\n\n")
        f.write(f"- Total pitcher-linked play rows: {len(rows):,}\n")
        f.write(f"- Distinct pitchers: {len(pitcher_counts):,}\n\n")

        f.write("## Contact Classification Counts\n\n")
        for k, v in class_counts.most_common():
            f.write(f"- {k}: {v:,}\n")

        f.write("\n## Top Event Types\n\n")
        for k, v in event_counts.most_common(25):
            f.write(f"- {k}: {v:,}\n")

        f.write("\n## Files Written\n\n")
        for name in [
            "event_type_counts.csv",
            "pitcher_coverage.csv",
            "batted_ball_examples.csv",
            "outs_semantics_review.csv",
        ]:
            f.write(f"- {name}\n")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {OUT_DIR / 'classification_summary.md'}")
    print(f"  {OUT_DIR / 'event_type_counts.csv'}")
    print(f"  {OUT_DIR / 'pitcher_coverage.csv'}")
    print(f"  {OUT_DIR / 'batted_ball_examples.csv'}")
    print(f"  {OUT_DIR / 'outs_semantics_review.csv'}")

if __name__ == "__main__":
    main()
