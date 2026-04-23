# Buck the Odds — NHL Playoff Analytics Pipeline

A local Python pipeline that pulls live NHL game data and produces structured JSON output for generating prediction graphics and player performance grade cards.

## Requirements

- Python 3.11+
- Internet access for the NHL API (`api-web.nhle.com`)

```bash
pip install -r requirements.txt
```

---

## Scripts

### `fetch_game.py` — NHL Data Fetcher

Pulls raw data from the free NHL API and caches responses under `cache/`.

```bash
# List all playoff games on a date
python fetch_game.py --date 2026-04-23

# Pull and summarise all data for a specific game
python fetch_game.py --game 2026030123

# Force-refresh (bypass cache)
python fetch_game.py --game 2026030123 --force
```

**Functions available for import:**

| Function | Returns |
|---|---|
| `get_schedule(date_str)` | Full schedule JSON |
| `get_play_by_play(game_id)` | Play-by-play event stream |
| `get_boxscore(game_id)` | Player stats |
| `get_landing(game_id)` | Shifts and on-ice context |
| `list_playoff_games(date_str)` | Filtered list of playoff games |
| `is_game_final(game_id)` | `True` if game state is FINAL/OFF |

---

### `hdc_calculator.py` — High Danger Chance Calculator

Classifies every shot in the play-by-play by danger zone and computes per-player xG and HDC stats.

**Danger zones (NHL rink, center = 0,0, nets at x ≈ ±89):**

| Zone | Criteria |
|---|---|
| **High** | ≤10 ft from net (crease), OR ≤20 ft AND within royal-road slot (|y| ≤ 22 ft) |
| **Medium** | ≤40 ft from net (faceoff circle range) |
| **Low** | >40 ft (perimeter, point shots) |

**xG weights:** High = 0.35, Medium = 0.10, Low = 0.03

```python
from fetch_game import get_play_by_play, get_landing
from hdc_calculator import calculate_hdc

pbp     = get_play_by_play("2026030123")
landing = get_landing("2026030123")
stats   = calculate_hdc(pbp, landing)

# stats[player_id] → {hdc_for, hdc_against, xgf, xga, ...}
```

---

### `grader.py` — Player Grader

Grades every player A+ through F using position-specific weights.

**Position weights:**

| Metric | Forwards | Defensemen | Goalies |
|---|---|---|---|
| Goals + Assists | 30% | — | — |
| xGF on-ice | 25% | — | — |
| HDC generated | 20% | — | — |
| xGA on-ice | — | 30% | — |
| HDC against | — | 25% | — |
| Blocked shots | — | 15% | — |
| Assists | — | 15% | — |
| Plus/minus | 15% | 15% | — |
| Penalties (negative) | 10% | — | — |
| Save% vs xSV% | — | — | 40% |
| HDC save% | — | — | 40% |
| Goals against | — | — | 20% |

**Grade scale:** 95+ = A+, 90 = A, 85 = A-, 80 = B+, 75 = B, 70 = B-, 65 = C+, 60 = C, 55 = C-, 50 = D+, 45 = D, <45 = F

```python
from grader import grade_players

grades = grade_players(boxscore, hdc_stats)
# grades['PHI'] → [{name, position, grade, verdict, stats}, ...]
```

---

### `series_prob.py` — Series Win Probability

Markov chain series probability with constant per-game win probability.

```python
from series_prob import series_win_probability, series_history

# Current probability from series state
prob = series_win_probability(p=0.55, wins_a=2, wins_b=1)

# Full history across a series
history = series_history(p=0.55, results=['A', 'B', 'A'])
# Returns [pre_series_prob, after_g1, after_g2, after_g3]
```

---

### `pipeline.py` — Master Pipeline

Ties everything together. Run after a game ends to get a full JSON output.

```bash
# Basic usage (after game is final)
python pipeline.py \
    --game 2026030123 \
    --team PHI \
    --opponent PIT \
    --p 0.69 \
    --series-wins 3 \
    --opponent-wins 0

# With explicit game-by-game results for an accurate series history graph
python pipeline.py \
    --game 2026030123 \
    --team PHI \
    --opponent PIT \
    --p 0.69 \
    --series-wins 2 \
    --opponent-wins 1 \
    --results A B A

# Watch mode — polls every 60 s during a live game, runs automatically when final
python pipeline.py \
    --game 2026030123 \
    --team PHI \
    --opponent PIT \
    --p 0.69 \
    --series-wins 3 \
    --opponent-wins 0 \
    --watch

# Custom poll interval (30 s)
python pipeline.py ... --watch --poll-interval 30
```

**Arguments:**

| Flag | Required | Description |
|---|---|---|
| `--game` | Yes | NHL game ID (e.g. `2026030123`) |
| `--team` | Yes | Your team's abbreviation (e.g. `PHI`) |
| `--opponent` | Yes | Opponent abbreviation (e.g. `PIT`) |
| `--p` | Yes | Pre-series per-game win probability for `--team` |
| `--series-wins` | Yes | Your team's wins **after** this game |
| `--opponent-wins` | Yes | Opponent wins |
| `--results` | No | Space-separated game results, e.g. `A A B A` |
| `--watch` | No | Poll live; run pipeline when game ends |
| `--poll-interval` | No | Seconds between polls (default 60) |
| `--force` | No | Bypass API cache |

**Output:** `output/<game_id>.json`

---

## Output Schema

```json
{
  "game_id": "2026030123",
  "date": "2026-04-23",
  "home": "PIT",
  "away": "PHI",
  "score": {"PHI": 5, "PIT": 2},
  "series": {"PHI": 3, "PIT": 0},
  "series_win_prob": {"PHI": 0.97, "PIT": 0.03},
  "series_history": [0.69, 0.83, 0.94, 0.97],
  "player_grades": {
    "PHI": [
      {
        "name": "Sean Couturier",
        "position": "C",
        "grade": "A+",
        "verdict": "2G 1A in 21:00. Put the series on ice.",
        "stats": {
          "goals": 2, "assists": 1, "toi": "21:00",
          "plus_minus": 3, "shots": 5,
          "hdc_for": 4, "hdc_against": 1,
          "xgf": 1.45, "xga": 0.35
        }
      }
    ],
    "PIT": []
  }
}
```

---

## File Structure

```
game_heat_maps/
├── fetch_game.py       # NHL API fetcher (all four endpoints)
├── hdc_calculator.py   # Shot classification and xG/HDC stats
├── grader.py           # Letter grader + one-line verdict generator
├── series_prob.py      # Markov chain series win probability
├── pipeline.py         # Master orchestrator (run this)
├── requirements.txt
├── README.md
├── cache/              # Auto-created — raw API responses
└── output/             # Auto-created — pipeline JSON output
```

---

## How to Find a Game ID

```bash
python fetch_game.py --date 2026-04-23
```

Game IDs follow the format `YYYY03XXXX` where `03` indicates playoffs. Copy the ID from the output and pass it to `--game`.

---

## Notes

- All API responses are cached in `cache/` — re-running with the same game ID is instant without `--force`.
- On-ice player attribution uses embedded event data when available; falls back to shift reconstruction from the landing endpoint, then to shooter-only if neither is present.
- The `--results` flag is recommended for accurate series history graphs. Without it, the pipeline infers a plausible game sequence from the standings.
