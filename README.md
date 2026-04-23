# Buck the Odds — NHL Playoff Analytics Pipeline

Pulls live NHL game data and produces structured JSON for player grade cards and series probability graphics.

---

## Setup (one time)

```bash
pip install -r requirements.txt
```

Requires Python 3.11+ and an internet connection for the NHL API.

---

## Before Every Game

### Step 1 — Find the game ID

Run this the morning of or any time before puck drop:

```bash
python fetch_game.py --date 2026-04-23
```

Output looks like:

```
Playoff games on 2026-04-23:
  [2026030123]  PHI @ PIT  0-0  (FUT)  PPG Paints Arena
  [2026030124]  EDM @ VGK  0-0  (FUT)  T-Mobile Arena
```

Copy the game ID in brackets for the game you're covering.

---

### Step 2 — Know your three inputs

You need these before you can run the pipeline:

| Input | Flag | What it is |
|---|---|---|
| Game ID | `--game` | From Step 1 |
| Your team | `--team` | Three-letter abbreviation, e.g. `PHI` |
| Opponent | `--opponent` | e.g. `PIT` |
| Pre-series win prob | `--p` | Your team's per-game win probability at the start of the series (use implied odds from the opening line, e.g. `-160` → `0.615`) |
| Series wins | `--series-wins` | Your team's wins going **into** this game |
| Opponent wins | `--opponent-wins` | Opponent wins going into this game |

**How to convert moneyline odds to `--p`:**

| Moneyline | Formula | Example |
|---|---|---|
| Favorite (negative) | `odds / (odds + 100)` | `-160` → `160/260` = `0.615` |
| Underdog (positive) | `100 / (odds + 100)` | `+140` → `100/240` = `0.417` |

Use the opening series line, not the game-by-game line — `--p` stays fixed for the entire series.

---

### Step 3 — Start watch mode before puck drop

Run this command before the game starts. It will poll the NHL API every 60 seconds and automatically process the data the moment the game goes final.

```bash
python pipeline.py \
    --game 2026030123 \
    --team PHI \
    --opponent PIT \
    --p 0.615 \
    --series-wins 1 \
    --opponent-wins 0 \
    --watch
```

Leave the terminal open. When the game ends you'll see:

```
[watch] Game is final. Running pipeline...
[pipeline] Done. Output written to output/2026030123.json
```

Your JSON file is ready to feed into graphics.

---

### Optional: add game-by-game results for an accurate series history graph

If you've been tracking who won each game, pass them with `--results`. Use `A` for your team, `B` for the opponent:

```bash
python pipeline.py \
    --game 2026030123 \
    --team PHI \
    --opponent PIT \
    --p 0.615 \
    --series-wins 2 \
    --opponent-wins 1 \
    --results A B A \
    --watch
```

This gives you an accurate `series_history` probability trace in the output (one value per game played, starting from the pre-series baseline). Without `--results` the pipeline will infer a plausible sequence from the standings.

---

## After the Game (manual run)

If you forgot to start watch mode, run the pipeline manually once the game is final:

```bash
python pipeline.py \
    --game 2026030123 \
    --team PHI \
    --opponent PIT \
    --p 0.615 \
    --series-wins 2 \
    --opponent-wins 1 \
    --results A B A
```

If the game isn't final yet it will tell you and exit cleanly.

---

## Output

Written to `output/<game_id>.json`:

```json
{
  "game_id": "2026030123",
  "date": "2026-04-23",
  "home": "PIT",
  "away": "PHI",
  "score": {"PHI": 5, "PIT": 2},
  "series": {"PHI": 2, "PIT": 1},
  "series_win_prob": {"PHI": 0.73, "PIT": 0.27},
  "series_history": [0.615, 0.72, 0.58, 0.73],
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

## Grade Scale

| Score | Grade | Score | Grade |
|---|---|---|---|
| 95+ | A+ | 64–60 | C |
| 90–94 | A | 59–55 | C- |
| 85–89 | A- | 54–50 | D+ |
| 80–84 | B+ | 49–45 | D |
| 75–79 | B | <45 | F |
| 70–74 | B- | | |
| 65–69 | C+ | | |

**Position weights:**

| Metric | F | D | G |
|---|---|---|---|
| Goals + Assists | 30% | — | — |
| xGF on-ice | 25% | — | — |
| HDC generated | 20% | — | — |
| Plus/minus | 15% | 15% | — |
| Penalties | 10% | — | — |
| xGA on-ice | — | 30% | — |
| HDC against | — | 25% | — |
| Blocked shots | — | 15% | — |
| Assists | — | 15% | — |
| Save% vs xSV% | — | — | 40% |
| HDC save% | — | — | 40% |
| Goals against | — | — | 20% |

---

## File Structure

```
game_heat_maps/
├── fetch_game.py       # NHL API fetcher
├── hdc_calculator.py   # Shot classification and xG/HDC stats
├── grader.py           # Letter grader + verdict generator
├── series_prob.py      # Markov chain series win probability
├── pipeline.py         # Master pipeline — the one you run
├── requirements.txt
├── README.md
├── cache/              # Auto-created — raw API responses (cached)
└── output/             # Auto-created — pipeline JSON output
```

All API responses are cached in `cache/` — re-running for the same game is instant. Use `--force` to bypass the cache and re-fetch live data.
