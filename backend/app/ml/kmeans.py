"""k-means clustering on sparse TF-IDF vectors, with k-means++ seeding.

Groups documents so that each is closest to the centre (centroid) of its own group:

1. choose ``k`` starting centroids, spread out (k-means++: each next one is picked with
   probability proportional to its squared distance from the ones already chosen);
2. assign every document to its nearest centroid (highest cosine similarity);
3. move each centroid to the mean of its documents;
4. repeat 2-3 until no document changes group.

Each pass is O(documents * k * non-zero terms). Results depend on the starting centroids, so the
random seed is fixed: the same jobs always produce the same themes.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from app.ml.tfidf import Vector, cosine, normalize


class KMeans:
    def __init__(self, k: int = 4, max_iterations: int = 25, seed: int = 7) -> None:
        if k < 1:
            raise ValueError("k must be at least 1")
        self.k = k
        self.max_iterations = max_iterations
        self.seed = seed
        self.centroids: list[Vector] = []
        self.labels: list[int] = []
        self.iterations = 0

    def _seed_centroids(self, vectors: Sequence[Vector], rng: random.Random) -> list[Vector]:
        centroids = [dict(vectors[rng.randrange(len(vectors))])]
        while len(centroids) < self.k:
            # distance = 1 - cosine similarity to the nearest centroid chosen so far
            distances = [max(0.0, 1.0 - max(cosine(vector, c) for c in centroids)) ** 2 for vector in vectors]
            total = sum(distances)
            if total == 0:  # every remaining point coincides with a centroid
                break
            pick, running = rng.random() * total, 0.0
            for vector, distance in zip(vectors, distances, strict=True):
                running += distance
                if running >= pick:
                    centroids.append(dict(vector))
                    break
        return centroids

    def fit(self, vectors: Sequence[Vector]) -> KMeans:
        usable = [v for v in vectors if v]
        if not usable:
            self.centroids, self.labels, self.iterations = [], [-1] * len(vectors), 0
            return self
        self.k = min(self.k, len(usable))
        rng = random.Random(self.seed)
        self.centroids = self._seed_centroids(usable, rng)
        self.labels = [-1] * len(vectors)
        for iteration in range(1, self.max_iterations + 1):
            self.iterations = iteration
            changed = False
            for index, vector in enumerate(vectors):
                label = self.predict(vector) if vector else -1
                if label != self.labels[index]:
                    self.labels[index] = label
                    changed = True
            if not changed:
                break
            for cluster in range(len(self.centroids)):
                total: Vector = {}
                for vector, label in zip(vectors, self.labels, strict=True):
                    if label == cluster:
                        for term, weight in vector.items():
                            total[term] = total.get(term, 0.0) + weight
                if total:
                    self.centroids[cluster] = normalize(total)
        return self

    def predict(self, vector: Vector) -> int:
        best, best_similarity = 0, -1.0
        for cluster, centroid in enumerate(self.centroids):
            similarity = cosine(vector, centroid)
            if similarity > best_similarity:
                best, best_similarity = cluster, similarity
        return best

    def top_terms(self, cluster: int, k: int = 5) -> list[str]:
        centroid = self.centroids[cluster]
        return [term for term, _ in sorted(centroid.items(), key=lambda item: (-item[1], item[0]))[:k]]

    def cohesion(self, vectors: Sequence[Vector]) -> float:
        """Average similarity of each document to its own centroid (1.0 = perfectly tight clusters)."""
        scored = [cosine(vector, self.centroids[label]) for vector, label in zip(vectors, self.labels, strict=True)
                  if label >= 0]
        return sum(scored) / len(scored) if scored else 0.0
