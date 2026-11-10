"""Hash table with separate chaining.

Keys are hashed into buckets; each bucket is a small list of ``(key, value)`` pairs. When the
load factor (items / buckets) passes 0.75 the table doubles and every item is re-hashed, which
keeps chains short.

==========  ==============  =============
Operation   Average         Worst case
==========  ==============  =============
get / set   O(1)            O(n) (every key in one bucket)
delete      O(1)            O(n)
resize      O(n), amortised O(1) per insert
==========  ==============  =============

Space O(n).

Python's built-in ``dict`` is the same idea (with open addressing instead of chaining) written
in C, so the app's hot paths use ``dict``. This class exists to show how it works: the DSA Lab
page benchmarks it against ``dict`` and shows its bucket occupancy.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterator
from typing import Any, Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING: Any = object()


class HashMap(Generic[K, V]):
    MAX_LOAD = 0.75

    __slots__ = ("_buckets", "_size", "resizes")

    def __init__(self, capacity: int = 8) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._buckets: list[list[tuple[K, V]]] = [[] for _ in range(capacity)]
        self._size = 0
        self.resizes = 0

    # ------------------------------------------------------------------ internals
    def _index(self, key: K, capacity: int | None = None) -> int:
        return hash(key) % (capacity or len(self._buckets))

    def _resize(self, capacity: int) -> None:
        buckets: list[list[tuple[K, V]]] = [[] for _ in range(capacity)]
        for bucket in self._buckets:
            for key, value in bucket:
                buckets[self._index(key, capacity)].append((key, value))
        self._buckets = buckets
        self.resizes += 1

    # ------------------------------------------------------------------ mapping API
    def set(self, key: K, value: V) -> None:
        bucket = self._buckets[self._index(key)]
        for i, (existing, _) in enumerate(bucket):
            if existing == key:
                bucket[i] = (key, value)
                return
        bucket.append((key, value))
        self._size += 1
        if self._size / len(self._buckets) > self.MAX_LOAD:
            self._resize(len(self._buckets) * 2)

    def get(self, key: K, default: Any = None) -> Any:
        for existing, value in self._buckets[self._index(key)]:
            if existing == key:
                return value
        return default

    def delete(self, key: K) -> bool:
        bucket = self._buckets[self._index(key)]
        for i, (existing, _) in enumerate(bucket):
            if existing == key:
                bucket.pop(i)
                self._size -= 1
                return True
        return False

    def __setitem__(self, key: K, value: V) -> None:
        self.set(key, value)

    def __getitem__(self, key: K) -> V:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __delitem__(self, key: K) -> None:
        if not self.delete(key):
            raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return self.get(key, _MISSING) is not _MISSING  # type: ignore[arg-type]

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[K]:
        for bucket in self._buckets:
            for key, _ in bucket:
                yield key

    def items(self) -> Iterator[tuple[K, V]]:
        for bucket in self._buckets:
            yield from bucket

    # ------------------------------------------------------------------ introspection
    def buckets(self) -> list[list[K]]:
        """The keys in each bucket, in table order (for the visualiser)."""
        return [[key for key, _ in bucket] for bucket in self._buckets]

    @property
    def capacity(self) -> int:
        return len(self._buckets)

    @property
    def load_factor(self) -> float:
        return self._size / len(self._buckets)

    def stats(self) -> dict[str, Any]:
        """Bucket occupancy, for the DSA Lab visualiser."""
        lengths = [len(b) for b in self._buckets]
        return {
            "size": self._size,
            "capacity": len(self._buckets),
            "load_factor": round(self.load_factor, 3),
            "longest_chain": max(lengths, default=0),
            "empty_buckets": sum(1 for n in lengths if n == 0),
            "resizes": self.resizes,
        }
