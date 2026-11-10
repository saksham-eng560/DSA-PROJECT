"""Dynamic programming: 0/1 knapsack and longest increasing subsequence.

Dynamic programming solves a problem by solving its overlapping sub-problems once each and
re-using the answers.

``knapsack`` - *choose items to maximise value without exceeding a capacity*.
    ``best[i][c]`` = the best value using the first ``i`` items with capacity ``c``; each item is
    either skipped (``best[i-1][c]``) or taken (``best[i-1][c-w] + v``).
    Time O(n * capacity), space O(n * capacity) (the table is kept so the chosen items can be
    read back).

    Used by the one-page resume optimiser (``services/resume_optimizer.py``): every bullet point
    has a *weight* (the lines it takes on the page) and a *value* (how relevant it is to the job),
    and the page is the knapsack.

``longest_increasing_subsequence`` - patience sorting with binary search, O(n log n).
    Used by analytics to find your longest run of improving match scores over time.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence


def knapsack(weights: Sequence[int], values: Sequence[float], capacity: int) -> tuple[float, list[int]]:
    """Returns ``(best total value, indexes of the chosen items in input order)``."""
    if len(weights) != len(values):
        raise ValueError("weights and values must be the same length")
    if any(weight < 0 for weight in weights):
        raise ValueError("weights must be non-negative")
    capacity = max(0, capacity)
    n = len(weights)
    best = [[0.0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        weight, value = weights[i - 1], values[i - 1]
        row, above = best[i], best[i - 1]
        for c in range(capacity + 1):
            row[c] = above[c]
            if weight <= c and above[c - weight] + value > row[c]:
                row[c] = above[c - weight] + value
    # Walk the table backwards: an item was taken wherever the value changed from the row above.
    chosen: list[int] = []
    c = capacity
    for i in range(n, 0, -1):
        if best[i][c] != best[i - 1][c]:
            chosen.append(i - 1)
            c -= weights[i - 1]
    chosen.reverse()
    return best[n][capacity], chosen


def greedy_knapsack(weights: Sequence[int], values: Sequence[float], capacity: int) -> tuple[float, list[int]]:
    """Value-per-weight greedy: fast (O(n log n)) but not always optimal. Kept for comparison in the DSA Lab."""
    order = sorted(range(len(weights)), key=lambda i: values[i] / weights[i] if weights[i] else float("inf"), reverse=True)
    total, remaining, chosen = 0.0, capacity, []
    for i in order:
        if weights[i] <= remaining:
            chosen.append(i)
            remaining -= weights[i]
            total += values[i]
    return total, sorted(chosen)


def longest_increasing_subsequence(values: Sequence[float]) -> list[int]:
    """Indexes of one longest strictly increasing subsequence. O(n log n).

    ``tails[k]`` is the smallest value that ends an increasing run of length ``k + 1``; each new
    value replaces the first tail that is >= it (binary search), or extends the list.
    """
    tails: list[float] = []
    tail_index: list[int] = []
    previous: list[int] = [-1] * len(values)
    for i, value in enumerate(values):
        position = bisect_left(tails, value)
        if position == len(tails):
            tails.append(value)
            tail_index.append(i)
        else:
            tails[position] = value
            tail_index[position] = i
        previous[i] = tail_index[position - 1] if position else -1
    result: list[int] = []
    i = tail_index[-1] if tail_index else -1
    while i != -1:
        result.append(i)
        i = previous[i]
    result.reverse()
    return result
