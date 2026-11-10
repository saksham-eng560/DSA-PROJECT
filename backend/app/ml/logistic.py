"""Logistic regression trained with batch gradient descent.

The model is a weighted sum of the features pushed through the sigmoid, which turns any number
into a probability between 0 and 1:

    p = sigmoid(w . x + b),      sigmoid(z) = 1 / (1 + e^-z)

Training minimises the log loss (plus an L2 penalty that keeps weights small) by repeatedly
stepping each weight against its gradient, ``(p - y) * x`` averaged over the examples. Features
are standardised first (zero mean, unit variance) so one learning rate suits all of them.

Training is O(epochs * examples * features). The learned weights are readable: a positive weight
means the feature raises the chance, and ``explain`` reports each feature's push for one example.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp = math.exp(z)  # avoids overflow for large negative z
    return exp / (1.0 + exp)


class LogisticRegression:
    def __init__(self, learning_rate: float = 0.3, epochs: int = 300, l2: float = 0.01) -> None:
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.l2 = l2
        self.weights: list[float] = []
        self.bias = 0.0
        self.means: list[float] = []
        self.scales: list[float] = []
        self.loss_history: list[float] = []

    def _standardize(self, row: Sequence[float]) -> list[float]:
        return [(value - mean) / scale for value, mean, scale in zip(row, self.means, self.scales, strict=True)]

    def fit(self, rows: Sequence[Sequence[float]], labels: Sequence[int]) -> LogisticRegression:
        if not rows or len(rows) != len(labels):
            raise ValueError("rows and labels must be non-empty and the same length")
        n, width = len(rows), len(rows[0])
        self.means = [sum(row[j] for row in rows) / n for j in range(width)]
        self.scales = [math.sqrt(sum((row[j] - self.means[j]) ** 2 for row in rows) / n) or 1.0 for j in range(width)]
        data = [self._standardize(row) for row in rows]
        self.weights = [0.0] * width
        self.bias = 0.0
        self.loss_history = []
        for _ in range(self.epochs):
            gradient = [0.0] * width
            bias_gradient = 0.0
            loss = 0.0
            for features, label in zip(data, labels, strict=True):
                p = sigmoid(sum(w * x for w, x in zip(self.weights, features, strict=True)) + self.bias)
                error = p - label
                for j, x in enumerate(features):
                    gradient[j] += error * x
                bias_gradient += error
                loss -= math.log(max(p if label else 1.0 - p, 1e-12))
            for j in range(width):
                self.weights[j] -= self.learning_rate * (gradient[j] / n + self.l2 * self.weights[j])
            self.bias -= self.learning_rate * bias_gradient / n
            self.loss_history.append(loss / n)
        return self

    def predict_proba(self, row: Sequence[float]) -> float:
        features = self._standardize(row)
        return sigmoid(sum(w * x for w, x in zip(self.weights, features, strict=True)) + self.bias)

    def predict(self, row: Sequence[float], threshold: float = 0.5) -> int:
        return int(self.predict_proba(row) >= threshold)

    def accuracy(self, rows: Sequence[Sequence[float]], labels: Sequence[int]) -> float:
        return sum(self.predict(row) == label for row, label in zip(rows, labels, strict=True)) / len(rows)

    def explain(self, row: Sequence[float], names: Sequence[str]) -> list[dict[str, float | str]]:
        """Each feature's contribution to this prediction's log-odds, largest effect first."""
        features = self._standardize(row)
        parts = [{"feature": name, "weight": round(weight, 3), "contribution": round(weight * x, 3)}
                 for name, weight, x in zip(names, self.weights, features, strict=True)]
        return sorted(parts, key=lambda part: -abs(float(part["contribution"])))
