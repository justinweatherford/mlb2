"""
pregame_ev_check.py

Read-only. For each team on today's slate, joins the pregame brain score to the
current Kalshi [TEAM]5 NO ask and computes expected value.

Answers the question: "Does the market ever misprice these enough to trade?"

Usage:
  python pregame_ev_check.py                     # today's date
  python pregame_ev_check.py --date 2026-06-25   # specific date

Output:
  Console: per-team EV table
  CSV:     outputs/pregame_ev_log/ev_log_YYYY-MM-DD.csv  (appended each run)

Read-only: no writes to the DB, no trades, no paper positions.
"""

import argparse
import csv
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

DB_PATH = Path("kalshi_mlb.db")
CARDS_CSV = Path("outputs") / "pregame_identifier_card_preview" / "pregame_identifier_cards.csv"
LOG_DIR = Path("outputs") / "pregame_ev_log"

FEE_CENTS = 1.5

# Calibrated hit-rate buckets from validated research (train 2023-24, test 2025, n=404)
# Score threshold → (hit_rate, sample_description)
SCORE_BANDS = [
    (0.50, 0.686, "0.50+"),
    (0.40, 0.686, "0.40-0.50"),
    (0.30, 0.644, "0.30-0.40"),
    (0.20, 0.633, "0.20-0.30"),
]

MIN_DISPLAY_SCORE = 0.20   # show all teams at or above this score
MIN_QUALIFY_SCORE = 0.40   # flag as "qualified" at or above this score


def _calib_prob(score: float) -> float:
    for threshold, prob, _ in SCORE_BANDS:
        if score >= threshold:
            return prob
    return 0.0


def _score_band_label(score: float) -> str:
    for threshold, _, label in SCORE_BANDS:
        if score >= threshold:
            return label
    return "<0.20"


def _load_brain_scores(slate_date: str) -> list[dict]:
    if not CARDS_CSV.exists():
        return []
    with CARDS_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [
        r for r in rows
        if r.get("game_date") == slate_date
        and float(r.get("team_runs_5plus_no_score") or 0) >= MIN_DISPLAY_SCORE
    ]


def _get_game_starts(conn: sqlite3.Connection, slate_date: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT game_pk, game_start_time_utc FROM mlb_games WHERE game_date = ?",
        (slate_date,),
    ).fetchall()
    return {str(r[0]): r[1] for r in rows}


def _get_kalshi_market(conn: sqlite3.Connection, game_pk: str, team_abbr: str) -> dict | None:
    row = conn.execute(
        """
        SELECT market_ticker, line_value, yes_ask_cents, yes_bid_cents
        FROM kalshi_markets
        WHERE game_pk = ? AND selected_team_abbr = ? AND line_value = 5
        ORDER BY discovered_at DESC LIMIT 1
        """,
        (int(game_pk), team_abbr),
    ).fetchone()
    if not row:
        return None
    return {"ticker": row[0], "line": row[1], "yes_ask_catalog": row[2], "yes_bid_catalog": row[3]}


def _get_latest_snapshot(conn: sqlite3.Connection, ticker: str, before_utc: str | None = None) -> dict | None:
    if before_utc:
        row = conn.execute(
            """
            SELECT snapped_at, yes_bid, yes_ask, no_bid, no_ask, spread_cents
            FROM kalshi_orderbook_snapshots
            WHERE market_ticker = ? AND snapped_at < ?
            ORDER BY snapped_at DESC LIMIT 1
            """,
            (ticker, before_utc),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT snapped_at, yes_bid, yes_ask, no_bid, no_ask, spread_cents
            FROM kalshi_orderbook_snapshots
            WHERE market_ticker = ?
            ORDER BY snapped_at DESC LIMIT 1
            """,
            (ticker,),
        ).fetchone()
    if not row:
        return None
    return {
        "snapped_at": row[0],
        "yes_bid": row[1],
        "yes_ask": row[2],
        "no_bid": row[3],
        "no_ask": row[4],
        "spread": row[5],
    }


def _snapshot_age_min(snapped_at: str) -> float:
    try:
        ts = datetime.fromisoformat(snapped_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - ts).total_seconds() / 60
    except Exception:
        return -1


def run(slate_date: str, db_path: Path = None) -> None:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    now_utc = datetime.now(timezone.utc).isoformat()
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")

    brain_rows_raw = _load_brain_scores(slate_date)
    # deduplicate: keep latest row per (team, game_pk) in case CSV has multiple runs
    seen: dict = {}
    for r in brain_rows_raw:
        key = (r.get("team"), r.get("game_pk"))
        seen[key] = r
    brain_rows = list(seen.values())

    if not brain_rows:
        print(f"No brain scores >= {MIN_DISPLAY_SCORE} found for {slate_date}.")
        print(f"  Run: python score_today_slate.py --date {slate_date}")
        return

    game_starts = _get_game_starts(conn, slate_date)

    results = []
    for row in brain_rows:
        team = row["team"]
        opp = row.get("opponent", "?")
        home_away = row.get("home_away", "?")
        game_pk = str(row.get("game_pk", ""))
        score = float(row.get("team_runs_5plus_no_score") or 0)

        game_start = game_starts.get(game_pk)
        calib_prob = _calib_prob(score)
        band = _score_band_label(score)

        mkt = _get_kalshi_market(conn, game_pk, team)
        if not mkt:
            results.append({
                "team": team, "opp": opp, "home_away": home_away,
                "game_start": game_start or "?", "score": score, "band": band,
                "calib_prob_pct": round(calib_prob * 100, 1),
                "no_ask": None, "spread": None, "ev_cents": None,
                "verdict": "NO_MARKET", "snap_age_min": None,
                "snap_ts": None, "ticker": None,
            })
            continue

        snap = _get_latest_snapshot(conn, mkt["ticker"])
        if not snap or snap["no_ask"] is None:
            results.append({
                "team": team, "opp": opp, "home_away": home_away,
                "game_start": game_start or "?", "score": score, "band": band,
                "calib_prob_pct": round(calib_prob * 100, 1),
                "no_ask": None, "spread": None, "ev_cents": None,
                "verdict": "NO_SNAP", "snap_age_min": None,
                "snap_ts": None, "ticker": mkt["ticker"],
            })
            continue

        no_ask = snap["no_ask"]
        ev = round(calib_prob * 100 - no_ask - FEE_CENTS, 1)
        age_min = round(_snapshot_age_min(snap["snapped_at"]), 1)

        if score < MIN_QUALIFY_SCORE:
            verdict = "BELOW_THRESH"
        elif ev > 0:
            verdict = "EV_POSITIVE"
        else:
            verdict = "NO_EDGE"

        results.append({
            "team": team, "opp": opp, "home_away": home_away,
            "game_start": game_start or "?", "score": score, "band": band,
            "calib_prob_pct": round(calib_prob * 100, 1),
            "no_ask": no_ask, "spread": snap["spread"],
            "ev_cents": ev,
            "verdict": verdict,
            "snap_age_min": age_min,
            "snap_ts": snap["snapped_at"][:19],
            "ticker": mkt["ticker"],
        })

    conn.close()

    # ── Console output ────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  Pregame EV Check -- {slate_date}   (run {run_ts} UTC)")
    print(f"  Brain: team_runs_5plus_no_score  |  Lane: [TEAM]5 NO  |  Fee: {FEE_CENTS}c")
    print(f"{'='*72}")

    qualified = [r for r in results if r["verdict"] in ("EV_POSITIVE", "NO_EDGE", "NO_MARKET", "NO_SNAP")
                 and r["score"] >= MIN_QUALIFY_SCORE]
    ev_positive = [r for r in results if r["verdict"] == "EV_POSITIVE"]

    if not results:
        print("  No results.")
    else:
        header = f"  {'Team':<5} {'Opp':<5} {'H/A':<5} {'Start':>10}  {'Score':>6}  {'Prob':>5}  {'NOask':>6}  {'EV':>6}  {'Verdict':<14}  {'SnapAge'}"
        print(header)
        print("  " + "-" * 68)
        for r in sorted(results, key=lambda x: -(x["score"] or 0)):
            no_ask_s = f"{r['no_ask']:>4}c" if r["no_ask"] is not None else " n/a "
            ev_s = f"{r['ev_cents']:>+5.1f}c" if r["ev_cents"] is not None else "  n/a "
            age_s = f"{r['snap_age_min']:>4.0f}m ago" if r["snap_age_min"] is not None else "     n/a"
            start_s = (r["game_start"] or "?")[:10] + " " + (r["game_start"] or "?")[11:16] if r["game_start"] else "?"
            flag = " <-- WATCH" if r["verdict"] == "EV_POSITIVE" else ""
            print(f"  {r['team']:<5} {r['opp']:<5} {r['home_away']:<5} {start_s:>15}  {r['score']:>6.4f}  {r['calib_prob_pct']:>4.1f}%  {no_ask_s}  {ev_s}  {r['verdict']:<14}{flag}")

    print()
    print(f"  Qualified (score >= {MIN_QUALIFY_SCORE}): {len(qualified)} teams")
    print(f"  EV positive: {len(ev_positive)} teams")
    if ev_positive:
        print()
        print("  >>> EV-POSITIVE SETUPS:")
        for r in ev_positive:
            print(f"      {r['team']} vs {r['opp']}  NO ask={r['no_ask']}c  EV={r['ev_cents']:+.1f}c  brain={r['calib_prob_pct']}%  [{r['ticker']}]")
    print(f"\n  Breakeven NO ask at 68.6% prob: {round(68.6 - FEE_CENTS, 1)}c (qualified tier)")
    print(f"  Market average NO ask (Jun 15-24 survey): ~76.9c")
    print(f"{'='*72}\n")

    # ── Log to CSV ────────────────────────────────────────────────────────────
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"ev_log_{slate_date}.csv"
    fieldnames = [
        "run_ts", "slate_date", "team", "opp", "home_away", "game_start",
        "score", "band", "calib_prob_pct", "no_ask", "spread",
        "ev_cents", "verdict", "snap_age_min", "snap_ts", "ticker",
    ]
    write_header = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for r in results:
            w.writerow({"run_ts": run_ts, "slate_date": slate_date, **r})

    print(f"  Logged {len(results)} rows -> {log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pregame EV check for [TEAM]5 NO lane.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Slate date YYYY-MM-DD")
    parser.add_argument("--db", default=str(DB_PATH), help="DB path")
    args = parser.parse_args()
    run(args.date, db_path=Path(args.db))


if __name__ == "__main__":
    main()
