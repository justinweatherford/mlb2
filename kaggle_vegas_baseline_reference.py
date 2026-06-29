"""
Vegas Baseline Reference v1
============================
Read-only research script. No DB writes. No model changes. No trades.
No brain-vs-Vegas lift -- no season overlap between Kaggle (ends 2021)
and our baseball backfill (starts 2023).

Source: outputs/vegas_odds_import_preview/normalized_preview.csv
"""
import csv
import collections
import math
import statistics
from pathlib import Path

INPUT_CSV = Path("outputs/vegas_odds_import_preview/normalized_preview.csv")
OUT_DIR   = Path("outputs/vegas_baseline_reference")


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_rows():
    with open(INPUT_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Bucket helpers
# ---------------------------------------------------------------------------

def nv_bucket(p: float) -> str:
    if p < 0.35: return "<35%"
    if p < 0.40: return "35-40%"
    if p < 0.45: return "40-45%"
    if p < 0.50: return "45-50%"
    if p < 0.55: return "50-55%"
    if p < 0.60: return "55-60%"
    if p < 0.65: return "60-65%"
    if p < 0.70: return "65-70%"
    return "70%+"

NV_BUCKET_ORDER = [
    "<35%", "35-40%", "40-45%", "45-50%",
    "50-55%", "55-60%", "60-65%", "65-70%", "70%+",
]

NV_BUCKET_MIDPOINT = {
    "<35%":   0.325, "35-40%": 0.375, "40-45%": 0.425, "45-50%": 0.475,
    "50-55%": 0.525, "55-60%": 0.575, "60-65%": 0.625, "65-70%": 0.675,
    "70%+":   0.750,
}


def ml_tier(ml: int) -> str:
    if ml <= -200:         return "heavy_favorite"
    if -199 <= ml <= -151: return "moderate_favorite"
    if -150 <= ml <= -121: return "slight_favorite"
    if -120 <= ml <= -101: return "lean_favorite"
    if ml == 100:          return "pickem"
    if 101 <= ml <= 120:   return "lean_underdog"
    if 121 <= ml <= 150:   return "slight_underdog"
    if 151 <= ml <= 200:   return "moderate_underdog"
    return "long_underdog"

ML_TIER_ORDER = [
    "heavy_favorite", "moderate_favorite", "slight_favorite", "lean_favorite",
    "pickem",
    "lean_underdog", "slight_underdog", "moderate_underdog", "long_underdog",
]


def gt_bucket(gt: float) -> str:
    fl = math.floor(gt)
    return f"{fl}-{fl+1}"


def pr_bucket(pr: float) -> str:
    if pr < 2.0:  return "<2.0"
    if pr >= 7.0: return "7.0+"
    fl = math.floor(pr * 2) / 2
    return f"{fl:.1f}-{fl+0.5:.1f}"

PR_BUCKET_ORDER = (
    ["<2.0"] +
    [f"{i/2:.1f}-{i/2+0.5:.1f}" for i in range(4, 14)] +
    ["7.0+"]
)


def season_group(season: str) -> str:
    if season == "2020": return "2020_shortened"
    if season == "2021": return "2021"
    return "2012-2019"

SEASON_GROUP_ORDER = ["2012-2019", "2020_shortened", "2021"]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

class Tally:
    __slots__ = ("n", "wins")

    def __init__(self):
        self.n    = 0
        self.wins = 0

    def add(self, won: int):
        self.n    += 1
        self.wins += won

    @property
    def win_rate(self):
        return self.wins / self.n if self.n else None

    @property
    def win_rate_pct(self):
        r = self.win_rate
        return f"{100*r:.1f}%" if r is not None else ""


def _safe_int(s):
    try: return int(s)
    except: return None

def _safe_float(s):
    try: return float(s)
    except: return None


# ---------------------------------------------------------------------------
# Computation 1: Moneyline calibration
# ---------------------------------------------------------------------------

def compute_moneyline_calibration(rows):
    buckets = {b: Tally() for b in NV_BUCKET_ORDER}

    for r in rows:
        if r["team_win"] not in ("0", "1"):
            continue
        p_str = r["moneyline_implied_probability_no_vig"]
        if not p_str or p_str == "NA":
            continue
        p   = float(p_str)
        won = int(r["team_win"])
        buckets[nv_bucket(p)].add(won)

    out = []
    for b in NV_BUCKET_ORDER:
        t = buckets[b]
        out.append({
            "prob_bucket":           b,
            "implied_prob_midpoint": NV_BUCKET_MIDPOINT[b],
            "n_team_rows":           t.n,
            "actual_wins":           t.wins,
            "actual_win_rate":       round(t.win_rate, 4) if t.win_rate is not None else "",
            "actual_win_rate_pct":   t.win_rate_pct,
            "calibration_error":     (
                round(t.win_rate - NV_BUCKET_MIDPOINT[b], 4)
                if t.win_rate is not None else ""
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Computation 2: Favorite / underdog summary
# ---------------------------------------------------------------------------

def compute_fav_dog_summary(rows):
    categories = {k: Tally() for k in [
        "all_favorite", "all_underdog", "pickem",
        "home_all", "away_all",
        "home_favorite", "home_underdog",
        "away_favorite", "away_underdog",
    ]}

    for r in rows:
        if r["team_win"] not in ("0", "1"):
            continue
        ml = _safe_int(r["moneyline"])
        if ml is None:
            continue
        won      = int(r["team_win"])
        is_fav   = ml < 0
        is_dog   = ml > 0
        is_pick  = ml == 100
        reliable = r["dh_assignment_reliable"] == "1"
        is_home  = r["is_home"] == "1"
        is_away  = r["is_home"] == "0"

        if is_fav:  categories["all_favorite"].add(won)
        if is_dog:  categories["all_underdog"].add(won)
        if is_pick: categories["pickem"].add(won)

        if not reliable:
            continue

        if is_home: categories["home_all"].add(won)
        if is_away: categories["away_all"].add(won)
        if is_home and is_fav: categories["home_favorite"].add(won)
        if is_home and is_dog: categories["home_underdog"].add(won)
        if is_away and is_fav: categories["away_favorite"].add(won)
        if is_away and is_dog: categories["away_underdog"].add(won)

    NOTES = {
        "all_favorite":  "ML < 0; all home/away",
        "all_underdog":  "ML > 0; all home/away",
        "pickem":        "ML = +100 exactly (dog side of 570 pickem games)",
        "home_all":      "All home teams (dh_assignment_reliable=1 only)",
        "away_all":      "All away teams (dh_assignment_reliable=1 only)",
        "home_favorite": "Home team AND ML < 0",
        "home_underdog": "Home team AND ML > 0",
        "away_favorite": "Away team AND ML < 0",
        "away_underdog": "Away team AND ML > 0",
    }

    return [
        {
            "category":     cat,
            "n":            categories[cat].n,
            "wins":         categories[cat].wins,
            "win_rate":     round(categories[cat].win_rate, 4) if categories[cat].win_rate is not None else "",
            "win_rate_pct": categories[cat].win_rate_pct,
            "note":         NOTES[cat],
        }
        for cat in [
            "all_favorite", "all_underdog", "pickem",
            "home_all", "away_all",
            "home_favorite", "home_underdog",
            "away_favorite", "away_underdog",
        ]
    ]


# ---------------------------------------------------------------------------
# Computation 3: ML price tier outcomes
# ---------------------------------------------------------------------------

def compute_ml_tier_outcomes(rows):
    tiers     = {t: Tally() for t in ML_TIER_ORDER}
    tier_pvec = collections.defaultdict(list)

    for r in rows:
        if r["team_win"] not in ("0", "1"):
            continue
        ml = _safe_int(r["moneyline"])
        if ml is None:
            continue
        won = int(r["team_win"])
        t   = ml_tier(ml)
        tiers[t].add(won)

        p_str = r["moneyline_implied_probability_no_vig"]
        if p_str and p_str not in ("", "NA"):
            tier_pvec[t].append(float(p_str))

    out = []
    for t in ML_TIER_ORDER:
        tl   = tiers[t]
        pvec = tier_pvec[t]
        out.append({
            "ml_tier":             t,
            "n":                   tl.n,
            "wins":                tl.wins,
            "actual_win_rate":     round(tl.win_rate, 4) if tl.win_rate is not None else "",
            "actual_win_rate_pct": tl.win_rate_pct,
            "mean_no_vig_prob":    round(statistics.mean(pvec), 4) if pvec else "",
            "mean_no_vig_prob_pct":f"{100*statistics.mean(pvec):.1f}%" if pvec else "",
            "calibration_error":   (
                round(tl.win_rate - statistics.mean(pvec), 4)
                if tl.win_rate is not None and pvec else ""
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Computation 4: Team scoring by projected_runs bucket
# ---------------------------------------------------------------------------

def compute_team_scoring(rows):
    class ScoreTally:
        def __init__(self):
            self.n = self.r4 = self.r5 = self.u5 = 0
        def add(self, r4, r5, u5):
            self.n  += 1
            self.r4 += r4
            self.r5 += r5
            self.u5 += u5

    buckets_all      = collections.defaultdict(ScoreTally)
    buckets_by_group = collections.defaultdict(lambda: collections.defaultdict(ScoreTally))

    for r in rows:
        pr_str = r["projected_runs"]
        if not pr_str or pr_str == "NA":
            continue
        if r["team_runs_4plus"] not in ("0", "1"):
            continue

        pr  = float(pr_str)
        b   = pr_bucket(pr)
        sg  = season_group(r["season"])
        r4  = int(r["team_runs_4plus"])
        r5  = int(r["team_runs_5plus"])
        u5  = int(r["team_runs_under_5"])

        buckets_all[b].add(r4, r5, u5)
        buckets_by_group[sg][b].add(r4, r5, u5)

    out = []
    for b in PR_BUCKET_ORDER:
        for sg in ["all"] + SEASON_GROUP_ORDER:
            tally = (buckets_all[b] if sg == "all" else buckets_by_group[sg][b])
            if tally.n < 10:
                continue
            out.append({
                "pr_bucket":              b,
                "season_group":           sg,
                "n":                      tally.n,
                "team_runs_4plus_rate":   round(tally.r4 / tally.n, 4),
                "team_runs_5plus_rate":   round(tally.r5 / tally.n, 4),
                "team_runs_under5_rate":  round(tally.u5 / tally.n, 4),
                "team_runs_4plus_pct":    f"{100*tally.r4/tally.n:.1f}%",
                "team_runs_5plus_pct":    f"{100*tally.r5/tally.n:.1f}%",
                "team_runs_under5_pct":   f"{100*tally.u5/tally.n:.1f}%",
                "caveat":                 "projected_runs_methodology_unconfirmed",
            })
    return out


# ---------------------------------------------------------------------------
# Computation 5: Game total baselines
# ---------------------------------------------------------------------------

def compute_game_total_baselines(rows):
    # Deduplicate by game_id -- game_over/under/push are game-level, not team-level
    seen_games = set()
    deduped    = []
    for r in rows:
        gid = r["game_id"]
        if gid not in seen_games:
            seen_games.add(gid)
            deduped.append(r)

    class TotalTally:
        def __init__(self):
            self.n = self.over = self.under = self.push = 0
        def add(self, go, gu, push):
            self.n     += 1
            self.over  += go
            self.under += gu
            self.push  += push

    by_bucket = collections.defaultdict(TotalTally)
    by_season = {s: TotalTally() for s in
                 ["2012","2013","2014","2015","2016","2017","2018","2019","2020","2021"]}
    by_group  = {g: TotalTally() for g in SEASON_GROUP_ORDER}

    for r in deduped:
        gt_str = r["game_total"]
        if not gt_str or gt_str == "NA":
            continue
        go   = 1 if r["game_over"]  == "1" else 0
        gu   = 1 if r["game_under"] == "1" else 0
        push = 1 if r["total_push"] == "1" else 0

        b   = gt_bucket(float(gt_str))
        sg  = season_group(r["season"])
        sea = r["season"]

        by_bucket[b].add(go, gu, push)
        if sea in by_season:
            by_season[sea].add(go, gu, push)
        by_group[sg].add(go, gu, push)

    def tally_row(label, label_type, tally):
        if tally.n == 0:
            return None
        return {
            "label_type":  label_type,
            "label":       label,
            "n_games":     tally.n,
            "over_count":  tally.over,
            "under_count": tally.under,
            "push_count":  tally.push,
            "over_rate":   round(tally.over  / tally.n, 4),
            "under_rate":  round(tally.under / tally.n, 4),
            "push_rate":   round(tally.push  / tally.n, 4),
            "over_pct":    f"{100*tally.over /tally.n:.1f}%",
            "under_pct":   f"{100*tally.under/tally.n:.1f}%",
            "push_pct":    f"{100*tally.push /tally.n:.1f}%",
        }

    out = []
    for b in sorted(by_bucket, key=lambda x: float(x.split("-")[0])):
        row = tally_row(b, "total_bucket", by_bucket[b])
        if row and row["n_games"] >= 20:
            out.append(row)

    for sea in sorted(by_season):
        row = tally_row(sea, "season", by_season[sea])
        if row:
            out.append(row)

    for sg in SEASON_GROUP_ORDER:
        row = tally_row(sg, "season_group", by_group[sg])
        if row:
            out.append(row)

    return out


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def write_csv(rows, path):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  Wrote {len(rows)} rows -> {path}")


# ---------------------------------------------------------------------------
# Summary markdown
# ---------------------------------------------------------------------------

def write_summary(cal_rows, fav_rows, scoring_rows, total_rows, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cal_map = {r["prob_bucket"]: r for r in cal_rows}
    fav_map = {r["category"]: r for r in fav_rows}

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Vegas Baseline Reference v1\n\n")
        f.write("> **Sportsbook baseline research only.**  \n")
        f.write("> No brain-vs-Vegas lift measured here.  \n")
        f.write("> No Kalshi EV claimed or implied.  \n")
        f.write("> No trades. No paper entries. No model changes.\n\n")
        f.write("---\n\n")

        f.write("## Scope and Limitations\n\n")
        f.write("| Item | Value |\n|---|---|\n")
        f.write("| Source | Kaggle MLB Vegas odds (oddsDataMLB.csv + oddsData.csv) |\n")
        f.write("| Date range | 2012-03-28 to 2021-10-03 |\n")
        f.write("| Seasons | 2012-2021 (2020 shortened to 60 games) |\n")
        f.write("| Total team-rows | 45,530 (22,765 games, 2 rows each) |\n")
        f.write("| Brain backfill starts | 2023 — **no season overlap with this dataset** |\n\n")
        f.write("> Brain-vs-Vegas calibration cannot be built until we have overlapping seasons.  \n")
        f.write("> This baseline will be the reference point once that overlap exists.\n\n")
        f.write("---\n\n")

        f.write("## 1. Moneyline Calibration (No-Vig)\n\n")
        f.write("No-vig implied probability vs actual win rate.\n")
        f.write("Calibration error = actual - implied midpoint; well-calibrated lines hover near 0.00.\n\n")
        f.write("| Bucket | Midpoint | N | Actual Win% | Error |\n|---|---|---|---|---|\n")
        for r in cal_rows:
            err = f"{float(r['calibration_error']):+.3f}" if r["calibration_error"] != "" else "—"
            f.write(
                f"| {r['prob_bucket']} | {r['implied_prob_midpoint']:.1%} |"
                f" {r['n_team_rows']:,} | {r['actual_win_rate_pct']} | {err} |\n"
            )
        f.write("\n---\n\n")

        f.write("## 2. Favorite / Underdog Behavior\n\n")
        f.write("| Category | N | Win% | Note |\n|---|---|---|---|\n")
        for r in fav_rows:
            f.write(f"| {r['category']} | {r['n']:,} | {r['win_rate_pct']} | {r['note']} |\n")
        f.write("\n> Home/away splits exclude the 11 `dh_assignment_reliable=0` rows.\n\n")
        f.write("---\n\n")

        f.write("## 3. Team Scoring Baselines (projected_runs)\n\n")
        f.write("> **CAVEAT: `projected_runs` methodology is unconfirmed.**  \n")
        f.write("> It is NOT a sportsbook team-total market line (only 4.4% of rows equal total/2).  \n")
        f.write("> Treat as an unconfirmed model projection. Do not use for direct EV inference.\n\n")
        f.write("All seasons (2012-2021):\n\n")
        f.write("| PR Bucket | N | 4+ Rate | 5+ Rate | <5 Rate |\n|---|---|---|---|---|\n")
        for r in scoring_rows:
            if r["season_group"] != "all":
                continue
            f.write(
                f"| {r['pr_bucket']} | {r['n']:,} |"
                f" {r['team_runs_4plus_pct']} | {r['team_runs_5plus_pct']} |"
                f" {r['team_runs_under5_pct']} |\n"
            )
        f.write("\nFull season-group breakdown in `team_scoring_by_projected_runs.csv`.\n\n")
        f.write("---\n\n")

        f.write("## 4. Game Total Baselines\n\n")
        f.write("**By game total bucket (game count, deduplicated to one row per game):**\n\n")
        f.write("| Total | Games | Over% | Under% | Push% |\n|---|---|---|---|---|\n")
        for r in total_rows:
            if r["label_type"] != "total_bucket":
                continue
            f.write(
                f"| {r['label']} | {r['n_games']:,} |"
                f" {r['over_pct']} | {r['under_pct']} | {r['push_pct']} |\n"
            )

        f.write("\n**By season:**\n\n")
        f.write("| Season | Games | Over% | Under% | Push% | Note |\n|---|---|---|---|---|---|\n")
        for r in total_rows:
            if r["label_type"] != "season":
                continue
            note = "60-game season" if r["label"] == "2020" else ""
            f.write(
                f"| {r['label']} | {r['n_games']:,} |"
                f" {r['over_pct']} | {r['under_pct']} | {r['push_pct']} | {note} |\n"
            )

        f.write("\n**By season group:**\n\n")
        f.write("| Group | Games | Over% | Under% | Push% |\n|---|---|---|---|---|\n")
        for r in total_rows:
            if r["label_type"] != "season_group":
                continue
            f.write(
                f"| {r['label']} | {r['n_games']:,} |"
                f" {r['over_pct']} | {r['under_pct']} | {r['push_pct']} |\n"
            )
        f.write("\n---\n\n")

        f.write("## What This Establishes\n\n")
        f.write("| Use | Status |\n|---|---|\n")
        f.write("| Moneyline calibration baseline (2012-2021) | Done |\n")
        f.write("| Favorite/underdog win rates | Done |\n")
        f.write("| Home vs away win rates | Done |\n")
        f.write("| ML price-tier outcomes (9 tiers) | Done |\n")
        f.write("| Team scoring rates by projected_runs (with caveat) | Done |\n")
        f.write("| Game total over/under/push baseline | Done |\n\n")

        f.write("## What This Does Not Do\n\n")
        f.write("| Limitation | Reason |\n|---|---|\n")
        f.write("| Brain-vs-Vegas lift | No season overlap: Kaggle ends 2021, backfill starts 2023 |\n")
        f.write("| Kalshi EV inference | Sportsbook lines != Kalshi market prices |\n")
        f.write("| Live EV | Historical final scores only, no in-game lines |\n")
        f.write("| F5 market benchmarks | Full-game lines only |\n")
        f.write("| True team-total sportsbook calibration | projectedRuns methodology unconfirmed |\n")
        f.write("| 2022+ calibration | Dataset ends 2021 |\n\n")

        f.write("## Next Step (when ready)\n\n")
        f.write("Once our baseball backfill extends to 2021 or the Kaggle data is extended to 2023+,  \n")
        f.write("compare brain-predicted win probabilities to this Vegas baseline to measure calibration lift.\n\n")
        f.write("---\n\n")
        f.write("> **Sportsbook baseline research only. No EV. No trades. No paper entries. No model changes.**\n")

    print(f"  Wrote -> {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Vegas Baseline Reference v1")
    print(f"  Input: {INPUT_CSV}")
    print()

    print("Loading rows...")
    rows = load_rows()
    print(f"  {len(rows):,} rows loaded")
    print()

    print("Computing moneyline calibration...")
    cal_rows = compute_moneyline_calibration(rows)

    print("Computing favorite/underdog summary...")
    fav_rows = compute_fav_dog_summary(rows)

    print("Computing ML price tier outcomes...")
    ml_tier_rows = compute_ml_tier_outcomes(rows)

    print("Computing team scoring baselines...")
    scoring_rows = compute_team_scoring(rows)

    print("Computing game total baselines...")
    total_rows = compute_game_total_baselines(rows)

    print()
    print("Writing outputs...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    write_csv(cal_rows,     OUT_DIR / "vegas_moneyline_calibration.csv")
    write_csv(fav_rows,     OUT_DIR / "favorite_underdog_summary.csv")
    write_csv(ml_tier_rows, OUT_DIR / "ml_price_tier_outcomes.csv")
    write_csv(scoring_rows, OUT_DIR / "team_scoring_by_projected_runs.csv")
    write_csv(total_rows,   OUT_DIR / "game_total_baselines.csv")
    write_summary(cal_rows, fav_rows, scoring_rows, total_rows,
                  OUT_DIR / "vegas_baseline_reference_summary.md")

    # Terminal summary
    print()
    print("=" * 60)
    print("VEGAS BASELINE REFERENCE v1 -- KEY NUMBERS")
    print("=" * 60)
    fav_map = {r["category"]: r for r in fav_rows}
    print(f"  Favorite win rate:   {fav_map['all_favorite']['win_rate_pct']}"
          f"  (n={fav_map['all_favorite']['n']:,})")
    print(f"  Underdog win rate:   {fav_map['all_underdog']['win_rate_pct']}"
          f"  (n={fav_map['all_underdog']['n']:,})")
    print(f"  Home team win rate:  {fav_map['home_all']['win_rate_pct']}"
          f"  (n={fav_map['home_all']['n']:,})")
    print(f"  Away team win rate:  {fav_map['away_all']['win_rate_pct']}"
          f"  (n={fav_map['away_all']['n']:,})")
    print(f"  Home fav win rate:   {fav_map['home_favorite']['win_rate_pct']}"
          f"  (n={fav_map['home_favorite']['n']:,})")
    print(f"  Home dog win rate:   {fav_map['home_underdog']['win_rate_pct']}"
          f"  (n={fav_map['home_underdog']['n']:,})")
    print(f"  Away fav win rate:   {fav_map['away_favorite']['win_rate_pct']}"
          f"  (n={fav_map['away_favorite']['n']:,})")
    print(f"  Away dog win rate:   {fav_map['away_underdog']['win_rate_pct']}"
          f"  (n={fav_map['away_underdog']['n']:,})")
    print()
    print("  Moneyline calibration (no-vig bucket vs actual win%):")
    for b in NV_BUCKET_ORDER:
        r = cal_map = {r2["prob_bucket"]: r2 for r2 in cal_rows}.get(b)
        if r and r["n_team_rows"] > 0:
            err = f"{float(r['calibration_error']):+.3f}" if r["calibration_error"] != "" else "  --  "
            print(f"    {b:8s}: implied={NV_BUCKET_MIDPOINT[b]:.1%}"
                  f"  actual={r['actual_win_rate_pct']:6s}  err={err}"
                  f"  n={r['n_team_rows']:,}")
    print()
    print("Outputs ->", OUT_DIR)
    print()
    print("SPORTSBOOK BASELINE ONLY. No EV. No brain-vs-Vegas. No trades.")
    print("No season overlap: Kaggle ends 2021, backfill starts 2023.")


if __name__ == "__main__":
    main()
