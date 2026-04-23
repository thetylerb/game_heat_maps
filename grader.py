"""
Player grader for Buck the Odds.

Generates letter grades (A+ → F) and one-line verdicts for every skater
and goalie using boxscore data combined with HDC/xG stats.

Position weights
----------------
Forwards  : Goals+Assists 30%, xGF 25%, HDC-for 20%, +/- 15%, PIM 10%
Defensemen: xGA 30%, HDC-against 25%, Blocked shots 15%, Assists 15%, +/- 15%
Goalies   : Save% vs xSV% 40%, HDC save% 40%, Goals-against 20%
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Grade map
# ---------------------------------------------------------------------------

_GRADE_THRESHOLDS: list[tuple[float, str]] = [
    (95, "A+"),
    (90, "A"),
    (85, "A-"),
    (80, "B+"),
    (75, "B"),
    (70, "B-"),
    (65, "C+"),
    (60, "C"),
    (55, "C-"),
    (50, "D+"),
    (45, "D"),
    (0,  "F"),
]


def score_to_grade(score: float) -> str:
    """Map a numeric score (0–100) to a letter grade."""
    for threshold, letter in _GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _norm(value: float, lo: float, hi: float, invert: bool = False) -> float:
    """
    Linearly normalise value to 0–100 within [lo, hi].

    Args:
        invert: If True, lower values score higher (e.g. goals-against, PIM).
    """
    if hi == lo:
        return 50.0
    raw = min(max((value - lo) / (hi - lo), 0.0), 1.0) * 100.0
    return 100.0 - raw if invert else raw


# ---------------------------------------------------------------------------
# TOI parser
# ---------------------------------------------------------------------------

def _parse_toi(toi_str: str) -> float:
    """Convert 'MM:SS' to fractional minutes."""
    try:
        m, s = str(toi_str).split(":")
        return int(m) + int(s) / 60.0
    except (ValueError, AttributeError):
        return 0.0


# ---------------------------------------------------------------------------
# Verdict generator
# ---------------------------------------------------------------------------

def _verdict(player: dict, hdc: dict, grade: str, position: str) -> str:
    """
    Produce a punchy one-line verdict.

    Format: "<stat line> in <TOI>. <impact phrase>"
    """
    goals = player.get("goals", 0)
    assists = player.get("assists", 0)
    toi_str = player.get("toi", "0:00")
    toi_min = _parse_toi(toi_str)
    toi_disp = f"{int(toi_min)}:{int((toi_min % 1) * 60):02d}"

    # ---- Goalie ----
    if position == "G":
        saves = player.get("saves", 0)
        sa = player.get("shotsAgainst", saves + player.get("goalsAgainst", 0))
        ga = player.get("goalsAgainst", 0)
        hdc_saves = hdc.get("hdc_saves", 0)
        hdc_faced = hdc.get("hdc_faced", 0)
        hdc_note = f" ({hdc_saves}/{hdc_faced} HDC)" if hdc_faced > 0 else ""

        if grade in ("A+", "A"):
            return f"{saves}/{sa} saves{hdc_note}. Stole one tonight."
        if grade == "A-":
            return f"{saves}/{sa} saves{hdc_note}. Stood tall when it counted."
        if grade in ("B+", "B"):
            return f"{saves}/{sa} saves. Kept them in it."
        if grade == "B-":
            return f"{saves}/{sa} saves. Serviceable, nothing more."
        return f"{ga} GA in {toi_disp}. Not enough from the last line of defense."

    # ---- Skater stat line ----
    parts: list[str] = []
    if goals:
        parts.append(f"{goals}G")
    if assists:
        parts.append(f"{assists}A")
    stat_line = " ".join(parts) if parts else "0 pts"

    xgf = hdc.get("xgf", 0.0)
    hdc_for = hdc.get("hdc_for", 0)
    pm = player.get("plusMinus", 0)
    pm_str = f"+{pm}" if pm >= 0 else str(pm)

    # ---- Impact phrase ----
    if grade == "A+":
        impact = "Put the series on ice."
    elif grade == "A":
        impact = "Best player on the ice."
    elif grade == "A-":
        impact = "Series-shifting performance."
    elif grade == "B+":
        impact = "Tilted the ice his team's way."
    elif grade == "B":
        impact = "Reliable in a tight spot."
    elif grade == "B-":
        impact = "Quietly solid."
    elif grade == "C+":
        impact = "Showed up in flashes."
    elif grade == "C":
        impact = "Blended into the game."
    elif grade == "C-":
        impact = "Rarely made his presence felt."
    elif grade == "D+":
        impact = "Series on life support."
    elif grade == "D":
        impact = "A night to forget."
    else:
        impact = "Invisible when it mattered most."

    return f"{stat_line} in {toi_disp}. {impact}"


# ---------------------------------------------------------------------------
# Per-position graders
# ---------------------------------------------------------------------------

def _grade_forward(player: dict, hdc: dict) -> tuple[float, str]:
    """
    Grade a forward.

    Returns:
        (numeric_score, letter_grade)
    """
    goals = float(player.get("goals", 0))
    assists = float(player.get("assists", 0))
    pm = float(player.get("plusMinus", 0))
    pim = float(player.get("pim", 0))
    xgf = hdc.get("xgf", 0.0)
    hdc_for = hdc.get("hdc_for", 0)

    # Individual components normalised to 0–100
    # Points ceiling = 5 (2G+1A or 1G+3A is an elite single-game performance)
    pts_score = _norm(goals * 2 + assists, 0, 5)
    xgf_score = _norm(xgf, 0, 2.5)
    hdc_score  = _norm(hdc_for, 0, 7)
    pm_score   = _norm(pm, -5, 5)
    pim_score  = _norm(pim, 0, 8, invert=True)     # 0 PIM is good

    score = (
        pts_score  * 0.30
        + xgf_score * 0.25
        + hdc_score  * 0.20
        + pm_score   * 0.15
        + pim_score  * 0.10
    )
    return round(score, 2), score_to_grade(score)


def _grade_defenseman(player: dict, hdc: dict) -> tuple[float, str]:
    """
    Grade a defenseman.

    Returns:
        (numeric_score, letter_grade)
    """
    assists = float(player.get("assists", 0))
    pm = float(player.get("plusMinus", 0))
    blocks = float(player.get("blockedShots", 0))
    xga = hdc.get("xga", 0.0)
    hdc_against = hdc.get("hdc_against", 0)

    xga_score     = _norm(xga, 0, 3.0, invert=True)     # lower xGA is better
    hdc_ag_score  = _norm(hdc_against, 0, 8, invert=True)
    blocks_score  = _norm(blocks, 0, 6)
    assists_score = _norm(assists, 0, 4)
    pm_score      = _norm(pm, -5, 5)

    score = (
        xga_score     * 0.30
        + hdc_ag_score  * 0.25
        + blocks_score  * 0.15
        + assists_score * 0.15
        + pm_score      * 0.15
    )
    return round(score, 2), score_to_grade(score)


def _grade_goalie(player: dict, hdc: dict) -> tuple[float, str]:
    """
    Grade a goalie.

    Returns:
        (numeric_score, letter_grade)
    """
    saves = float(player.get("saves", 0))
    shots_against = float(player.get("shotsAgainst", 0)) or float(
        player.get("shots", saves)
    )
    goals_against = float(player.get("goalsAgainst", 0))
    hdc_saves = hdc.get("hdc_saves", 0)
    hdc_faced = hdc.get("hdc_faced", 0)
    xga = hdc.get("xga", 0.0)

    # Actual save%
    actual_sv = saves / shots_against if shots_against else 0.900

    # Expected save% from xG: xGA / shots_against = expected goals per shot
    # xSV% = 1 - expected_goals/shots_against
    expected_sv = 1.0 - (xga / shots_against) if shots_against and xga else 0.900

    sv_diff = actual_sv - expected_sv  # positive = outperformed
    sv_score = _norm(sv_diff, -0.10, 0.10)

    # HDC save%: realistic range is 0.65 (bad night) to 0.95 (elite)
    hdc_sv_pct = hdc_saves / hdc_faced if hdc_faced else 0.85
    hdc_sv_score = _norm(hdc_sv_pct, 0.65, 0.95)

    # Goals against (0 = perfect, 5+ = rough)
    ga_score = _norm(goals_against, 0, 5, invert=True)

    score = (
        sv_score    * 0.40
        + hdc_sv_score * 0.40
        + ga_score     * 0.20
    )
    return round(score, 2), score_to_grade(score)


# ---------------------------------------------------------------------------
# Boxscore parser
# ---------------------------------------------------------------------------

def _parse_boxscore_players(boxscore: dict) -> dict[str, list[dict]]:
    """
    Extract player dicts from boxscore keyed by team abbreviation.

    Handles the 'playerByGameStats' structure returned by the NHL API.

    Returns:
        {'HOME_ABBREV': [player_dict, ...], 'AWAY_ABBREV': [player_dict, ...]}
    """
    result: dict[str, list[dict]] = {}

    home_abbrev = boxscore.get("homeTeam", {}).get("abbrev", "HOME")
    away_abbrev = boxscore.get("awayTeam", {}).get("abbrev", "AWAY")

    by_game = boxscore.get("playerByGameStats", {})

    for team_key, abbrev in [("homeTeam", home_abbrev), ("awayTeam", away_abbrev)]:
        team_block = by_game.get(team_key, {})
        players: list[dict] = []
        for group in ("forwards", "defense", "goalies", "skaters"):
            for p in team_block.get(group, []):
                # Normalise name field
                name_field = p.get("name", {})
                if isinstance(name_field, dict):
                    p["name"] = name_field.get("default", "")
                p["_group"] = group
                players.append(p)
        result[abbrev] = players

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def grade_players(
    boxscore: dict,
    hdc_stats: dict[int, dict],
    teams: Optional[list[str]] = None,
) -> dict[str, list[dict]]:
    """
    Grade every player who logged ice time in the game.

    Args:
        boxscore:   Raw boxscore JSON from get_boxscore().
        hdc_stats:  Output of calculate_hdc() — keyed by player ID.
        teams:      If provided, only include these team abbreviations.

    Returns:
        Dict keyed by team abbreviation, each value a list of player grade dicts:
            {name, position, grade, numeric_score, verdict, stats}
    """
    team_players = _parse_boxscore_players(boxscore)
    output: dict[str, list[dict]] = {}

    for team_abbrev, players in team_players.items():
        if teams and team_abbrev not in teams:
            continue

        graded: list[dict] = []

        for p in players:
            pid = p.get("playerId")
            toi_str = p.get("toi", "0:00")

            # Skip players with no ice time
            if _parse_toi(toi_str) < 0.1:
                continue

            hdc = hdc_stats.get(pid, {})
            group = p.get("_group", "forwards")

            position_code = p.get("position", "")
            if not position_code:
                # Infer from group
                if group == "goalies":
                    position_code = "G"
                elif group == "defense":
                    position_code = "D"
                else:
                    position_code = "F"

            # Grade
            if position_code == "G":
                score, grade = _grade_goalie(p, hdc)
            elif position_code == "D":
                score, grade = _grade_defenseman(p, hdc)
            else:
                score, grade = _grade_forward(p, hdc)

            verdict_str = _verdict(p, hdc, grade, position_code)

            # Build unified stats block
            stats_block: dict = {
                "goals": p.get("goals", 0),
                "assists": p.get("assists", 0),
                "toi": toi_str,
                "plus_minus": p.get("plusMinus", 0),
                "shots": p.get("shots", 0),
                "hits": p.get("hits", 0),
                "blocked_shots": p.get("blockedShots", 0),
                "pim": p.get("pim", 0),
                "hdc_for": hdc.get("hdc_for", 0),
                "hdc_against": hdc.get("hdc_against", 0),
                "xgf": round(hdc.get("xgf", 0.0), 3),
                "xga": round(hdc.get("xga", 0.0), 3),
            }
            if position_code == "G":
                stats_block["saves"] = p.get("saves", 0)
                stats_block["shots_against"] = p.get("shotsAgainst", 0)
                stats_block["goals_against"] = p.get("goalsAgainst", 0)
                stats_block["hdc_saves"] = hdc.get("hdc_saves", 0)
                stats_block["hdc_faced"] = hdc.get("hdc_faced", 0)

            graded.append(
                {
                    "name": p.get("name", ""),
                    "position": position_code,
                    "sweater": p.get("sweaterNumber", ""),
                    "grade": grade,
                    "numeric_score": score,
                    "verdict": verdict_str,
                    "stats": stats_block,
                }
            )

        # Sort: forwards first, then D, then G; within group by score desc
        def _sort_key(entry: dict) -> tuple:
            pos = entry["position"]
            order = {"G": 2, "D": 1}.get(pos, 0)
            return (order, -entry["numeric_score"])

        graded.sort(key=_sort_key)
        output[team_abbrev] = graded

    return output


def build_grade_dataframe(grades: dict[str, list[dict]]) -> pd.DataFrame:
    """
    Flatten graded output into a tidy pandas DataFrame for further analysis.

    Returns:
        DataFrame with columns: team, name, position, grade, numeric_score,
        verdict, and all stats columns.
    """
    rows: list[dict] = []
    for team, players in grades.items():
        for p in players:
            row = {"team": team, "name": p["name"], "position": p["position"],
                   "grade": p["grade"], "numeric_score": p["numeric_score"],
                   "verdict": p["verdict"]}
            row.update(p["stats"])
            rows.append(row)
    return pd.DataFrame(rows)
