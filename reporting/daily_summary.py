import json
import sqlite3
from datetime import date, datetime
from typing import Optional

from db.repository import _now
from reporting.pace_fade_report import get_pace_fade_summary_stats


def generate_daily_summary(conn: sqlite3.Connection,
                            for_date: Optional[date] = None) -> dict:
    d = for_date or date.today()
    date_str = d.isoformat()
    prefix = date_str + "T"

    total_messages = conn.execute(
        "SELECT COUNT(*) FROM raw_messages WHERE received_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    total_signals = conn.execute(
        "SELECT COUNT(*) FROM signal_events WHERE created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    total_entries = conn.execute(
        "SELECT COUNT(*) FROM signal_events WHERE action_taken='paper_entry' AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    total_skipped = total_signals - total_entries

    open_pos = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status='open' AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    exited_pos = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status='exited' AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    settled_pos = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status='settled' AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()[0]

    pnl = conn.execute(
        "SELECT COALESCE(SUM(gross_pnl_cents),0), COALESCE(SUM(net_pnl_cents),0) "
        "FROM paper_positions WHERE status != 'open' AND created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()
    gross_pnl = pnl[0]
    net_pnl = pnl[1]

    signal_stats = {}
    rows = conn.execute(
        "SELECT signal_type, COUNT(*) as cnt, "
        "SUM(CASE WHEN net_pnl_cents > 0 THEN 1 ELSE 0 END) as wins, "
        "COALESCE(SUM(net_pnl_cents),0) as net_pnl "
        "FROM paper_positions WHERE status != 'open' AND created_at LIKE ? "
        "GROUP BY signal_type",
        (prefix + "%",),
    ).fetchall()
    for row in rows:
        cnt = row["cnt"]
        signal_stats[row["signal_type"]] = {
            "count": cnt,
            "wins": row["wins"],
            "win_rate": round(row["wins"] / cnt, 3) if cnt else 0,
            "net_pnl_cents": row["net_pnl"],
        }

    excursion = conn.execute(
        "SELECT AVG(mfe_cents), AVG(mae_cents) FROM paper_positions "
        "WHERE created_at LIKE ?",
        (prefix + "%",),
    ).fetchone()

    pace_fade = get_pace_fade_summary_stats(conn, d)

    summary = {
        "date": date_str,
        "total_messages": total_messages,
        "total_signals": total_signals,
        "total_entries": total_entries,
        "total_skipped": total_skipped,
        "open_positions": open_pos,
        "exited_positions": exited_pos,
        "settled_positions": settled_pos,
        "gross_pnl_cents": gross_pnl,
        "net_pnl_cents": net_pnl,
        "gross_pnl_dollars": round(gross_pnl / 100, 2),
        "net_pnl_dollars": round(net_pnl / 100, 2),
        "signal_stats": signal_stats,
        "avg_mfe_cents": round(excursion[0] or 0, 1),
        "avg_mae_cents": round(excursion[1] or 0, 1),
        "pace_fade": pace_fade,
    }

    conn.execute("""
        INSERT INTO daily_summaries (
            date, total_messages, total_signals, total_entries, total_skipped,
            open_positions, exited_positions, settled_positions,
            gross_pnl_cents, net_pnl_cents, summary_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            total_messages=excluded.total_messages,
            total_signals=excluded.total_signals,
            total_entries=excluded.total_entries,
            total_skipped=excluded.total_skipped,
            open_positions=excluded.open_positions,
            exited_positions=excluded.exited_positions,
            settled_positions=excluded.settled_positions,
            gross_pnl_cents=excluded.gross_pnl_cents,
            net_pnl_cents=excluded.net_pnl_cents,
            summary_json=excluded.summary_json
    """, (
        date_str, total_messages, total_signals, total_entries, total_skipped,
        open_pos, exited_pos, settled_pos, gross_pnl, net_pnl,
        json.dumps(summary), _now(),
    ))
    conn.commit()
    return summary


def print_daily_summary(summary: dict) -> None:
    print(f"\n{'='*50}")
    print(f"  DAILY SUMMARY — {summary['date']}")
    print(f"{'='*50}")
    print(f"  Messages parsed:    {summary['total_messages']}")
    print(f"  Signals generated:  {summary['total_signals']}")
    print(f"  Paper entries:      {summary['total_entries']}")
    print(f"  Skipped / no-bet:   {summary['total_skipped']}")
    print(f"  Open positions:     {summary['open_positions']}")
    print(f"  Exited positions:   {summary['exited_positions']}")
    print(f"  Settled positions:  {summary['settled_positions']}")
    print(f"  Gross P/L:          ${summary['gross_pnl_dollars']:+.2f}")
    print(f"  Net P/L (fees):     ${summary['net_pnl_dollars']:+.2f}")
    print(f"  Avg MFE:            {summary['avg_mfe_cents']}c")
    print(f"  Avg MAE:            {summary['avg_mae_cents']}c")
    _FADE_LABELS = {
        "fade_overreaction":  "generic_fade",
        "midgame_blowup_fade": "midgame_blowup_fade",
        "pace_fade_under_candidate": "pace_fade",
    }

    if summary["signal_stats"]:
        print(f"\n  By Signal Type:")
        for sig, stats in summary["signal_stats"].items():
            label = _FADE_LABELS.get(sig, sig)
            print(f"    {label:<30} {stats['count']:>3} trades  "
                  f"win={stats['win_rate']:.0%}  "
                  f"net={stats['net_pnl_cents']:+d}c")

        # Show fade family roll-up if more than one subtype is present
        fade_present = [s for s in _FADE_LABELS if s in summary["signal_stats"]]
        if len(fade_present) > 1:
            total_fade = sum(summary["signal_stats"][s]["count"] for s in fade_present)
            total_net = sum(summary["signal_stats"][s]["net_pnl_cents"] for s in fade_present)
            print(f"    {'--- fade total ---':<30} {total_fade:>3} trades  "
                  f"net={total_net:+d}c")

    pf = summary.get("pace_fade", {})
    if pf.get("total_candidate_rows", 0) > 0 or pf.get("total_explosion_snapshots", 0) > 0:
        print(f"\n  Pace-Fade (observational):")
        print(f"    Early explosion snapshots: {pf.get('total_explosion_snapshots', 0)}")
        print(f"    Candidate rows:            {pf.get('total_candidate_rows', 0)}")
        print(f"    Avg score:                 {pf.get('avg_score', 0):.3f}")
        print(f"    Unresolved outcomes:       {pf.get('unresolved_outcomes', 0)}")
        wins = pf.get("settled_wins", 0)
        losses = pf.get("settled_losses", 0)
        if wins or losses:
            total = wins + losses
            pct = f"{wins/total:.0%}" if total else "—"
            print(f"    Settled wins/losses:       {wins}/{losses}  ({pct})")
        by_class = pf.get("by_classification", {})
        if by_class:
            print(f"    By classification:")
            for cls, d in by_class.items():
                print(f"      {cls:<32} {d['count']:>3}  avg={d['avg_score']:.3f}")
        top = pf.get("top_candidates", [])
        if top:
            print(f"    Top candidates by score:")
            for t in top:
                inning = f"T{t['inning_number']}" if t["inning_half"] == "T" else f"B{t['inning_number']}"
                print(f"      {t['game_id']:<12} {inning} line={t['line']:.1f}"
                      f"  score={t['pace_fade_score']:.3f}"
                      f"  entry={t['estimated_under_entry']}¢"
                      f"  [{t['classification']}]")

    print(f"{'='*50}\n")
