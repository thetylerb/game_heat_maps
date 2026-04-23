"""
Buck the Odds — Master analytics pipeline.

Fetches NHL game data, computes HDC/xG stats, grades every player, calculates
series win probability, and writes a structured JSON file ready for graphics.

Usage
-----
  # Run after a completed game
  python pipeline.py --game 2026030123 --team PHI --opponent PIT \\
      --p 0.69 --series-wins 3 --opponent-wins 0

  # Provide explicit game-by-game results for accurate series history
  python pipeline.py ... --results A A A

  # Poll every 60 s during a live game; process automatically when it ends
  python pipeline.py --game 2026030123 --team PHI --opponent PIT \\
      --p 0.69 --series-wins 3 --opponent-wins 0 --watch
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

from fetch_game import (
    get_boxscore,
    get_landing,
    get_play_by_play,
    is_game_final,
    list_playoff_games,
)
from grader import grade_players
from hdc_calculator import calculate_hdc
from series_prob import infer_results_from_standings, series_history, series_win_probability

OUTPUT_DIR = Path("output")


# ---------------------------------------------------------------------------
# Score extraction
# ---------------------------------------------------------------------------

def _extract_score(pbp: dict) -> dict[str, int]:
    """
    Return {'HOME_ABBREV': goals, 'AWAY_ABBREV': goals} from play-by-play data.
    """
    home = pbp.get("homeTeam", {})
    away = pbp.get("awayTeam", {})
    return {
        home.get("abbrev", "HOME"): home.get("score", 0),
        away.get("abbrev", "AWAY"): away.get("score", 0),
    }


def _extract_teams(pbp: dict) -> tuple[str, str]:
    """Return (home_abbrev, away_abbrev)."""
    home = pbp.get("homeTeam", {}).get("abbrev", "HOME")
    away = pbp.get("awayTeam", {}).get("abbrev", "AWAY")
    return home, away


def _extract_date(pbp: dict) -> str:
    """Extract game date string from PBP; fall back to today."""
    game_date = pbp.get("gameDate") or pbp.get("startTimeUTC", "")[:10]
    return game_date or str(date.today())


# ---------------------------------------------------------------------------
# Core pipeline logic
# ---------------------------------------------------------------------------

def run_pipeline(
    game_id: str,
    team: str,
    opponent: str,
    p: float,
    series_wins: int,
    opponent_wins: int,
    results: Optional[list[str]] = None,
    force: bool = False,
) -> dict:
    """
    Fetch data, grade players, and compute series probability for one game.

    Args:
        game_id:       NHL numeric game ID.
        team:          Primary team abbreviation (e.g. 'PHI').
        opponent:      Opposing team abbreviation (e.g. 'PIT').
        p:             Pre-series per-game win probability for `team`.
        series_wins:   Current wins for `team` (including this game).
        opponent_wins: Current wins for `opponent`.
        results:       Optional ordered list of game winners ('A'=team,
                       'B'=opponent) for accurate series history graph.
        force:         Bypass cache and re-fetch all data.

    Returns:
        Structured dict matching the Buck the Odds output schema.
    """
    print(f"[pipeline] Fetching data for game {game_id}…")
    pbp      = get_play_by_play(game_id, force=force)
    boxscore = get_boxscore(game_id, force=force)
    landing  = get_landing(game_id, force=force)

    home_abbrev, away_abbrev = _extract_teams(pbp)
    score = _extract_score(pbp)
    game_date = _extract_date(pbp)

    print("[pipeline] Computing HDC and xG stats…")
    hdc_stats = calculate_hdc(pbp, landing)

    print("[pipeline] Grading players…")
    grades = grade_players(boxscore, hdc_stats)

    # -----------------------------------------------------------------------
    # Series probability
    # -----------------------------------------------------------------------
    # Reconstruct game-by-game results if not provided
    if results is None:
        results = infer_results_from_standings(series_wins, opponent_wins)

    history_probs = series_history(p, results)  # len = total_games_played + 1
    current_prob_team = series_win_probability(p, series_wins, opponent_wins)
    current_prob_opp  = round(1.0 - current_prob_team, 4)

    # -----------------------------------------------------------------------
    # Assemble output
    # -----------------------------------------------------------------------
    # Strip internal fields not meant for the output
    def _clean_player(entry: dict) -> dict:
        return {
            "name":          entry["name"],
            "position":      entry["position"],
            "grade":         entry["grade"],
            "verdict":       entry["verdict"],
            "stats":         entry["stats"],
        }

    player_grades: dict[str, list[dict]] = {
        abbrev: [_clean_player(p) for p in players]
        for abbrev, players in grades.items()
    }

    output = {
        "game_id":   game_id,
        "date":      game_date,
        "home":      home_abbrev,
        "away":      away_abbrev,
        "score":     score,
        "series":    {team: series_wins, opponent: opponent_wins},
        "series_win_prob": {team: current_prob_team, opponent: current_prob_opp},
        "series_history":  history_probs,
        "player_grades":   player_grades,
    }
    return output


def save_output(data: dict, game_id: str) -> Path:
    """
    Write the pipeline output to output/<game_id>.json.

    Returns:
        Path to the written file.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{game_id}.json"
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def watch_and_run(
    game_id: str,
    team: str,
    opponent: str,
    p: float,
    series_wins: int,
    opponent_wins: int,
    results: Optional[list[str]],
    poll_interval: int = 60,
) -> dict:
    """
    Poll a live game every `poll_interval` seconds until it ends, then run
    the full pipeline and return the output.

    Args:
        poll_interval: Seconds between status checks (default 60).
    """
    print(f"[watch] Monitoring game {game_id} — polling every {poll_interval}s…")
    attempt = 0
    while True:
        attempt += 1
        pbp = get_play_by_play(game_id, force=True)
        state = pbp.get("gameState", "UNKNOWN")
        period = pbp.get("periodDescriptor", {}).get("number", "?")
        time_remaining = pbp.get("clock", {}).get("timeRemaining", "--:--")
        home_score = pbp.get("homeTeam", {}).get("score", 0)
        away_score = pbp.get("awayTeam", {}).get("score", 0)
        home_abbrev, away_abbrev = _extract_teams(pbp)

        print(
            f"[watch #{attempt}] State={state}  "
            f"P{period} {time_remaining}  "
            f"{away_abbrev} {away_score} – {home_abbrev} {home_score}"
        )

        if state in {"FINAL", "OFF"}:
            print("[watch] Game is final. Running pipeline…")
            return run_pipeline(
                game_id, team, opponent, p, series_wins, opponent_wins, results, force=True
            )

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Buck the Odds — NHL playoff analytics pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python pipeline.py --game 2026030123 --team PHI --opponent PIT \\\n"
            "      --p 0.69 --series-wins 3 --opponent-wins 0\n\n"
            "  python pipeline.py --game 2026030123 --team PHI --opponent PIT \\\n"
            "      --p 0.69 --series-wins 2 --opponent-wins 1 --results A B A\n\n"
            "  python pipeline.py --game 2026030123 --team PHI --opponent PIT \\\n"
            "      --p 0.69 --series-wins 3 --opponent-wins 0 --watch\n"
        ),
    )
    parser.add_argument("--game",          required=True, metavar="GAME_ID",
                        help="NHL game ID (e.g. 2026030123)")
    parser.add_argument("--team",          required=True, metavar="ABBREV",
                        help="Your primary team abbreviation (e.g. PHI)")
    parser.add_argument("--opponent",      required=True, metavar="ABBREV",
                        help="Opposing team abbreviation (e.g. PIT)")
    parser.add_argument("--p",             required=True, type=float, metavar="PROB",
                        help="Pre-series per-game win probability for --team (0 < p < 1)")
    parser.add_argument("--series-wins",   required=True, type=int, metavar="N",
                        dest="series_wins",
                        help="Current wins for --team (after this game)")
    parser.add_argument("--opponent-wins", required=True, type=int, metavar="N",
                        dest="opponent_wins",
                        help="Current wins for --opponent")
    parser.add_argument("--results",       nargs="+", metavar="A|B",
                        help="Game-by-game results list, e.g. --results A A B A")
    parser.add_argument("--watch",         action="store_true",
                        help="Poll every 60 s and run when game is final")
    parser.add_argument("--poll-interval", type=int, default=60, metavar="SEC",
                        dest="poll_interval",
                        help="Seconds between polls in watch mode (default 60)")
    parser.add_argument("--force",         action="store_true",
                        help="Bypass API cache")
    parser.add_argument("--date",          metavar="YYYY-MM-DD",
                        help="List today's playoff games and exit")
    return parser


def _cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # --date shortcut: list games and exit
    if args.date:
        games = list_playoff_games(args.date)
        if not games:
            print(f"No playoff games on {args.date}.")
        else:
            print(f"Playoff games on {args.date}:")
            for g in games:
                print(f"  [{g['id']}]  {g['away']} @ {g['home']}  "
                      f"{g['away_score']}-{g['home_score']}  ({g['status']})")
        return

    # Validate pre-series probability
    if not (0.0 < args.p < 1.0):
        print("[ERROR] --p must be between 0 and 1 (exclusive).", file=sys.stderr)
        sys.exit(1)

    # Validate results list if provided
    results: Optional[list[str]] = None
    if args.results:
        normalised = [r.upper() for r in args.results]
        bad = [r for r in normalised if r not in ("A", "B")]
        if bad:
            print(f"[ERROR] --results values must be 'A' or 'B', got: {bad}", file=sys.stderr)
            sys.exit(1)
        results = normalised

    # --watch mode: check if game is already final before entering watch loop
    if args.watch:
        pbp = get_play_by_play(args.game, force=True)
        state = pbp.get("gameState", "UNKNOWN")
        if state in {"FINAL", "OFF"}:
            print(f"[watch] Game is already final ({state}). Running pipeline immediately.")
            data = run_pipeline(
                args.game, args.team, args.opponent, args.p,
                args.series_wins, args.opponent_wins, results, force=args.force,
            )
        else:
            data = watch_and_run(
                args.game, args.team, args.opponent, args.p,
                args.series_wins, args.opponent_wins, results, args.poll_interval,
            )
    else:
        # Check game is final before running full pipeline
        pbp = get_play_by_play(args.game, force=args.force)
        state = pbp.get("gameState", "UNKNOWN")
        if state not in {"FINAL", "OFF"}:
            home, away = _extract_teams(pbp)
            period = pbp.get("periodDescriptor", {}).get("number", "?")
            time_left = pbp.get("clock", {}).get("timeRemaining", "--:--")
            print(
                f"[ERROR] Game {args.game} is not yet final "
                f"(state={state}, P{period} {time_left} remaining).\n"
                f"  Use --watch to wait for the game to end, or re-run when final.",
                file=sys.stderr,
            )
            sys.exit(1)

        data = run_pipeline(
            args.game, args.team, args.opponent, args.p,
            args.series_wins, args.opponent_wins, results, force=args.force,
        )

    out_path = save_output(data, args.game)
    print(f"[pipeline] Done. Output written to {out_path}")

    # Pretty-print key summary
    print("\n--- Summary ---")
    score = data.get("score", {})
    series = data.get("series", {})
    prob = data.get("series_win_prob", {})
    team, opp = args.team, args.opponent
    home_abbrev = data.get("home", "")
    away_abbrev = data.get("away", "")
    print(f"  Score:  {away_abbrev} {score.get(away_abbrev, 0)}  {home_abbrev} {score.get(home_abbrev, 0)}")
    print(f"  Series: {team} {series.get(team,0)} – {opp} {series.get(opp,0)}")
    print(f"  Series win prob: {team} {prob.get(team,0):.1%}  {opp} {prob.get(opp,0):.1%}")
    total_players = sum(len(v) for v in data.get("player_grades", {}).values())
    print(f"  Players graded: {total_players}")
    history = data.get("series_history", [])
    if history:
        print(f"  Series history: {[round(h, 3) for h in history]}")


if __name__ == "__main__":
    _cli()
