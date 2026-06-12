"""
mlb/stats_api.py — Direct HTTP client for statsapi.mlb.com.

No API key or authentication required. All public functions return the
parsed JSON dict on success, or None on any network/HTTP/parse failure.
The app never crashes due to MLB API unavailability.
"""
import logging
import sys
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_BASE = "https://statsapi.mlb.com"
_TIMEOUT = 15.0


def _get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """GET {_BASE}{path}. Returns parsed JSON or None on any failure."""
    url = f"{_BASE}{path}"
    try:
        resp = httpx.get(url, params=params or {}, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        log.warning("MLB API timeout: %s", url)
    except httpx.HTTPStatusError as exc:
        log.warning("MLB API HTTP %d: %s", exc.response.status_code, url)
    except httpx.RequestError as exc:
        log.warning("MLB API connection error: %s — %s", url, exc)
    except Exception as exc:
        log.warning("MLB API unexpected error: %s — %s", url, exc)
    return None


def fetch_schedule(date_str: str) -> Optional[dict]:
    """GET /api/v1/schedule?sportId=1&date=YYYY-MM-DD&hydrate=team"""
    return _get("/api/v1/schedule", {
        "sportId": "1",
        "date": date_str,
        "hydrate": "team",
    })


def fetch_game_feed(game_pk: int) -> Optional[dict]:
    """GET /api/v1.1/game/{gamePk}/feed/live — full live game data."""
    return _get(f"/api/v1.1/game/{game_pk}/feed/live")


def fetch_linescore(game_pk: int) -> Optional[dict]:
    """GET /api/v1/game/{gamePk}/linescore"""
    return _get(f"/api/v1/game/{game_pk}/linescore")


def fetch_play_by_play(game_pk: int) -> Optional[dict]:
    """GET /api/v1/game/{gamePk}/playByPlay"""
    return _get(f"/api/v1/game/{game_pk}/playByPlay")


def fetch_boxscore(game_pk: int) -> Optional[dict]:
    """GET /api/v1/game/{gamePk}/boxscore"""
    return _get(f"/api/v1/game/{game_pk}/boxscore")


def get_game_status(game_pk: int) -> Optional[str]:
    """Return abstractGameState string, or None if API unavailable."""
    data = fetch_game_feed(game_pk)
    if not data:
        return None
    return data.get("gameData", {}).get("status", {}).get("abstractGameState")


def get_final_score(game_pk: int) -> Optional[tuple[int, int]]:
    """Return (away_score, home_score) if abstractGameState == 'Final', else None."""
    data = fetch_game_feed(game_pk)
    if not data:
        return None
    status = data.get("gameData", {}).get("status", {}).get("abstractGameState")
    if status != "Final":
        return None
    teams = data.get("liveData", {}).get("linescore", {}).get("teams", {})
    away = (teams.get("away") or {}).get("runs", 0) or 0
    home = (teams.get("home") or {}).get("runs", 0) or 0
    return (away, home)


def get_final_total(game_pk: int) -> Optional[int]:
    """Return combined runs if game is Final, else None."""
    score = get_final_score(game_pk)
    return None if score is None else score[0] + score[1]


def is_game_final(game_pk: int) -> bool:
    """Return True iff abstractGameState == 'Final'."""
    return get_game_status(game_pk) == "Final"


if __name__ == "__main__":
    # Manual smoke test — requires live internet:
    #   python -m mlb.stats_api 823215
    game_pk = int(sys.argv[1]) if len(sys.argv) > 1 else 823215
    print(f"Fetching gamePk={game_pk} ...")
    data = fetch_game_feed(game_pk)
    if data:
        gd = data.get("gameData", {})
        state = gd.get("status", {}).get("abstractGameState", "?")
        away = gd.get("teams", {}).get("away", {}).get("abbreviation", "?")
        home = gd.get("teams", {}).get("home", {}).get("abbreviation", "?")
        score = get_final_score(game_pk)
        total = get_final_total(game_pk)
        print(f"  {away} @ {home}  status={state}  score={score}  total={total}")
    else:
        print("  No data returned (check internet / gamePk)")
