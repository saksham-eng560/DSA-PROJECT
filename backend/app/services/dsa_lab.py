"""Backs the DSA Lab page: interactive demos and live benchmarks of the project's structures.

Each demo runs the *real* implementation from ``app.dsa`` on a small input and returns the
intermediate states (the heap's array after every push, the edit-distance table, the bits a Bloom
filter sets...), so the page animates what the code actually did rather than a re-implementation.

``benchmarks`` times each structure against the naive alternative on the same input, which turns
the complexity table into measured numbers: a trie against scanning every word, a size-K heap
against a full sort, one Aho-Corasick pass against 250 regular expressions.
"""

from __future__ import annotations

import random
import re
import sys
import time
from collections.abc import Callable
from typing import Any

from app.dsa.bloom_filter import BloomFilter
from app.dsa.dp import greedy_knapsack, knapsack
from app.dsa.fenwick import FenwickTree
from app.dsa.hashmap import HashMap
from app.dsa.heap import BinaryHeap, top_k
from app.dsa.inverted_index import InvertedIndex
from app.dsa.linear import Queue, Stack
from app.dsa.lru_cache import LRUCache
from app.dsa.sorting import lower_bound, merge_sort
from app.dsa.strings import AhoCorasick, edit_distance, kmp_search, lcs_table, longest_common_subsequence
from app.dsa.trie import Trie
from app.dsa.union_find import UnionFind
from app.services.skill_graph import skill_graph
from app.services.text_utils import SKILL_VOCABULARY, display_skill, extract_skills, extract_skills_regex

MAX_ITEMS = 40
MAX_TEXT = 60

SAMPLE_TITLES = ["python backend intern", "python developer intern", "pytorch research intern", "product design intern",
                 "data analyst intern", "data engineer intern", "devops intern", "deep learning intern",
                 "frontend react intern", "full stack developer", "machine learning intern", "mobile android intern"]
SAMPLE_JD = ("We are hiring a Python backend intern to build REST APIs with FastAPI and PostgreSQL. You will "
             "containerise services with Docker, deploy them on AWS and write tests with pytest. Experience with "
             "machine learning, LLMs or RAG pipelines is a plus. Good knowledge of data structures and Git expected.")


class DemoError(ValueError):
    """The demo's input was not usable (reported to the client as a 422)."""


def _words(value: Any, default: list[str], limit: int = MAX_ITEMS) -> list[str]:
    if value is None:
        return default
    if isinstance(value, str):
        value = re.split(r"[,\n]+", value)
    if not isinstance(value, list):
        raise DemoError("expected a list of words")
    words = [str(item).strip()[:MAX_TEXT] for item in value if str(item).strip()]
    return words[:limit] or default


def _numbers(value: Any, default: list[int], limit: int = MAX_ITEMS, low: int = -9999, high: int = 9999) -> list[int]:
    if value is None:
        return default
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value.strip())
    if not isinstance(value, list):
        raise DemoError("expected a list of numbers")
    numbers: list[int] = []
    for item in value[:limit]:
        if item in ("", None):
            continue
        try:
            numbers.append(max(low, min(high, int(float(item)))))
        except (TypeError, ValueError) as exc:
            raise DemoError(f"not a number: {item!r}") from exc
    return numbers or default


def _text(value: Any, default: str, limit: int = MAX_TEXT) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and str(value).strip() else default


# --------------------------------------------------------------------------- demos
def demo_stack(params: dict[str, Any]) -> dict[str, Any]:
    stack: Stack[str] = Stack()
    steps = []
    for word in _words(params.get("swipes"), ["keep Zomato", "skip Acme", "keep Razorpay", "undo", "keep Swiggy", "undo", "undo"]):
        if word.lower() == "undo":
            popped = stack.pop() if stack else None
            steps.append({"op": "pop", "value": popped, "stack": list(stack)[::-1]})
        else:
            stack.push(word)
            steps.append({"op": "push", "value": word, "stack": list(stack)[::-1]})
    return {"steps": steps, "final": list(stack)[::-1]}


def demo_queue(params: dict[str, Any]) -> dict[str, Any]:
    graph = skill_graph()
    start = _text(params.get("start"), "python").lower()
    if start not in graph:
        raise DemoError(f"unknown skill: {start}")
    seen = {start}
    queue: Queue[str] = Queue([start])
    steps = []
    while queue and len(steps) < 14:
        node = queue.dequeue()
        added = []
        for neighbor in graph.neighbors(node):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.enqueue(neighbor)
                added.append(neighbor)
        steps.append({"visit": node, "enqueued": added, "queue": list(queue)[:14]})
    return {"start": start, "steps": steps}


def demo_hash_map(params: dict[str, Any]) -> dict[str, Any]:
    table: HashMap[str, int] = HashMap(capacity=4)
    steps = []
    for word in _words(params.get("keys"), ["google", "microsoft", "zomato", "razorpay", "swiggy", "flipkart", "cred", "meesho"]):
        before = table.capacity
        table.set(word, len(word))
        steps.append({"key": word, "bucket": hash(word) % table.capacity, "resized": table.capacity != before,
                      "buckets": table.buckets(), "load_factor": round(table.load_factor, 2)})
    return {"steps": steps, "stats": table.stats()}


def demo_heap(params: dict[str, Any]) -> dict[str, Any]:
    scores = _numbers(params.get("scores"), [62, 91, 45, 78, 88, 53, 97, 70, 84, 39], low=0, high=100)
    k = max(1, min(int(params.get("k") or 3), len(scores)))
    heap: BinaryHeap[int] = BinaryHeap()
    steps = []
    for score in scores:
        if len(heap) < k:
            heap.push(score)
            action = "push"
        elif score > heap.peek():
            evicted = heap.pushpop(score)
            action = f"replace {evicted}"
        else:
            action = "skip"
        steps.append({"value": score, "action": action, "heap": heap.items(), "min": heap.peek()})
    return {"k": k, "steps": steps, "result": top_k(scores, k), "sorted": sorted(scores, reverse=True),
            "note": f"{sum(1 for s in steps if s['action'] == 'skip')} of {len(scores)} values never entered the heap"}


def demo_trie(params: dict[str, Any]) -> dict[str, Any]:
    words = _words(params.get("words"), SAMPLE_TITLES)
    prefix = _text(params.get("prefix"), "py").lower()
    trie = Trie()
    for position, word in enumerate(words):
        trie.insert(word, weight=float(len(words) - position))
    path, node = [], trie.root
    for char in prefix:
        node = node.children.get(char)  # type: ignore[assignment]
        if node is None:
            break
        path.append(char)
    return {"prefix": prefix, "path": path, "found": len(path) == len(prefix),
            "suggestions": [{"word": word, "weight": weight} for word, weight, _ in trie.suggest(prefix, 6)],
            "tree": trie.to_dict(max_nodes=220), "stats": trie.stats(),
            "characters_stored_flat": sum(len(" ".join(w.lower().split())) for w in words)}


def demo_lru(params: dict[str, Any]) -> dict[str, Any]:
    capacity = max(1, min(int(params.get("capacity") or 3), 8))
    cache: LRUCache[str, str] = LRUCache(capacity=capacity)
    steps = []
    for key in _words(params.get("requests"), ["ria", "aman", "ria", "sana", "dev", "ria", "aman", "sana"]):
        hit = cache.get(key) is not None
        if not hit:
            cache.put(key, key)
        steps.append({"key": key, "hit": hit, "order": list(cache.keys()), "evictions": cache.evictions})
    return {"capacity": capacity, "steps": steps, "stats": cache.stats()}


def demo_bloom(params: dict[str, Any]) -> dict[str, Any]:
    items = _words(params.get("items"), ["zomato|sde intern|gurugram", "razorpay|backend intern|bengaluru",
                                         "cred|ml intern|bengaluru", "swiggy|data intern|remote"])
    probes = _words(params.get("probes"), ["zomato|sde intern|gurugram", "meesho|sde intern|bengaluru",
                                           "google|step intern|hyderabad"])
    bloom = BloomFilter(expected_items=max(4, len(items)), false_positive_rate=0.05)
    added = []
    for item in items:
        bloom.add(item)
        added.append({"item": item, "positions": bloom.positions(item)})
    return {"added": added, "bits": bloom.bits(),
            "probes": [{"item": item, "positions": bloom.positions(item), "maybe_present": item in bloom,
                        "actually_present": item in items} for item in probes],
            "stats": bloom.stats()}


def demo_union_find(params: dict[str, Any]) -> dict[str, Any]:
    pairs = params.get("pairs") or [["LinkedIn #1", "Internshala #4"], ["Company site #2", "LinkedIn #1"],
                                    ["Naukri #7", "Unstop #3"], ["Internshala #4", "Company site #2"],
                                    ["Wellfound #9", "Naukri #7"]]
    if not isinstance(pairs, list) or len(pairs) > MAX_ITEMS:
        raise DemoError("expected a list of [a, b] pairs")
    dsu: UnionFind[str] = UnionFind()
    steps = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise DemoError("each pair needs two items")
        a, b = str(pair[0])[:MAX_TEXT], str(pair[1])[:MAX_TEXT]
        merged = dsu.union(a, b)
        steps.append({"a": a, "b": b, "merged": merged, "parents": dsu.parents(), "components": dsu.components})
    return {"steps": steps, "groups": dsu.groups()}


def demo_graph(params: dict[str, Any]) -> dict[str, Any]:
    graph = skill_graph()
    have = [s for s in _words(params.get("have"), ["python", "sql", "git"]) if s.lower() in graph]
    have = [s.lower() for s in have] or ["python"]
    target = _text(params.get("target"), "rag").lower()
    if target not in graph:
        raise DemoError(f"unknown skill: {target}")
    distance, parent = graph.dijkstra(have)
    found = graph.cheapest_path(have, target)
    order = sorted(distance, key=lambda node: (distance[node], node))
    nearby = set(order[:36]) | set(found[0] if found else [])
    return {
        "have": have, "target": target,
        "path": found[0] if found else None, "effort": found[1] if found else None,
        "bfs_order": list(graph.bfs(have[0]))[:20], "dfs_order": list(graph.dfs(have[0]))[:20],
        "settled_order": [{"skill": node, "distance": distance[node], "via": parent[node]} for node in order[:20]],
        "nodes": [{"id": node, "label": display_skill(node), "family": graph.node_data(node).get("family"),
                   "distance": distance.get(node)} for node in graph.nodes() if node in nearby],
        "edges": [{"source": a, "target": b, "weight": w} for a, b, w in graph.edges() if a in nearby and b in nearby],
    }


def demo_aho(params: dict[str, Any]) -> dict[str, Any]:
    text = _text(params.get("text"), SAMPLE_JD, limit=1200)
    automaton = AhoCorasick(SKILL_VOCABULARY)
    lowered = text.lower()
    matches = [{"start": start, "end": end, "pattern": pattern} for start, end, pattern in automaton.finditer(lowered)
               if _bounded(lowered, start, end)]
    return {"text": text, "matches": matches, "skills": [display_skill(s) for s in extract_skills(text)],
            "automaton": automaton.stats(), "characters_read": len(text),
            "regex_characters_read": len(text) * len(SKILL_VOCABULARY)}


def _bounded(text: str, start: int, end: int) -> bool:
    boundary = "abcdefghijklmnopqrstuvwxyz0123456789+#"
    return (start == 0 or text[start - 1] not in boundary) and (end == len(text) or text[end] not in boundary)


def demo_edit_distance(params: dict[str, Any]) -> dict[str, Any]:
    a = _text(params.get("a"), "pyhton", limit=18).lower()
    b = _text(params.get("b"), "python", limit=18).lower()
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        for j in range(len(b) + 1):
            if i == 0 or j == 0:
                table[i][j] = i + j
            else:
                table[i][j] = min(table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    lcs = longest_common_subsequence(a, b)
    return {"a": a, "b": b, "table": table, "distance": edit_distance(a, b), "lcs": "".join(lcs),
            "lcs_table_cells": len(lcs_table(a, b)) * (len(b) + 1), "kmp_matches": kmp_search(a, b[:2]) if len(b) >= 2 else []}


def demo_search(params: dict[str, Any]) -> dict[str, Any]:
    query = _text(params.get("query"), "pyhton intern")
    docs = _words(params.get("documents"), SAMPLE_TITLES)
    index: InvertedIndex[int] = InvertedIndex()
    for position, doc in enumerate(docs):
        index.add(position, doc)
    explained = index.search_explained(query, k=6)
    terms = {term: {"documents": index.document_frequency(term), "idf": round(index.idf(term), 3)} for term in explained["terms"]}
    return {"query": query, "terms": terms, "corrections": explained["corrections"],
            "hits": [{"document": docs[doc_id], "score": score} for doc_id, score in explained["hits"]],
            "stats": index.stats()}


def demo_fenwick(params: dict[str, Any]) -> dict[str, Any]:
    values = _numbers(params.get("values"), [3, 0, 5, 2, 0, 4, 1, 6, 2, 0, 3, 5], limit=24, low=0, high=999)
    left = max(0, min(int(params.get("left") or 3), len(values) - 1))
    right = max(left, min(int(params.get("right") if params.get("right") is not None else 9), len(values) - 1))
    tree = FenwickTree(values)
    return {"values": values, "nodes": tree.nodes(), "left": left, "right": right,
            "sum": tree.range_sum(left, right),
            "reads_for_right": tree.query_path(right + 1), "reads_for_left": tree.query_path(left),
            "naive_reads": right - left + 1}


def demo_knapsack(params: dict[str, Any]) -> dict[str, Any]:
    weights = _numbers(params.get("weights"), [2, 1, 3, 2, 1, 2], limit=10, low=1, high=12)
    values = _numbers(params.get("values"), [9, 4, 10, 6, 5, 3], limit=10, low=0, high=99)
    if len(weights) != len(values):
        raise DemoError("weights and values must have the same length")
    capacity = max(1, min(int(params.get("capacity") or 6), 30))
    n = len(weights)
    table = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for c in range(capacity + 1):
            table[i][c] = table[i - 1][c]
            if weights[i - 1] <= c:
                table[i][c] = max(table[i][c], table[i - 1][c - weights[i - 1]] + values[i - 1])
    best, chosen = knapsack(weights, values, capacity)
    greedy_value, greedy_chosen = greedy_knapsack(weights, values, capacity)
    return {"weights": weights, "values": values, "capacity": capacity, "table": table, "best": best,
            "chosen": chosen, "greedy": {"value": greedy_value, "chosen": greedy_chosen}}


def demo_sorting(params: dict[str, Any]) -> dict[str, Any]:
    scores = _numbers(params.get("scores"), [72, 45, 91, 60, 83, 55, 97, 68, 79, 50, 88, 64], low=0, high=100)
    target = max(0, min(int(params.get("target") if params.get("target") is not None else 80), 100))
    ordered = merge_sort(scores)
    probes, lo, hi = [], 0, len(ordered)
    while lo < hi:
        mid = (lo + hi) // 2
        probes.append({"lo": lo, "hi": hi, "mid": mid, "value": ordered[mid]})
        if ordered[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return {"scores": scores, "sorted": ordered, "target": target, "probes": probes, "index": lower_bound(ordered, target),
            "percentile": round(100 * lo / len(ordered), 1), "linear_probes": lo + 1}


DEMOS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "stack": demo_stack, "queue": demo_queue, "hash_map": demo_hash_map, "heap": demo_heap, "trie": demo_trie,
    "lru": demo_lru, "bloom": demo_bloom, "union_find": demo_union_find, "graph": demo_graph, "aho": demo_aho,
    "edit_distance": demo_edit_distance, "search": demo_search, "fenwick": demo_fenwick, "knapsack": demo_knapsack,
    "sorting": demo_sorting,
}


def run_demo(name: str, params: dict[str, Any] | None) -> dict[str, Any]:
    demo = DEMOS.get(name)
    if demo is None:
        raise KeyError(name)
    try:
        return demo(params or {})
    except (TypeError, ValueError) as exc:
        if isinstance(exc, DemoError):
            raise
        raise DemoError(str(exc)) from exc


# --------------------------------------------------------------------------- benchmarks
def _time(function: Callable[[], Any], repeat: int = 3) -> float:
    """Best of ``repeat`` runs, in milliseconds."""
    best = float("inf")
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        best = min(best, time.perf_counter() - started)
    return round(best * 1000, 3)


def _row(name: str, structure: str, ours: float, naive: float, naive_label: str, detail: str) -> dict[str, Any]:
    return {"name": name, "structure": structure, "ours_ms": ours, "naive_ms": naive, "naive_label": naive_label,
            "speedup": round(naive / ours, 1) if ours > 0 else None, "detail": detail}


_benchmark_cache: LRUCache[str, dict[str, Any]] = LRUCache(capacity=1, ttl=120)


def benchmarks() -> dict[str, Any]:
    """Measured on this server just now (cached for two minutes); inputs are generated from a fixed seed."""
    return _benchmark_cache.get_or_set("all", _run_benchmarks)


def _run_benchmarks() -> dict[str, Any]:
    rng = random.Random(42)
    syllables = ["py", "ja", "re", "da", "ml", "dev", "ops", "net", "sys", "web", "ai", "go", "ru", "sw", "ka", "ta"]
    words = sorted({"".join(rng.choice(syllables) for _ in range(rng.randrange(2, 5))) for _ in range(6000)})
    rows: list[dict[str, Any]] = []

    # A vocabulary the size of a busy account's titles, companies and skills (with their suffixes).
    phrases = sorted({" ".join(rng.choice(words) for _ in range(rng.randrange(1, 4))) for _ in range(40_000)})
    trie = Trie()
    for phrase in phrases:
        trie.insert(phrase, weight=rng.randrange(1, 50))
    prefixes = [phrase[:4] for phrase in rng.sample(phrases, 60)]
    rows.append(_row("Autocomplete: 60 prefixes, best 8 each", "Trie",
                     _time(lambda: [trie.suggest(p, 8) for p in prefixes]),
                     _time(lambda: [sorted(w for w in phrases if w.startswith(p))[:8] for p in prefixes], repeat=1),
                     "scan every phrase",
                     f"{len(phrases):,} phrases, {trie.node_count:,} nodes; the trie's cost doesn't grow with the vocabulary"))

    scores = [rng.random() for _ in range(50_000)]
    builtin = _time(lambda: sorted(scores, reverse=True)[:10])
    rows.append(_row("Best 10 of 50,000 scores", "Binary heap (top-K)",
                     _time(lambda: top_k(scores, 10)), _time(lambda: merge_sort(scores)[-10:], repeat=1),
                     "merge-sort everything",
                     f"O(n log k) against O(n log n), both in pure Python (the built-in C sort takes {builtin} ms)"))

    text = (SAMPLE_JD + " ") * 6
    rows.append(_row("Find every known skill in a job description", "Aho-Corasick automaton",
                     _time(lambda: extract_skills(text)), _time(lambda: extract_skills_regex(text)),
                     "one regular expression per skill", f"{len(text):,} characters, {len(SKILL_VOCABULARY)} patterns"))

    urls = [f"https://jobs.example.com/company-{i % 400}/posting-{i}" for i in range(20_000)]
    bloom = BloomFilter(expected_items=len(urls), false_positive_rate=0.01, items=urls)
    url_set = set(urls)
    probes = [f"https://jobs.example.com/company-{i % 400}/new-{i}" for i in range(5000)]
    false_positives = sum(1 for probe in probes if probe in bloom)
    set_bytes = sys.getsizeof(url_set) + sum(sys.getsizeof(url) for url in urls)
    rows.append({"name": "Remember 20,000 posting URLs", "structure": "Bloom filter",
                 "ours_ms": _time(lambda: [p in bloom for p in probes]), "naive_ms": _time(lambda: [p in url_set for p in probes]),
                 "naive_label": "Python set", "speedup": None,
                 "detail": f"{bloom.stats()['bytes']:,} bytes against {set_bytes:,} for a set "
                           f"({set_bytes // max(1, bloom.stats()['bytes'])}x smaller); "
                           f"{false_positives} false positives in {len(probes):,} lookups, 0 false negatives"})

    docs = [" ".join(rng.choice(words) for _ in range(40)) for _ in range(1500)]
    index: InvertedIndex[int] = InvertedIndex()
    for position, doc in enumerate(docs):
        index.add(position, doc)
    queries = [" ".join(rng.sample(words, 2)) for _ in range(50)]
    rows.append(_row("50 searches over 1,500 documents", "Inverted index + BM25",
                     _time(lambda: [index.search(q, 10, fuzzy=False, prefix=False) for q in queries]),
                     _time(lambda: [[d for d in docs if any(term in d.split() for term in q.split())][:10] for q in queries], repeat=1),
                     "scan every document", f"{index.stats()['terms']:,} terms, {index.stats()['postings']:,} postings"))

    values = [rng.randrange(10) for _ in range(4000)]
    tree = FenwickTree(values)
    ranges = [(a, min(len(values) - 1, a + rng.randrange(1, 2000))) for a in (rng.randrange(2000) for _ in range(2000))]
    rows.append(_row("2,000 date-range totals", "Fenwick tree",
                     _time(lambda: [tree.range_sum(a, b) for a, b in ranges]),
                     _time(lambda: [sum(values[a:b + 1]) for a, b in ranges]),
                     "add up the range each time", "4,000 days of counts"))

    keys = [f"user-{i}" for i in range(20_000)]

    def fill_ours() -> None:
        table: HashMap[str, int] = HashMap()
        for position, key in enumerate(keys):
            table.set(key, position)

    def fill_dict() -> None:
        table: dict[str, int] = {}
        for position, key in enumerate(keys):
            table[key] = position

    rows.append({"name": "Insert 20,000 keys", "structure": "Hash table (ours)", "ours_ms": _time(fill_ours, repeat=1),
                 "naive_ms": _time(fill_dict), "naive_label": "built-in dict (C)", "speedup": None,
                 "detail": "same O(1) idea; dict is written in C, which is why the app uses it on hot paths"})

    pairs = [(rng.randrange(5000), rng.randrange(5000)) for _ in range(20_000)]

    def run_union_find() -> None:
        dsu: UnionFind[int] = UnionFind(range(5000))
        for a, b in pairs:
            dsu.union(a, b)

    def run_relabel() -> None:
        label = list(range(5000))
        for a, b in pairs[:1500]:
            old, new = label[a], label[b]
            if old != new:
                label = [new if value == old else value for value in label]

    rows.append({"name": "Merge duplicate groups", "structure": "Union-Find", "ours_ms": _time(run_union_find, repeat=1),
                 "naive_ms": _time(run_relabel, repeat=1), "naive_label": "relabel the whole list (first 1,500 merges only)",
                 "speedup": None, "detail": "20,000 unions on 5,000 items; the naive version is given 13x less work"})

    return {"rows": rows, "python": sys.version.split()[0], "measured_at": time.time()}
