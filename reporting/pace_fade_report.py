"""
reporting/pace_fade_report.py — Inspection queries for pace-fade training rows.
"""
import json
import sqlite3
from datetime import date
from typing import Optional


def get_pace_fade_candidates(
    conn: sqlite3.Connection,
    game_id: Optional[str] = None,
    line: Optional[float] = None,
    classification: Optional[str] = None,
    limit: int = 50,
) -> list:
    """
    Return pace-fade training rows from the DB, newest first.

    All filter params are optional and combinable.
    """
    where = []
    params = []

    if game_id:
        where.append("game_id = ?")
        params.append(game_id)
    if line is not None:
        where.append("line = ?")
        params.append(line)
    if classification:
        where.append("classification = ?")
        params.append(classification)

    sql = (
        "SELECT * FROM pace_fade_training_rows"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY pace_fade_score DESC, created_at DESC"
        + " LIMIT ?"
    )
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_pace_fade_summary_stats(
    conn: sqlite3.Connection,
    for_date: Optional[date] = None,
) -> dict:
    """
    Aggregate pace-fade stats for a given date (default: today).

    Used by generate_daily_summary.
    """
    d = for_date or date.today()
    prefix = d.isoformat() + "T"

    total_rows = conn.execute(
        "SELECT COUNT(*) FROM pace_fade_training_rows WHERE created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    # Distinct early-explosion snapshots = distinct (game_id, inning_half, inning_number, current_total)
    total_explosions = conn.execute(
        "SELECT COUNT(DISTINCT game_id || '|' || inning_half || inning_number || '|' || current_total)"
        " FROM pace_fade_training_rows WHERE created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    by_class_rows = conn.execute(
        "SELECT classification, COUNT(*) as cnt, AVG(pace_fade_score) as avg_score"
        " FROM pace_fade_training_rows WHERE created_at LIKE ?"
        " GROUP BY classification ORDER BY cnt DESC",
        (prefix + "%",),
    ).fetchall()
    by_class = {}
    for row in by_class_rows:
        by_class[row["classification"]] = {
            "count": row["cnt"],
            "avg_score": round(row["avg_score"] or 0.0, 3),
        }

    avg_score = conn.execute(
        "SELECT AVG(pace_fade_score) FROM pace_fade_training_rows WHERE created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    top_rows = conn.execute(
        "SELECT game_id, line, inning_half, inning_number, current_total,"
        " pace_fade_score, estimated_under_entry, classification"
        " FROM pace_fade_training_rows WHERE created_at LIKE ?"
        " ORDER BY pace_fade_score DESC LIMIT 5",
        (prefix + "%",),
    ).fetchall()
    top = [dict(r) for r in top_rows]

    unresolved = conn.execute(
        "SELECT COUNT(*) FROM pace_fade_training_rows"
        " WHERE final_total IS NULL AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    settled_wins = conn.execute(
        "SELECT COUNT(*) FROM pace_fade_training_rows"
        " WHERE under_won=1 AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    settled_losses = conn.execute(
        "SELECT COUNT(*) FROM pace_fade_training_rows"
        " WHERE under_won=0 AND final_total IS NOT NULL AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    return {
        "total_explosion_snapshots": total_explosions,
        "total_candidate_rows": total_rows,
        "by_classification": by_class,
        "avg_score": round(avg_score or 0.0, 3),
        "top_candidates": top,
        "unresolved_outcomes": unresolved,
        "settled_wins": settled_wins,
        "settled_losses": settled_losses,
    }


def print_pace_fade_candidates(rows: list, title: str = "Pace-Fade Candidates") -> None:
    """Print a formatted table of pace-fade training rows to stdout."""
    sep = "=" * 80
    print(f"\n{sep}")
    print(f"  {title.upper()}")
    print(sep)

    if not rows:
        print("  No pace-fade candidates found.")
        print(f"{sep}\n")
        return

    header = (
        f"  {'Game':<12} {'T':<4} {'Line':<6} {'Entry':>6} "
        f"{'Score':>7} {'Class':<30} {'Flags'}"
    )
    print(header)
    print("  " + "-" * 76)

    for r in rows:
        flags = json.loads(r["risk_flags_json"]) if r["risk_flags_json"] else []
        flag_str = ",".join(flags[:3]) + ("…" if len(flags) > 3 else "")
        inning = f"T{r['inning_number']}" if r["inning_half"] == "T" else f"B{r['inning_number']}"
        settled = ""
        if r["final_total"] is not None:
            won = r["under_won"]
            settled = f" [{'WIN' if won else 'LOSS'} final={r['final_total']}]"
        print(
            f"  {r['game_id']:<12} {inning:<4} {r['line']:<6.1f} {r['estimated_under_entry']:>5}¢ "
            f"{r['pace_fade_score']:>7.3f} {r['classification']:<30} {flag_str}{settled}"
        )

    print(f"{sep}\n")


def print_pace_fade_detail(row) -> None:
    """Print full score breakdown for one pace-fade training row."""
    print(f"\n  PACE-FADE DETAIL: {row['game_id']} line={row['line']}")
    print(f"  {'Game state:':<28} T{row['inning_number']} score={row['current_total']}")
    print(f"  {'Classification:':<28} {row['classification']}")
    print(f"  {'Total score:':<28} {row['pace_fade_score']:.4f}")
    print(f"  {'Early explosion:':<28} {row['early_explosion_score']:.4f}")
    print(f"  {'Line cushion:':<28} {row['line_cushion_score']:.4f}  (cushion={row['line_cushion']:.1f}r)")
    print(f"  {'Under entry value:':<28} {row['under_entry_value_score']:.4f}  ({row['estimated_under_entry']}¢)")
    print(f"  {'Run env:':<28} {row['run_env_tag']}  HR: {row['hr_env_tag']}")
    print(f"  {'Context:':<28} {row['context_source']} (conf={row['context_confidence']:.2f})")

    flags = json.loads(row["risk_flags_json"]) if row["risk_flags_json"] else []
    if flags:
        print(f"  {'Risk flags:':<28} {', '.join(flags)}")

    missing = json.loads(row["missing_context_json"]) if row["missing_context_json"] else []
    if missing:
        print(f"  {'Missing context:':<28} {', '.join(missing)}")

    if row["final_total"] is not None:
        won = row["under_won"]
        print(f"  {'Outcome:':<28} {'WON' if won else 'LOST'} (final={row['final_total']})")
    else:
        print(f"  {'Outcome:':<28} unresolved")
