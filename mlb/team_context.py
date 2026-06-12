"""
mlb/team_context.py — Season-to-date team ratings from stored MLB data.

Ratings are 0-100, calibrated so an average MLB team scores ~50.
All formulas are explicit and tweak-friendly; no ML involved.

Rating calibration (2026 MLB estimates):
  _LEAGUE_AVG_RPG  = 4.5   runs/game (full game, per team)
  _LEAGUE_AVG_F5   = 2.2   runs in innings 1-5 (per team)
  _LEAGUE_AVG_LATE = 2.3   runs in innings 6+ (per team)
  _SCALE_RPG       = 10.0  rating points per 1 RPG above/below avg
  _SCALE_F5        = 12.0  more sensitive for inning-level splits
"""
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

from db.schema import init_db

log = logging.getLogger(__name__)

# ── League average calibration ─────────────────────────────────────────────────
# Adjust these constants as the season progresses or if MLB run environment shifts.

_LEAGUE_AVG_RPG  = 4.5
_LEAGUE_AVG_F5   = 2.2
_LEAGUE_AVG_LATE = 2.3
_SCALE_RPG       = 10.0   # rating points per 1 RPG delta
_SCALE_F5        = 12.0   # more sensitive for inning-level splits


# ── Internal helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat()


def _open_conn() -> sqlite3.Connection:
    return init_db(os.environ.get("DB_PATH", "kalshi_mlb.db"))


def _avg(lst: list) -> Optional[float]:
    return round(sum(lst) / len(lst), 3) if lst else None


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _get_f5_scores(game_pk: int, conn: sqlite3.Connection) -> Optional[tuple[int, int]]:
    """
    Returns (away_f5_runs, home_f5_runs) using the last at-bat in inning ≤ 5.
    The cumulative away_score/home_score at that at-bat equals F5 totals.
    Returns None if no play-by-play data is stored for this game.
    """
    row = conn.execute(
        """
        SELECT away_score, home_score FROM mlb_play_events
        WHERE game_pk = ? AND inning <= 5
        ORDER BY at_bat_index DESC LIMIT 1
        """,
        (game_pk,),
    ).fetchone()
    if row is None:
        return None
    return (row["away_score"] or 0, row["home_score"] or 0)


# ── Rating formulas ────────────────────────────────────────────────────────────

def _rate_offense(rpg: Optional[float], recent_7: Optional[float]) -> float:
    """
    Blend season RPG (40%) with last-7 RPG (60%) to weight recent form.
    Falls back to season RPG alone when fewer than 7 games have been played.
    """
    if rpg is None:
        return 50.0
    eff = (0.6 * recent_7 + 0.4 * rpg) if recent_7 is not None else rpg
    return round(_clamp(50.0 + (eff - _LEAGUE_AVG_RPG) * _SCALE_RPG), 1)


def _rate_defense(ra_pg: Optional[float], recent_ra_7: Optional[float]) -> float:
    """Lower runs allowed → higher rating (inverted scale)."""
    if ra_pg is None:
        return 50.0
    eff = (0.6 * recent_ra_7 + 0.4 * ra_pg) if recent_ra_7 is not None else ra_pg
    return round(_clamp(50.0 + (_LEAGUE_AVG_RPG - eff) * _SCALE_RPG), 1)


def _rate_f5_offense(f5_rpg: Optional[float]) -> float:
    if f5_rpg is None:
        return 50.0
    return round(_clamp(50.0 + (f5_rpg - _LEAGUE_AVG_F5) * _SCALE_F5), 1)


def _rate_f5_pitching_risk(f5_ra_pg: Optional[float]) -> float:
    """Higher F5 runs allowed → higher risk score."""
    if f5_ra_pg is None:
        return 50.0
    return round(_clamp(50.0 + (f5_ra_pg - _LEAGUE_AVG_F5) * _SCALE_F5), 1)


def _rate_bullpen_risk(late_ra_pg: Optional[float]) -> float:
    """Higher late-inning runs allowed → higher bullpen risk score."""
    if late_ra_pg is None:
        return 50.0
    return round(_clamp(50.0 + (late_ra_pg - _LEAGUE_AVG_LATE) * _SCALE_F5), 1)


def _rate_comeback_scoring(late_rpg: Optional[float], rpg: Optional[float]) -> float:
    """
    Teams that score late AND score overall are dangerous when trailing.
    Composite: 60% late-inning scoring + 40% overall offense.
    """
    if late_rpg is None or rpg is None:
        return 50.0
    composite = 0.6 * late_rpg + 0.4 * rpg
    avg_composite = 0.6 * _LEAGUE_AVG_LATE + 0.4 * _LEAGUE_AVG_RPG
    return round(_clamp(50.0 + (composite - avg_composite) * _SCALE_F5), 1)


def _overall_score(offense: float, defense: float, f5_offense: float) -> float:
    """40% offense + 40% defense/pitching + 20% F5 offense."""
    return round(0.4 * offense + 0.4 * defense + 0.2 * f5_offense, 1)


# ── Core computation ───────────────────────────────────────────────────────────

def compute_team_context(
    team_abbr: str,
    season: str,
    conn: sqlite3.Connection,
) -> Optional[dict]:
    """
    Compute all season metrics and ratings for team_abbr in the given season.
    Returns None if no final games are found for this team.
    """
    like = f"{season}%"

    away_rows = conn.execute(
        """
        SELECT game_pk, game_date,
               final_away_score AS scored, final_home_score AS allowed
        FROM mlb_games
        WHERE away_abbr = ? AND is_final = 1 AND game_date LIKE ?
        ORDER BY game_date ASC
        """,
        (team_abbr, like),
    ).fetchall()

    home_rows = conn.execute(
        """
        SELECT game_pk, game_date,
               final_home_score AS scored, final_away_score AS allowed
        FROM mlb_games
        WHERE home_abbr = ? AND is_final = 1 AND game_date LIKE ?
        ORDER BY game_date ASC
        """,
        (team_abbr, like),
    ).fetchall()

    name_row = conn.execute(
        """
        SELECT CASE WHEN away_abbr = ? THEN away_team ELSE home_team END AS team_name
        FROM mlb_games
        WHERE (away_abbr = ? OR home_abbr = ?) AND is_final = 1 LIMIT 1
        """,
        (team_abbr, team_abbr, team_abbr),
    ).fetchone()
    team_name = name_row["team_name"] if name_row else team_abbr

    # Merge away and home game records, sorted chronologically, drop rows with missing scores
    all_games: list[tuple[dict, str]] = sorted(
        [(dict(r), "away") for r in away_rows] +
        [(dict(r), "home") for r in home_rows],
        key=lambda x: x[0]["game_date"],
    )
    all_games = [
        (g, side) for g, side in all_games
        if g.get("scored") is not None and g.get("allowed") is not None
    ]

    if not all_games:
        return None

    # ── Season stats ──────────────────────────────────────────────────────────
    scored_list  = [g["scored"]  for g, _ in all_games]
    allowed_list = [g["allowed"] for g, _ in all_games]
    home_scored  = [g["scored"]  for g, side in all_games if side == "home"]
    away_scored  = [g["scored"]  for g, side in all_games if side == "away"]

    rpg      = _avg(scored_list)
    ra_pg    = _avg(allowed_list)
    home_rpg = _avg(home_scored)
    away_rpg = _avg(away_scored)

    # ── Last-7 stats ──────────────────────────────────────────────────────────
    last_7       = all_games[-7:]
    recent_rpg_7 = _avg([g["scored"]  for g, _ in last_7])
    recent_ra_7  = _avg([g["allowed"] for g, _ in last_7])

    # ── F5 and late stats (requires mlb_play_events rows for the game) ────────
    f5_scored_list    = []
    f5_allowed_list   = []
    late_scored_list  = []
    late_allowed_list = []

    for game, side in all_games:
        f5 = _get_f5_scores(game["game_pk"], conn)
        if f5 is None:
            continue

        f5_away, f5_home = f5
        if side == "away":
            f5_team = f5_away
            f5_opp  = f5_home
            late_t  = (game["scored"]  or 0) - f5_away
            late_o  = (game["allowed"] or 0) - f5_home
        else:
            f5_team = f5_home
            f5_opp  = f5_away
            late_t  = (game["scored"]  or 0) - f5_home
            late_o  = (game["allowed"] or 0) - f5_away

        # Negative late runs = bad or partial play data; skip this game for F5
        if late_t < 0 or late_o < 0:
            continue

        f5_scored_list.append(f5_team)
        f5_allowed_list.append(f5_opp)
        late_scored_list.append(late_t)
        late_allowed_list.append(late_o)

    f5_rpg     = _avg(f5_scored_list)
    f5_ra_pg   = _avg(f5_allowed_list)
    late_rpg   = _avg(late_scored_list)
    late_ra_pg = _avg(late_allowed_list)

    # ── Ratings ───────────────────────────────────────────────────────────────
    offense_r  = _rate_offense(rpg, recent_rpg_7)
    defense_r  = _rate_defense(ra_pg, recent_ra_7)
    f5_off_r   = _rate_f5_offense(f5_rpg)
    f5_pit_r   = _rate_f5_pitching_risk(f5_ra_pg)
    bp_risk_r  = _rate_bullpen_risk(late_ra_pg)
    comeback_r = _rate_comeback_scoring(late_rpg, rpg)
    overall_r  = _overall_score(offense_r, defense_r, f5_off_r)

    return {
        "team_abbr":                      team_abbr,
        "team_name":                      team_name,
        "season":                         season,
        "games_played":                   len(all_games),
        "runs_per_game":                  rpg,
        "runs_allowed_per_game":          ra_pg,
        "home_runs_per_game":             home_rpg,
        "away_runs_per_game":             away_rpg,
        "recent_runs_per_game_7":         recent_rpg_7,
        "recent_runs_allowed_per_game_7": recent_ra_7,
        "f5_runs_per_game":               f5_rpg,
        "f5_runs_allowed_per_game":       f5_ra_pg,
        "late_runs_per_game":             late_rpg,
        "late_runs_allowed_per_game":     late_ra_pg,
        "offense_rating":                 offense_r,
        "defense_pitching_rating":        defense_r,
        "f5_offense_rating":              f5_off_r,
        "f5_pitching_risk_rating":        f5_pit_r,
        "bullpen_risk_rating":            bp_risk_r,
        "late_game_risk_rating":          bp_risk_r,  # bullpen risk and late risk are the same metric
        "comeback_scoring_rating":        comeback_r,
        "overall_context_score":          overall_r,
        "sample_size":                    len(all_games),
        "f5_sample_size":                 len(f5_scored_list),
        "last_updated":                   _now(),
    }


def _upsert_team_context(conn: sqlite3.Connection, ctx: dict) -> None:
    conn.execute(
        """
        INSERT INTO mlb_team_context
          (team_abbr, team_name, season, games_played,
           runs_per_game, runs_allowed_per_game,
           home_runs_per_game, away_runs_per_game,
           recent_runs_per_game_7, recent_runs_allowed_per_game_7,
           f5_runs_per_game, f5_runs_allowed_per_game,
           late_runs_per_game, late_runs_allowed_per_game,
           offense_rating, defense_pitching_rating,
           f5_offense_rating, f5_pitching_risk_rating,
           bullpen_risk_rating, late_game_risk_rating,
           comeback_scoring_rating, overall_context_score,
           sample_size, f5_sample_size, last_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(team_abbr, season) DO UPDATE SET
          team_name                       = excluded.team_name,
          games_played                    = excluded.games_played,
          runs_per_game                   = excluded.runs_per_game,
          runs_allowed_per_game           = excluded.runs_allowed_per_game,
          home_runs_per_game              = excluded.home_runs_per_game,
          away_runs_per_game              = excluded.away_runs_per_game,
          recent_runs_per_game_7          = excluded.recent_runs_per_game_7,
          recent_runs_allowed_per_game_7  = excluded.recent_runs_allowed_per_game_7,
          f5_runs_per_game                = excluded.f5_runs_per_game,
          f5_runs_allowed_per_game        = excluded.f5_runs_allowed_per_game,
          late_runs_per_game              = excluded.late_runs_per_game,
          late_runs_allowed_per_game      = excluded.late_runs_allowed_per_game,
          offense_rating                  = excluded.offense_rating,
          defense_pitching_rating         = excluded.defense_pitching_rating,
          f5_offense_rating               = excluded.f5_offense_rating,
          f5_pitching_risk_rating         = excluded.f5_pitching_risk_rating,
          bullpen_risk_rating             = excluded.bullpen_risk_rating,
          late_game_risk_rating           = excluded.late_game_risk_rating,
          comeback_scoring_rating         = excluded.comeback_scoring_rating,
          overall_context_score           = excluded.overall_context_score,
          sample_size                     = excluded.sample_size,
          f5_sample_size                  = excluded.f5_sample_size,
          last_updated                    = excluded.last_updated
        """,
        (
            ctx["team_abbr"], ctx["team_name"], ctx["season"],
            ctx["games_played"],
            ctx["runs_per_game"],        ctx["runs_allowed_per_game"],
            ctx["home_runs_per_game"],   ctx["away_runs_per_game"],
            ctx["recent_runs_per_game_7"], ctx["recent_runs_allowed_per_game_7"],
            ctx["f5_runs_per_game"],     ctx["f5_runs_allowed_per_game"],
            ctx["late_runs_per_game"],   ctx["late_runs_allowed_per_game"],
            ctx["offense_rating"],       ctx["defense_pitching_rating"],
            ctx["f5_offense_rating"],    ctx["f5_pitching_risk_rating"],
            ctx["bullpen_risk_rating"],  ctx["late_game_risk_rating"],
            ctx["comeback_scoring_rating"], ctx["overall_context_score"],
            ctx["sample_size"],          ctx["f5_sample_size"],
            ctx["last_updated"],
        ),
    )


def refresh_team_context(
    season: str = "2026",
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Compute and upsert context for every team with final games in the season.
    Returns {refreshed, team_count, teams, errors}.
    """
    _own = conn is None
    if _own:
        conn = _open_conn()

    summary: dict = {"refreshed": True, "team_count": 0, "teams": [], "errors": []}

    try:
        rows = conn.execute(
            """
            SELECT DISTINCT abbr FROM (
                SELECT away_abbr AS abbr FROM mlb_games
                WHERE is_final = 1 AND game_date LIKE ?
                UNION
                SELECT home_abbr AS abbr FROM mlb_games
                WHERE is_final = 1 AND game_date LIKE ?
            ) ORDER BY abbr
            """,
            (f"{season}%", f"{season}%"),
        ).fetchall()

        for row in rows:
            abbr = row[0]
            try:
                ctx = compute_team_context(abbr, season, conn)
                if ctx is not None:
                    _upsert_team_context(conn, ctx)
                    summary["teams"].append(abbr)
                    summary["team_count"] += 1
            except Exception as exc:
                log.error("team_context error team=%s: %s", abbr, exc)
                summary["errors"].append(f"{abbr}: {exc}")

        conn.commit()

    except Exception as exc:
        log.error("refresh_team_context error: %s", exc)
        summary["refreshed"] = False
        summary["errors"].append(str(exc))
    finally:
        if _own:
            conn.close()

    return summary


def get_all_team_contexts(season: str, conn: sqlite3.Connection) -> list[dict]:
    """Fetch all rows for a season, sorted by overall_context_score DESC."""
    rows = conn.execute(
        "SELECT * FROM mlb_team_context WHERE season = ? ORDER BY overall_context_score DESC",
        (season,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_team_context(
    team_abbr: str,
    season: str,
    conn: sqlite3.Connection,
) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM mlb_team_context WHERE team_abbr = ? AND season = ?",
        (team_abbr, season),
    ).fetchone()
    return dict(row) if row else None
