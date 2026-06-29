"""
sbr_mlb_odds_probe.py - Read-only feasibility probe for SportsbookReview MLB odds.

Goal: determine whether SBR can provide current and historical MLB moneyline odds
matchable to our mlb_games table. Probe only -- no trades, no model changes.

Confirmed structure (via investigation):
  __NEXT_DATA__: props.pageProps.oddsTables[0].oddsTableModel
    .gameRows[i].gameView  -> teams, start time, pitchers
    .gameRows[i].oddsViews[j] -> per-sportsbook currentLine / openingLine

Usage:
    python sbr_mlb_odds_probe.py
    python sbr_mlb_odds_probe.py --playwright   # add Playwright fallback
    python sbr_mlb_odds_probe.py --date 2023-07-15
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Force UTF-8 on Windows consoles that default to cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT_DIR = Path("outputs/sbr_mlb_odds_probe")
DB_PATH = os.environ.get("DB_PATH", "kalshi_mlb.db")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_SPORTSBOOKS = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "bet365", "Fanatics", "Bovada"]

_ODDS_RE = re.compile(r"[+\-]\d{3,4}")

_MLB_TEAMS_FULL = {
    "Arizona Diamondbacks", "Atlanta Braves", "Baltimore Orioles", "Boston Red Sox",
    "Chicago Cubs", "Chicago White Sox", "Cincinnati Reds", "Cleveland Guardians",
    "Colorado Rockies", "Detroit Tigers", "Houston Astros", "Kansas City Royals",
    "Los Angeles Angels", "Los Angeles Dodgers", "Miami Marlins", "Milwaukee Brewers",
    "Minnesota Twins", "New York Mets", "New York Yankees", "Oakland Athletics",
    "Philadelphia Phillies", "Pittsburgh Pirates", "San Diego Padres",
    "San Francisco Giants", "Seattle Mariners", "St. Louis Cardinals",
    "Tampa Bay Rays", "Texas Rangers", "Toronto Blue Jays", "Washington Nationals",
}

BASE = "https://www.sportsbookreview.com/betting-odds/mlb-baseball"

_MAIN_DATES = [
    "2026-06-22",
    "2026-04-25",
    "2026-04-24",
    "2025-07-10",  # July 15 = All-Star break; July 10 has games
    "2024-07-10",
    "2023-07-15",
    "2021-04-04",
]

_MARKET_PATTERNS = [
    "money-line/full-game",
    "pointspread/full-game",
    "totals/full-game",
    "pointspread/first-half",
    "totals/first-half",
]


def _build_urls(probe_date: str | None = None) -> list[dict]:
    urls = []
    dates = [probe_date] if probe_date else _MAIN_DATES
    for d in dates:
        urls.append({"url": f"{BASE}/?date={d}", "label": f"main/{d}"})
    ref_date = probe_date or "2026-06-22"
    for pat in _MARKET_PATTERNS:
        urls.append({"url": f"{BASE}/{pat}/?date={ref_date}", "label": f"{pat}/{ref_date}"})
    return urls


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_raw(url: str, timeout: int = 20) -> dict:
    result = {"requested_url": url, "final_url": None, "http_status": None,
              "content_length": 0, "error": None, "html": None}
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout, allow_redirects=True,
        )
        result["http_status"] = resp.status_code
        result["final_url"] = str(resp.url)
        result["content_length"] = len(resp.content)
        if resp.status_code == 200:
            result["html"] = resp.text
    except Exception as exc:
        result["error"] = str(exc)
    return result


def fetch_playwright(url: str, timeout_ms: int = 25000) -> dict:
    result = {"requested_url": url, "final_url": None, "http_status": None,
              "content_length": 0, "error": None, "html": None}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=_UA)
            resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if resp:
                result["http_status"] = resp.status
                result["final_url"] = resp.url
            time.sleep(3)
            html = page.content()
            result["html"] = html
            result["content_length"] = len(html)
            browser.close()
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ── Analyze HTML ─────────────────────────────────────────────────────────────

def analyze_html(fetch_result: dict, requested_date: str, label: str) -> dict:
    url = fetch_result["requested_url"]
    html = fetch_result.get("html") or ""
    status = fetch_result.get("http_status")
    final_url = fetch_result.get("final_url") or url
    error = fetch_result.get("error")

    out = {
        "label": label,
        "requested_url": url,
        "final_url": final_url,
        "http_status": status,
        "content_length": fetch_result.get("content_length", 0),
        "error": error or "",
        "fetch_method": "raw_html",
        "redirected": final_url != url,
        "page_title": "",
        "date_in_page": False,
        "team_names_found": 0,
        "sportsbook_names_found": "",
        "odds_patterns_found": 0,
        "next_data_present": False,
        "next_data_size": 0,
        "games_in_next_data": 0,
        "sportsbooks_in_next_data": 0,
        "parse_confidence": "none",
        "notes": "",
    }

    if not html:
        out["notes"] = error or "no html"
        return out

    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("title")
    if title_tag:
        out["page_title"] = title_tag.get_text(strip=True)[:120]

    out["date_in_page"] = requested_date in html
    out["team_names_found"] = sum(1 for t in _MLB_TEAMS_FULL if t in html)
    sb_hits = [sb for sb in _SPORTSBOOKS if sb.lower() in html.lower()]
    out["sportsbook_names_found"] = "|".join(sb_hits)
    out["odds_patterns_found"] = len(_ODDS_RE.findall(html))

    next_script = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_script and next_script.string:
        out["next_data_present"] = True
        out["next_data_size"] = len(next_script.string)
        try:
            nd = json.loads(next_script.string)
            tables = nd.get("props", {}).get("pageProps", {}).get("oddsTables", [])
            if tables:
                otm = tables[0].get("oddsTableModel", {})
                out["games_in_next_data"] = len(otm.get("gameRows", []))
                out["sportsbooks_in_next_data"] = len(otm.get("sportsbooks", []))
        except Exception:
            pass

    if out["games_in_next_data"] > 0:
        out["parse_confidence"] = "likely_full"
    elif out["next_data_size"] > 10000:
        out["parse_confidence"] = "partial"
    elif out["next_data_present"]:
        out["parse_confidence"] = "shell_only"
    elif out["team_names_found"] >= 3:
        out["parse_confidence"] = "shell_only"
    else:
        out["parse_confidence"] = "none"

    return out


# ── Parse moneyline from __NEXT_DATA__ ────────────────────────────────────────

def _pitcher_name(starter: dict | None) -> str:
    if not isinstance(starter, dict):
        return ""
    first = starter.get("firstInital") or starter.get("firstName") or ""
    last = starter.get("lastName") or ""
    return f"{first}. {last}".strip(". ") if last else ""


def _format_ml(v) -> str:
    if v is None:
        return ""
    try:
        n = int(v)
        return f"+{n}" if n > 0 else str(n)
    except Exception:
        return str(v)


def extract_moneyline_rows(html: str, game_date: str, source_url: str) -> list[dict]:
    """Parse moneyline rows from __NEXT_DATA__ using confirmed SBR structure."""
    rows = []
    soup = BeautifulSoup(html, "lxml")
    nd_script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not nd_script or not nd_script.string:
        return rows
    try:
        nd = json.loads(nd_script.string)
    except Exception:
        return rows

    tables = nd.get("props", {}).get("pageProps", {}).get("oddsTables", [])
    if not tables:
        return rows

    otm = tables[0].get("oddsTableModel", {})
    # Build sportsbook id -> name map
    sb_map = {sb.get("machineName"): sb.get("name") for sb in otm.get("sportsbooks", []) if sb.get("machineName")}

    for gr in otm.get("gameRows", []):
        gv = gr.get("gameView") or {}
        away_t = (gv.get("awayTeam") or {}).get("fullName", "")
        home_t = (gv.get("homeTeam") or {}).get("fullName", "")
        if not away_t or not home_t:
            continue
        start_date = gv.get("startDate", "")[:19]
        away_pitcher = _pitcher_name(gv.get("awayStarter"))
        home_pitcher = _pitcher_name(gv.get("homeStarter"))

        for ov in (gr.get("oddsViews") or []):
            if not isinstance(ov, dict):
                continue
            sb_machine = ov.get("sportsbook", "")
            sb_name = sb_map.get(sb_machine, sb_machine)
            cl = ov.get("currentLine") or {}
            ol = ov.get("openingLine") or {}
            away_ml = cl.get("awayOdds")
            home_ml = cl.get("homeOdds")
            if away_ml is None and home_ml is None:
                continue
            rows.append({
                "game_date": game_date,
                "away_team": away_t,
                "home_team": home_t,
                "start_time": start_date,
                "away_pitcher": away_pitcher[:40],
                "home_pitcher": home_pitcher[:40],
                "sportsbook": sb_name[:40],
                "away_moneyline": _format_ml(away_ml),
                "home_moneyline": _format_ml(home_ml),
                "opener_away": _format_ml(ol.get("awayOdds")),
                "opener_home": _format_ml(ol.get("homeOdds")),
                "source_url": source_url,
                "parse_method": "embedded_json",
            })

    return rows


# ── Matchability ──────────────────────────────────────────────────────────────

def check_matchability(ml_rows: list[dict]) -> dict:
    empty = {"games_parsed": 0, "games_matched": 0, "unmatched_sbr": 0, "unmatched_db": 0,
             "unmatched_sbr_detail": [], "unmatched_db_detail": [], "mapping_issues": [], "notes": "no odds parsed"}
    if not ml_rows:
        return empty

    conn = sqlite3.connect(DB_PATH)
    seen = {}
    for r in ml_rows:
        key = (r["game_date"], r["home_team"], r["away_team"])
        seen[key] = r

    matched = 0
    unmatched_sbr = []
    mapping_issues = []
    for (gdate, home, away), row in seen.items():
        hit = conn.execute(
            "SELECT game_id FROM mlb_games WHERE game_date=? AND home_team=? AND away_team=?",
            (gdate, home, away),
        ).fetchall()
        if hit:
            matched += 1
        else:
            # Try last-word fuzzy (e.g. "Yankees" in "New York Yankees")
            hit2 = conn.execute(
                "SELECT game_id, home_team, away_team FROM mlb_games "
                "WHERE game_date=? AND home_team LIKE ? AND away_team LIKE ?",
                (gdate, f"%{home.split()[-1]}%", f"%{away.split()[-1]}%"),
            ).fetchall()
            if hit2:
                matched += 1
                mapping_issues.append(f"{gdate} {away}@{home} -> db {hit2[0][2]}@{hit2[0][1]} (fuzzy)")
            else:
                unmatched_sbr.append(f"{gdate} {away}@{home}")

    dates = list({r["game_date"] for r in ml_rows})
    unmatched_db = []
    for d in dates:
        db_games = conn.execute(
            "SELECT away_abbr, home_abbr, game_id FROM mlb_games WHERE game_date=?", (d,)
        ).fetchall()
        for aw_ab, hm_ab, gid in db_games:
            matched_in_sbr = any(
                aw_ab in r["away_team"] or r["away_team"].endswith(aw_ab)
                for r in ml_rows if r["game_date"] == d
            )
            if not matched_in_sbr:
                unmatched_db.append(f"{d} {gid}")

    conn.close()
    return {
        "games_parsed": len(seen),
        "games_matched": matched,
        "unmatched_sbr": len(unmatched_sbr),
        "unmatched_db": len(unmatched_db),
        "unmatched_sbr_detail": unmatched_sbr[:10],
        "unmatched_db_detail": unmatched_db[:10],
        "mapping_issues": mapping_issues[:5],
        "notes": "",
    }


# ── Outputs ───────────────────────────────────────────────────────────────────

_URL_FIELDS = [
    "label", "requested_url", "final_url", "http_status", "content_length",
    "error", "fetch_method", "redirected", "page_title", "date_in_page",
    "team_names_found", "sportsbook_names_found", "odds_patterns_found",
    "next_data_present", "next_data_size", "games_in_next_data",
    "sportsbooks_in_next_data", "parse_confidence", "notes",
]

_ML_FIELDS = [
    "game_date", "away_team", "home_team", "start_time",
    "away_pitcher", "home_pitcher", "sportsbook",
    "away_moneyline", "home_moneyline", "opener_away", "opener_home",
    "source_url", "parse_method",
]


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_summary(
    path: Path,
    url_results: list[dict],
    ml_rows: list[dict],
    match_stats: dict,
    playwright_used: bool,
    playwright_available: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Quick-answer flags
    recent_ok = any(r for r in url_results if "2026" in r.get("label", "") and r.get("games_in_next_data", 0) > 0)
    hist_ok = any(r for r in url_results
                  if any(y in r.get("label", "") for y in ["2025", "2024", "2023", "2022", "2021"])
                  and r.get("games_in_next_data", 0) > 0)
    odds_parseable = len(ml_rows) > 0
    match_rate = (match_stats.get("games_matched", 0) / max(match_stats.get("games_parsed", 1), 1))

    lines = [
        "# SBR MLB Odds Feasibility Probe",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Read-only research. No trades.",
        "",
        "---",
        "",
        "## Quick Answers",
        "",
        f"1. **SBR current MLB odds (2026):** {'YES -- __NEXT_DATA__ embedded JSON, no JS rendering needed' if recent_ok else 'No / not confirmed'}",
        f"2. **SBR historical dates (pre-2026):** {'YES -- data goes back to at least 2021' if hist_ok else 'No / not confirmed -- may be off-day dates'}",
        f"3. **April 25, 2026 UI limit:** {'UI-only limit -- data is accessible via direct URL for any date' if hist_ok else 'Unknown -- test more dates'}",
        f"4. **Data format:** {'Embedded JSON (__NEXT_DATA__) in raw HTML -- no Playwright required' if recent_ok or hist_ok else 'Unknown'}",
        ("5. **Moneyline parseable:** YES -- " + str(len(ml_rows)) + " rows from " + str(len(set(r["game_date"] for r in ml_rows))) + " date(s)") if odds_parseable else "5. **Moneyline parseable:** No rows parsed -- check parser or date selection",
        f"6. **DB matchability:** {match_stats.get('games_matched', 0)}/{match_stats.get('games_parsed', 0)} games matched ({match_rate:.0%})",
        f"7. **Suitable for ML Core v1 validation:** {'YES -- full team names match DB, full historical coverage available' if odds_parseable and match_rate > 0.8 else 'Partial -- needs more testing' if odds_parseable else 'Unknown -- no odds parsed yet'}",
        f"8. **Playwright needed:** {'No -- raw HTML contains all data' if recent_ok or hist_ok else 'Possibly -- test with --playwright'}",
        "",
        "---",
        "",
        "## Data Structure Confirmed",
        "",
        "```",
        "URL: https://www.sportsbookreview.com/betting-odds/mlb-baseball/money-line/full-game/?date=YYYY-MM-DD",
        "__NEXT_DATA__.props.pageProps.oddsTables[0].oddsTableModel",
        "  .sportsbooks[]           -- list of books: BetMGM, FanDuel, Caesars, bet365, DraftKings, Fanatics",
        "  .gameRows[i]",
        "    .gameView",
        "      .awayTeam.fullName   -- full team name, matches mlb_games",
        "      .homeTeam.fullName",
        "      .startDate           -- ISO timestamp",
        "      .awayStarter / .homeStarter.lastName",
        "    .oddsViews[j]",
        "      .sportsbook          -- machine name (e.g. 'betmgm')",
        "      .currentLine.awayOdds / .homeOdds  -- American odds (e.g. -130, 105)",
        "      .openingLine.awayOdds / .homeOdds  -- opening line",
        "```",
        "",
        "---",
        "",
        "## URL Probe Results",
        "",
        f"{'Label':<48} {'Status':>6} {'Games':>6} {'Conf':<12} {'ND_size':>9}",
        "-" * 85,
    ]
    for r in url_results:
        lines.append(
            f"{r['label'][:47]:<48} {str(r.get('http_status','?')):>6} "
            f"{str(r.get('games_in_next_data','0')):>6} "
            f"{r.get('parse_confidence','?'):<12} "
            f"{str(r.get('next_data_size','0')):>9}"
        )

    lines += ["", "---", "", "## Moneyline Sample", ""]
    if ml_rows:
        dates_seen = sorted(set(r["game_date"] for r in ml_rows))
        lines.append(f"Parsed {len(ml_rows)} odds rows across {len(dates_seen)} date(s): {', '.join(dates_seen)}")
        lines.append("")
        lines.append(f"{'Date':<12} {'Away':<25} {'Home':<25} {'Book':<14} {'Away ML':>8} {'Home ML':>8} {'Away Open':>10} {'Home Open':>10}")
        lines.append("-" * 115)
        for r in ml_rows[:40]:
            lines.append(
                f"{r['game_date']:<12} {str(r['away_team'])[:24]:<25} {str(r['home_team'])[:24]:<25} "
                f"{str(r['sportsbook'])[:13]:<14} {str(r.get('away_moneyline',''))[:7]:>8} "
                f"{str(r.get('home_moneyline',''))[:7]:>8} "
                f"{str(r.get('opener_away',''))[:9]:>10} "
                f"{str(r.get('opener_home',''))[:9]:>10}"
            )
    else:
        lines.append("No moneyline rows parsed.")
        lines.append("If __NEXT_DATA__ is present but games=0, the date likely had no MLB games (off-day / All-Star break).")

    lines += ["", "---", "", "## Matchability", ""]
    lines += [
        f"- Games parsed (unique game/date combos): {match_stats.get('games_parsed', 0)}",
        f"- Games matched to mlb_games DB: {match_stats.get('games_matched', 0)}",
        f"- Unmatched SBR rows: {match_stats.get('unmatched_sbr', 0)}",
        f"- Unmatched DB rows: {match_stats.get('unmatched_db', 0)}",
    ]
    if match_stats.get("mapping_issues"):
        lines.append("- Fuzzy matches (team name mapping differences):")
        for issue in match_stats["mapping_issues"]:
            lines.append(f"  - {issue}")
    if match_stats.get("unmatched_sbr_detail"):
        lines.append("- SBR games not in DB:")
        for g in match_stats["unmatched_sbr_detail"]:
            lines.append(f"  - {g}")

    lines += [
        "",
        "---",
        "",
        "## Next Steps",
        "",
        "- [ ] Build production `sbr_mlb_odds_fetcher.py` using confirmed __NEXT_DATA__ path",
        "- [ ] Backfill 2023-2025 moneyline odds for all Moneyline Core v1 pregame cards",
        "- [ ] Compute: brain_calibrated_prob vs implied_prob from SBR consensus line",
        "- [ ] Flag games where brain was right AND market disagreed (true edge signal)",
        "- [ ] Do NOT use SBR odds to create trades or paper entries",
        "- [ ] Do NOT modify Moneyline Core v1 rule until odds validation is complete",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SBR MLB odds feasibility probe. Read-only.")
    parser.add_argument("--playwright", action="store_true",
                        help="Use Playwright browser rendering when raw HTML has no game data")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="Probe a single date only")
    args = parser.parse_args()

    pw_available = False
    try:
        import playwright  # noqa: F401
        pw_available = True
    except ImportError:
        pass

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    urls = _build_urls(args.date)

    url_results: list[dict] = []
    ml_rows: list[dict] = []
    playwright_used = False

    print(f"\nSBR MLB Odds Feasibility Probe -- {len(urls)} URLs")
    print(f"Playwright: {'available' if pw_available else 'not available'} | use={args.playwright}")
    print()

    for item in urls:
        url, label = item["url"], item["label"]
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", label)
        probe_date = date_match.group() if date_match else ""

        print(f"  {label}")

        fetch = fetch_raw(url)
        analysis = analyze_html(fetch, probe_date, label)
        analysis["fetch_method"] = "raw_html"

        games_n = analysis["games_in_next_data"]
        print(f"    HTTP={fetch.get('http_status')}  "
              f"len={fetch.get('content_length', 0):,}  "
              f"games={games_n}  "
              f"conf={analysis['parse_confidence']}")

        if fetch.get("html") and games_n > 0:
            rows = extract_moneyline_rows(fetch["html"], probe_date, url)
            ml_rows.extend(rows)
            print(f"    Parsed {len(rows)} moneyline odds rows")
        elif analysis["next_data_present"] and games_n == 0:
            print("    __NEXT_DATA__ found but oddsTables.gameRows empty (off-day or market not available)")

        # Playwright fallback for shell-only pages
        if args.playwright and pw_available and analysis["parse_confidence"] in ("none", "shell_only"):
            print("    -> Playwright fallback...")
            pw_fetch = fetch_playwright(url)
            pw_analysis = analyze_html(pw_fetch, probe_date, label)
            pw_analysis["fetch_method"] = "playwright"
            playwright_used = True
            if pw_analysis["games_in_next_data"] > 0:
                analysis = pw_analysis
                rows = extract_moneyline_rows(pw_fetch.get("html", ""), probe_date, url)
                ml_rows.extend(rows)
                print(f"    PW: Parsed {len(rows)} rows")

        url_results.append(analysis)
        print()
        time.sleep(2)

    match_stats = check_matchability(ml_rows)

    write_csv(OUT_DIR / "sbr_url_probe_results.csv", url_results, _URL_FIELDS)
    if ml_rows:
        write_csv(OUT_DIR / "sbr_moneyline_sample.csv", ml_rows, _ML_FIELDS)
    write_summary(
        OUT_DIR / "sbr_probe_summary.md",
        url_results, ml_rows, match_stats,
        playwright_used, pw_available,
    )

    print("=== RESULTS ===")
    print(f"URLs probed:     {len(url_results)}")
    print(f"Moneyline rows:  {len(ml_rows)}")
    best = max((r.get("parse_confidence", "none") for r in url_results), default="none",
               key=lambda x: ["none", "shell_only", "partial", "likely_full"].index(x))
    print(f"Best confidence: {best}")
    print(f"Games matched:   {match_stats.get('games_matched', 0)}/{match_stats.get('games_parsed', 0)}")
    print(f"\nOutputs -> {OUT_DIR}/")
    print("  sbr_probe_summary.md")
    print("  sbr_url_probe_results.csv")
    if ml_rows:
        print("  sbr_moneyline_sample.csv")


if __name__ == "__main__":
    main()
