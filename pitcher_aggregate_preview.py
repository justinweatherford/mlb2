import argparse
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = "kalshi_mlb.db"
OUT_DIR = Path("outputs") / "pitcher_aggregate_preview"

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

def safe_rate(num: float, den: float) -> float | None:
    if den <= 0:
        return None
    return round(num / den, 4)

def safe_per_9(num: float, ip: float) -> float | None:
    if ip <= 0:
        return None
    return round((num * 9.0) / ip, 2)

def confidence_label(ip: float, events_seen: int, unclassified_contact: int, contact_total: int) -> str:
    if ip < 20:
        return "low"
    unclassified_rate = (unclassified_contact / contact_total) if contact_total else 0
    if ip >= 50 and unclassified_rate <= 0.10 and events_seen >= 150:
        return "high"
    return "medium"

def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only pitcher aggregate preview from mlb_play_events.")
    parser.add_argument("--season", default=None, help="Optional season filter, e.g. 2025 or 2026.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    where = "WHERE p.pitcher_name IS NOT NULL"
    params: list[str] = []
    if args.season:
        where += " AND substr(g.game_date, 1, 4) = ?"
        params.append(str(args.season))

    rows = conn.execute(f"""
        SELECT
            p.game_pk,
            g.game_date,
            substr(g.game_date, 1, 4) AS season,
            p.event_type,
            p.description,
            p.is_home_run,
            p.outs,
            p.pitcher_name
        FROM mlb_play_events p
        LEFT JOIN mlb_games g ON g.game_pk = p.game_pk
        {where}
    """, params).fetchall()

    agg = defaultdict(lambda: {
        "pitcher_name": None,
        "season": None,
        "games": set(),
        "events_seen": 0,
        "estimated_outs": 0,
        "strikeouts": 0,
        "walks": 0,
        "intent_walks": 0,
        "hit_by_pitch": 0,
        "home_runs": 0,
        "ground_balls": 0,
        "fly_balls": 0,
        "line_drives": 0,
        "popups": 0,
        "home_run_unclassified_contact": 0,
        "unclassified": 0,
        "ignored": 0,
    })

    for r in rows:
        pitcher = r["pitcher_name"]
        season = r["season"] or "unknown"
        key = (season, pitcher)
        a = agg[key]

        event_type = r["event_type"]
        cls = classify_batted_ball(event_type, r["description"], r["is_home_run"])
        est_outs = estimate_outs(event_type)

        a["pitcher_name"] = pitcher
        a["season"] = season
        a["games"].add(r["game_pk"])
        a["events_seen"] += 1
        a["estimated_outs"] += est_outs

        if event_type in {"Strikeout", "Strikeout Double Play"}:
            a["strikeouts"] += 1
        elif event_type == "Walk":
            a["walks"] += 1
        elif event_type == "Intent Walk":
            a["intent_walks"] += 1
        elif event_type == "Hit By Pitch":
            a["hit_by_pitch"] += 1

        if event_type == "Home Run" or r["is_home_run"]:
            a["home_runs"] += 1

        if cls == "ground_ball":
            a["ground_balls"] += 1
        elif cls == "fly_ball":
            a["fly_balls"] += 1
        elif cls == "line_drive":
            a["line_drives"] += 1
        elif cls == "popup":
            a["popups"] += 1
        elif cls == "home_run_unclassified_contact":
            a["home_run_unclassified_contact"] += 1
        elif cls == "unclassified":
            a["unclassified"] += 1
        elif cls == "ignore":
            a["ignored"] += 1

    output_rows = []
    for (_season, _pitcher), a in agg.items():
        ip = round(a["estimated_outs"] / 3.0, 2)
        contact_total = (
            a["ground_balls"]
            + a["fly_balls"]
            + a["line_drives"]
            + a["popups"]
            + a["home_run_unclassified_contact"]
        )
        unclassified_contact = a["home_run_unclassified_contact"]

        notes = []
        if ip < 20:
            notes.append("small_sample")
        if contact_total == 0:
            notes.append("no_classified_contact")
        if a["unclassified"] > a["events_seen"] * 0.40:
            notes.append("many_unclassified_events_expected_includes_K_BB_hits_without_keywords")

        output_rows.append({
            "season": a["season"],
            "pitcher_name": a["pitcher_name"],
            "games_seen": len(a["games"]),
            "events_seen": a["events_seen"],
            "estimated_outs": a["estimated_outs"],
            "ip_est": ip,
            "strikeouts": a["strikeouts"],
            "walks": a["walks"],
            "intent_walks": a["intent_walks"],
            "hit_by_pitch": a["hit_by_pitch"],
            "home_runs": a["home_runs"],
            "ground_balls": a["ground_balls"],
            "fly_balls": a["fly_balls"],
            "line_drives": a["line_drives"],
            "popups": a["popups"],
            "home_run_unclassified_contact": a["home_run_unclassified_contact"],
            "classified_contact_total": contact_total,
            "k9": safe_per_9(a["strikeouts"], ip),
            "bb9_excluding_ibb": safe_per_9(a["walks"], ip),
            "bb9_including_ibb": safe_per_9(a["walks"] + a["intent_walks"], ip),
            "hr9": safe_per_9(a["home_runs"], ip),
            "gb_rate": safe_rate(a["ground_balls"], contact_total),
            "fb_rate": safe_rate(a["fly_balls"], contact_total),
            "ld_rate": safe_rate(a["line_drives"], contact_total),
            "popup_rate": safe_rate(a["popups"], contact_total),
            "confidence_label": confidence_label(ip, a["events_seen"], unclassified_contact, contact_total),
            "notes": ";".join(notes),
        })

    output_rows.sort(key=lambda r: (r["season"], -(r["ip_est"] or 0), r["pitcher_name"]))

    fieldnames = [
        "season", "pitcher_name", "games_seen", "events_seen",
        "estimated_outs", "ip_est",
        "strikeouts", "walks", "intent_walks", "hit_by_pitch", "home_runs",
        "ground_balls", "fly_balls", "line_drives", "popups",
        "home_run_unclassified_contact", "classified_contact_total",
        "k9", "bb9_excluding_ibb", "bb9_including_ibb", "hr9",
        "gb_rate", "fb_rate", "ld_rate", "popup_rate",
        "confidence_label", "notes",
    ]

    suffix = f"_{args.season}" if args.season else ""
    csv_path = OUT_DIR / f"pitcher_aggregates_preview{suffix}.csv"
    write_csv(csv_path, output_rows, fieldnames)

    confidence_counts = defaultdict(int)
    for r in output_rows:
        confidence_counts[r["confidence_label"]] += 1

    summary_path = OUT_DIR / f"pitcher_aggregate_summary{suffix}.md"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# Pitcher Aggregate Preview\n\n")
        f.write(f"Generated: {datetime.utcnow().isoformat()} UTC\n\n")
        f.write(f"- Season filter: {args.season or 'all'}\n")
        f.write(f"- Pitchers: {len(output_rows):,}\n")
        f.write(f"- Source rows: {len(rows):,}\n\n")

        f.write("## Confidence Counts\n\n")
        for k in ["high", "medium", "low"]:
            f.write(f"- {k}: {confidence_counts[k]:,}\n")

        f.write("\n## Top 20 by IP Estimate\n\n")
        for r in output_rows[:20]:
            f.write(
                f"- {r['pitcher_name']} ({r['season']}): "
                f"IP {r['ip_est']}, K/9 {r['k9']}, BB/9 {r['bb9_excluding_ibb']}, "
                f"HR/9 {r['hr9']}, conf {r['confidence_label']}\n"
            )

        f.write("\n## Notes\n\n")
        f.write("- This is read-only and output-only.\n")
        f.write("- IP uses estimated outs from event type, not stored `outs`.\n")
        f.write("- Local xFIP is not calculated here. This is the aggregate foundation.\n")

    print(f"WROTE: {OUT_DIR}")
    print(f"  {csv_path}")
    print(f"  {summary_path}")
    print(f"Pitchers: {len(output_rows):,}")
    print("Confidence:", dict(confidence_counts))

if __name__ == "__main__":
    main()
