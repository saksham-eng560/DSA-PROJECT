"""Sorting and searching: merge sort, quickselect and binary search.

=================  ====================  ======================================================
Algorithm          Time                  What the app uses it for
=================  ====================  ======================================================
merge_sort         O(n log n), stable    ranking by several keys in turn (stability keeps the
                                         earlier order among ties)
quickselect        O(n) average          the median match score without sorting everything
lower/upper_bound  O(log n)              "your score beats 87% of the deck" (percentile rank),
                                         counting scores inside a range
=================  ====================  ======================================================

Python's own ``sorted`` (Timsort) is used in the request path; ``merge_sort`` is the readable
reference version, checked against ``sorted`` in the tests and timed in the DSA Lab.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")


def merge_sort(items: Sequence[T], key: Callable[[T], Any] | None = None, reverse: bool = False) -> list[T]:
    """Stable merge sort, bottom-up (no recursion): merge runs of 1, then 2, then 4, ..."""
    key = key or (lambda item: item)
    keyed = [(key(item), item) for item in items]
    n = len(keyed)
    width = 1
    buffer = keyed[:]
    while width < n:
        for left in range(0, n, 2 * width):
            mid = min(left + width, n)
            right = min(left + 2 * width, n)
            i, j, out = left, mid, left
            while i < mid and j < right:
                # Take from the right run only when it is strictly ahead: equal keys keep their order.
                take_right = keyed[j][0] > keyed[i][0] if reverse else keyed[j][0] < keyed[i][0]
                if take_right:
                    buffer[out] = keyed[j]
                    j += 1
                else:
                    buffer[out] = keyed[i]
                    i += 1
                out += 1
            buffer[out:right] = keyed[i:mid] if i < mid else keyed[j:right]
        keyed, buffer = buffer, keyed
        width *= 2
    return [item for _, item in keyed]


def quickselect(items: Sequence[T], k: int, key: Callable[[T], Any] | None = None) -> T:
    """The item that would be at index ``k`` if ``items`` were sorted. O(n) on average.

    Partition around a random pivot, then keep only the side that contains position ``k``.
    """
    if not 0 <= k < len(items):
        raise IndexError(k)
    key = key or (lambda item: item)
    pool = list(items)
    rng = random.Random(len(pool))  # deterministic for a given input size
    while True:
        pivot = key(pool[rng.randrange(len(pool))])
        lower = [item for item in pool if key(item) < pivot]
        equal = [item for item in pool if key(item) == pivot]
        if k < len(lower):
            pool = lower
        elif k < len(lower) + len(equal):
            return equal[0]
        else:
            k -= len(lower) + len(equal)
            pool = [item for item in pool if key(item) > pivot]


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return float(quickselect(values, mid))
    return (quickselect(values, mid - 1) + quickselect(values, mid)) / 2


def lower_bound(sorted_values: Sequence[Any], target: Any) -> int:
    """Index of the first value ``>= target`` in an ascending sequence (``len`` when there is none)."""
    lo, hi = 0, len(sorted_values)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def upper_bound(sorted_values: Sequence[Any], target: Any) -> int:
    """Index of the first value ``> target`` in an ascending sequence."""
    lo, hi = 0, len(sorted_values)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def binary_search(sorted_values: Sequence[Any], target: Any) -> int:
    """Index of ``target`` in an ascending sequence, or -1."""
    index = lower_bound(sorted_values, target)
    return index if index < len(sorted_values) and sorted_values[index] == target else -1


def count_in_range(sorted_values: Sequence[Any], low: Any, high: Any) -> int:
    """How many values lie in ``[low, high]``: two binary searches."""
    return max(0, upper_bound(sorted_values, high) - lower_bound(sorted_values, low))


def percentile_rank(sorted_values: Sequence[float], value: float) -> float:
    """Percentage of values strictly below ``value`` (0-100)."""
    if not sorted_values:
        return 0.0
    return round(100.0 * lower_bound(sorted_values, value) / len(sorted_values), 1)
