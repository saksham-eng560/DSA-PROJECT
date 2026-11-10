"""Binary heap, top-K selection and k-way merge.

A binary heap is a complete binary tree stored in an array: the children of index ``i`` are at
``2i + 1`` and ``2i + 2``, and every parent is ``<=`` its children (min-heap). The smallest
item is always at index 0.

==============  ===========
Operation       Time
==============  ===========
push            O(log n)
pop             O(log n)
peek            O(1)
heapify (build) O(n)
==============  ===========

Space O(n).

Where the app uses it:

* :func:`top_k` - the best K matches out of N scored jobs in O(N log K) with a size-K min-heap,
  instead of sorting all N in O(N log N). Powers "best matches" on the Overview page, the
  chatbot's recommendations and the top suggestions of the autocomplete trie.
* :func:`k_way_merge` - merges the already-sorted result lists of several job sources into one
  sorted stream in O(N log k).
* :class:`BinaryHeap` is the priority queue of Dijkstra's algorithm (:mod:`app.dsa.graph`) and
  of the interview-room sweep (:mod:`app.dsa.intervals`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class BinaryHeap(Generic[T]):
    """Min-heap ordered by ``key(item)``. Pass ``reverse=True`` for a max-heap.

    Each entry is ``(key, counter, item)``. The counter is unique, so comparing two entries is
    decided by the key or, on a tie, by insertion order - it never reaches the items, which
    therefore don't need to be orderable themselves (dicts, ORM rows...).
    """

    __slots__ = ("_counter", "_data", "_key", "_sign")

    def __init__(self, items: Iterable[T] = (), key: Callable[[T], Any] | None = None, reverse: bool = False) -> None:
        self._key = key or (lambda item: item)
        self._sign = -1 if reverse else 1  # -1: keys are wrapped in _Reversed
        self._counter = 0
        self._data: list[tuple[Any, int, T]] = []
        for item in items:
            self._data.append(self._entry(item))
        # Floyd's build-heap: sift down every internal node, bottom-up. O(n) in total.
        for i in range(len(self._data) // 2 - 1, -1, -1):
            self._sift_down(i)

    def _entry(self, item: T) -> tuple[Any, int, T]:
        self._counter += 1
        key = self._key(item)
        return (_Reversed(key) if self._sign < 0 else key, self._counter, item)

    # ------------------------------------------------------------------ sifting
    def _sift_up(self, i: int) -> None:
        data = self._data
        entry = data[i]
        while i > 0:
            parent = (i - 1) // 2
            if data[parent] <= entry:
                break
            data[i] = data[parent]
            i = parent
        data[i] = entry

    def _sift_down(self, i: int) -> None:
        data = self._data
        n = len(data)
        entry = data[i]
        while True:
            child = 2 * i + 1
            if child >= n:
                break
            if child + 1 < n and data[child + 1] < data[child]:
                child += 1
            if entry <= data[child]:
                break
            data[i] = data[child]
            i = child
        data[i] = entry

    # ------------------------------------------------------------------ API
    def push(self, item: T) -> None:
        self._data.append(self._entry(item))
        self._sift_up(len(self._data) - 1)

    def pop(self) -> T:
        if not self._data:
            raise IndexError("pop from an empty heap")
        top = self._data[0]
        last = self._data.pop()
        if self._data:
            self._data[0] = last
            self._sift_down(0)
        return top[2]

    def peek(self) -> T:
        if not self._data:
            raise IndexError("peek at an empty heap")
        return self._data[0][2]

    def pushpop(self, item: T) -> T:
        """Push ``item`` then pop the smallest: one sift instead of two."""
        entry = self._entry(item)
        if self._data and self._data[0] < entry:
            entry, self._data[0] = self._data[0], entry
            self._sift_down(0)
        return entry[2]

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

    def items(self) -> list[T]:
        """The heap's array, in storage order (for the visualiser)."""
        return [entry[2] for entry in self._data]

    def drain(self) -> Iterator[T]:
        """Pop everything in sorted order (this is heapsort: O(n log n))."""
        while self._data:
            yield self.pop()


class _Reversed:
    """Wraps a key so comparisons run backwards, turning the min-heap into a max-heap.

    Negating the key would only work for numbers; keys here may be tuples or strings.
    """

    __slots__ = ("key",)

    def __init__(self, key: Any) -> None:
        self.key = key

    def __lt__(self, other: _Reversed) -> bool:
        return other.key < self.key

    def __le__(self, other: _Reversed) -> bool:
        return other.key <= self.key

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Reversed) and self.key == other.key

    def __hash__(self) -> int:  # pragma: no cover - entries are never hashed
        return hash(self.key)


def top_k(items: Iterable[T], k: int, key: Callable[[T], Any] | None = None) -> list[T]:
    """The ``k`` largest items by ``key``, largest first.

    Keeps a min-heap of the best ``k`` seen so far; each further item costs O(log k) only when it
    beats the smallest of them. Time O(n log k), space O(k). Ties go to the item seen first.
    """
    if k <= 0:
        return []
    key = key or (lambda item: item)
    # Order by (key, -position): among equal keys the earlier item is "larger", so it survives.
    heap: BinaryHeap[tuple[Any, int, T]] = BinaryHeap(key=_rank)
    for position, item in enumerate(items):
        entry = (key(item), -position, item)
        if len(heap) < k:
            heap.push(entry)
        else:
            smallest = heap.peek()
            if entry[0] > smallest[0] or (entry[0] == smallest[0] and entry[1] > smallest[1]):
                heap.pushpop(entry)
    return [entry[2] for entry in reversed(list(heap.drain()))]


def _rank(entry: tuple[Any, int, Any]) -> tuple[Any, int]:
    return entry[0], entry[1]


def k_way_merge(sequences: Sequence[Sequence[T]], key: Callable[[T], Any] | None = None) -> Iterator[T]:
    """Merge ``k`` sorted sequences into one sorted stream.

    The heap holds one "head" per sequence; popping the smallest and pushing its successor costs
    O(log k), so merging N items in total is O(N log k). Stable across sequences.
    """
    key = key or (lambda item: item)
    heap: BinaryHeap[tuple[Any, int, int]] = BinaryHeap(key=_rank)
    for index, sequence in enumerate(sequences):
        if sequence:
            heap.push((key(sequence[0]), index, 0))
    while heap:
        _, index, position = heap.pop()
        sequence = sequences[index]
        yield sequence[position]
        if position + 1 < len(sequence):
            heap.push((key(sequence[position + 1]), index, position + 1))
