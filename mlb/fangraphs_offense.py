"""
mlb/fangraphs_offense.py — FanGraphs team offense import and calibration.

Imports wide-format FanGraphs batting CSV, computes external_true_offense_score,
and produces a side-by-side calibration view vs our current scoring-form rating.

IMPORTANT:
  - FanGraphs Off = batting + baserunning runs above average (not run prevention).
  - FanGraphs Def = fielding + positional adjustment above average.
    Do NOT compare FanGraphs Def to our app's defense_pitching_rating,
    which measures run prevention (runs allowed), not fielding.
  - external_true_offense_score is computed but NOT wired into candidate generation.
"""
from __future__ import annotations

import csv
import io
import logging
import sqlite3
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── Required CSV columns ──────────────────────────────────────────────────────

FG_REQUIRED_COLS: frozenset[str] = frozenset({
    "Team", "wRC+",
})

FG_ALL_COLS: list[str] = [
    "Team", "G", "PA", "HR", "R", "RBI",
    "BB%", "K%", "ISO", "BABIP", "AVG", "OBP", "SLG",
    "wOBA", "wRC+", "BsR", "Off", "Def", "WAR",
]

FG_SAMPLE_CSV = """\
Team,G,PA,HR,R,RBI,BB%,K%,ISO,BABIP,AVG,OBP,SLG,wOBA,wRC+,BsR,Off,Def,WAR
LAD,65,2401,90,375,358,9.8%,18.3%,0.210,0.295,0.278,0.355,0.488,0.372,121,13.4,64.0,12.5,16.4
ATL,63,2280,75,310,292,9.2%,22.1%,0.178,0.285,0.262,0.338,0.440,0.350,108,5.2,23.8,8.1,11.2
CHC,64,2310,68,305,285,9.0%,21.5%,0.170,0.282,0.258,0.334,0.428,0.346,107,4.9,22.4,3.2,10.8
MIL,62,2250,72,295,278,8.8%,22.8%,0.168,0.288,0.260,0.332,0.428,0.344,106,-1.2,14.3,5.6,9.4
ATH,63,2195,70,280,264,8.5%,23.2%,0.163,0.279,0.253,0.326,0.416,0.336,100,0.8,2.1,4.4,7.8
COL,62,2310,78,290,274,7.8%,25.5%,0.155,0.305,0.255,0.320,0.410,0.331,87,-5.4,-43.0,-12.3,2.1
"""

FG_IMPORT_INSTRUCTIONS = (
    "Export from FanGraphs → Team Stats → Batting. "
    "Required: Team, wRC+. "
    "Supported optional columns: G, PA, HR, R, RBI, BB%, K%, ISO, BABIP, AVG, OBP, SLG, wOBA, BsR, Off, Def, WAR. "
    "BB% and K% may include the '%' symbol — it is stripped automatically. "
    "Team abbreviations are uppercased and stored as-is. "
    "NOTE: FanGraphs Def column (fielding+positional) is imported as informational only "
    "and is NOT used for run-prevention calibration. "
    "POST to /api/mlb/team-context/fangraphs-offense/import with "
    "{csv_text: '<paste CSV>', season: '2026', date_as_of: 'YYYY-MM-DD'}."
)


# ── Scoring formula constants ──────────────────────────────────────────────────
# wRC+ normalization: 70=weak floor, 140=elite ceiling → 0-100
_WRC_FLOOR   = 70.0
_WRC_RANGE   = 70.0   # 140 - 70

# FanGraphs Off normalization: -50=weak, +80=elite → 0-100
_OFF_SHIFT   = 50.0   # add 50 so -50→0
_OFF_RANGE   = 130.0  # 80+50

# wOBA normalization: .270=weak, .380=elite → 0-100
_WOBA_FLOOR  = 0.270
_WOBA_RANGE  = 0.110

# OBP normalization: .290=weak, .380=elite → 0-100
_OBP_FLOOR   = 0.290
_OBP_RANGE   = 0.090

# SLG normalization: .360=weak, .500=elite → 0-100
_SLG_FLOOR   = 0.360
_SLG_RANGE   = 0.140

# ISO normalization: .100=weak, .220=elite → 0-100
_ISO_FLOOR   = 0.100
_ISO_RANGE   = 0.120

# Formula weights (must sum to 1.0)
_W_WRC_PLUS = 0.40
_W_FG_OFF   = 0.25
_W_WOBA     = 0.15
_W_OBP      = 0.10
_W_SLG      = 0.05
_W_ISO      = 0.05

# Tier thresholds
_TIER_ELITE   = 70.0
_TIER_ABOVE   = 55.0
_TIER_AVERAGE = 40.0
_TIER_BELOW   = 25.0

# Mismatch thresholds vs our internal offense_rating
_MISMATCH_FLAG_GAP   = 20.0   # flag if |our_off - ext_score| >= 20
_MISMATCH_STRONG_GAP = 15.0   # use for calibration_recommendation
_MISMATCH_SEVERE_GAP = 30.0   # needs_review


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(val: Optional[float], floor: float, rng: float) -> Optional[float]:
    """Normalize a raw metric to 0-100 given floor and range. Returns None if val is None."""
    if val is None:
        return None
    return _clamp((val - floor) / rng * 100.0)


def _parse_pct(raw: str) -> Optional[float]:
    """Parse '9.8%' or '9.8' to 9.8. Returns None on failure."""
    if not raw:
        return None
    try:
        return float(raw.strip().rstrip("%"))
    except ValueError:
        return None


def _parse_float(raw: str) -> Optional[float]:
    if not raw or raw.strip() in ("", "—", "-", "N/A"):
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


def _parse_int(raw: str) -> Optional[int]:
    v = _parse_float(raw)
    return int(v) if v is not None else None


# ── Core scoring ──────────────────────────────────────────────────────────────

def compute_external_true_offense_score(
    wrc_plus:   Optional[float],
    fg_off:     Optional[float],
    woba:       Optional[float],
    obp:        Optional[float],
    slg:        Optional[float],
    iso:        Optional[float],
) -> tuple[float, str, str]:
    """
    Compute external_true_offense_score (0-100) from FanGraphs batting metrics.

    Weights:
      40% wRC+        — primary quality-adjusted offense metric
      25% FanGraphs Off — batting + baserunning runs above average
      15% wOBA        — park-neutral hitting quality
      10% OBP         — on-base discipline
       5% SLG         — extra-base power component
       5% ISO         — raw power (SLG - AVG)

    Returns: (score, tier, explanation)
    NOTE: FanGraphs Def is NOT used here. It measures fielding, not run prevention.
    """
    components: list[tuple[str, float, float]] = []

    def _add(name: str, raw: Optional[float], floor: float, rng: float, weight: float) -> float:
        n = _norm(raw, floor, rng)
        if n is None:
            components.append((name, 50.0, weight))  # neutral default
            return 50.0 * weight
        components.append((name, n, weight))
        return n * weight

    score = (
        _add("wRC+",  wrc_plus, _WRC_FLOOR, _WRC_RANGE,  _W_WRC_PLUS)
        + _add("Off", fg_off,  -_OFF_SHIFT, _OFF_RANGE,  _W_FG_OFF)
        + _add("wOBA", woba,    _WOBA_FLOOR, _WOBA_RANGE, _W_WOBA)
        + _add("OBP",  obp,     _OBP_FLOOR,  _OBP_RANGE,  _W_OBP)
        + _add("SLG",  slg,     _SLG_FLOOR,  _SLG_RANGE,  _W_SLG)
        + _add("ISO",  iso,     _ISO_FLOOR,  _ISO_RANGE,  _W_ISO)
    )
    score = round(_clamp(score), 1)

    if score >= _TIER_ELITE:
        tier = "elite"
    elif score >= _TIER_ABOVE:
        tier = "above_average"
    elif score >= _TIER_AVERAGE:
        tier = "average"
    elif score >= _TIER_BELOW:
        tier = "below_average"
    else:
        tier = "weak"

    parts = [
        f"{name}={c:.0f}" for name, c, _ in components
        if c is not None
    ]
    explanation = (
        f"score={score} ({tier}). "
        f"Components: {', '.join(parts)}. "
        f"Weights: wRC+={_W_WRC_PLUS}, Off={_W_FG_OFF}, wOBA={_W_WOBA}, "
        f"OBP={_W_OBP}, SLG={_W_SLG}, ISO={_W_ISO}. "
        f"FanGraphs Def excluded (measures fielding, not run prevention)."
    )

    return (score, tier, explanation)


# ── CSV import ────────────────────────────────────────────────────────────────

def import_fangraphs_offense_csv(
    csv_text: str,
    conn: sqlite3.Connection,
    season: str = "2026",
    date_as_of: Optional[str] = None,
) -> dict:
    """
    Import a wide-format FanGraphs team offense CSV.

    Each row is one team. Computes external_true_offense_score on import.
    Rows are upserted — re-importing the same data is safe.

    Returns {imported, skipped, errors}.
    """
    if date_as_of is None:
        date_as_of = datetime.now().date().isoformat()

    imported = 0
    skipped  = 0
    errors: list[str] = []
    imported_at = datetime.now().isoformat()

    try:
        reader  = csv.DictReader(io.StringIO(csv_text.strip()))
        headers = [h.strip() for h in (reader.fieldnames or [])]

        missing = [c for c in FG_REQUIRED_COLS if c not in headers]
        if missing:
            return {
                "imported": 0,
                "skipped":  0,
                "errors":   [f"Missing required columns: {', '.join(sorted(missing))}"],
            }

        for i, row in enumerate(reader, start=2):
            team = (row.get("Team") or "").strip().upper()
            if not team:
                errors.append(f"Row {i}: empty Team — skipped")
                skipped += 1
                continue

            wrc_raw = (row.get("wRC+") or "").strip()
            if not wrc_raw:
                errors.append(f"Row {i} ({team}): missing wRC+ — skipped")
                skipped += 1
                continue

            wrc_plus = _parse_float(wrc_raw)
            if wrc_plus is None:
                errors.append(f"Row {i} ({team}): non-numeric wRC+ '{wrc_raw}' — skipped")
                skipped += 1
                continue

            games   = _parse_int(row.get("G")    or "")
            pa      = _parse_int(row.get("PA")   or "")
            hr      = _parse_int(row.get("HR")   or "")
            r       = _parse_int(row.get("R")    or "")
            rbi     = _parse_int(row.get("RBI")  or "")
            bb_pct  = _parse_pct(row.get("BB%")  or "")
            k_pct   = _parse_pct(row.get("K%")   or "")
            iso     = _parse_float(row.get("ISO")   or "")
            babip   = _parse_float(row.get("BABIP") or "")
            avg     = _parse_float(row.get("AVG")   or "")
            obp     = _parse_float(row.get("OBP")   or "")
            slg     = _parse_float(row.get("SLG")   or "")
            woba    = _parse_float(row.get("wOBA")  or "")
            bsr     = _parse_float(row.get("BsR")   or "")
            fg_off  = _parse_float(row.get("Off")   or "")
            fg_def  = _parse_float(row.get("Def")   or "")
            war     = _parse_float(row.get("WAR")   or "")

            score, tier, explanation = compute_external_true_offense_score(
                wrc_plus=wrc_plus,
                fg_off=fg_off,
                woba=woba,
                obp=obp,
                slg=slg,
                iso=iso,
            )

            conn.execute(
                """
                INSERT INTO fangraphs_team_offense
                  (season, date_as_of, team,
                   games, pa, hr, r, rbi, bb_pct, k_pct,
                   iso, babip, avg, obp, slg, woba, wrc_plus,
                   bsr, fg_off, fg_def, war,
                   external_true_offense_score, external_offense_tier,
                   external_offense_explanation, imported_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(season, date_as_of, team) DO UPDATE SET
                    games = excluded.games,
                    pa    = excluded.pa,
                    hr    = excluded.hr,
                    r     = excluded.r,
                    rbi   = excluded.rbi,
                    bb_pct  = excluded.bb_pct,
                    k_pct   = excluded.k_pct,
                    iso     = excluded.iso,
                    babip   = excluded.babip,
                    avg     = excluded.avg,
                    obp     = excluded.obp,
                    slg     = excluded.slg,
                    woba    = excluded.woba,
                    wrc_plus = excluded.wrc_plus,
                    bsr     = excluded.bsr,
                    fg_off  = excluded.fg_off,
                    fg_def  = excluded.fg_def,
                    war     = excluded.war,
                    external_true_offense_score  = excluded.external_true_offense_score,
                    external_offense_tier        = excluded.external_offense_tier,
                    external_offense_explanation = excluded.external_offense_explanation,
                    imported_at = excluded.imported_at
                """,
                (
                    season, date_as_of, team,
                    games, pa, hr, r, rbi, bb_pct, k_pct,
                    iso, babip, avg, obp, slg, woba, wrc_plus,
                    bsr, fg_off, fg_def, war,
                    score, tier, explanation, imported_at,
                ),
            )
            imported += 1

        conn.commit()

    except Exception as exc:
        log.error("import_fangraphs_offense_csv error: %s", exc)
        errors.append(f"Fatal: {exc}")

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── Calibration view ──────────────────────────────────────────────────────────

def _calibration_recommendation(
    current_off: Optional[float],
    ext_score: Optional[float],
) -> str:
    if current_off is None or ext_score is None:
        return "no_data"
    gap = ext_score - current_off  # positive = external rates higher
    abs_gap = abs(gap)
    if abs_gap >= _MISMATCH_SEVERE_GAP:
        return "needs_review"
    if gap >= _MISMATCH_STRONG_GAP:
        return "trust_external_more"
    if gap <= -_MISMATCH_STRONG_GAP:
        return "trust_recent_form_more"
    return "aligned"


def get_fangraphs_offense_calibration(
    season: str,
    conn: sqlite3.Connection,
    team_abbr: Optional[str] = None,
) -> dict:
    """
    Return side-by-side FanGraphs external offense vs our internal scoring-form rating.

    Column naming in response:
      current_model_offense_form = our offense_rating (scoring-form-based, recent-weighted)
      external_true_offense_score = quality-adjusted FanGraphs composite
      calibrated_offense_score = 50% blend (computed but not used in candidate generation)

    FanGraphs Def is returned as fg_def_informational — it measures fielding+positional,
    NOT run prevention. Do not compare it to our defense_pitching_rating.
    """
    where = "WHERE fg.season = ?"
    params: list = [season]
    if team_abbr:
        where += " AND fg.team = ?"
        params.append(team_abbr.upper())

    rows = conn.execute(
        f"""
        SELECT
            fg.team,
            fg.date_as_of,
            fg.games             AS fg_games,
            fg.wrc_plus,
            fg.woba,
            fg.obp,
            fg.slg,
            fg.iso,
            fg.bsr,
            fg.fg_off,
            fg.fg_def            AS fg_def_informational,
            fg.war,
            fg.external_true_offense_score,
            fg.external_offense_tier,
            fg.external_offense_explanation,
            tc.offense_rating    AS current_model_offense_form,
            tc.runs_per_game     AS rpg,
            tc.recent_runs_per_game_7 AS recent_rpg_7,
            tc.games_played      AS our_games_played,
            tc.context_confidence
        FROM fangraphs_team_offense fg
        LEFT JOIN mlb_team_context tc
            ON tc.team_abbr = fg.team AND tc.season = fg.season
        {where}
        ORDER BY fg.external_true_offense_score DESC NULLS LAST
        """,
        params,
    ).fetchall()

    if not rows:
        return {
            "has_data": False,
            "rows": [],
            "note": (
                "No FanGraphs offense data imported for this season. "
                "Use POST /api/mlb/team-context/fangraphs-offense/import "
                "to load a CSV."
            ),
            "import_instructions": FG_IMPORT_INSTRUCTIONS,
            "sample_csv": FG_SAMPLE_CSV,
        }

    result_rows = []
    for r in rows:
        d = dict(r)
        current_off = d.get("current_model_offense_form")
        ext_score   = d.get("external_true_offense_score")

        # Calibrated blend: 50% each — computed but NOT used in candidates
        if current_off is not None and ext_score is not None:
            calibrated = round(0.5 * current_off + 0.5 * ext_score, 1)
        elif ext_score is not None:
            calibrated = ext_score
        elif current_off is not None:
            calibrated = current_off
        else:
            calibrated = None

        # Rating gap: positive = external rates higher
        rating_gap = None
        if current_off is not None and ext_score is not None:
            rating_gap = round(ext_score - current_off, 1)

        abs_gap = abs(rating_gap) if rating_gap is not None else None
        mismatch_flag = abs_gap is not None and abs_gap >= _MISMATCH_FLAG_GAP

        rec = _calibration_recommendation(current_off, ext_score)

        # Human-readable mismatch explanation
        if rec == "aligned":
            mismatch_note = "Aligned — both views agree on this team's offense quality."
        elif rec == "trust_external_more":
            mismatch_note = (
                f"External rates {d['team']} higher (gap={rating_gap:+.1f}). "
                "Consider FanGraphs quality-adjusted view; our model may underrate this team."
            )
        elif rec == "trust_recent_form_more":
            mismatch_note = (
                f"Our model rates {d['team']} higher (gap={rating_gap:+.1f}). "
                "Recent scoring form or park effects may be inflating our score. "
                "FanGraphs Off adjusts for park."
            )
        elif rec == "needs_review":
            mismatch_note = (
                f"Large disagreement (gap={rating_gap:+.1f}). "
                "Review both our recent-form model and FanGraphs quality data."
            )
        else:
            mismatch_note = "No data for comparison."

        result_rows.append({
            **d,
            "calibrated_offense_score":    calibrated,
            "rating_gap":                  rating_gap,
            "mismatch_flag":               mismatch_flag,
            "calibration_recommendation":  rec,
            "mismatch_note":               mismatch_note,
            # Clarify the naming in the response
            "_label_current_model_offense_form": (
                "scoring_form_rating — season RPG blended 40% with last-7 RPG 60%; "
                "NOT park-adjusted."
            ),
            "_label_external_true_offense_score": (
                "quality_adjusted_offense — FanGraphs wRC+/Off composite; "
                "park-neutral, does NOT reflect recent form."
            ),
            "_label_calibrated_offense_score": (
                "blended_view — 50% scoring_form + 50% quality_adjusted. "
                "Computed for review only; NOT used in candidate generation."
            ),
            "_label_fg_def_informational": (
                "FanGraphs Def = fielding + positional adjustment. "
                "Do NOT compare to our defense_pitching_rating (runs allowed)."
            ),
        })

    flagged = [r for r in result_rows if r["mismatch_flag"]]
    return {
        "has_data": True,
        "rows": result_rows,
        "flagged_mismatches": [r["team"] for r in flagged],
        "note": (
            f"{len(result_rows)} team(s) with FanGraphs offense data for {season}. "
            f"{len(flagged)} mismatch(es) flagged (|gap| >= {_MISMATCH_FLAG_GAP:.0f})."
        ),
        "calibration_note": (
            "calibrated_offense_score is computed for review only. "
            "It is NOT used in candidate generation or baseball_support_score."
        ),
        "import_instructions": FG_IMPORT_INSTRUCTIONS,
    }
