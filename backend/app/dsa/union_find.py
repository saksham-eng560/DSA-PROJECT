"""Union-Find (disjoint set union) with union by rank and path compression.

Keeps a collection of items split into groups and answers "are these two in the same group?".
Every group is a tree whose root names the group:

* ``find(x)`` walks up to the root, then points every node on the way straight at it
  (path compression);
* ``union(a, b)`` hangs the shallower tree under the deeper one (union by rank).

With both tricks a sequence of m operations on n items costs O(m * alpha(n)), where alpha is the
inverse Ackermann function - below 5 for any input that fits in a computer, so effectively O(1)
per operation. Space O(n).

Used to group duplicate postings: the same internship often appears on LinkedIn, Internshala and
the company's own board under slightly different titles. Each pair that looks like the same job
is ``union``-ed, and the groups that fall out are shown as one card with "also posted on ...".
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import Generic, TypeVar

T = TypeVar("T", bound=Hashable)


class UnionFind(Generic[T]):
    __slots__ = ("_parent", "_rank", "_size", "components")

    def __init__(self, items: Iterable[T] = ()) -> None:
        self._parent: dict[T, T] = {}
        self._rank: dict[T, int] = {}
        self._size: dict[T, int] = {}
        self.components = 0
        for item in items:
            self.add(item)

    def add(self, item: T) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0
            self._size[item] = 1
            self.components += 1

    def find(self, item: T) -> T:
        """The representative (root) of ``item``'s group."""
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression, iteratively (no recursion limit)
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: T, b: T) -> bool:
        """Merge the groups of ``a`` and ``b``. Returns False when they were already together."""
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return False
        if self._rank[root_a] < self._rank[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]
        if self._rank[root_a] == self._rank[root_b]:
            self._rank[root_a] += 1
        self.components -= 1
        return True

    def connected(self, a: T, b: T) -> bool:
        return self.find(a) == self.find(b)

    def size_of(self, item: T) -> int:
        return self._size[self.find(item)]

    def __len__(self) -> int:
        return len(self._parent)

    def __contains__(self, item: object) -> bool:
        return item in self._parent

    def parents(self) -> dict[T, T]:
        """Each item's current parent pointer (roots point at themselves), for the visualiser."""
        return dict(self._parent)

    def groups(self, min_size: int = 1) -> list[list[T]]:
        """Every group with at least ``min_size`` members, largest first; members keep insertion order."""
        by_root: dict[T, list[T]] = {}
        for item in self._parent:
            by_root.setdefault(self.find(item), []).append(item)
        groups = [members for members in by_root.values() if len(members) >= min_size]
        groups.sort(key=len, reverse=True)
        return groups
