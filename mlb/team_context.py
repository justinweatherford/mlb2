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


def _get_inning_totals(game_pk: int, conn: sqlite3.Connection) -> Optional[dict]:
    """
    Returns {away_f5, home_f5, away_late, home_late} from mlb_inning_scores,
    or None if no inning data is stored for this game.
    F5 = innings 1-5.  Late = innings 6+.
    """
    rows = conn.execute(
        "SELECT inning, away_runs, home_runs FROM mlb_inning_scores "
        "WHERE game_pk = ? ORDER BY inning",
        (game_pk,),
    ).fetchall()
    if not rows:
        return None
    return {
        "away_f5":   sum((r["away_runs"] or 0) for r in rows if r["inning"] <= 5),
        "home_f5":   sum((r["home_runs"] or 0) for r in rows if r["inning"] <= 5),
        "away_late": sum((r["away_runs"] or 0) for r in rows if r["inning"] >= 6),
        "home_late": sum((r["home_runs"] or 0) for r in rows if r["inning"] >= 6),
    }


def _context_confidence(games_played: int) -> str:
    """low < 10 games, medium 10-30, high 31+."""
    if games_played >= 31:
        return "high"
    if games_played >= 10:
        return "medium"
    return "low"


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


def _rate_scoring_form(rpg: Optional[float]) -> Optional[float]:
    """Simple single-window form rating: 50 + (rpg - league_avg) × scale, clamped."""
    if rpg is None:
        return None
    return round(_clamp(50.0 + (rpg - _LEAGUE_AVG_RPG) * _SCALE_RPG), 1)


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
          AND final_away_score IS NOT NULL
          AND final_home_score IS NOT NULL
          AND final_total IS NOT NULL
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
          AND final_away_score IS NOT NULL
          AND final_home_score IS NOT NULL
          AND final_total IS NOT NULL
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

    # ── Rolling form windows ──────────────────────────────────────────────────
    l1_rpg  = _avg([g["scored"] for g, _ in all_games[-1:]])
    l5_rpg  = _avg([g["scored"] for g, _ in all_games[-5:]])
    l10_rpg = _avg([g["scored"] for g, _ in all_games[-10:]])

    # ── F5 and late stats (requires mlb_inning_scores rows for the game) ────────
    f5_scored_list    = []
    f5_allowed_list   = []
    late_scored_list  = []
    late_allowed_list = []

    for game, side in all_games:
        totals = _get_inning_totals(game["game_pk"], conn)
        if totals is None:
            continue

        if side == "away":
            f5_scored_list.append(totals["away_f5"])
            f5_allowed_list.append(totals["home_f5"])
            late_scored_list.append(totals["away_late"])
            late_allowed_list.append(totals["home_late"])
        else:
            f5_scored_list.append(totals["home_f5"])
            f5_allowed_list.append(totals["away_f5"])
            late_scored_list.append(totals["home_late"])
            late_allowed_list.append(totals["away_late"])

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

    l1_rating  = _rate_scoring_form(l1_rpg)
    l5_rating  = _rate_scoring_form(l5_rpg)
    l10_rating = _rate_scoring_form(l10_rpg)

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
        "l1_rpg":                         l1_rpg,
        "l5_rpg":                         l5_rpg,
        "l10_rpg":                        l10_rpg,
        "l1_scoring_form_rating":         l1_rating,
        "l5_scoring_form_rating":         l5_rating,
        "l10_scoring_form_rating":        l10_rating,
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
        "context_confidence":             _context_confidence(len(all_games)),
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
           l1_rpg, l5_rpg, l10_rpg,
           l1_scoring_form_rating, l5_scoring_form_rating, l10_scoring_form_rating,
           f5_runs_per_game, f5_runs_allowed_per_game,
           late_runs_per_game, late_runs_allowed_per_game,
           offense_rating, defense_pitching_rating,
           f5_offense_rating, f5_pitching_risk_rating,
           bullpen_risk_rating, late_game_risk_rating,
           comeback_scoring_rating, overall_context_score,
           sample_size, f5_sample_size, context_confidence, last_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(team_abbr, season) DO UPDATE SET
          team_name                       = excluded.team_name,
          games_played                    = excluded.games_played,
          runs_per_game                   = excluded.runs_per_game,
          runs_allowed_per_game           = excluded.runs_allowed_per_game,
          home_runs_per_game              = excluded.home_runs_per_game,
          away_runs_per_game              = excluded.away_runs_per_game,
          recent_runs_per_game_7          = excluded.recent_runs_per_game_7,
          recent_runs_allowed_per_game_7  = excluded.recent_runs_allowed_per_game_7,
          l1_rpg                          = excluded.l1_rpg,
          l5_rpg                          = excluded.l5_rpg,
          l10_rpg                         = excluded.l10_rpg,
          l1_scoring_form_rating          = excluded.l1_scoring_form_rating,
          l5_scoring_form_rating          = excluded.l5_scoring_form_rating,
          l10_scoring_form_rating         = excluded.l10_scoring_form_rating,
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
          context_confidence              = excluded.context_confidence,
          last_updated                    = excluded.last_updated
        """,
        (
            ctx["team_abbr"], ctx["team_name"], ctx["season"],
            ctx["games_played"],
            ctx["runs_per_game"],        ctx["runs_allowed_per_game"],
            ctx["home_runs_per_game"],   ctx["away_runs_per_game"],
            ctx["recent_runs_per_game_7"], ctx["recent_runs_allowed_per_game_7"],
            ctx["l1_rpg"],               ctx["l5_rpg"],               ctx["l10_rpg"],
            ctx["l1_scoring_form_rating"], ctx["l5_scoring_form_rating"], ctx["l10_scoring_form_rating"],
            ctx["f5_runs_per_game"],     ctx["f5_runs_allowed_per_game"],
            ctx["late_runs_per_game"],   ctx["late_runs_allowed_per_game"],
            ctx["offense_rating"],       ctx["defense_pitching_rating"],
            ctx["f5_offense_rating"],    ctx["f5_pitching_risk_rating"],
            ctx["bullpen_risk_rating"],  ctx["late_game_risk_rating"],
            ctx["comeback_scoring_rating"], ctx["overall_context_score"],
            ctx["sample_size"],          ctx["f5_sample_size"],
            ctx["context_confidence"],   ctx["last_updated"],
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


# ── Formula transparency ───────────────────────────────────────────────────────

def _rating_detail(
    label: str,
    higher_is_better: Optional[bool],
    formula: str,
    inputs: dict,
    blended_input: Optional[float],
    blend_formula: Optional[str],
    league_avg: float,
    scale: float,
    raw_result: Optional[float],
    final: float,
    is_default_50: bool,
    note: Optional[str] = None,
) -> dict:
    return {
        "label": label,
        "higher_is_better": higher_is_better,
        "formula": formula,
        "inputs": inputs,
        "blended_input": blended_input,
        "blend_formula": blend_formula,
        "league_avg": league_avg,
        "scale": scale,
        "raw_result": raw_result,
        "final": final,
        "is_default_50": is_default_50,
        "note": note,
    }


def compute_team_context_debug(
    team_abbr: str,
    season: str,
    conn: sqlite3.Connection,
) -> Optional[dict]:
    """
    Return full formula-by-formula breakdown for stored team context.
    Does not update the database — audit/debugging only.
    """
    stored = get_team_context(team_abbr, season, conn)
    if stored is None:
        return None

    rpg        = stored.get("runs_per_game")
    ra_pg      = stored.get("runs_allowed_per_game")
    rec_7      = stored.get("recent_runs_per_game_7")
    rec_ra_7   = stored.get("recent_runs_allowed_per_game_7")
    f5_rpg     = stored.get("f5_runs_per_game")
    f5_ra_pg   = stored.get("f5_runs_allowed_per_game")
    late_rpg   = stored.get("late_runs_per_game")
    late_ra_pg = stored.get("late_runs_allowed_per_game")
    f5_n       = stored.get("f5_sample_size", 0)
    l1_rpg_s   = stored.get("l1_rpg")
    l5_rpg_s   = stored.get("l5_rpg")
    l10_rpg_s  = stored.get("l10_rpg")

    _rolling_note = "L1/L5/L10 shown for comparison; scoring form formula uses 0.6×L7 + 0.4×season"

    # ── Offense ──────────────────────────────────────────────────────────────
    if rpg is None:
        off_d = _rating_detail(
            "Scoring Form Rating", True,
            f"50 + (eff_rpg - {_LEAGUE_AVG_RPG}) × {_SCALE_RPG}",
            {"season_rpg": None, "recent_7_rpg": None,
             "l1_rpg": l1_rpg_s, "l5_rpg": l5_rpg_s, "l10_rpg": l10_rpg_s},
            None, None, _LEAGUE_AVG_RPG, _SCALE_RPG,
            None, 50.0, True, "No season RPG data",
        )
    else:
        if rec_7 is not None:
            eff = round(0.6 * rec_7 + 0.4 * rpg, 4)
            bf = f"0.6×recent7({rec_7:.3f}) + 0.4×season({rpg:.3f}) = {eff:.4f}"
        else:
            eff = rpg
            bf = "season only (fewer than 7 games)"
        raw = 50.0 + (eff - _LEAGUE_AVG_RPG) * _SCALE_RPG
        off_d = _rating_detail(
            "Scoring Form Rating", True,
            f"50 + (eff_rpg - {_LEAGUE_AVG_RPG}) × {_SCALE_RPG}",
            {"season_rpg": rpg, "recent_7_rpg": rec_7,
             "l1_rpg": l1_rpg_s, "l5_rpg": l5_rpg_s, "l10_rpg": l10_rpg_s},
            eff, bf, _LEAGUE_AVG_RPG, _SCALE_RPG,
            round(raw, 3), round(_clamp(raw), 1), False,
            _rolling_note,
        )

    # ── Defense ──────────────────────────────────────────────────────────────
    if ra_pg is None:
        def_d = _rating_detail(
            "Defense/Pitching Rating", True,
            f"50 + ({_LEAGUE_AVG_RPG} - eff_ra) × {_SCALE_RPG}  [lower RA = better]",
            {"season_ra_pg": None, "recent_7_ra_pg": None},
            None, None, _LEAGUE_AVG_RPG, _SCALE_RPG,
            None, 50.0, True, "No season RA/G data",
        )
    else:
        if rec_ra_7 is not None:
            eff = round(0.6 * rec_ra_7 + 0.4 * ra_pg, 4)
            bf = f"0.6×recent_ra7({rec_ra_7:.3f}) + 0.4×season({ra_pg:.3f}) = {eff:.4f}"
        else:
            eff = ra_pg
            bf = "season only (fewer than 7 games)"
        raw = 50.0 + (_LEAGUE_AVG_RPG - eff) * _SCALE_RPG
        def_d = _rating_detail(
            "Defense/Pitching Rating", True,
            f"50 + ({_LEAGUE_AVG_RPG} - eff_ra) × {_SCALE_RPG}  [lower RA = better]",
            {"season_ra_pg": ra_pg, "recent_7_ra_pg": rec_ra_7},
            eff, bf, _LEAGUE_AVG_RPG, _SCALE_RPG,
            round(raw, 3), round(_clamp(raw), 1), False,
        )

    # ── F5 Offense ───────────────────────────────────────────────────────────
    if f5_rpg is None:
        f5o_d = _rating_detail(
            "F5 Offense Rating", True,
            f"50 + (f5_rpg - {_LEAGUE_AVG_F5}) × {_SCALE_F5}",
            {"f5_rpg": None, "f5_sample_size": f5_n},
            None, None, _LEAGUE_AVG_F5, _SCALE_F5,
            None, 50.0, True, f"No inning data (f5_sample_size={f5_n})",
        )
    else:
        raw = 50.0 + (f5_rpg - _LEAGUE_AVG_F5) * _SCALE_F5
        f5o_d = _rating_detail(
            "F5 Offense Rating", True,
            f"50 + (f5_rpg - {_LEAGUE_AVG_F5}) × {_SCALE_F5}",
            {"f5_rpg": f5_rpg, "f5_sample_size": f5_n},
            f5_rpg, "no blending — season only", _LEAGUE_AVG_F5, _SCALE_F5,
            round(raw, 3), round(_clamp(raw), 1), False,
        )

    # ── F5 Pitching Risk ─────────────────────────────────────────────────────
    if f5_ra_pg is None:
        f5p_d = _rating_detail(
            "F5 Pitching Risk", False,
            f"50 + (f5_ra_pg - {_LEAGUE_AVG_F5}) × {_SCALE_F5}  [higher = more risk]",
            {"f5_ra_pg": None, "f5_sample_size": f5_n},
            None, None, _LEAGUE_AVG_F5, _SCALE_F5,
            None, 50.0, True,
            f"No inning data (f5_sample_size={f5_n}). "
            "CAUTION: higher score = MORE pitching risk — green in UI means bad.",
        )
    else:
        raw = 50.0 + (f5_ra_pg - _LEAGUE_AVG_F5) * _SCALE_F5
        f5p_d = _rating_detail(
            "F5 Pitching Risk", False,
            f"50 + (f5_ra_pg - {_LEAGUE_AVG_F5}) × {_SCALE_F5}  [higher = more risk]",
            {"f5_ra_pg": f5_ra_pg, "f5_sample_size": f5_n},
            f5_ra_pg, "no blending — season only", _LEAGUE_AVG_F5, _SCALE_F5,
            round(raw, 3), round(_clamp(raw), 1), False,
            "CAUTION: higher score = MORE pitching risk — green in UI means bad.",
        )

    # ── Bullpen Risk ─────────────────────────────────────────────────────────
    if late_ra_pg is None:
        bp_d = _rating_detail(
            "Bullpen Risk Rating", False,
            f"50 + (late_ra_pg - {_LEAGUE_AVG_LATE}) × {_SCALE_F5}  [higher = more risk]",
            {"late_ra_pg": None, "f5_sample_size": f5_n},
            None, None, _LEAGUE_AVG_LATE, _SCALE_F5,
            None, 50.0, True,
            f"No inning data (f5_sample_size={f5_n}). "
            "CAUTION: higher score = MORE bullpen risk — green in UI means bad.",
        )
    else:
        raw = 50.0 + (late_ra_pg - _LEAGUE_AVG_LATE) * _SCALE_F5
        bp_d = _rating_detail(
            "Bullpen Risk Rating", False,
            f"50 + (late_ra_pg - {_LEAGUE_AVG_LATE}) × {_SCALE_F5}  [higher = more risk]",
            {"late_ra_pg": late_ra_pg, "f5_sample_size": f5_n},
            late_ra_pg, "no blending — season only", _LEAGUE_AVG_LATE, _SCALE_F5,
            round(raw, 3), round(_clamp(raw), 1), False,
            "CAUTION: higher score = MORE bullpen risk — green in UI means bad.",
        )

    # ── Comeback ─────────────────────────────────────────────────────────────
    if late_rpg is None or rpg is None:
        cmb_d = _rating_detail(
            "Comeback Scoring Rating", True,
            f"50 + (0.6×late_rpg + 0.4×rpg - {0.6*_LEAGUE_AVG_LATE+0.4*_LEAGUE_AVG_RPG:.3f}) × {_SCALE_F5}",
            {"late_rpg": late_rpg, "season_rpg": rpg},
            None, None,
            round(0.6 * _LEAGUE_AVG_LATE + 0.4 * _LEAGUE_AVG_RPG, 3), _SCALE_F5,
            None, 50.0, True, "Requires both late_rpg and season rpg",
        )
    else:
        composite = round(0.6 * late_rpg + 0.4 * rpg, 4)
        avg_comp = 0.6 * _LEAGUE_AVG_LATE + 0.4 * _LEAGUE_AVG_RPG
        raw = 50.0 + (composite - avg_comp) * _SCALE_F5
        bf = f"0.6×late({late_rpg:.3f}) + 0.4×season({rpg:.3f}) = {composite:.4f}"
        cmb_d = _rating_detail(
            "Comeback Scoring Rating", True,
            f"50 + (composite - {round(avg_comp,3)}) × {_SCALE_F5}",
            {"late_rpg": late_rpg, "season_rpg": rpg},
            composite, bf, round(avg_comp, 3), _SCALE_F5,
            round(raw, 3), round(_clamp(raw), 1), False,
        )

    # ── Overall ──────────────────────────────────────────────────────────────
    off_r = off_d["final"]
    def_r = def_d["final"]
    f5o_r = f5o_d["final"]
    ovr   = round(0.4 * off_r + 0.4 * def_r + 0.2 * f5o_r, 1)
    ovr_d = _rating_detail(
        "Overall Context Score", True,
        "0.4×offense + 0.4×defense + 0.2×f5_offense",
        {"offense_rating": off_r, "defense_rating": def_r, "f5_offense_rating": f5o_r},
        None, None, 50.0, 1.0, ovr, ovr, False,
    )

    return {
        "team_abbr": team_abbr,
        "season": season,
        "calibration_constants": {
            "league_avg_rpg":  _LEAGUE_AVG_RPG,
            "league_avg_f5":   _LEAGUE_AVG_F5,
            "league_avg_late": _LEAGUE_AVG_LATE,
            "scale_rpg": _SCALE_RPG,
            "scale_f5":  _SCALE_F5,
            "note": "All ratings are 0-100; ~50 = league average. SCALE controls rating sensitivity to deviations.",
        },
        "ratings": {
            "offense":          off_d,
            "defense":          def_d,
            "f5_offense":       f5o_d,
            "f5_pitching_risk": f5p_d,
            "bullpen_risk":     bp_d,
            "comeback":         cmb_d,
            "overall":          ovr_d,
        },
        "baseball_support_note": {
            "summary": (
                "baseball_support_score starts from live play-event signals (HR, errors, walks) "
                "and applies a secondary ±15pt team-context adjustment, clamped to prevent "
                "context from overriding the play-event read."
            ),
            "default_value": 50.0,
            "adjustments": {
                "home_run": -25,
                "error_or_wild_pitch_or_passed_ball": +20,
                "walk_driven_rally": +10,
            },
            "why_mostly_50": (
                "Scoring plays that are singles, doubles, triples, fielder's choices, "
                "or other neutral hit types do not trigger any play-event adjustment. If all "
                "scoring plays are neutral hits, the play-event score stays at 50.0. "
                "A secondary team-context adjustment (clamped ±15) may then shift the final "
                "score using offense, defense, and bullpen-risk ratings."
            ),
        },
    }


# ── Sanity checks ─────────────────────────────────────────────────────────────

def run_sanity_checks(season: str, conn: sqlite3.Connection) -> dict:
    """
    Compare all teams for suspicious rating divergences.
    Returns {flags, pairs, summary}.
    """
    teams = get_all_team_contexts(season, conn)
    if not teams:
        return {"flags": [], "pairs": [], "summary": "No team data for this season."}

    flags: list[dict] = []
    pairs: list[dict] = []

    for t in teams:
        abbr    = t["team_abbr"]
        rpg     = t.get("runs_per_game")
        ra_pg   = t.get("runs_allowed_per_game")
        off     = t.get("offense_rating")
        deff    = t.get("defense_pitching_rating")
        rec_7   = t.get("recent_runs_per_game_7")
        rec_ra7 = t.get("recent_runs_allowed_per_game_7")
        f5_n    = t.get("f5_sample_size", 0)

        # Recent form heavily dominates offense vs season-only baseline
        if rpg is not None and off is not None:
            season_off = round(_clamp(50.0 + (rpg - _LEAGUE_AVG_RPG) * _SCALE_RPG), 1)
            div = abs(off - season_off)
            if div >= 15:
                flags.append({
                    "team": abbr,
                    "rating": "offense",
                    "flag": "recent_form_dominates",
                    "season_only_rating": season_off,
                    "actual_rating": off,
                    "divergence": round(div, 1),
                    "season_rpg": rpg,
                    "recent_7_rpg": rec_7,
                    "explanation": (
                        f"Recent 7-game RPG ({rec_7}) diverges enough from season ({rpg}) "
                        f"that the 60% recent weighting drives the rating {div:.0f}pt away "
                        f"from the season-only value ({season_off})."
                    ),
                })

        # Recent form heavily dominates defense vs season-only baseline
        if ra_pg is not None and deff is not None:
            season_def = round(_clamp(50.0 + (_LEAGUE_AVG_RPG - ra_pg) * _SCALE_RPG), 1)
            div = abs(deff - season_def)
            if div >= 15:
                flags.append({
                    "team": abbr,
                    "rating": "defense",
                    "flag": "recent_form_dominates",
                    "season_only_rating": season_def,
                    "actual_rating": deff,
                    "divergence": round(div, 1),
                    "season_ra_pg": ra_pg,
                    "recent_7_ra_pg": rec_ra7,
                    "explanation": (
                        f"Recent 7-game RA/G ({rec_ra7}) diverges enough from season ({ra_pg}) "
                        f"that the 60% recent weighting drives the defense rating {div:.0f}pt "
                        f"away from the season-only value ({season_def})."
                    ),
                })

        # No inning-level data — all F5/late ratings are default 50
        if f5_n == 0:
            flags.append({
                "team": abbr,
                "rating": "f5_all",
                "flag": "no_inning_data",
                "f5_sample_size": 0,
                "explanation": (
                    "No mlb_inning_scores rows for this team's games. "
                    "F5-Off, F5-Pit, BP Risk, and Comeback all default to 50.0."
                ),
            })

    # Cross-team pair checks
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            rpg_a = a.get("runs_per_game")
            rpg_b = b.get("runs_per_game")
            off_a = a.get("offense_rating")
            off_b = b.get("offense_rating")
            ra_a  = a.get("runs_allowed_per_game")
            ra_b  = b.get("runs_allowed_per_game")
            def_a = a.get("defense_pitching_rating")
            def_b = b.get("defense_pitching_rating")

            if (rpg_a is not None and rpg_b is not None
                    and off_a is not None and off_b is not None):
                rpg_diff = abs(rpg_a - rpg_b)
                off_diff = abs(off_a - off_b)
                if rpg_diff < 0.5 and off_diff > 15:
                    pairs.append({
                        "team_a": a["team_abbr"],
                        "team_b": b["team_abbr"],
                        "flag": "similar_rpg_divergent_offense",
                        "rpg_a": rpg_a, "rpg_b": rpg_b,
                        "rpg_diff": round(rpg_diff, 3),
                        "offense_a": off_a, "offense_b": off_b,
                        "offense_diff": round(off_diff, 1),
                        "recent_7_a": a.get("recent_runs_per_game_7"),
                        "recent_7_b": b.get("recent_runs_per_game_7"),
                        "explanation": (
                            "Similar season RPG but offense ratings differ by "
                            f"{off_diff:.0f}pt — driven by recent 7-game form divergence."
                        ),
                    })

            if (ra_a is not None and ra_b is not None
                    and def_a is not None and def_b is not None):
                ra_diff  = abs(ra_a - ra_b)
                def_diff = abs(def_a - def_b)
                if ra_diff < 0.5 and def_diff > 15:
                    pairs.append({
                        "team_a": a["team_abbr"],
                        "team_b": b["team_abbr"],
                        "flag": "similar_ra_divergent_defense",
                        "ra_pg_a": ra_a, "ra_pg_b": ra_b,
                        "ra_diff": round(ra_diff, 3),
                        "defense_a": def_a, "defense_b": def_b,
                        "defense_diff": round(def_diff, 1),
                        "recent_ra7_a": a.get("recent_runs_allowed_per_game_7"),
                        "recent_ra7_b": b.get("recent_runs_allowed_per_game_7"),
                        "explanation": (
                            "Similar season RA/G but defense ratings differ by "
                            f"{def_diff:.0f}pt — driven by recent 7-game defensive form."
                        ),
                    })

    n_f = len(flags)
    n_p = len(pairs)
    summary = (
        "No suspicious divergences detected."
        if n_f == 0 and n_p == 0
        else f"{n_f} individual flag(s), {n_p} cross-team pair(s) flagged."
    )
    return {"flags": flags, "pairs": pairs, "summary": summary}


# ── Team comparison ───────────────────────────────────────────────────────────

_COMPARE_FIELDS = [
    ("runs_per_game",                  "RPG (season)",        None),
    ("runs_allowed_per_game",          "RA/G (season)",       None),
    ("recent_runs_per_game_7",         "RPG (last 7)",        None),
    ("recent_runs_allowed_per_game_7", "RA/G (last 7)",       None),
    ("offense_rating",                 "Offense",             True),
    ("defense_pitching_rating",        "Defense/Pitching",    True),
    ("f5_runs_per_game",               "F5 RPG",              None),
    ("f5_runs_allowed_per_game",       "F5 RA/G",             None),
    ("f5_offense_rating",              "F5 Offense",          True),
    ("f5_pitching_risk_rating",        "F5 Pitching Risk",    False),
    ("late_runs_per_game",             "Late+ Scoring",       None),
    ("late_runs_allowed_per_game",     "Late- Allowed",       None),
    ("bullpen_risk_rating",            "Bullpen Risk",        False),
    ("comeback_scoring_rating",        "Comeback",            True),
    ("overall_context_score",          "Overall",             True),
]

_RATING_TO_RAW = {
    "offense_rating":           "runs_per_game",
    "defense_pitching_rating":  "runs_allowed_per_game",
    "f5_offense_rating":        "f5_runs_per_game",
    "f5_pitching_risk_rating":  "f5_runs_allowed_per_game",
    "bullpen_risk_rating":      "late_runs_allowed_per_game",
    "comeback_scoring_rating":  "late_runs_per_game",
}


def compare_teams(
    team_a: str,
    team_b: str,
    season: str,
    conn: sqlite3.Connection,
) -> Optional[dict]:
    """Side-by-side comparison with formula-aware warnings."""
    ctx_a = get_team_context(team_a.upper(), season, conn)
    ctx_b = get_team_context(team_b.upper(), season, conn)
    if ctx_a is None or ctx_b is None:
        return None

    rows = []
    for field, label, higher_is_better in _COMPARE_FIELDS:
        va = ctx_a.get(field)
        vb = ctx_b.get(field)
        diff = round(va - vb, 3) if va is not None and vb is not None else None

        warning = None
        if diff is not None and higher_is_better is not None:
            abs_diff = abs(diff)
            if abs_diff > 15:
                raw_field = _RATING_TO_RAW.get(field)
                if raw_field:
                    raw_a = ctx_a.get(raw_field)
                    raw_b = ctx_b.get(raw_field)
                    if raw_a is not None and raw_b is not None:
                        raw_diff = abs(raw_a - raw_b)
                        if raw_diff < 0.5:
                            warning = (
                                f"Rating gap {abs_diff:.0f}pt but raw stat diff "
                                f"is only {raw_diff:.2f} — likely driven by recent-7 weighting."
                            )
                        else:
                            warning = (
                                f"Rating gap {abs_diff:.0f}pt; raw stat diff is {raw_diff:.2f}."
                            )
                    else:
                        warning = f"Rating gap {abs_diff:.0f}pt."

        rows.append({
            "field": field,
            "label": label,
            "value_a": va,
            "value_b": vb,
            "diff_a_minus_b": diff,
            "higher_is_better": higher_is_better,
            "warning": warning,
        })

    warnings = [r["warning"] for r in rows if r["warning"]]
    return {
        "team_a": team_a.upper(),
        "team_b": team_b.upper(),
        "season": season,
        "comparison": rows,
        "warnings": warnings,
        "games_played_a": ctx_a.get("games_played"),
        "games_played_b": ctx_b.get("games_played"),
        "confidence_a": ctx_a.get("context_confidence"),
        "confidence_b": ctx_b.get("context_confidence"),
    }
