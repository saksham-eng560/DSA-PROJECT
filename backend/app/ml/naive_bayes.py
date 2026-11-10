"""Multinomial Naive Bayes text classifier.

Bayes' rule with the "naive" assumption that words are independent given the class:

    P(class | words)  is proportional to  P(class) * product over words of P(word | class)

Training just counts: how many documents per class (the prior) and how often each word appears
in each class (the likelihood). Laplace smoothing adds 1 to every count so a word never seen in
a class doesn't zero out the whole product. Scores are summed in log space to avoid underflow.

Training O(total tokens); predicting O(tokens in the message * classes). It works well with very
little data, which is why it suits the handful of e-mail intents here.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

from app.dsa.inverted_index import default_tokenizer


class NaiveBayes:
    def __init__(self, tokenizer: Callable[[str], list[str]] = default_tokenizer, alpha: float = 1.0,
                 bigrams: bool = True) -> None:
        self.tokenizer = tokenizer
        self.alpha = alpha
        self.bigrams = bigrams
        self.class_documents: Counter[str] = Counter()
        self.word_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.class_totals: Counter[str] = Counter()
        self.vocabulary: set[str] = set()

    def features(self, text: str) -> list[str]:
        tokens = self.tokenizer(text)
        if self.bigrams:  # "not selected" and "selected" should not look alike
            tokens = tokens + [f"{a} {b}" for a, b in itertools.pairwise(tokens)]
        return tokens

    def fit(self, examples: Iterable[tuple[str, str]]) -> NaiveBayes:
        """``examples`` is an iterable of ``(text, label)``."""
        for text, label in examples:
            self.class_documents[label] += 1
            for feature in self.features(text):
                self.word_counts[label][feature] += 1
                self.class_totals[label] += 1
                self.vocabulary.add(feature)
        return self

    @property
    def classes(self) -> list[str]:
        return sorted(self.class_documents)

    def log_scores(self, text: str) -> dict[str, float]:
        total_documents = sum(self.class_documents.values())
        size = len(self.vocabulary) or 1
        features = [f for f in self.features(text) if f in self.vocabulary]
        scores: dict[str, float] = {}
        for label in self.classes:
            score = math.log(self.class_documents[label] / total_documents)
            denominator = self.class_totals[label] + self.alpha * size
            counts = self.word_counts[label]
            for feature in features:
                score += math.log((counts[feature] + self.alpha) / denominator)
            scores[label] = score
        return scores

    def predict_proba(self, text: str) -> dict[str, float]:
        """Class probabilities (softmax of the log scores, computed stably)."""
        scores = self.log_scores(text)
        if not scores:
            return {}
        top = max(scores.values())
        exp = {label: math.exp(score - top) for label, score in scores.items()}
        total = sum(exp.values())
        return {label: value / total for label, value in exp.items()}

    def predict(self, text: str) -> tuple[str, float]:
        """``(most likely label, its probability)``."""
        probabilities = self.predict_proba(text)
        if not probabilities:
            raise ValueError("the classifier has not been trained")
        label = max(probabilities, key=lambda name: probabilities[name])
        return label, probabilities[label]

    def informative_features(self, label: str, k: int = 8) -> list[str]:
        """The words that most separate ``label`` from the other classes (highest log-odds)."""
        size = len(self.vocabulary) or 1
        others = [name for name in self.classes if name != label]
        other_total = sum(self.class_totals[name] for name in others)

        def log_odds(feature: str) -> float:
            inside = (self.word_counts[label][feature] + self.alpha) / (self.class_totals[label] + self.alpha * size)
            outside = (sum(self.word_counts[name][feature] for name in others) + self.alpha) / (other_total + self.alpha * size)
            return math.log(inside / outside)

        return sorted(self.word_counts[label], key=lambda feature: (-log_odds(feature), feature))[:k]
