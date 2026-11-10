"""The catalogue of data structures and algorithms in this project.

One entry per structure: what it is, its complexity, where the code lives and which feature
uses it. The DSA Lab page renders it, the chatbot searches it to answer "how does the trie work
here?", and a test checks that every file it names exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from app.dsa.inverted_index import InvertedIndex


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    kind: str  # "Data structure" | "Algorithm" | "Machine learning"
    category: str
    idea: str  # one or two plain sentences
    complexity: dict[str, str]
    space: str
    module: str  # implementation, relative to backend/app
    used_in: str  # the service that uses it, relative to backend/app
    feature: str  # what the user sees
    demo: str = ""  # id of the interactive demo in the DSA Lab, if any


CATALOG: tuple[Entry, ...] = (
    Entry("stack", "Stack", "Data structure", "Linear",
          "Last in, first out. Every swipe is pushed; undo pops the most recent one.",
          {"push": "O(1)", "pop": "O(1)", "peek": "O(1)"}, "O(n)",
          "dsa/linear.py", "api/review.py", "Undo the last swipe (Z) in Swipe Review", "stack"),
    Entry("queue", "Queue (linked list)", "Data structure", "Linear",
          "First in, first out, with head and tail pointers so both ends are O(1). The frontier of breadth-first search.",
          {"enqueue": "O(1)", "dequeue": "O(1)"}, "O(n)",
          "dsa/linear.py", "dsa/graph.py", "BFS over the skill graph and the status state machine", "queue"),
    Entry("ring_buffer", "Ring buffer", "Data structure", "Linear",
          "A fixed-size circular array that overwrites its oldest item. Memory never grows.",
          {"append": "O(1)", "iterate": "O(capacity)"}, "O(capacity)",
          "dsa/linear.py", "services/copilot.py", "The chatbot's short-term memory: only the last messages go to the model"),
    Entry("hash_map", "Hash table", "Data structure", "Hashing",
          "Keys are hashed into buckets; a full table doubles and re-hashes. The idea behind Python's dict, "
          "which the app uses on every hot path.",
          {"get": "O(1) average", "set": "O(1) amortised", "delete": "O(1) average"}, "O(n)",
          "dsa/hashmap.py", "services/dsa_lab.py", "Benchmarked against dict in the DSA Lab; dict indexes jobs by id everywhere",
          "hash_map"),
    Entry("heap", "Binary heap / priority queue", "Data structure", "Trees",
          "A complete binary tree in an array where every parent is smaller than its children, so the minimum is "
          "always at the root.",
          {"push": "O(log n)", "pop": "O(log n)", "peek": "O(1)", "build": "O(n)"}, "O(n)",
          "dsa/heap.py", "services/insights.py", "Best-K matches, autocomplete ranking, Dijkstra, interview rooms", "heap"),
    Entry("top_k", "Top-K selection", "Algorithm", "Trees",
          "Keep a min-heap of the best K seen so far; a new item only costs work if it beats the smallest of them.",
          {"select": "O(n log k)"}, "O(k)",
          "dsa/heap.py", "services/insights.py", "\"Best matches\" without sorting the whole deck", "heap"),
    Entry("trie", "Trie (prefix tree)", "Data structure", "Trees",
          "One node per character; words sharing a prefix share a path. Completions come from the subtree under "
          "the prefix, best-first.",
          {"insert": "O(L)", "lookup": "O(L)", "suggest": "O(L + m log k)"}, "O(total characters)",
          "dsa/trie.py", "services/search_index.py", "Search-as-you-type suggestions in the command palette (Ctrl+K)", "trie"),
    Entry("lru", "LRU cache", "Data structure", "Hashing",
          "A hash map for O(1) lookup plus a doubly linked list in order of use; the entry at the tail is evicted "
          "when the cache is full.",
          {"get": "O(1)", "put": "O(1)", "evict": "O(1)"}, "O(capacity)",
          "dsa/lru_cache.py", "services/search_index.py", "Each user's search index is built once and reused", "lru"),
    Entry("bloom", "Bloom filter", "Data structure", "Hashing",
          "A bit array and k hash functions. \"No\" is always right; \"yes\" may be wrong ~1% of the time. "
          "About 1.2 bytes per item.",
          {"add": "O(k)", "contains": "O(k)"}, "O(m) bits",
          "dsa/bloom_filter.py", "services/agent/jobs.py", "Scans skip the duplicate-check query for postings that are certainly new",
          "bloom"),
    Entry("union_find", "Union-Find", "Data structure", "Graphs",
          "Disjoint sets as trees, with union by rank and path compression. Near-constant time per operation.",
          {"find": "O(alpha(n))", "union": "O(alpha(n))"}, "O(n)",
          "dsa/union_find.py", "services/duplicates.py", "The same internship posted on several sites is grouped into one",
          "union_find"),
    Entry("graph", "Graph (adjacency list)", "Data structure", "Graphs",
          "Each node maps to its neighbours and edge weights. Three graphs are built on it: skills, statuses and "
          "the pipeline.",
          {"add edge": "O(1)", "neighbours": "O(degree)"}, "O(V + E)",
          "dsa/graph.py", "services/skill_graph.py", "Skill graph, application state machine, system graph", "graph"),
    Entry("bfs", "Breadth-first search", "Algorithm", "Graphs",
          "Visit nodes level by level using a queue: finds the path with the fewest edges.",
          {"traverse": "O(V + E)"}, "O(V)",
          "dsa/graph.py", "services/state_machine.py", "Fewest steps to an offer; the neighbourhood of your skills in the skill graph",
          "graph"),
    Entry("dfs", "Depth-first search", "Algorithm", "Graphs",
          "Follow one branch to the end before backtracking, with an explicit stack. Answers reachability.",
          {"traverse": "O(V + E)"}, "O(V)",
          "dsa/graph.py", "services/state_machine.py", "Can this application still reach an offer?", "graph"),
    Entry("dijkstra", "Dijkstra's shortest path", "Algorithm", "Graphs",
          "Always settle the closest unsettled node (a min-heap picks it). Started from all your skills at once.",
          {"shortest paths": "O((V + E) log V)"}, "O(V)",
          "dsa/graph.py", "services/skill_graph.py", "The cheapest learning path to a skill you are missing", "graph"),
    Entry("topo", "Topological sort (Kahn)", "Algorithm", "Graphs",
          "Repeatedly remove a node with no remaining prerequisites. Fails if there is a cycle.",
          {"sort": "O(V + E)"}, "O(V)",
          "dsa/graph.py", "services/pipeline_graph.py", "Execution order, parallel layers and critical path of the agent pipeline",
          "pipeline"),
    Entry("aho", "Aho-Corasick automaton", "Algorithm", "Strings",
          "A trie of all patterns with failure links: finds ~250 skills in a job description in a single pass.",
          {"build": "O(sum of pattern lengths)", "search": "O(text + matches)"}, "O(sum of pattern lengths)",
          "dsa/strings.py", "services/text_utils.py", "Skill extraction for every posting and resume", "aho"),
    Entry("edit_distance", "Edit distance (Levenshtein)", "Algorithm", "Dynamic programming",
          "The fewest inserts, deletes and substitutions to turn one string into another, filled row by row.",
          {"distance": "O(a * b)"}, "O(min(a, b))",
          "dsa/strings.py", "dsa/inverted_index.py", "\"Did you mean python?\" for mistyped searches; near-duplicate titles",
          "edit_distance"),
    Entry("lcs", "Longest common subsequence", "Algorithm", "Dynamic programming",
          "The longest sequence that appears in both inputs in order; reading the table back gives a diff.",
          {"table": "O(a * b)"}, "O(a * b)",
          "dsa/strings.py", "services/resume_optimizer.py", "Line-by-line diff between your resume and the tailored one"),
    Entry("kmp", "KMP string search", "Algorithm", "Strings",
          "A prefix table tells the search how far to shift after a mismatch, so the text is never re-read.",
          {"search": "O(n + m)"}, "O(m)",
          "dsa/strings.py", "services/dsa_lab.py", "Reference matcher that the Aho-Corasick tests are checked against"),
    Entry("inverted_index", "Inverted index + BM25", "Data structure", "Search",
          "For every word, the list of documents containing it. BM25 ranks hits by term rarity and frequency.",
          {"index a doc": "O(tokens)", "search": "O(postings of the query terms)"}, "O(postings)",
          "dsa/inverted_index.py", "services/search_index.py", "Full-text job search and the chatbot's retrieval step", "search"),
    Entry("fenwick", "Fenwick tree", "Data structure", "Trees",
          "Prefix sums that stay O(log n) while values change, using the lowest set bit to pick block sizes.",
          {"update": "O(log n)", "range sum": "O(log n)"}, "O(n)",
          "dsa/fenwick.py", "services/insights.py", "Rolling 7- and 30-day activity on the Insights page", "fenwick"),
    Entry("intervals", "Interval sweep", "Algorithm", "Greedy",
          "Sort by start time and sweep once; a min-heap of end times counts simultaneous interviews.",
          {"conflicts": "O(n log n)", "free slots": "O(n log n)"}, "O(n)",
          "dsa/intervals.py", "services/insights.py", "Interview clashes and free slots on the Interviews page"),
    Entry("knapsack", "0/1 knapsack", "Algorithm", "Dynamic programming",
          "Choose items to maximise value within a capacity; each cell reuses the best answer for a smaller problem.",
          {"solve": "O(n * capacity)"}, "O(n * capacity)",
          "dsa/dp.py", "services/resume_optimizer.py", "Pick the most relevant bullets that fit on one page", "knapsack"),
    Entry("merge_sort", "Merge sort", "Algorithm", "Sorting",
          "Split, sort halves, merge. Stable: equal items keep their order, which lets ranking by several keys work.",
          {"sort": "O(n log n)"}, "O(n)",
          "dsa/sorting.py", "services/dsa_lab.py", "Reference stable sort, timed against Python's Timsort", "sorting"),
    Entry("binary_search", "Binary search", "Algorithm", "Searching",
          "Halve a sorted range until the target is found: lower and upper bounds count items in a range.",
          {"search": "O(log n)"}, "O(1)",
          "dsa/sorting.py", "services/insights.py", "\"This job beats 87% of your deck\" (percentile rank)", "sorting"),
    Entry("quickselect", "Quickselect", "Algorithm", "Sorting",
          "Partition around a pivot and keep only the side holding position k: the median without a full sort.",
          {"select": "O(n) average"}, "O(n)",
          "dsa/sorting.py", "services/insights.py", "Median match score on the Insights page"),
    Entry("sliding_window", "Sliding-window rate limiter", "Algorithm", "Queues",
          "A deque of recent request times per user; old ones leave from the left, new ones join on the right.",
          {"allow": "O(1) amortised"}, "O(limit) per user",
          "dsa/rate_limit.py", "api/copilot.py", "At most 20 chatbot messages a minute per user"),
    Entry("tfidf", "TF-IDF + cosine similarity", "Machine learning", "Vectors",
          "Documents become sparse weighted word vectors; the cosine of the angle between two is their similarity.",
          {"fit": "O(tokens)", "cosine": "O(min(|a|, |b|))"}, "O(distinct terms)",
          "ml/tfidf.py", "services/insights.py", "\"Jobs like this one\" and the input to clustering"),
    Entry("naive_bayes", "Naive Bayes classifier", "Machine learning", "Classification",
          "Counts how often each word appears per class, then applies Bayes' rule with Laplace smoothing.",
          {"train": "O(tokens)", "predict": "O(tokens * classes)"}, "O(vocabulary * classes)",
          "ml/naive_bayes.py", "services/email_intent.py", "Classifies recruiter e-mails when no LLM is configured"),
    Entry("logistic", "Logistic regression", "Machine learning", "Classification",
          "A weighted sum through a sigmoid, trained by gradient descent on your own application history.",
          {"train": "O(epochs * n * features)", "predict": "O(features)"}, "O(features)",
          "ml/logistic.py", "services/insights.py", "Chance of a reply for each waiting job, with the reasons"),
    Entry("kmeans", "k-means clustering", "Machine learning", "Clustering",
          "Assign each job to its nearest centroid, move centroids to the mean, repeat until stable.",
          {"fit": "O(iterations * n * k * terms)"}, "O(k * terms)",
          "ml/kmeans.py", "services/insights.py", "Groups your jobs into themes (AI/ML, backend, data, ...)"),
)


def catalog() -> list[dict[str, Any]]:
    return [asdict(entry) for entry in CATALOG]


@lru_cache(maxsize=1)
def _index() -> InvertedIndex[str]:
    index: InvertedIndex[str] = InvertedIndex()
    for entry in CATALOG:
        index.add(entry.id, {"name": entry.name, "idea": entry.idea, "feature": entry.feature,
                             "category": f"{entry.kind} {entry.category}"},
                  weights={"name": 4.0, "feature": 1.5})
    return index


def find(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Catalogue entries that best match a question (BM25 over the catalogue itself)."""
    by_id = {entry.id: entry for entry in CATALOG}
    return [asdict(by_id[doc_id]) for doc_id, _ in _index().search(query, k=limit)]
