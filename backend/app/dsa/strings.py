"""String algorithms: edit distance, LCS, KMP and the Aho-Corasick automaton.

=====================  ========================  ==========================================
Algorithm              Time                      What the app uses it for
=====================  ========================  ==========================================
Edit distance (DP)     O(a * b), O(min) space    "did you mean ...?" for mistyped searches,
                                                 near-duplicate job titles
LCS (DP)               O(a * b)                  line diff: master resume vs. tailored one
KMP search             O(n + m)                  finding a phrase without re-reading text
Aho-Corasick           O(text + matches)         finding ~250 skills in a job description
                                                 in one pass
=====================  ========================  ==========================================

Aho-Corasick is a trie of all the patterns with *failure links* (computed by BFS) that say where
to continue after a mismatch. The text is read once, character by character; without it, each of
the ~250 skill patterns would scan the whole description separately.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any, TypeVar

from app.dsa.linear import Queue

T = TypeVar("T")


# --------------------------------------------------------------------------- edit distance
def edit_distance(a: str, b: str, max_distance: int | None = None) -> int:
    """Levenshtein distance: the fewest single-character inserts, deletes or substitutions.

    Dynamic programming over a table whose cell (i, j) is the distance between ``a[:i]`` and
    ``b[:j]``. Only the previous row is needed, so space is O(min(a, b)).

    With ``max_distance`` the search stops as soon as a whole row exceeds it and returns
    ``max_distance + 1`` - enough for "is this within 2 typos?" and much faster on long strings.
    """
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a  # b is the shorter one: it sets the row width
    if max_distance is not None and len(a) - len(b) > max_distance:
        return max_distance + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,  # delete char_a
                current[j - 1] + 1,  # insert char_b
                previous[j - 1] + (char_a != char_b),  # substitute (free when equal)
            ))
        if max_distance is not None and min(current) > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    """1.0 for identical strings, 0.0 for nothing in common (edit distance, normalised by length)."""
    longest = max(len(a), len(b))
    return 1.0 if longest == 0 else 1.0 - edit_distance(a, b) / longest


def closest(word: str, candidates: Iterable[str], max_distance: int = 2) -> str | None:
    """The candidate within ``max_distance`` edits of ``word`` (closest first, then shortest)."""
    best: tuple[int, int, str] | None = None
    for candidate in candidates:
        if abs(len(candidate) - len(word)) > max_distance:
            continue
        distance = edit_distance(word, candidate, max_distance)
        if distance <= max_distance and (best is None or (distance, len(candidate), candidate) < best):
            best = (distance, len(candidate), candidate)
    return best[2] if best else None


# --------------------------------------------------------------------------- LCS and diff
def lcs_table(a: Sequence[T], b: Sequence[T]) -> list[list[int]]:
    """``table[i][j]`` = length of the longest common subsequence of ``a[i:]`` and ``b[j:]``."""
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            table[i][j] = table[i + 1][j + 1] + 1 if a[i] == b[j] else max(table[i + 1][j], table[i][j + 1])
    return table


def longest_common_subsequence(a: Sequence[T], b: Sequence[T]) -> list[T]:
    """The longest sequence of items that appears in both, in order (not necessarily adjacent)."""
    table = lcs_table(a, b)
    result: list[T] = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            result.append(a[i])
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return result


def diff(a: Sequence[T], b: Sequence[T]) -> list[tuple[str, T]]:
    """Turn ``a`` into ``b``: a list of ``("=" | "-" | "+", item)`` steps, built from the LCS table."""
    table = lcs_table(a, b)
    steps: list[tuple[str, T]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            steps.append(("=", a[i]))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            steps.append(("-", a[i]))
            i += 1
        else:
            steps.append(("+", b[j]))
            j += 1
    steps.extend(("-", item) for item in a[i:])
    steps.extend(("+", item) for item in b[j:])
    return steps


# --------------------------------------------------------------------------- KMP
def prefix_function(pattern: str) -> list[int]:
    """``pi[i]`` = length of the longest proper prefix of ``pattern[:i + 1]`` that is also its suffix."""
    pi = [0] * len(pattern)
    k = 0
    for i in range(1, len(pattern)):
        while k and pattern[i] != pattern[k]:
            k = pi[k - 1]
        if pattern[i] == pattern[k]:
            k += 1
        pi[i] = k
    return pi


def kmp_search(text: str, pattern: str) -> list[int]:
    """Start index of every occurrence of ``pattern`` in ``text`` (overlaps included). O(n + m).

    After a mismatch the prefix function says how much of the pattern still matches, so the text
    pointer never moves backwards.
    """
    if not pattern:
        return []
    pi = prefix_function(pattern)
    matches: list[int] = []
    k = 0
    for i, char in enumerate(text):
        while k and char != pattern[k]:
            k = pi[k - 1]
        if char == pattern[k]:
            k += 1
        if k == len(pattern):
            matches.append(i - k + 1)
            k = pi[k - 1]
    return matches


# --------------------------------------------------------------------------- Aho-Corasick
class AhoCorasick:
    """Finds every occurrence of many patterns in one pass over the text.

    Build: O(total pattern length). Search: O(len(text) + number of matches).

    States are numbered; ``_goto[state]`` maps a character to the next state, ``_fail[state]``
    is the longest proper suffix of the state's string that is also a prefix of some pattern, and
    ``_output[state]`` lists the patterns that end at this state (its own and those reached
    through failure links, merged at build time).
    """

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._output: list[list[int]] = [[]]
        self.patterns: list[str] = []
        self._built = False
        for pattern in patterns:
            self.add(pattern)

    def add(self, pattern: str) -> None:
        if not pattern:
            return
        state = 0
        for char in pattern:
            nxt = self._goto[state].get(char)
            if nxt is None:
                nxt = len(self._goto)
                self._goto[state][char] = nxt
                self._goto.append({})
                self._fail.append(0)
                self._output.append([])
            state = nxt
        self._output[state].append(len(self.patterns))
        self.patterns.append(pattern)
        self._built = False

    def build(self) -> None:
        """Breadth-first over the trie: a node's failure link is found from its parent's."""
        queue: Queue[int] = Queue()
        for state in self._goto[0].values():
            self._fail[state] = 0
            queue.enqueue(state)
        while queue:
            state = queue.dequeue()
            for char, nxt in self._goto[state].items():
                fallback = self._fail[state]
                while fallback and char not in self._goto[fallback]:
                    fallback = self._fail[fallback]
                self._fail[nxt] = self._goto[fallback].get(char, 0)
                self._output[nxt] = self._output[nxt] + self._output[self._fail[nxt]]
                queue.enqueue(nxt)
        self._built = True

    def finditer(self, text: str) -> Iterator[tuple[int, int, str]]:
        """Yield ``(start, end, pattern)`` for every occurrence; ``text[start:end] == pattern``."""
        if not self._built:
            self.build()
        state = 0
        for index, char in enumerate(text):
            while state and char not in self._goto[state]:
                state = self._fail[state]
            state = self._goto[state].get(char, 0)
            for pattern_index in self._output[state]:
                pattern = self.patterns[pattern_index]
                yield index - len(pattern) + 1, index + 1, pattern

    def search(self, text: str) -> list[tuple[int, int, str]]:
        return list(self.finditer(text))

    def found(self, text: str) -> set[str]:
        """The set of patterns that occur in ``text`` at least once."""
        return {pattern for _, _, pattern in self.finditer(text)}

    def stats(self) -> dict[str, Any]:
        return {"patterns": len(self.patterns), "states": len(self._goto)}
