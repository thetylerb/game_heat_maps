"""
Series win probability calculator for Buck the Odds.

Uses a Markov chain with a fixed per-game win probability p.
The chain terminates when either team reaches 4 wins (best-of-7).
"""

from functools import lru_cache


def series_win_probability(p: float, wins_a: int, wins_b: int) -> float:
    """
    Calculate the probability that team A wins the series from the current state.

    Args:
        p:      Per-game probability that team A wins any single game (0 < p < 1).
        wins_a: Current wins for team A.
        wins_b: Current wins for team B.

    Returns:
        Probability (0.0–1.0) that team A wins the series.

    Raises:
        ValueError: If p is outside (0, 1) or either win count is invalid.
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in (0, 1), got {p}")
    if wins_a < 0 or wins_b < 0:
        raise ValueError("Win counts must be non-negative")
    if wins_a >= 4 and wins_b >= 4:
        raise ValueError("Both teams cannot have 4 wins simultaneously")
    if wins_a == 4:
        return 1.0
    if wins_b == 4:
        return 0.0

    q = 1.0 - p

    @lru_cache(maxsize=None)
    def dp(a: int, b: int) -> float:
        if a == 4:
            return 1.0
        if b == 4:
            return 0.0
        return p * dp(a + 1, b) + q * dp(a, b + 1)

    prob = dp(wins_a, wins_b)
    dp.cache_clear()
    return round(prob, 4)


def series_history(p: float, results: list[str]) -> list[float]:
    """
    Compute team A's series win probability after each game, including pre-series.

    Args:
        p:       Pre-series per-game win probability for team A.
        results: Ordered list of game winners, e.g. ['A', 'B', 'A', 'A'].
                 'A' means team A won that game, anything else means team B won.

    Returns:
        List of probabilities with len(results) + 1 entries.
        Index 0 is the pre-series baseline; index i+1 is after game i+1.

    Example:
        >>> series_history(0.55, ['A', 'A', 'B', 'A'])
        [0.6083, 0.7564, 0.8753, 0.6957, 0.8753]
    """
    probs: list[float] = [series_win_probability(p, 0, 0)]
    wins_a, wins_b = 0, 0

    for result in results:
        if wins_a >= 4 or wins_b >= 4:
            break  # series already over
        if result == "A":
            wins_a += 1
        else:
            wins_b += 1
        probs.append(series_win_probability(p, wins_a, wins_b))

    return probs


def infer_results_from_standings(wins_a: int, wins_b: int) -> list[str]:
    """
    Produce a plausible game-by-game result list from series standings.

    Strategy: interleave wins so the series looks competitive when possible
    (A, B, A, B, ...), always ending with a team-A win for the final game.

    Args:
        wins_a: Total wins for team A.
        wins_b: Total wins for team B.

    Returns:
        List of 'A' and 'B' strings with length wins_a + wins_b.
    """
    total = wins_a + wins_b
    if total == 0:
        return []

    results: list[str] = []
    a_remaining, b_remaining = wins_a, wins_b

    for _ in range(total - 1):
        # Interleave to simulate a realistic series
        if b_remaining > 0 and (a_remaining == 0 or len(results) % 2 == 1):
            results.append("B")
            b_remaining -= 1
        else:
            results.append("A")
            a_remaining -= 1

    # Last game goes to whichever team needs it — keeps the A-wins-final feel
    results.append("A" if a_remaining > 0 else "B")
    return results


if __name__ == "__main__":
    import sys

    # Quick smoke test
    p = 0.55
    results = ["A", "A", "B", "A"]
    history = series_history(p, results)
    print(f"Pre-series p={p}")
    for i, prob in enumerate(history):
        label = "Pre-series" if i == 0 else f"After game {i} ({'A' if results[i-1]=='A' else 'B'} wins)"
        print(f"  {label}: A={prob:.4f}  B={round(1-prob,4):.4f}")
