"""Inverted index with BM25 ranking: a small full-text search engine.

A forward lookup asks "which words are in this document?". An *inverted* index stores the
opposite - for every word, the list of documents containing it (its *postings list*) - so a query
only touches the documents that share a word with it instead of scanning every document.

==================  ==========================================================
Operation           Time
==================  ==========================================================
add document        O(tokens in the document)
remove document     O(distinct tokens in the document)
search              O(sum of the postings lists of the query's terms + k log k)
==================  ==========================================================

Space O(total distinct (term, document) pairs).

Ranking is Okapi BM25, the scoring function behind most search engines::

    score(q, d) = sum over terms t in q of
                  IDF(t) * tf(t, d) * (k1 + 1) / (tf(t, d) + k1 * (1 - b + b * |d| / avgdl))
    IDF(t)      = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

A term counts for more when it is rare across documents (IDF) and frequent in this one (tf),
with diminishing returns (k1) and a correction for long documents (b).

Fields can be weighted (a hit in the title outweighs one in the description). Query words that
match no term are corrected to the nearest indexed term by edit distance ("pyhton" -> "python"),
and the last word of a query can match as a prefix ("fast" -> "fastapi") using a trie of the
vocabulary.

Used by ``GET /api/v1/search`` (the job search box) and as the retrieval step of the chatbot.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Hashable, Iterable, Mapping
from typing import Any, Generic, TypeVar

from app.dsa.heap import top_k
from app.dsa.strings import closest
from app.dsa.trie import Trie

D = TypeVar("D", bound=Hashable)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.\-/]*[a-z0-9+#]|[a-z0-9]")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or our that the their this to we will with you your".split()
)


PREFIX_WEIGHT = 0.35  # a completion of a word that is itself a term ("python" -> "python3")
COMPLETION_WEIGHT = 0.8  # a completion of a word that is not a term yet ("fasta" -> "fastapi")
FUZZY_WEIGHT = 0.7  # a spelling correction ("pyhton" -> "python")


def default_tokenizer(text: str) -> list[str]:
    return [token for token in _TOKEN.findall(text.lower()) if token not in _STOPWORDS]


class InvertedIndex(Generic[D]):
    def __init__(self, tokenizer: Callable[[str], list[str]] = default_tokenizer, k1: float = 1.5, b: float = 0.75) -> None:
        self.tokenizer = tokenizer
        self.k1 = k1
        self.b = b
        self._postings: dict[str, dict[D, float]] = {}  # term -> {document: weighted term frequency}
        self._lengths: dict[D, float] = {}  # document -> weighted length
        self._terms: dict[D, set[str]] = {}  # document -> its terms (for removal)
        self._total_length = 0.0
        self._vocabulary = Trie()  # for prefix matching

    # ------------------------------------------------------------------ writes
    def add(self, doc_id: D, fields: Mapping[str, str] | str, weights: Mapping[str, float] | None = None) -> None:
        """Index a document. ``fields`` is its text, or ``{field name: text}`` with optional ``weights``."""
        if doc_id in self._lengths:
            self.remove(doc_id)
        if isinstance(fields, str):
            fields = {"text": fields}
        frequencies: dict[str, float] = {}
        length = 0.0
        for name, text in fields.items():
            weight = (weights or {}).get(name, 1.0)
            for token in self.tokenizer(text or ""):
                frequencies[token] = frequencies.get(token, 0.0) + weight
                length += weight
        for term, frequency in frequencies.items():
            postings = self._postings.get(term)
            if postings is None:
                postings = self._postings[term] = {}
                self._vocabulary.insert(term)
            postings[doc_id] = frequency
        self._lengths[doc_id] = length
        self._terms[doc_id] = set(frequencies)
        self._total_length += length

    def remove(self, doc_id: D) -> bool:
        if doc_id not in self._lengths:
            return False
        for term in self._terms.pop(doc_id):
            postings = self._postings[term]
            del postings[doc_id]
            if not postings:
                del self._postings[term]
                self._vocabulary.delete(term)
        self._total_length -= self._lengths.pop(doc_id)
        return True

    # ------------------------------------------------------------------ reads
    def __len__(self) -> int:
        return len(self._lengths)

    def __contains__(self, doc_id: object) -> bool:
        return doc_id in self._lengths

    @property
    def vocabulary_size(self) -> int:
        return len(self._postings)

    def document_frequency(self, term: str) -> int:
        return len(self._postings.get(term, ()))

    def idf(self, term: str) -> float:
        n, df = len(self._lengths), self.document_frequency(term)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def expand(self, query: str, fuzzy: bool = True, prefix: bool = True) -> tuple[dict[str, float], dict[str, str]]:
        """Query words -> indexed terms. Returns ``({term: weight}, corrections)``.

        An exact term weighs 1. The last word of the query is also tried as a prefix (the user may
        still be typing), and a word that matches nothing is corrected to the nearest term within
        two edits. Completions and corrections weigh less than an exact hit, so "python" still
        ranks documents about Python above ones that only mention "python-adjacent".
        """
        tokens = self.tokenizer(query)
        terms: dict[str, float] = {}
        corrections: dict[str, str] = {}

        def use(term: str, weight: float) -> None:
            terms[term] = max(terms.get(term, 0.0), weight)

        for position, token in enumerate(tokens):
            exact = token in self._postings
            if exact:
                use(token, 1.0)
            if prefix and position == len(tokens) - 1 and len(token) >= 2:
                completions = [word for word, _, _ in self._vocabulary.suggest(token, 6) if word != token]
                for word in completions:
                    use(word, PREFIX_WEIGHT if exact else COMPLETION_WEIGHT)
                if completions:
                    continue
            if not exact and fuzzy and len(token) >= 4:
                match = closest(token, self._candidates(token), max_distance=1 if len(token) < 6 else 2)
                if match:
                    use(match, FUZZY_WEIGHT)
                    corrections[token] = match
        return terms, corrections

    def _candidates(self, token: str) -> Iterable[str]:
        """Terms worth an edit-distance check: similar length and the same first letter or last letter."""
        return (term for term in self._postings
                if abs(len(term) - len(token)) <= 2 and (term[0] == token[0] or term[-1] == token[-1]))

    def scores(self, terms: Iterable[str] | Mapping[str, float]) -> dict[D, float]:
        """BM25 score of every document that contains at least one of ``terms`` (optionally weighted)."""
        n = len(self._lengths)
        if n == 0:
            return {}
        weighted = terms if isinstance(terms, Mapping) else dict.fromkeys(terms, 1.0)
        average = self._total_length / n or 1.0
        result: dict[D, float] = {}
        for term, weight in weighted.items():
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self.idf(term) * weight
            for doc_id, frequency in postings.items():
                norm = self.k1 * (1 - self.b + self.b * self._lengths[doc_id] / average)
                result[doc_id] = result.get(doc_id, 0.0) + idf * frequency * (self.k1 + 1) / (frequency + norm)
        return result

    def search(self, query: str, k: int = 10, fuzzy: bool = True, prefix: bool = True,
               allowed: Callable[[D], bool] | None = None) -> list[tuple[D, float]]:
        """The ``k`` best documents for ``query``: ``[(doc_id, score), ...]``, best first."""
        return self.search_explained(query, k, fuzzy, prefix, allowed)["hits"]

    def search_explained(self, query: str, k: int = 10, fuzzy: bool = True, prefix: bool = True,
                         allowed: Callable[[D], bool] | None = None) -> dict[str, Any]:
        """Like :meth:`search`, plus the terms used and the spelling corrections made."""
        terms, corrections = self.expand(query, fuzzy, prefix)
        scored = self.scores(terms)
        candidates = ((doc_id, score) for doc_id, score in scored.items() if allowed is None or allowed(doc_id))
        hits = top_k(candidates, k, key=lambda hit: hit[1])
        return {"hits": [(doc_id, round(score, 4)) for doc_id, score in hits], "terms": list(terms),
                "corrections": corrections, "matched": len(scored)}

    def stats(self) -> dict[str, Any]:
        postings = sum(len(p) for p in self._postings.values())
        return {
            "documents": len(self._lengths),
            "terms": len(self._postings),
            "postings": postings,
            "average_length": round(self._total_length / len(self._lengths), 1) if self._lengths else 0.0,
        }
