"""
High Danger Chance (HDC) calculator for Buck the Odds.

Rink geometry (feet, center-ice = origin):
  x: –100 → +100  (left → right from broadcast view)
  y: –42.5 → +42.5 (bottom → top)
  Nets at x ≈ ±89, y = 0

Danger zones:
  HIGH   — ≤10 ft from net (crease / front), OR ≤20 ft AND |y| ≤ 22 ft (royal-road slot)
  MEDIUM — ≤40 ft from net (faceoff-circle range)
  LOW    — everything else (perimeter, point shots)

xG weights:  HIGH = 0.35, MEDIUM = 0.10, LOW = 0.03
"""

import math
from collections import defaultdict
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NET_X: float = 89.0          # absolute x of each net
HD_CREASE_DIST: float = 10.0 # high-danger crease radius
HD_SLOT_DIST: float = 20.0   # high-danger slot max distance
HD_SLOT_Y: float = 22.0      # high-danger slot half-width (royal road)
MD_MAX_DIST: float = 40.0    # medium-danger max distance

XG_WEIGHTS: dict[str, float] = {"high": 0.35, "medium": 0.10, "low": 0.03}

SHOT_EVENT_TYPES: frozenset[str] = frozenset(
    {"shot-on-goal", "missed-shot", "goal", "blocked-shot"}
)
# Events that count as shots on goal for save% purposes
SHOTS_ON_GOAL_TYPES: frozenset[str] = frozenset({"shot-on-goal", "goal"})


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _shot_distance(x: float, y: float, net_x: float) -> float:
    """Euclidean distance from shot coordinate to the target net."""
    return math.sqrt((x - net_x) ** 2 + y ** 2)


def classify_shot(x: float, y: float, attacking_right: bool) -> str:
    """
    Classify a shot location as 'high', 'medium', or 'low' danger.

    Args:
        x:               Shot x-coordinate in feet.
        y:               Shot y-coordinate in feet.
        attacking_right: True when the shooting team attacks the right net (x ≈ +89).

    Returns:
        One of 'high', 'medium', 'low'.
    """
    net_x = NET_X if attacking_right else -NET_X
    dist = _shot_distance(x, y, net_x)

    if dist <= HD_CREASE_DIST:
        return "high"
    if dist <= HD_SLOT_DIST and abs(y) <= HD_SLOT_Y:
        return "high"
    if dist <= MD_MAX_DIST:
        return "medium"
    return "low"


def _attacking_right(period: int, home_defends_left_p1: bool, is_home_team: bool) -> bool:
    """
    Determine which direction a team is attacking in a given period.

    The NHL switches ends between periods.  Odd periods mirror period 1;
    even periods are the opposite.  Playoff OT follows the same pattern
    (OT periods are full 20-minute frames).

    Args:
        period:               Period number (1-based; OT = 4+).
        home_defends_left_p1: True if home team defends the left net in period 1.
        is_home_team:         True if the shooting team is the home team.

    Returns:
        True when the shooting team attacks the right (positive-x) net.
    """
    # In odd periods home team is on their period-1 side; even periods they flip.
    home_attacks_right = home_defends_left_p1 ^ (period % 2 == 0)
    return home_attacks_right if is_home_team else not home_attacks_right


# ---------------------------------------------------------------------------
# Roster helpers
# ---------------------------------------------------------------------------

def extract_roster(pbp_data: dict) -> dict[int, dict]:
    """
    Build a playerId → player-info mapping from rosterSpots in the PBP payload.

    Returns:
        Dict with keys name, position, team_id, team_abbrev.
    """
    home = pbp_data.get("homeTeam", {})
    away = pbp_data.get("awayTeam", {})
    home_id = home.get("id")
    home_abbrev = home.get("abbrev", "")
    away_abbrev = away.get("abbrev", "")

    roster: dict[int, dict] = {}
    for spot in pbp_data.get("rosterSpots", []):
        pid = spot.get("playerId")
        if not pid:
            continue
        team_id = spot.get("teamId")
        first = spot.get("firstName", {})
        last = spot.get("lastName", {})
        # name may be a plain string or a localised dict
        first_str = first.get("default", first) if isinstance(first, dict) else first
        last_str = last.get("default", last) if isinstance(last, dict) else last
        roster[pid] = {
            "name": f"{first_str} {last_str}".strip(),
            "position": spot.get("positionCode", ""),
            "team_id": team_id,
            "team_abbrev": home_abbrev if team_id == home_id else away_abbrev,
        }
    return roster


# ---------------------------------------------------------------------------
# Shift reconstruction
# ---------------------------------------------------------------------------

def _to_seconds(time_str: str) -> int:
    """Convert 'MM:SS' string to total seconds."""
    try:
        m, s = time_str.split(":")
        return int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return 0


def _build_shift_index(landing_data: dict) -> dict[int, list[tuple[int, int]]]:
    """
    Build playerId → [(abs_start_sec, abs_end_sec), ...] from landing shift data.

    The landing endpoint exposes shifts either under 'shifts' (with homeTeam /
    awayTeam sub-keys) or as a flat list.  We try both shapes.
    """
    intervals: dict[int, list[tuple[int, int]]] = defaultdict(list)
    period_length = 1200  # 20 min

    def _process_shift_list(shifts: list) -> None:
        for shift in shifts:
            pid = shift.get("playerId") or shift.get("id")
            period = shift.get("period", 1)
            start_str = shift.get("startTime", "0:00")
            end_str = shift.get("endTime", "0:00")
            if not pid:
                continue
            offset = (period - 1) * period_length
            intervals[pid].append(
                (_to_seconds(start_str) + offset, _to_seconds(end_str) + offset)
            )

    shift_block = landing_data.get("shifts", {})
    if isinstance(shift_block, dict):
        for team_shifts in shift_block.values():
            if isinstance(team_shifts, list):
                _process_shift_list(team_shifts)
    elif isinstance(shift_block, list):
        _process_shift_list(shift_block)

    return intervals


def _on_ice_from_shifts(event_abs_sec: int, shift_index: dict[int, list[tuple[int, int]]]) -> set[int]:
    """Return player IDs whose shift interval covers event_abs_sec."""
    return {
        pid
        for pid, intervals in shift_index.items()
        for start, end in intervals
        if start <= event_abs_sec <= end
    }


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------

def calculate_hdc(pbp_data: dict, landing_data: Optional[dict] = None) -> dict[int, dict]:
    """
    Compute per-player HDC and xG stats from play-by-play data.

    On-ice players per event are sourced in priority order:
      1. homeOnIcePlayersIds / awayOnIcePlayersIds embedded in the event.
      2. Shift reconstruction from landing_data (if provided).
      3. Shooter-only fallback (on-ice attrs will be zero).

    Args:
        pbp_data:     Raw play-by-play JSON from get_play_by_play().
        landing_data: Optional landing JSON for shift reconstruction.

    Returns:
        Dict keyed by playerId with keys:
            hdc_for, hdc_against, hdc_shooting_pct,
            xgf, xga, shots_on_goal, goals_scored, team_id, team_abbrev,
            name, position, hdc_saves, hdc_faced (goalies only).
    """
    home_team = pbp_data.get("homeTeam", {})
    away_team = pbp_data.get("awayTeam", {})
    home_id: Optional[int] = home_team.get("id")

    defending_side = pbp_data.get("homeTeamDefendingSide", "left")
    home_defends_left_p1 = defending_side == "left"

    roster = extract_roster(pbp_data)

    shift_index: dict[int, list[tuple[int, int]]] = {}
    if landing_data:
        shift_index = _build_shift_index(landing_data)

    stats: dict[int, dict] = defaultdict(
        lambda: {
            "hdc_for": 0,
            "hdc_against": 0,
            "hdc_goals": 0,       # internal: goals scored on HDC shots
            "hdc_shots": 0,       # internal: HDC shots taken by player individually
            "hdc_saves": 0,       # goalie: HDC shots stopped
            "hdc_faced": 0,       # goalie: total HDC shots faced
            "xgf": 0.0,
            "xga": 0.0,
            "shots_on_goal": 0,
            "goals_scored": 0,
            "team_id": None,
            "team_abbrev": "",
            "name": "",
            "position": "",
        }
    )

    # Pre-populate identity from roster so even non-shooting players appear
    for pid, info in roster.items():
        stats[pid]["team_id"] = info["team_id"]
        stats[pid]["team_abbrev"] = info["team_abbrev"]
        stats[pid]["name"] = info["name"]
        stats[pid]["position"] = info["position"]

    for play in pbp_data.get("plays", []):
        type_key: str = play.get("typeDescKey", "")
        if type_key not in SHOT_EVENT_TYPES:
            continue

        details = play.get("details", {})
        x = details.get("xCoord")
        y = details.get("yCoord")
        if x is None or y is None:
            continue

        x, y = float(x), float(y)
        period: int = play.get("periodDescriptor", {}).get("number", 1)
        shooting_team_id: Optional[int] = details.get("eventOwnerTeamId")
        is_home_shot = shooting_team_id == home_id

        att_right = _attacking_right(period, home_defends_left_p1, is_home_shot)
        danger = classify_shot(x, y, att_right)
        xg_val = XG_WEIGHTS[danger]
        is_goal = type_key == "goal"
        is_sog = type_key in SHOTS_ON_GOAL_TYPES
        is_hdc = danger == "high"

        shooter_id: Optional[int] = details.get("shootingPlayerId")
        goalie_id: Optional[int] = details.get("goalieInNetId")

        # ---------------------------------------------------------------
        # Determine on-ice players
        # ---------------------------------------------------------------
        # Try embedded on-ice arrays first (field names vary by API version)
        home_on_ice: set[int] = set(
            details.get("homeOnIcePlayersIds")
            or details.get("homeOnIce")
            or []
        )
        away_on_ice: set[int] = set(
            details.get("awayOnIcePlayersIds")
            or details.get("awayOnIce")
            or []
        )

        if not home_on_ice and not away_on_ice and shift_index:
            abs_sec = (period - 1) * 1200 + _to_seconds(play.get("timeInPeriod", "0:00"))
            all_on_ice = _on_ice_from_shifts(abs_sec, shift_index)
            for pid in all_on_ice:
                if pid in roster:
                    if roster[pid]["team_id"] == home_id:
                        home_on_ice.add(pid)
                    else:
                        away_on_ice.add(pid)

        # If still empty, fall back to shooter-only attribution
        if not home_on_ice and not away_on_ice:
            if shooter_id and shooter_id in roster:
                if is_home_shot:
                    home_on_ice = {shooter_id}
                else:
                    away_on_ice = {shooter_id}

        all_on_ice_this_play = home_on_ice | away_on_ice

        # ---------------------------------------------------------------
        # Attribute stats to on-ice players
        # ---------------------------------------------------------------
        for pid in all_on_ice_this_play:
            if pid not in roster:
                continue
            p_team = roster[pid]["team_id"]
            offensive = (p_team == shooting_team_id)

            if offensive:
                stats[pid]["xgf"] += xg_val
                if is_hdc:
                    stats[pid]["hdc_for"] += 1
            else:
                stats[pid]["xga"] += xg_val
                if is_hdc:
                    stats[pid]["hdc_against"] += 1

        # ---------------------------------------------------------------
        # Shooter individual stats
        # ---------------------------------------------------------------
        if shooter_id and shooter_id in roster:
            if is_sog:
                stats[shooter_id]["shots_on_goal"] += 1
            if is_goal:
                stats[shooter_id]["goals_scored"] += 1
            if is_hdc:
                stats[shooter_id]["hdc_shots"] += 1
                if is_goal:
                    stats[shooter_id]["hdc_goals"] += 1

        # ---------------------------------------------------------------
        # Goalie stats (HDC saves)
        # ---------------------------------------------------------------
        if goalie_id and goalie_id in roster and type_key != "missed-shot":
            if is_hdc:
                stats[goalie_id]["hdc_faced"] += 1
                if not is_goal:
                    stats[goalie_id]["hdc_saves"] += 1

    # ---------------------------------------------------------------
    # Finalise
    # ---------------------------------------------------------------
    result: dict[int, dict] = {}
    for pid, s in stats.items():
        hdc_shots = s["hdc_shots"]
        hdc_goals = s["hdc_goals"]
        result[pid] = {
            "name": s["name"],
            "position": s["position"],
            "team_id": s["team_id"],
            "team_abbrev": s["team_abbrev"],
            "hdc_for": s["hdc_for"],
            "hdc_against": s["hdc_against"],
            "hdc_shooting_pct": round(hdc_goals / hdc_shots, 3) if hdc_shots else 0.0,
            "xgf": round(s["xgf"], 3),
            "xga": round(s["xga"], 3),
            "shots_on_goal": s["shots_on_goal"],
            "goals_scored": s["goals_scored"],
            "hdc_saves": s["hdc_saves"],
            "hdc_faced": s["hdc_faced"],
        }
    return result
