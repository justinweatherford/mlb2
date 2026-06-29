"""
mlb/game_store.py — Fetch, log, and store MLB Stats API data.

Orchestrates: stats_api (HTTP) → jsonl_logger (raw JSONL) → DB tables.
No settlement logic — that lives in mlb/reconciler.py (Task 4).
"""
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

from db.schema import init_db
from mlb import stats_api
from mlb.jsonl_logger import log_response

log = logging.getLogger(__name__)

_ABBREV_MAP = {"WSH": "WSN"}


def _norm(abbr: str) -> str:
    return _ABBREV_MAP.get(abbr.upper(), abbr.upper())


def _now() -> str:
    return datetime.now().isoformat()


def _open_conn() -> sqlite3.Connection:
    return init_db(os.environ.get("DB_PATH", "kalshi_mlb.db"))


def _ensure_probable_pitcher_cols(conn: sqlite3.Connection) -> None:
    for col, typ in [
        ("home_probable_pitcher_id", "INTEGER"),
        ("home_probable_pitcher_name", "TEXT"),
        ("away_probable_pitcher_id", "INTEGER"),
        ("away_probable_pitcher_name", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE mlb_games ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _upsert_game(conn: sqlite3.Connection, g: dict) -> None:
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, game_start_time_utc,
           home_probable_pitcher_id, home_probable_pitcher_name,
           away_probable_pitcher_id, away_probable_pitcher_name,
           last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(game_pk) DO UPDATE SET
          status              = excluded.status,
          game_id             = excluded.game_id,
          is_final            = MAX(is_final, excluded.is_final),
          final_away_score    = COALESCE(excluded.final_away_score, final_away_score),
          final_home_score    = COALESCE(excluded.final_home_score, final_home_score),
          final_total         = COALESCE(excluded.final_total, final_total),
          game_start_time_utc = COALESCE(excluded.game_start_time_utc, game_start_time_utc),
          home_probable_pitcher_id   = COALESCE(excluded.home_probable_pitcher_id, home_probable_pitcher_id),
          home_probable_pitcher_name = COALESCE(excluded.home_probable_pitcher_name, home_probable_pitcher_name),
          away_probable_pitcher_id   = COALESCE(excluded.away_probable_pitcher_id, away_probable_pitcher_id),
          away_probable_pitcher_name = COALESCE(excluded.away_probable_pitcher_name, away_probable_pitcher_name),
          last_checked_at     = excluded.last_checked_at
        """,
        (
            g["game_pk"], g["game_date"], g["away_team"], g["home_team"],
            g["away_abbr"], g["home_abbr"], g["game_id"], g["status"],
            g.get("is_final", 0),
            g.get("final_away_score"),
            g.get("final_home_score"),
            g.get("final_total"),
            g.get("game_start_time_utc"),
            g.get("home_probable_pitcher_id"),
            g.get("home_probable_pitcher_name"),
            g.get("away_probable_pitcher_id"),
            g.get("away_probable_pitcher_name"),
            _now(), _now(),
        ),
    )


def _insert_game_state(conn: sqlite3.Connection, game_pk: int, feed: dict) -> None:
    gd = feed.get("gameData") or {}
    ls = (feed.get("liveData") or {}).get("linescore") or {}
    teams = ls.get("teams") or {}
    offense = ls.get("offense") or {}
    plays = (feed.get("liveData") or {}).get("plays") or {}
    matchup = (plays.get("currentPlay") or {}).get("matchup") or {}

    runner_state = "".join([
        "1" if offense.get("first") else "-",
        "2" if offense.get("second") else "-",
        "3" if offense.get("third") else "-",
    ])

    conn.execute(
        """
        INSERT INTO mlb_game_states
          (game_pk, checked_at, status, inning, inning_half, outs,
           away_score, home_score, balls, strikes, runner_state,
           current_batter, current_pitcher)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            game_pk,
            _now(),
            (gd.get("status") or {}).get("abstractGameState"),
            ls.get("currentInning"),
            (ls.get("inningHalf") or "").lower() or None,
            ls.get("outs"),
            ((teams.get("away") or {}).get("runs") or 0),
            ((teams.get("home") or {}).get("runs") or 0),
            ls.get("balls"),
            ls.get("strikes"),
            runner_state if runner_state != "---" else None,
            (matchup.get("batter") or {}).get("fullName"),
            (matchup.get("pitcher") or {}).get("fullName"),
        ),
    )


def _upsert_plays(conn: sqlite3.Connection, game_pk: int, pbp: dict) -> tuple[int, int]:
    """Insert play-by-play rows (one per at-bat). Returns (inserted, skipped)."""
    inserted = skipped = 0
    for play in pbp.get("allPlays") or []:
        try:
            about = play.get("about") or {}
            result = play.get("result") or {}
            matchup = play.get("matchup") or {}
            count = play.get("count") or {}

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO mlb_play_events
                  (game_pk, at_bat_index, play_index, event_time, inning,
                   inning_half, description, event_type, is_scoring_play,
                   is_home_run, rbi, outs, away_score, home_score,
                   batter_name, pitcher_name)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    game_pk,
                    about.get("atBatIndex", 0),
                    0,  # one row per at-bat outcome; play-level events are in raw JSONL
                    about.get("startTime"),
                    about.get("inning"),
                    about.get("halfInning"),
                    result.get("description"),
                    result.get("event"),
                    int(bool(about.get("isScoringPlay"))),
                    int((result.get("event") or "").lower() == "home run"),
                    result.get("rbi") or 0,
                    count.get("outs") or 0,
                    result.get("awayScore"),
                    result.get("homeScore"),
                    (matchup.get("batter") or {}).get("fullName"),
                    (matchup.get("pitcher") or {}).get("fullName"),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            log.warning("play upsert error (game_pk=%d at_bat=%s): %s",
                        game_pk, (play.get("about") or {}).get("atBatIndex"), exc)
            skipped += 1
    return inserted, skipped


def _upsert_inning_scores(
    conn: sqlite3.Connection,
    game_pk: int,
    linescore: dict,
    away_abbr: str,
    home_abbr: str,
) -> tuple[int, int]:
    """
    Parse the linescore innings array and upsert one row per inning.
    Returns (inserted, skipped).
    """
    inserted = skipped = 0
    for inn in linescore.get("innings") or []:
        try:
            num = inn.get("num")
            if num is None:
                skipped += 1
                continue
            away_runs = (inn.get("away") or {}).get("runs") or 0
            home_runs = (inn.get("home") or {}).get("runs") or 0
            conn.execute(
                """
                INSERT INTO mlb_inning_scores
                  (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(game_pk, inning) DO UPDATE SET
                  away_runs = excluded.away_runs,
                  home_runs = excluded.home_runs
                """,
                (game_pk, num, away_abbr, home_abbr, away_runs, home_runs, _now()),
            )
            inserted += 1
        except Exception as exc:
            log.warning("inning score error (game_pk=%d inning=%s): %s", game_pk, inn.get("num"), exc)
            skipped += 1
    return inserted, skipped


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_and_store_schedule(
    date_str: str,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Fetch the MLB schedule for date_str, write raw JSONL, upsert mlb_games.

    Returns:
      {fetched, date, games_seen, games_inserted_or_updated, errors}
    """
    _own = conn is None
    if _own:
        conn = _open_conn()

    _ensure_probable_pitcher_cols(conn)

    summary: dict = {
        "fetched": False,
        "date": date_str,
        "games_seen": 0,
        "games_inserted_or_updated": 0,
        "errors": [],
    }

    try:
        data = stats_api.fetch_schedule(date_str)
        if data is None:
            summary["errors"].append("schedule: fetch returned None")
            return summary

        summary["fetched"] = True
        log_response("schedule", data, date_str=date_str)

        for date_entry in data.get("dates") or []:
            for raw_game in date_entry.get("games") or []:
                try:
                    teams = raw_game.get("teams") or {}
                    away_t = (teams.get("away") or {}).get("team") or {}
                    home_t = (teams.get("home") or {}).get("team") or {}
                    away_abbr = _norm(away_t.get("abbreviation") or away_t.get("name", "???"))
                    home_abbr = _norm(home_t.get("abbreviation") or home_t.get("name", "???"))
                    game_date = (
                        raw_game.get("officialDate")
                        or (raw_game.get("gameDate") or "")[:10]
                        or date_entry.get("date", date_str)
                    )
                    status = (raw_game.get("status") or {}).get(
                        "abstractGameState", "Scheduled"
                    )
                    is_final = 1 if status == "Final" else 0
                    # Schedule response includes scores in teams.away.score / teams.home.score
                    away_score = (teams.get("away") or {}).get("score") if is_final else None
                    home_score = (teams.get("home") or {}).get("score") if is_final else None
                    final_total = (
                        (away_score + home_score)
                        if is_final and away_score is not None and home_score is not None
                        else None
                    )
                    # Extract actual UTC start time from gameDate (e.g. "2026-06-15T23:05:00Z")
                    raw_game_date = raw_game.get("gameDate") or ""
                    game_start_time_utc = raw_game_date[:16] if len(raw_game_date) >= 16 else None

                    away_pp = (teams.get("away") or {}).get("probablePitcher") or {}
                    home_pp = (teams.get("home") or {}).get("probablePitcher") or {}

                    _upsert_game(conn, {
                        "game_pk":              raw_game["gamePk"],
                        "game_date":            game_date,
                        "away_team":            away_t.get("name") or away_abbr,
                        "home_team":            home_t.get("name") or home_abbr,
                        "away_abbr":            away_abbr,
                        "home_abbr":            home_abbr,
                        "game_id":              f"{away_abbr}@{home_abbr}",
                        "status":               status,
                        "is_final":             is_final,
                        "final_away_score":     away_score,
                        "final_home_score":     home_score,
                        "final_total":          final_total,
                        "game_start_time_utc":  game_start_time_utc,
                        "away_probable_pitcher_id":   away_pp.get("id"),
                        "away_probable_pitcher_name": away_pp.get("fullName"),
                        "home_probable_pitcher_id":   home_pp.get("id"),
                        "home_probable_pitcher_name": home_pp.get("fullName"),
                    })
                    summary["games_seen"] += 1
                    summary["games_inserted_or_updated"] += 1
                except Exception as exc:
                    log.warning("schedule game parse error: %s", exc)
                    summary["errors"].append(f"game parse: {exc}")

        conn.commit()

    except Exception as exc:
        log.error("fetch_and_store_schedule error: %s", exc)
        summary["errors"].append(str(exc))
    finally:
        if _own:
            conn.close()

    return summary


def fetch_and_store_game(
    game_pk: int,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Fetch all 4 endpoints for game_pk, log raw JSONL, upsert mlb_games,
    insert mlb_game_states snapshot, upsert mlb_play_events.

    Returns:
      {fetched, game_pk, date, endpoints_logged, game_upserted,
       game_state_inserted, plays_inserted, plays_skipped, errors}
    """
    _own = conn is None
    if _own:
        conn = _open_conn()

    summary: dict = {
        "fetched": False,
        "game_pk": game_pk,
        "date": None,
        "endpoints_logged": [],
        "game_upserted": False,
        "game_state_inserted": False,
        "plays_inserted": 0,
        "plays_skipped": 0,
        "innings_inserted": 0,
        "innings_skipped": 0,
        "errors": [],
    }

    away_abbr = home_abbr = None  # resolved from game feed; needed for inning upsert

    try:
        # ── 1. Game feed (primary source for game/state data) ──────────────
        feed = stats_api.fetch_game_feed(game_pk)
        if feed is None:
            summary["errors"].append("game_feed: fetch returned None")
        else:
            gd = feed.get("gameData") or {}
            game_date = (gd.get("datetime") or {}).get("officialDate") or _now()[:10]
            summary["date"] = game_date
            summary["fetched"] = True

            log_response("game_feed", feed, date_str=game_date, game_pk=game_pk)
            summary["endpoints_logged"].append("game_feed")

            away = gd.get("teams", {}).get("away") or {}
            home = gd.get("teams", {}).get("home") or {}
            away_abbr = _norm(away.get("abbreviation") or away.get("fileCode", "???"))
            home_abbr = _norm(home.get("abbreviation") or home.get("fileCode", "???"))
            status = (gd.get("status") or {}).get("abstractGameState", "Unknown")
            is_final = 1 if status == "Final" else 0

            ls_teams = ((feed.get("liveData") or {}).get("linescore") or {}).get("teams") or {}
            if is_final:
                away_runs = (ls_teams.get("away") or {}).get("runs")
                home_runs = (ls_teams.get("home") or {}).get("runs")
                final_total = (
                    (away_runs + home_runs)
                    if away_runs is not None and home_runs is not None
                    else None
                )
            else:
                away_runs = home_runs = final_total = None

            _upsert_game(conn, {
                "game_pk":          game_pk,
                "game_date":        game_date,
                "away_team":        away.get("name") or away_abbr,
                "home_team":        home.get("name") or home_abbr,
                "away_abbr":        away_abbr,
                "home_abbr":        home_abbr,
                "game_id":          f"{away_abbr}@{home_abbr}",
                "status":           status,
                "is_final":         is_final,
                "final_away_score": away_runs,
                "final_home_score": home_runs,
                "final_total":      final_total,
            })
            summary["game_upserted"] = True

            try:
                _insert_game_state(conn, game_pk, feed)
                summary["game_state_inserted"] = True
            except Exception as exc:
                log.warning("game_state insert error (game_pk=%d): %s", game_pk, exc)
                summary["errors"].append(f"game_state: {exc}")

        # ── 2. Linescore ────────────────────────────────────────────────────
        linescore = stats_api.fetch_linescore(game_pk)
        date_for_log = summary["date"] or _now()[:10]
        if linescore is not None:
            log_response("linescore", linescore, date_str=date_for_log, game_pk=game_pk)
            summary["endpoints_logged"].append("linescore")
            if away_abbr and home_abbr:
                ins, skip = _upsert_inning_scores(conn, game_pk, linescore, away_abbr, home_abbr)
                summary["innings_inserted"] = ins
                summary["innings_skipped"] = skip
        else:
            summary["errors"].append("linescore: fetch returned None")

        # ── 3. Play-by-play ─────────────────────────────────────────────────
        pbp = stats_api.fetch_play_by_play(game_pk)
        if pbp is not None:
            log_response("play_by_play", pbp, date_str=date_for_log, game_pk=game_pk)
            summary["endpoints_logged"].append("play_by_play")
            inserted, skipped = _upsert_plays(conn, game_pk, pbp)
            summary["plays_inserted"] = inserted
            summary["plays_skipped"] = skipped
        else:
            summary["errors"].append("play_by_play: fetch returned None")

        # ── 4. Boxscore ─────────────────────────────────────────────────────
        boxscore = stats_api.fetch_boxscore(game_pk)
        if boxscore is not None:
            log_response("boxscore", boxscore, date_str=date_for_log, game_pk=game_pk)
            summary["endpoints_logged"].append("boxscore")
        else:
            summary["errors"].append("boxscore: fetch returned None")

        conn.commit()

    except Exception as exc:
        log.error("fetch_and_store_game error (game_pk=%d): %s", game_pk, exc)
        summary["errors"].append(str(exc))
    finally:
        if _own:
            conn.close()

    return summary
