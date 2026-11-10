"""Interval algorithms: overlaps, merging, free slots and the fewest rooms needed.

All of them start by sorting the intervals by start time, then sweep once from left to right.

=======================  ===============  ============================================
Algorithm                Time             What the app uses it for
=======================  ===============  ============================================
find_conflicts           O(n log n + c)   interviews that overlap (c = clashing pairs)
merge_intervals          O(n log n)       busy blocks on the calendar
free_slots               O(n log n)       when you could still book an interview
min_rooms (heap)         O(n log n)       most interviews happening at the same time
max_non_overlapping      O(n log n)       greedy: the most interviews you can attend
=======================  ===============  ============================================

Intervals are half-open ``[start, end)``: one that ends at 3 pm does not clash with one that
starts at 3 pm. Start and end can be numbers or datetimes - anything that compares.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

from app.dsa.heap import BinaryHeap


class Interval(NamedTuple):
    start: Any
    end: Any
    label: Any = None


def find_conflicts(intervals: Sequence[Interval]) -> list[tuple[Interval, Interval]]:
    """Every pair of overlapping intervals.

    Sweep in start order keeping the intervals that are still "open"; a new interval clashes with
    exactly those whose end lies after its start.
    """
    ordered = sorted(intervals, key=lambda interval: (interval.start, interval.end))
    conflicts: list[tuple[Interval, Interval]] = []
    active: list[Interval] = []
    for interval in ordered:
        active = [other for other in active if other.end > interval.start]
        conflicts.extend((other, interval) for other in active)
        active.append(interval)
    return conflicts


def merge_intervals(intervals: Sequence[Interval]) -> list[Interval]:
    """Join overlapping or touching intervals into the blocks of time they cover."""
    merged: list[Interval] = []
    for interval in sorted(intervals, key=lambda interval: (interval.start, interval.end)):
        if merged and interval.start <= merged[-1].end:
            last = merged[-1]
            if interval.end > last.end:
                merged[-1] = Interval(last.start, interval.end, last.label)
        else:
            merged.append(Interval(interval.start, interval.end, interval.label))
    return merged


def free_slots(intervals: Sequence[Interval], window_start: Any, window_end: Any, min_length: Any = None) -> list[Interval]:
    """Gaps between the busy blocks inside ``[window_start, window_end)``, at least ``min_length`` long."""
    slots: list[Interval] = []
    cursor = window_start
    for busy in merge_intervals(intervals):
        if busy.end <= window_start or busy.start >= window_end:
            continue
        if busy.start > cursor:
            slots.append(Interval(cursor, busy.start))
        if busy.end > cursor:
            cursor = busy.end
    if cursor < window_end:
        slots.append(Interval(cursor, window_end))
    if min_length is not None:
        slots = [slot for slot in slots if slot.end - slot.start >= min_length]
    return slots


def min_rooms(intervals: Sequence[Interval]) -> int:
    """The largest number of intervals open at the same moment.

    A min-heap holds the end times of the intervals in progress. Before each new interval starts,
    every room that has already been freed is popped; the heap's size is the rooms in use.
    """
    ends: BinaryHeap[Any] = BinaryHeap()
    most = 0
    for interval in sorted(intervals, key=lambda interval: (interval.start, interval.end)):
        while ends and ends.peek() <= interval.start:
            ends.pop()
        ends.push(interval.end)
        most = max(most, len(ends))
    return most


def max_non_overlapping(intervals: Sequence[Interval]) -> list[Interval]:
    """The largest set of intervals with no clashes (activity selection).

    Greedy: always take the one that finishes first among those that start after the last one
    taken. Finishing early leaves the most room for the rest, which is why greedy is optimal here.
    """
    chosen: list[Interval] = []
    for interval in sorted(intervals, key=lambda interval: (interval.end, interval.start)):
        if not chosen or interval.start >= chosen[-1].end:
            chosen.append(interval)
    return chosen
