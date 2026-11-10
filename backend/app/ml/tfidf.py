"""TF-IDF vectors and cosine similarity.

A document becomes a sparse vector ``{term: weight}`` where

    weight(t, d) = (1 + ln tf(t, d)) * (ln((1 + N) / (1 + df(t))) + 1)

so a word counts for more when it is frequent in this document (tf) and rare across all
documents (idf). Vectors are L2-normalised, which makes the dot product of two vectors their
cosine similarity: 1.0 = same direction (same mix of words), 0.0 = no words in common.

Sparse dictionaries keep the cost proportional to the words a document actually has, not to the
size of the vocabulary. fit is O(total tokens); cosine is O(min(|a|, |b|)).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable

from app.dsa.inverted_index import default_tokenizer

Vector = dict[str, float]


def cosine(a: Vector, b: Vector) -> float:
    """Dot product of two L2-normalised sparse vectors (iterate over the smaller one)."""
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(term, 0.0) for term, weight in a.items())


def normalize(vector: Vector) -> Vector:
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    return {term: weight / norm for term, weight in vector.items()} if norm else {}


class TfidfVectorizer:
    def __init__(self, tokenizer: Callable[[str], list[str]] = default_tokenizer, min_df: int = 1,
                 max_features: int | None = None) -> None:
        self.tokenizer = tokenizer
        self.min_df = min_df
        self.max_features = max_features
        self.idf: dict[str, float] = {}
        self.documents = 0

    def fit(self, texts: Iterable[str]) -> TfidfVectorizer:
        document_frequency: Counter[str] = Counter()
        self.documents = 0
        for text in texts:
            self.documents += 1
            document_frequency.update(set(self.tokenizer(text)))
        terms = [(term, df) for term, df in document_frequency.items() if df >= self.min_df]
        if self.max_features is not None and len(terms) > self.max_features:
            terms = sorted(terms, key=lambda item: (-item[1], item[0]))[: self.max_features]
        self.idf = {term: math.log((1 + self.documents) / (1 + df)) + 1.0 for term, df in terms}
        return self

    def transform(self, text: str) -> Vector:
        counts = Counter(token for token in self.tokenizer(text) if token in self.idf)
        return normalize({term: (1.0 + math.log(count)) * self.idf[term] for term, count in counts.items()})

    def fit_transform(self, texts: list[str]) -> list[Vector]:
        self.fit(texts)
        return [self.transform(text) for text in texts]

    def top_terms(self, vector: Vector, k: int = 5) -> list[str]:
        return [term for term, _ in sorted(vector.items(), key=lambda item: (-item[1], item[0]))[:k]]
