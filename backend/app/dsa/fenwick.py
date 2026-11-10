"""Fenwick tree (binary indexed tree): prefix sums that stay fast while values change.

A plain array answers "sum of the first i values" in O(n); a prefix-sum array answers in O(1) but
costs O(n) to update. A Fenwick tree does both in O(log n) by storing, at index i, the sum of a
block whose length is the lowest set bit of i (``i & -i``).

==============  ==========
Operation       Time
==============  ==========
add(i, delta)   O(log n)
prefix_sum(i)   O(log n)
range_sum(l,r)  O(log n)
build           O(n)
==============  ==========

Space O(n).

Used by the activity analytics: applications are bucketed per day, and "how many in the last 7 /
30 days ending on any date" is a range sum, so the rolling-window chart is O(days * log days)
instead of O(days * window).
"""

from __future__ import annotations

from collections.abc import Iterable


class FenwickTree:
    __slots__ = ("_tree", "size")

    def __init__(self, values: Iterable[float] | int = 0) -> None:
        if isinstance(values, int):
            self.size = values
            self._tree = [0.0] * (values + 1)
            return
        data = list(values)
        self.size = len(data)
        self._tree = [0.0, *data]
        # O(n) build: push each node's total into the node that covers it.
        for i in range(1, self.size + 1):
            parent = i + (i & -i)
            if parent <= self.size:
                self._tree[parent] += self._tree[i]

    def add(self, index: int, delta: float) -> None:
        """Add ``delta`` to the value at ``index`` (0-based)."""
        if not 0 <= index < self.size:
            raise IndexError(index)
        i = index + 1
        while i <= self.size:
            self._tree[i] += delta
            i += i & -i

    def prefix_sum(self, count: int) -> float:
        """Sum of the first ``count`` values (indexes ``0 .. count - 1``)."""
        i = max(0, min(count, self.size))
        total = 0.0
        while i > 0:
            total += self._tree[i]
            i -= i & -i
        return total

    def range_sum(self, left: int, right: int) -> float:
        """Sum of the values at indexes ``left .. right``, both inclusive."""
        if right < left:
            return 0.0
        return self.prefix_sum(right + 1) - self.prefix_sum(left)

    def get(self, index: int) -> float:
        return self.range_sum(index, index)

    def total(self) -> float:
        return self.prefix_sum(self.size)

    def __len__(self) -> int:
        return self.size

    def nodes(self) -> list[dict[str, float | int]]:
        """Each tree node with the block of values it covers (for the visualiser)."""
        return [{"index": i - 1, "covers_from": i - (i & -i), "covers_to": i - 1, "sum": self._tree[i]}
                for i in range(1, self.size + 1)]

    def query_path(self, count: int) -> list[int]:
        """The 0-based node indexes a ``prefix_sum(count)`` reads: one per set bit of ``count``."""
        path, i = [], max(0, min(count, self.size))
        while i > 0:
            path.append(i - 1)
            i -= i & -i
        return path

    def lower_bound(self, target: float) -> int:
        """Smallest index whose prefix sum reaches ``target`` (values must be >= 0), or ``size``.

        Binary lifting down the tree: O(log n). Answers "on which day did I reach my 50th application?".
        """
        if target <= 0:
            return 0
        position = 0
        step = 1 << self.size.bit_length()
        remaining = target
        while step:
            nxt = position + step
            if nxt <= self.size and self._tree[nxt] < remaining:
                position = nxt
                remaining -= self._tree[nxt]
            step >>= 1
        return position
