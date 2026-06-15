"""
mlb/historical_patterns.py — Read-only historical pattern analysis engine.

Answers "what usually happened next?" for live MLB game setups.
No candidate generation.  No TAKE labels.  No trades.  No guardrail changes.
as_of_date safety: all queries use WHERE game_date < :as_of_date (strictly before).
"""
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ── PatternResult ─────────────────────────────────────────────────────────────

@dataclass
class PatternResult:
    pattern_name: str
    sample_size: int
    filters_used: dict
    as_of_date: str
    matching_cases: list
    outcome_summary: dict
    continuation_rate: Optional[float]
    cooldown_rate: Optional[float]
    average_rest_of_game_runs: Optional[float]
    median_rest_of_game_runs: Optional[float]
    threshold_hit_rates: dict
    confidence_label: str
    notes: str
    warnings: list = field(default_factory=list)


# ── Confidence label ──────────────────────────────────────────────────────────

def confidence_label(n: int) -> str:
    if n < 5:
        return "insufficient_sample"
    if n < 20:
        return "thin_sample"
    if n < 50:
        return "usable_sample"
    return "strong_sample"


# ── Threshold helpers ─────────────────────────────────────────────────────────

_THRESHOLDS = [2.5, 3.5, 4.5, 5.5, 6.5, 7.5]


def _hit_rates(values: list[float]) -> dict:
    if not values:
        return {str(t): None for t in _THRESHOLDS}
    n = len(values)
    return {str(t): round(sum(1 for v in values if v >= t) / n, 4)
            for t in _THRESHOLDS}


def _warnings_for_sample(n: int) -> list[str]:
    if n == 0:
        return ["No matching cases found — pattern cannot be evaluated."]
    if n < 20:
        return [f"Thin sample ({n} cases) — interpret with caution."]
    return []


def _season_from_date(game_date: str) -> str:
    return game_date[:4]


# ── find_noisy_inning_cases ───────────────────────────────────────────────────

def find_noisy_inning_cases(
    conn: sqlite3.Connection,
    *,
    min_runs: int = 3,
    as_of_date: Optional[str] = None,
    season: Optional[str] = None,
    team: Optional[str] = None,
    inning: Optional[int] = None,
) -> PatternResult:
    """
    Find games where a team scored >= min_runs in a single inning, then compute
    rest-of-game runs for that team after the noisy inning.
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()

    filters: dict = {
        "min_runs": min_runs,
        "as_of_date": as_of_date,
    }
    if season:
        filters["season"] = season
    if team:
        filters["team"] = team
    if inning:
        filters["inning"] = inning

    # ── Step 1: find noisy inning events ─────────────────────────────────────
    # We need to find (game_pk, team_abbr, noisy_inning, noisy_runs) where
    # the team is either away or home and scored >= min_runs in that inning.
    # Rows from mlb_inning_scores — one row per inning with away_runs/home_runs.
    params: list = [as_of_date, min_runs]
    season_clause = ""
    if season:
        season_clause = "AND substr(g.game_date, 1, 4) = ?"
        params.append(season)

    # Returns two sets of (game_pk, team_abbr, side, noisy_inning, noisy_runs)
    # Union of away team and home team events
    team_clause_away = ""
    team_clause_home = ""
    if team:
        team_clause_away = "AND s.away_abbr = ?"
        team_clause_home = "AND s.home_abbr = ?"

    inning_clause = ""
    if inning:
        inning_clause = "AND s.inning = ?"

    # Build params for both halves of UNION
    away_params = list(params)
    if team:
        away_params.append(team)
    if inning:
        away_params.append(inning)

    home_params = list(params)
    if team:
        home_params.append(team)
    if inning:
        home_params.append(inning)

    all_params = away_params + home_params

    rows = conn.execute(
        f"""
        SELECT g.game_pk, g.game_date, s.away_abbr AS team, 'away' AS side,
               s.inning AS noisy_inning, s.away_runs AS noisy_runs
        FROM mlb_inning_scores s
        JOIN mlb_games g ON g.game_pk = s.game_pk
        WHERE g.game_date < ?
          AND g.is_final = 1
          AND s.away_runs >= ?
          {season_clause}
          {team_clause_away}
          {inning_clause}
        UNION ALL
        SELECT g.game_pk, g.game_date, s.home_abbr AS team, 'home' AS side,
               s.inning AS noisy_inning, s.home_runs AS noisy_runs
        FROM mlb_inning_scores s
        JOIN mlb_games g ON g.game_pk = s.game_pk
        WHERE g.game_date < ?
          AND g.is_final = 1
          AND s.home_runs >= ?
          {season_clause}
          {team_clause_home}
          {inning_clause}
        ORDER BY 2, 1, 5
        """,
        all_params,
    ).fetchall()

    if not rows:
        return PatternResult(
            pattern_name="noisy_inning",
            sample_size=0,
            filters_used=filters,
            as_of_date=as_of_date,
            matching_cases=[],
            outcome_summary={},
            continuation_rate=None,
            cooldown_rate=None,
            average_rest_of_game_runs=None,
            median_rest_of_game_runs=None,
            threshold_hit_rates={str(t): None for t in _THRESHOLDS},
            confidence_label="insufficient_sample",
            notes="",
            warnings=_warnings_for_sample(0),
        )

    # ── Step 2: for each event compute rest-of-game runs ─────────────────────
    cases: list[dict] = []
    rest_runs_list: list[float] = []
    final_totals: list[float] = []
    continuation_count = 0  # scored ≥ 1 run after noisy inning
    cooldown_count = 0       # scored 0 after noisy inning

    for row in rows:
        game_pk = row[0]
        game_date = row[1]
        team_abbr = row[2]
        side = row[3]
        noisy_inning = row[4]
        noisy_runs = row[5]

        # Fetch all innings for this game to sum rest-of-game runs
        all_innings = conn.execute(
            """
            SELECT inning,
                   CASE ? WHEN 'away' THEN away_runs ELSE home_runs END AS team_runs
            FROM mlb_inning_scores
            WHERE game_pk = ?
            ORDER BY inning
            """,
            (side, game_pk),
        ).fetchall()

        # Total team runs this game
        total_team_runs = sum(r[1] for r in all_innings)
        # Rest-of-game: innings strictly after the noisy inning
        rest_runs = sum(r[1] for r in all_innings if r[0] > noisy_inning)

        rest_runs_list.append(float(rest_runs))
        final_totals.append(float(total_team_runs))

        if rest_runs >= 1:
            continuation_count += 1
        else:
            cooldown_count += 1

        cases.append({
            "game_pk": game_pk,
            "game_date": game_date,
            "team": team_abbr,
            "side": side,
            "inning": noisy_inning,
            "noisy_runs": noisy_runs,
            "rest_of_game_runs": rest_runs,
            "final_team_total": total_team_runs,
        })

    n = len(cases)
    avg_rest = round(statistics.mean(rest_runs_list), 4) if rest_runs_list else None
    med_rest = round(statistics.median(rest_runs_list), 4) if rest_runs_list else None
    cont_rate = round(continuation_count / n, 4) if n else None
    cool_rate = round(cooldown_count / n, 4) if n else None

    return PatternResult(
        pattern_name="noisy_inning",
        sample_size=n,
        filters_used=filters,
        as_of_date=as_of_date,
        matching_cases=cases,
        outcome_summary={
            "continuation_count": continuation_count,
            "cooldown_count": cooldown_count,
            "average_final_team_total": (
                round(statistics.mean(final_totals), 4) if final_totals else None
            ),
        },
        continuation_rate=cont_rate,
        cooldown_rate=cool_rate,
        average_rest_of_game_runs=avg_rest,
        median_rest_of_game_runs=med_rest,
        threshold_hit_rates=_hit_rates(final_totals),
        confidence_label=confidence_label(n),
        notes="",
        warnings=_warnings_for_sample(n),
    )


# ── summarize_team_total_after_state ─────────────────────────────────────────

def summarize_team_total_after_state(
    conn: sqlite3.Connection,
    *,
    team: Optional[str] = None,
    runs_through_inning: int,
    inning: int,
    as_of_date: Optional[str] = None,
    season: Optional[str] = None,
    runs_range: Optional[tuple[int, int]] = None,
) -> PatternResult:
    """
    Find games where *team* had *runs_through_inning* runs through *inning*.

    team=None queries all teams (league-level).
    runs_range=(lo, hi) broadens the filter to a range instead of exact match.
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()

    filters: dict = {
        "team": team,
        "runs_through_inning": runs_through_inning,
        "inning": inning,
        "as_of_date": as_of_date,
    }
    if season:
        filters["season"] = season
    if runs_range is not None:
        filters["runs_range"] = runs_range

    season_clause = ""
    params2: list = [as_of_date]
    if season:
        season_clause = "AND substr(g.game_date, 1, 4) = ?"
        params2.append(season)

    if team is not None:
        team_clause = "AND (g.away_abbr = ? OR g.home_abbr = ?)"
        params2 += [team, team]
    else:
        team_clause = ""

    candidate_games = conn.execute(
        f"""
        SELECT g.game_pk, g.game_date,
               g.away_abbr, g.home_abbr,
               g.final_away_score, g.final_home_score
        FROM mlb_games g
        WHERE g.game_date < ?
          AND g.is_final = 1
          {season_clause}
          {team_clause}
        ORDER BY g.game_date
        """,
        params2,
    ).fetchall()

    cases: list[dict] = []
    final_totals: list[float] = []

    def _runs_match(actual: int) -> bool:
        if runs_range is not None:
            lo, hi = runs_range
            return lo <= actual <= hi
        return actual == runs_through_inning

    for row in candidate_games:
        game_pk = row[0]
        game_date = row[1]
        away_abbr = row[2]
        home_abbr = row[3]
        final_away = row[4]
        final_home = row[5]

        # When team=None check both sides; when team specified check only that side.
        if team is not None:
            sides = [("away" if away_abbr == team else "home", team)]
        else:
            sides = [("away", away_abbr), ("home", home_abbr)]

        for side, team_abbr in sides:
            innings = conn.execute(
                """
                SELECT inning,
                       CASE ? WHEN 'away' THEN away_runs ELSE home_runs END AS team_runs
                FROM mlb_inning_scores
                WHERE game_pk = ? AND inning <= ?
                ORDER BY inning
                """,
                (side, game_pk, inning),
            ).fetchall()

            actual_runs_through = sum(r[1] for r in innings)
            if not _runs_match(actual_runs_through):
                continue

            final_total = final_away if side == "away" else final_home
            final_totals.append(float(final_total))
            cases.append({
                "game_pk": game_pk,
                "game_date": game_date,
                "team": team_abbr,
                "side": side,
                "runs_through_inning": actual_runs_through,
                "final_team_total": final_total,
            })

    n = len(cases)
    return PatternResult(
        pattern_name="team_total_after_state",
        sample_size=n,
        filters_used=filters,
        as_of_date=as_of_date,
        matching_cases=cases,
        outcome_summary={
            "average_final_total": (
                round(statistics.mean(final_totals), 4) if final_totals else None
            ),
        },
        continuation_rate=None,
        cooldown_rate=None,
        average_rest_of_game_runs=None,
        median_rest_of_game_runs=None,
        threshold_hit_rates=_hit_rates(final_totals),
        confidence_label=confidence_label(n),
        notes="",
        warnings=_warnings_for_sample(n),
    )


# ── summarize_f5_pace ─────────────────────────────────────────────────────────

def summarize_f5_pace(
    conn: sqlite3.Connection,
    *,
    runs_through_inning: int,
    inning: int,
    as_of_date: Optional[str] = None,
    season: Optional[str] = None,
    team: Optional[str] = None,
    runs_range: Optional[tuple[int, int]] = None,
) -> PatternResult:
    """
    Find games where combined runs (both teams) through *inning* equals
    *runs_through_inning*, then aggregate the F5 total (innings 1-5).
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()

    filters = {
        "runs_through_inning": runs_through_inning,
        "inning": inning,
        "as_of_date": as_of_date,
    }
    if season:
        filters["season"] = season
    if team:
        filters["team"] = team
    if runs_range is not None:
        filters["runs_range"] = runs_range

    season_clause = ""
    params: list = [as_of_date]
    if season:
        season_clause = "AND substr(g.game_date, 1, 4) = ?"
        params.append(season)
    if team:
        params += [team, team]
        team_clause = "AND (g.away_abbr = ? OR g.home_abbr = ?)"
    else:
        team_clause = ""

    candidate_games = conn.execute(
        f"""
        SELECT g.game_pk, g.game_date, g.away_abbr, g.home_abbr
        FROM mlb_games g
        WHERE g.game_date < ?
          AND g.is_final = 1
          {season_clause}
          {team_clause}
        ORDER BY g.game_date
        """,
        params,
    ).fetchall()

    cases: list[dict] = []
    f5_totals: list[float] = []

    for row in candidate_games:
        game_pk = row[0]
        game_date = row[1]

        innings_data = conn.execute(
            """
            SELECT inning, away_runs + home_runs AS combined_runs
            FROM mlb_inning_scores
            WHERE game_pk = ?
            ORDER BY inning
            """,
            (game_pk,),
        ).fetchall()

        runs_dict = {r[0]: r[1] for r in innings_data}

        # Runs through target inning
        actual = sum(runs_dict.get(i, 0) for i in range(1, inning + 1))
        if runs_range is not None:
            lo, hi = runs_range
            if not (lo <= actual <= hi):
                continue
        elif actual != runs_through_inning:
            continue

        # F5 total = innings 1-5 combined
        f5_total = sum(runs_dict.get(i, 0) for i in range(1, 6))
        f5_totals.append(float(f5_total))
        cases.append({
            "game_pk": game_pk,
            "game_date": game_date,
            "runs_through_inning": actual,
            "f5_total": f5_total,
        })

    n = len(cases)
    avg_f5 = round(statistics.mean(f5_totals), 4) if f5_totals else None
    med_f5 = round(statistics.median(f5_totals), 4) if f5_totals else None

    return PatternResult(
        pattern_name="f5_pace",
        sample_size=n,
        filters_used=filters,
        as_of_date=as_of_date,
        matching_cases=cases,
        outcome_summary={"average_f5_total": avg_f5, "median_f5_total": med_f5},
        continuation_rate=None,
        cooldown_rate=None,
        average_rest_of_game_runs=avg_f5,
        median_rest_of_game_runs=med_f5,
        threshold_hit_rates=_hit_rates(f5_totals),
        confidence_label=confidence_label(n),
        notes="",
        warnings=_warnings_for_sample(n),
    )


# ── summarize_late_scoring ────────────────────────────────────────────────────

def summarize_late_scoring(
    conn: sqlite3.Connection,
    *,
    inning_start: int = 6,
    as_of_date: Optional[str] = None,
    season: Optional[str] = None,
    team: Optional[str] = None,
) -> PatternResult:
    """
    Aggregate combined runs in innings >= inning_start across all final games.
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()

    filters = {"inning_start": inning_start, "as_of_date": as_of_date}
    if season:
        filters["season"] = season
    if team:
        filters["team"] = team

    season_clause = ""
    params: list = [as_of_date]
    if season:
        season_clause = "AND substr(g.game_date, 1, 4) = ?"
        params.append(season)
    if team:
        params += [team, team]
        team_clause = "AND (g.away_abbr = ? OR g.home_abbr = ?)"
    else:
        team_clause = ""

    candidate_games = conn.execute(
        f"""
        SELECT g.game_pk, g.game_date, g.away_abbr, g.home_abbr
        FROM mlb_games g
        WHERE g.game_date < ?
          AND g.is_final = 1
          {season_clause}
          {team_clause}
        ORDER BY g.game_date
        """,
        params,
    ).fetchall()

    cases: list[dict] = []
    late_run_totals: list[float] = []

    for row in candidate_games:
        game_pk = row[0]
        game_date = row[1]

        innings_data = conn.execute(
            """
            SELECT inning, away_runs + home_runs AS combined_runs
            FROM mlb_inning_scores
            WHERE game_pk = ? AND inning >= ?
            ORDER BY inning
            """,
            (game_pk, inning_start),
        ).fetchall()

        if not innings_data:
            continue

        late_runs = sum(r[1] for r in innings_data)
        late_run_totals.append(float(late_runs))
        cases.append({
            "game_pk": game_pk,
            "game_date": game_date,
            "late_runs": late_runs,
        })

    n = len(cases)
    avg = round(statistics.mean(late_run_totals), 4) if late_run_totals else None
    med = round(statistics.median(late_run_totals), 4) if late_run_totals else None

    return PatternResult(
        pattern_name="late_scoring",
        sample_size=n,
        filters_used=filters,
        as_of_date=as_of_date,
        matching_cases=cases,
        outcome_summary={"average_late_runs": avg, "median_late_runs": med},
        continuation_rate=None,
        cooldown_rate=None,
        average_rest_of_game_runs=avg,
        median_rest_of_game_runs=med,
        threshold_hit_rates=_hit_rates(late_run_totals),
        confidence_label=confidence_label(n),
        notes="",
        warnings=_warnings_for_sample(n),
    )


# ── summarize_true_offense_mismatch_cases ────────────────────────────────────

def summarize_true_offense_mismatch_cases(
    conn: sqlite3.Connection,
    *,
    as_of_date: Optional[str] = None,
    season: Optional[str] = "2025",
    true_offense_weak_threshold: float = 45.0,
    recent_runs_hot_threshold: float = 5.5,
) -> PatternResult:
    """
    Tag teams whose FanGraphs true offense is weak but recent run scoring is hot.
    Cross-references fangraphs_team_offense and mlb_team_context.
    Read-only analysis only — no recommendations, no signals.
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()

    filters = {
        "as_of_date": as_of_date,
        "season": season,
        "true_offense_weak_threshold": true_offense_weak_threshold,
        "recent_runs_hot_threshold": recent_runs_hot_threshold,
    }

    fg_rows = conn.execute(
        """
        SELECT team, external_true_offense_score, wrc_plus
        FROM fangraphs_team_offense
        WHERE season = ?
          AND date_as_of < ?
        ORDER BY date_as_of DESC
        """,
        (season, as_of_date),
    ).fetchall()

    # Latest FG row per team
    fg_by_team: dict = {}
    for row in fg_rows:
        t = row[0]
        if t not in fg_by_team:
            fg_by_team[t] = {"team": t, "true_offense_score": row[1], "wrc_plus": row[2]}

    ctx_rows = conn.execute(
        """
        SELECT team_abbr, runs_per_game, recent_runs_per_game_7, offense_rating
        FROM mlb_team_context
        WHERE season = ?
        """,
        (season,),
    ).fetchall()

    ctx_by_team: dict = {r[0]: {"runs_per_game": r[1], "recent_7": r[2], "offense_rating": r[3]}
                         for r in ctx_rows}

    cases: list[dict] = []

    for team, fg in fg_by_team.items():
        true_offense = fg.get("true_offense_score")
        if true_offense is None:
            continue
        if true_offense >= true_offense_weak_threshold:
            continue  # not a weak true offense team

        ctx = ctx_by_team.get(team)
        if not ctx:
            continue

        recent_7 = ctx.get("recent_7")
        season_avg = ctx.get("runs_per_game")
        if recent_7 is None or recent_7 < recent_runs_hot_threshold:
            continue  # not hot recently

        cases.append({
            "team": team,
            "true_offense_score": true_offense,
            "wrc_plus": fg.get("wrc_plus"),
            "season_runs_per_game": season_avg,
            "recent_runs_per_game_7": recent_7,
            "offense_rating": ctx.get("offense_rating"),
            "mismatch_note": (
                f"True offense weak ({true_offense:.1f}) but recent 7-game "
                f"scoring hot ({recent_7:.1f})"
            ),
        })

    n = len(cases)
    return PatternResult(
        pattern_name="true_offense_mismatch",
        sample_size=n,
        filters_used=filters,
        as_of_date=as_of_date,
        matching_cases=cases,
        outcome_summary={"tagged_teams": n},
        continuation_rate=None,
        cooldown_rate=None,
        average_rest_of_game_runs=None,
        median_rest_of_game_runs=None,
        threshold_hit_rates={},
        confidence_label=confidence_label(n),
        notes=(
            "Tags teams with low FanGraphs true offense but hot recent scoring. "
            "Read-only analysis — no trade signal."
        ),
        warnings=(_warnings_for_sample(n) if n < 5 else []),
    )


# ── Layered fallback wrappers ─────────────────────────────────────────────────

_FALLBACK_THRESHOLD = 5  # pick first layer with sample_size >= this


def layered_team_total_after_state(
    conn: sqlite3.Connection,
    *,
    team: Optional[str],
    runs_through_inning: int,
    inning: int,
    as_of_date: Optional[str] = None,
    season: Optional[str] = None,
) -> tuple[PatternResult, list[dict], str, bool, str]:
    """
    Try 4 layers in order; return first with sample_size >= _FALLBACK_THRESHOLD.

    Returns: (best_result, all_layers_summary, selected_layer, fallback_used, warning)
    """
    lo, hi = runs_through_inning - 1, runs_through_inning + 1
    kw: dict = dict(inning=inning, as_of_date=as_of_date, season=season)

    layers = [
        ("exact_team_exact_state", summarize_team_total_after_state(
            conn, team=team, runs_through_inning=runs_through_inning, **kw)),
        ("exact_team_nearby_state", summarize_team_total_after_state(
            conn, team=team, runs_through_inning=runs_through_inning,
            runs_range=(lo, hi), **kw)),
        ("league_exact_state", summarize_team_total_after_state(
            conn, team=None, runs_through_inning=runs_through_inning, **kw)),
        ("league_nearby_state", summarize_team_total_after_state(
            conn, team=None, runs_through_inning=runs_through_inning,
            runs_range=(lo, hi), **kw)),
    ]

    all_layers_summary = [
        {"layer": name, "sample_size": r.sample_size, "confidence_label": r.confidence_label}
        for name, r in layers
    ]

    selected_idx = len(layers) - 1
    for i, (_, r) in enumerate(layers):
        if r.sample_size >= _FALLBACK_THRESHOLD:
            selected_idx = i
            break

    selected_name, selected_result = layers[selected_idx]
    exact_sample_size = layers[0][1].sample_size
    fallback_used = selected_idx > 0
    warning = (
        f"Exact sample ({exact_sample_size}) below threshold. "
        f"Using {selected_name} ({selected_result.sample_size} cases)."
        if fallback_used else ""
    )
    return selected_result, all_layers_summary, selected_name, fallback_used, warning


def layered_noisy_inning(
    conn: sqlite3.Connection,
    *,
    min_runs: int = 3,
    team: Optional[str] = None,
    inning: Optional[int] = None,
    as_of_date: Optional[str] = None,
    season: Optional[str] = None,
) -> tuple[PatternResult, list[dict], str, bool, str]:
    """
    Try 3 layers in order; return first with sample_size >= _FALLBACK_THRESHOLD.

    Returns: (best_result, all_layers_summary, selected_layer, fallback_used, warning)
    """
    kw: dict = dict(min_runs=min_runs, as_of_date=as_of_date, season=season)

    layers = [
        ("exact_team_exact_inning", find_noisy_inning_cases(
            conn, team=team, inning=inning, **kw)),
        ("league_exact_inning", find_noisy_inning_cases(
            conn, team=None, inning=inning, **kw)),
        ("league_any_inning", find_noisy_inning_cases(
            conn, team=None, inning=None, **kw)),
    ]

    all_layers_summary = [
        {"layer": name, "sample_size": r.sample_size, "confidence_label": r.confidence_label}
        for name, r in layers
    ]

    selected_idx = len(layers) - 1
    for i, (_, r) in enumerate(layers):
        if r.sample_size >= _FALLBACK_THRESHOLD:
            selected_idx = i
            break

    selected_name, selected_result = layers[selected_idx]
    exact_sample_size = layers[0][1].sample_size
    fallback_used = selected_idx > 0
    warning = (
        f"Exact sample ({exact_sample_size}) below threshold. "
        f"Using {selected_name} ({selected_result.sample_size} cases)."
        if fallback_used else ""
    )
    return selected_result, all_layers_summary, selected_name, fallback_used, warning


def layered_f5_pace(
    conn: sqlite3.Connection,
    *,
    runs_through_inning: int,
    inning: int,
    as_of_date: Optional[str] = None,
    season: Optional[str] = None,
) -> tuple[PatternResult, list[dict], str, bool, str]:
    """
    Try 3 layers in order; return first with sample_size >= _FALLBACK_THRESHOLD.

    Returns: (best_result, all_layers_summary, selected_layer, fallback_used, warning)
    """
    lo1, hi1 = runs_through_inning - 1, runs_through_inning + 1
    lo2, hi2 = runs_through_inning - 2, runs_through_inning + 2
    kw: dict = dict(inning=inning, as_of_date=as_of_date, season=season)

    layers = [
        ("exact_state", summarize_f5_pace(
            conn, runs_through_inning=runs_through_inning, **kw)),
        ("nearby_state", summarize_f5_pace(
            conn, runs_through_inning=runs_through_inning, runs_range=(lo1, hi1), **kw)),
        ("nearby_state_wider", summarize_f5_pace(
            conn, runs_through_inning=runs_through_inning, runs_range=(lo2, hi2), **kw)),
    ]

    all_layers_summary = [
        {"layer": name, "sample_size": r.sample_size, "confidence_label": r.confidence_label}
        for name, r in layers
    ]

    selected_idx = len(layers) - 1
    for i, (_, r) in enumerate(layers):
        if r.sample_size >= _FALLBACK_THRESHOLD:
            selected_idx = i
            break

    selected_name, selected_result = layers[selected_idx]
    exact_sample_size = layers[0][1].sample_size
    fallback_used = selected_idx > 0
    warning = (
        f"Exact sample ({exact_sample_size}) below threshold. "
        f"Using {selected_name} ({selected_result.sample_size} cases)."
        if fallback_used else ""
    )
    return selected_result, all_layers_summary, selected_name, fallback_used, warning


# ── Kalshi hook stub ──────────────────────────────────────────────────────────

def get_nearest_market_snapshots(
    conn: sqlite3.Connection,
    market_ticker: str,
    event_time_utc: str,
    window_seconds: int = 60,
) -> dict:
    """
    Stub: returns empty structure until Kalshi correlation is built in v2.
    Future: query kalshi_orderbook_snapshots for nearest snapshot before/after
    event_time_utc within window_seconds.
    """
    return {"pre_snapshot": None, "post_snapshot": None}
