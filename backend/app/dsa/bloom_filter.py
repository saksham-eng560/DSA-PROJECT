"""Bloom filter: a compact, probabilistic "have I seen this before?" set.

A bit array of ``m`` bits and ``k`` hash functions. Adding an item sets ``k`` bits; an item is
"possibly present" only if all ``k`` of its bits are set. So:

* a **no** is always right (no false negatives), and
* a **yes** is wrong with a small, tunable probability (false positives).

For ``n`` expected items and a target false-positive rate ``p``::

    m = -n * ln(p) / (ln 2)^2        bits
    k = (m / n) * ln 2               hash functions

add and contains are O(k); space is ``m`` bits - about 1.2 bytes per item at p = 1%, whatever the
length of the items, where a ``set`` of URLs costs 100+ bytes each.

The ``k`` hashes come from one 128-bit BLAKE2b digest split in two (``h1 + i * h2``, the
Kirsch-Mitzenmacher scheme), so each item is hashed once.

Used during a scan (``services/agent/jobs.py``): a filter over every saved posting's duplicate
key answers "definitely new" without a database query. Only a "maybe" falls through to the exact
``SELECT``, so a false positive costs one query and never loses a job.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from typing import Any


class BloomFilter:
    __slots__ = ("_bits", "count", "expected_items", "false_positive_rate", "hash_count", "size")

    def __init__(self, expected_items: int = 1000, false_positive_rate: float = 0.01,
                 items: Iterable[str] = ()) -> None:
        if expected_items <= 0:
            raise ValueError("expected_items must be positive")
        if not 0 < false_positive_rate < 1:
            raise ValueError("false_positive_rate must be between 0 and 1")
        self.expected_items = expected_items
        self.false_positive_rate = false_positive_rate
        self.size = max(8, math.ceil(-expected_items * math.log(false_positive_rate) / (math.log(2) ** 2)))
        self.hash_count = max(1, round((self.size / expected_items) * math.log(2)))
        self._bits = bytearray((self.size + 7) // 8)
        self.count = 0
        for item in items:
            self.add(item)

    def _positions(self, item: str) -> Iterable[int]:
        digest = hashlib.blake2b(item.encode("utf-8"), digest_size=16).digest()
        h1 = int.from_bytes(digest[:8], "little")
        h2 = int.from_bytes(digest[8:], "little") | 1  # odd, so the k probes differ
        return ((h1 + i * h2) % self.size for i in range(self.hash_count))

    def positions(self, item: str) -> list[int]:
        """The bit positions ``item`` maps to (for the visualiser)."""
        return list(self._positions(item))

    def bits(self) -> list[int]:
        """The bit array as 0/1 values. Only sensible for small demo filters."""
        return [(self._bits[i >> 3] >> (i & 7)) & 1 for i in range(self.size)]

    def add(self, item: str) -> None:
        for position in self._positions(item):
            self._bits[position >> 3] |= 1 << (position & 7)
        self.count += 1

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False
        return all(self._bits[position >> 3] & (1 << (position & 7)) for position in self._positions(item))

    def might_contain(self, item: str) -> bool:
        return item in self

    def __len__(self) -> int:
        return self.count

    @property
    def bits_set(self) -> int:
        return sum(bin(byte).count("1") for byte in self._bits)

    def estimated_false_positive_rate(self) -> float:
        """From the fraction of bits actually set: (bits_set / m) ^ k."""
        return (self.bits_set / self.size) ** self.hash_count

    def stats(self) -> dict[str, Any]:
        return {
            "items": self.count,
            "bits": self.size,
            "bytes": len(self._bits),
            "hash_functions": self.hash_count,
            "bits_set": self.bits_set,
            "fill_ratio": round(self.bits_set / self.size, 4),
            "target_false_positive_rate": self.false_positive_rate,
            "estimated_false_positive_rate": round(self.estimated_false_positive_rate(), 6),
        }
