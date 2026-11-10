"""LRU (least recently used) cache: a hash map plus a doubly linked list.

The hash map finds an entry in O(1); the linked list keeps entries in order of use. Reading or
writing an entry moves its node to the front, and when the cache is full the node at the back -
the one unused for longest - is unlinked. Because the map points straight at list nodes, moving
or removing one is O(1) too.

=========  =====
Operation  Time
=========  =====
get        O(1)
put        O(1)
evict      O(1)
=========  =====

Space O(capacity).

Entries can also expire after ``ttl`` seconds, and the cache counts hits, misses and evictions.
It is thread-safe (one lock), since the API serves requests from a thread pool.

Used for: the per-user search index (trie + inverted index) so it isn't rebuilt on every
keystroke, the chatbot's retrieval results, and LLM answers to repeated prompts.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable, Iterator
from typing import Any, Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING: Any = object()


class _Node(Generic[K, V]):
    __slots__ = ("expires", "key", "next", "prev", "value")

    def __init__(self, key: K, value: V, expires: float | None) -> None:
        self.key = key
        self.value = value
        self.expires = expires
        self.prev: _Node[K, V] | None = None
        self.next: _Node[K, V] | None = None


class LRUCache(Generic[K, V]):
    def __init__(self, capacity: int = 128, ttl: float | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.ttl = ttl
        self._clock = clock
        self._map: dict[K, _Node[K, V]] = {}
        # Sentinel head and tail: real nodes always sit between them, so linking never checks for None.
        self._head: _Node[Any, Any] = _Node(None, None, None)
        self._tail: _Node[Any, Any] = _Node(None, None, None)
        self._head.next = self._tail
        self._tail.prev = self._head
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    # ------------------------------------------------------------------ list surgery
    def _unlink(self, node: _Node[K, V]) -> None:
        node.prev.next = node.next  # type: ignore[union-attr]
        node.next.prev = node.prev  # type: ignore[union-attr]

    def _push_front(self, node: _Node[K, V]) -> None:
        node.prev = self._head
        node.next = self._head.next
        self._head.next.prev = node  # type: ignore[union-attr]
        self._head.next = node

    def _expired(self, node: _Node[K, V]) -> bool:
        return node.expires is not None and node.expires <= self._clock()

    # ------------------------------------------------------------------ API
    def get(self, key: K, default: Any = None) -> Any:
        with self._lock:
            node = self._map.get(key)
            if node is None or self._expired(node):
                if node is not None:  # expired: drop it now
                    self._unlink(node)
                    del self._map[key]
                self.misses += 1
                return default
            self._unlink(node)
            self._push_front(node)
            self.hits += 1
            return node.value

    def put(self, key: K, value: V, ttl: float | None = None) -> None:
        ttl = self.ttl if ttl is None else ttl
        expires = self._clock() + ttl if ttl is not None else None
        with self._lock:
            node = self._map.get(key)
            if node is not None:
                node.value, node.expires = value, expires
                self._unlink(node)
                self._push_front(node)
                return
            if len(self._map) >= self.capacity:
                oldest = self._tail.prev
                self._unlink(oldest)  # type: ignore[arg-type]
                del self._map[oldest.key]  # type: ignore[union-attr]
                self.evictions += 1
            node = _Node(key, value, expires)
            self._map[key] = node
            self._push_front(node)

    def get_or_set(self, key: K, factory: Callable[[], V], ttl: float | None = None) -> V:
        """Return the cached value, or compute, store and return it."""
        value = self.get(key, _MISSING)
        if value is _MISSING:
            value = factory()
            self.put(key, value, ttl)
        return value

    def delete(self, key: K) -> bool:
        with self._lock:
            node = self._map.pop(key, None)
            if node is None:
                return False
            self._unlink(node)
            return True

    def clear(self) -> None:
        with self._lock:
            self._map.clear()
            self._head.next = self._tail
            self._tail.prev = self._head

    def __contains__(self, key: object) -> bool:
        with self._lock:
            node = self._map.get(key)  # type: ignore[arg-type]
            return node is not None and not self._expired(node)

    def __len__(self) -> int:
        return len(self._map)

    def keys(self) -> Iterator[K]:
        """Most recently used first."""
        with self._lock:
            node = self._head.next
            keys = []
            while node is not self._tail:
                keys.append(node.key)  # type: ignore[union-attr]
                node = node.next  # type: ignore[union-attr]
        return iter(keys)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "size": len(self._map),
            "capacity": self.capacity,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }
