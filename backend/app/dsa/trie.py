"""Trie (prefix tree) for search-as-you-type suggestions.

Each node is one character; a path from the root spells a prefix. Words that share a prefix
share the nodes for it, so finding every word that starts with ``"pyt"`` means walking three
nodes and then reading the subtree below them - the rest of the vocabulary is never touched.

==================  =============================================
Operation           Time (L = length of the word / prefix)
==================  =============================================
insert              O(L)
contains / weight   O(L)
delete              O(L)
suggest (top k)     O(L + m log k), m = nodes under the prefix
==================  =============================================

Space O(total characters stored).

Each node also remembers the best weight anywhere beneath it. ``suggest`` uses that to explore
the most promising branches first with a max-heap, and stops as soon as the best unexplored
branch cannot beat the k-th result already found - so popular completions come back without
reading the whole subtree.

Used by ``GET /api/v1/search/suggest``: the dashboard's command palette completes job titles,
companies, skills and locations as you type.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from app.dsa.heap import BinaryHeap


def _suggest_rank(entry: tuple[float, bool, str, Any]) -> tuple[float, bool, int, str]:
    return -entry[0], entry[1], len(entry[2]), entry[2]


class TrieNode:
    __slots__ = ("best", "children", "payload", "terminal", "weight")

    def __init__(self) -> None:
        self.children: dict[str, TrieNode] = {}
        self.terminal = False
        self.weight = 0.0  # weight of the word ending here
        self.best = 0.0  # highest weight of any word in this subtree (drives best-first search)
        self.payload: Any = None


class Trie:
    def __init__(self, words: Iterable[str] = ()) -> None:
        self.root = TrieNode()
        self._size = 0
        self.node_count = 1
        for word in words:
            self.insert(word)

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())

    # ------------------------------------------------------------------ writes
    def insert(self, word: str, weight: float = 1.0, payload: Any = None, accumulate: bool = True) -> None:
        """Add ``word``. Inserting it again adds to its weight (``accumulate``) or replaces it."""
        key = self._normalize(word)
        if not key:
            return
        path = [self.root]
        node = self.root
        for char in key:
            child = node.children.get(char)
            if child is None:
                child = node.children[char] = TrieNode()
                self.node_count += 1
            node = child
            path.append(node)
        if not node.terminal:
            node.terminal = True
            self._size += 1
            node.weight = weight
        else:
            node.weight = node.weight + weight if accumulate else weight
        if payload is not None or node.payload is None:
            node.payload = payload if payload is not None else word
        for ancestor in path:
            if node.weight > ancestor.best:
                ancestor.best = node.weight

    def delete(self, word: str) -> bool:
        """Remove ``word`` and prune nodes that no longer lead anywhere."""
        key = self._normalize(word)
        path: list[tuple[TrieNode, str]] = []
        node = self.root
        for char in key:
            child = node.children.get(char)
            if child is None:
                return False
            path.append((node, char))
            node = child
        if not node.terminal:
            return False
        node.terminal = False
        node.weight = 0.0
        node.payload = None
        self._size -= 1
        # Walk back up: drop empty leaves, then recompute ``best`` along the path.
        for parent, char in reversed(path):
            child = parent.children[char]
            if not child.terminal and not child.children:
                del parent.children[char]
                self.node_count -= 1
            else:
                child.best = max([child.weight if child.terminal else 0.0, *(c.best for c in child.children.values())])
        self.root.best = max((c.best for c in self.root.children.values()), default=0.0)
        return True

    # ------------------------------------------------------------------ reads
    def _find(self, prefix: str) -> TrieNode | None:
        node = self.root
        for char in self._normalize(prefix):
            node = node.children.get(char)  # type: ignore[assignment]
            if node is None:
                return None
        return node

    def __contains__(self, word: object) -> bool:
        if not isinstance(word, str):
            return False
        node = self._find(word)
        return node is not None and node.terminal

    def weight(self, word: str) -> float:
        node = self._find(word)
        return node.weight if node is not None and node.terminal else 0.0

    def __len__(self) -> int:
        return self._size

    def starts_with(self, prefix: str) -> bool:
        return self._find(prefix) is not None

    def suggest(self, prefix: str, k: int = 8) -> list[tuple[str, float, Any]]:
        """The ``k`` heaviest words starting with ``prefix``: ``[(word, weight, payload), ...]``.

        Best-first search: a max-heap of subtrees ordered by the best weight inside them. A word
        popped from the heap is final, because nothing still in the heap can outweigh it.
        """
        key = self._normalize(prefix)
        start = self._find(key)
        if start is None or k <= 0:
            return []
        results: list[tuple[str, float, Any]] = []
        # Entries: (priority, is_branch, text, node). At equal priority a finished word is taken
        # before another branch is opened, so the search stops as early as possible; among words
        # of equal weight the order is fixed (shorter, then alphabetical, within what was opened).
        heap: BinaryHeap[tuple[float, bool, str, TrieNode]] = BinaryHeap(key=_suggest_rank)
        heap.push((start.best, True, key, start))
        while heap and len(results) < k:
            _, is_branch, text, node = heap.pop()
            if not is_branch:
                results.append((text, node.weight, node.payload))
                continue
            if node.terminal:
                heap.push((node.weight, False, text, node))
            for char, child in node.children.items():
                heap.push((child.best, True, text + char, child))
        return results

    def words(self, prefix: str = "") -> Iterator[str]:
        """Every stored word under ``prefix`` in alphabetical order (depth-first, explicit stack)."""
        key = self._normalize(prefix)
        start = self._find(key)
        if start is None:
            return
        stack: list[tuple[str, TrieNode]] = [(key, start)]
        while stack:
            text, node = stack.pop()
            if node.terminal:
                yield text
            for char in sorted(node.children, reverse=True):
                stack.append((text + char, node.children[char]))

    def to_dict(self, max_nodes: int = 400) -> dict[str, Any]:
        """The tree as nested ``{char, terminal, weight, children}`` (breadth-first, capped), for the visualiser."""
        root: dict[str, Any] = {"char": "", "terminal": False, "weight": 0.0, "children": []}
        queue: list[tuple[TrieNode, dict[str, Any]]] = [(self.root, root)]
        count = 1
        while queue:
            node, out = queue.pop(0)
            for char in sorted(node.children):
                if count >= max_nodes:
                    out["truncated"] = True
                    break
                child = node.children[char]
                child_out = {"char": char, "terminal": child.terminal, "weight": child.weight, "children": []}
                out["children"].append(child_out)
                queue.append((child, child_out))
                count += 1
        return root

    def stats(self) -> dict[str, int]:
        return {"words": self._size, "nodes": self.node_count}
