"""
NHL API data fetcher for Buck the Odds analytics pipeline.

Endpoints used (api-web.nhle.com):
  GET /v1/schedule/{date}                      — daily schedule
  GET /v1/gamecenter/{gameId}/play-by-play     — shot/event stream
  GET /v1/gamecenter/{gameId}/boxscore         — player stats
  GET /v1/gamecenter/{gameId}/landing          — shifts, on-ice context
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import requests

BASE_URL = "https://api-web.nhle.com"
CACHE_DIR = Path("cache")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "BuckTheOdds/1.0"})


def _cache_path(key: str) -> Path:
    """Return cache file path for a given key string."""
    CACHE_DIR.mkdir(exist_ok=True)
    safe = key.replace("/", "_").strip("_")
    return CACHE_DIR / f"{safe}.json"


def _fetch(endpoint: str, cache_key: Optional[str] = None, force: bool = False) -> dict:
    """
    GET request to the NHL API with file-based caching.

    Args:
        endpoint:  Path component e.g. '/v1/schedule/2026-04-23'.
        cache_key: Override the cache filename (defaults to endpoint).
        force:     Bypass the cache and re-fetch.

    Returns:
        Parsed JSON dict.

    Raises:
        SystemExit on HTTP or network failure.
    """
    path = _cache_path(cache_key or endpoint)

    if not force and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    url = f"{BASE_URL}{endpoint}"
    try:
        resp = _SESSION.get(url, timeout=20)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        print(f"[ERROR] HTTP {exc.response.status_code} fetching {url}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"[ERROR] Network error fetching {url}: {exc}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# Public fetchers
# ---------------------------------------------------------------------------

def get_schedule(date_str: str, force: bool = False) -> dict:
    """
    Fetch the NHL schedule for a date (YYYY-MM-DD).

    Returns:
        Raw schedule JSON containing a 'gameWeek' list.
    """
    return _fetch(f"/v1/schedule/{date_str}", force=force)


def get_play_by_play(game_id: str, force: bool = False) -> dict:
    """
    Fetch play-by-play event stream for a game.

    Args:
        game_id: NHL numeric game ID (e.g. '2026030123').

    Returns:
        Raw play-by-play JSON with 'plays', 'rosterSpots', team info.
    """
    return _fetch(f"/v1/gamecenter/{game_id}/play-by-play", force=force)


def get_boxscore(game_id: str, force: bool = False) -> dict:
    """
    Fetch boxscore stats for a game.

    Returns:
        Raw boxscore JSON with 'playerByGameStats' keyed by team.
    """
    return _fetch(f"/v1/gamecenter/{game_id}/boxscore", force=force)


def get_landing(game_id: str, force: bool = False) -> dict:
    """
    Fetch the landing page (includes shifts and on-ice context).

    Returns:
        Raw landing JSON; structure varies by game state.
    """
    return _fetch(f"/v1/gamecenter/{game_id}/landing", force=force)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def list_playoff_games(date_str: str, force: bool = False) -> list[dict]:
    """
    Return all playoff games (gameType == 3) on a given date.

    Args:
        date_str: Date in YYYY-MM-DD format.

    Returns:
        List of dicts with keys: id, home, away, home_score, away_score,
        status, venue.
    """
    schedule = get_schedule(date_str, force=force)
    games: list[dict] = []

    for day in schedule.get("gameWeek", []):
        if day.get("date") != date_str:
            continue
        for g in day.get("games", []):
            if g.get("gameType") != 3:
                continue
            games.append(
                {
                    "id": str(g["id"]),
                    "home": g["homeTeam"]["abbrev"],
                    "away": g["awayTeam"]["abbrev"],
                    "home_score": g["homeTeam"].get("score", 0),
                    "away_score": g["awayTeam"].get("score", 0),
                    "status": g.get("gameState", "unknown"),
                    "venue": g.get("venue", {}).get("default", ""),
                }
            )
    return games


def is_game_final(game_id: str) -> bool:
    """
    Return True if the game state is FINAL or OFF.

    Always bypasses cache so the state is live.
    """
    pbp = get_play_by_play(game_id, force=True)
    return pbp.get("gameState", "") in {"FINAL", "OFF"}


def get_game_state(game_id: str) -> str:
    """Return raw gameState string for a game (live bypass)."""
    pbp = get_play_by_play(game_id, force=True)
    return pbp.get("gameState", "UNKNOWN")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Buck the Odds — NHL data fetcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python fetch_game.py --date 2026-04-23\n"
            "  python fetch_game.py --game 2026030123\n"
            "  python fetch_game.py --game 2026030123 --force\n"
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", metavar="YYYY-MM-DD", help="List playoff games for a date")
    group.add_argument("--game", metavar="GAME_ID", help="Pull data for a specific game")
    parser.add_argument("--force", action="store_true", help="Bypass cache and re-fetch")
    args = parser.parse_args()

    if args.date:
        games = list_playoff_games(args.date, force=args.force)
        if not games:
            print(f"No playoff games found for {args.date}.")
        else:
            print(f"Playoff games on {args.date}:")
            for g in games:
                print(
                    f"  [{g['id']}]  {g['away']:>3} @ {g['home']:<3}  "
                    f"{g['away_score']}-{g['home_score']}  ({g['status']})  {g['venue']}"
                )
        return

    # --game path: fetch all four data sources and summarise
    game_id = args.game
    pbp = get_play_by_play(game_id, force=args.force)
    boxscore = get_boxscore(game_id, force=args.force)
    landing = get_landing(game_id, force=args.force)

    home = pbp.get("homeTeam", {})
    away = pbp.get("awayTeam", {})
    print(json.dumps(
        {
            "game_id": game_id,
            "game_state": pbp.get("gameState"),
            "home": home.get("abbrev"),
            "home_score": home.get("score", 0),
            "away": away.get("abbrev"),
            "away_score": away.get("score", 0),
            "total_plays": len(pbp.get("plays", [])),
            "roster_spots": len(pbp.get("rosterSpots", [])),
            "cached_files": [
                str(_cache_path(f"/v1/gamecenter/{game_id}/play-by-play")),
                str(_cache_path(f"/v1/gamecenter/{game_id}/boxscore")),
                str(_cache_path(f"/v1/gamecenter/{game_id}/landing")),
            ],
        },
        indent=2,
    ))


if __name__ == "__main__":
    _cli()
