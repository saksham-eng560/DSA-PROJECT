"""The from-scratch data structures and algorithms in ``app.dsa``.

Most tests compare against a trusted reference (``sorted``, ``heapq``, ``set``, brute force) on
random inputs with a fixed seed, so a structure is checked on thousands of cases, not a handful.
"""

from __future__ import annotations

import heapq
import itertools
import random
import re
from datetime import datetime, timedelta

import pytest

from app.dsa import (
    AhoCorasick,
    BinaryHeap,
    BloomFilter,
    FenwickTree,
    Graph,
    HashMap,
    InvertedIndex,
    LRUCache,
    Queue,
    RingBuffer,
    SlidingWindowLimiter,
    Stack,
    TokenBucket,
    Trie,
    UnionFind,
    edit_distance,
    k_way_merge,
    kmp_search,
    longest_common_subsequence,
    top_k,
)
from app.dsa.dp import greedy_knapsack, knapsack, longest_increasing_subsequence
from app.dsa.graph import CycleError
from app.dsa.intervals import Interval, find_conflicts, free_slots, max_non_overlapping, merge_intervals, min_rooms
from app.dsa.sorting import (
    binary_search,
    count_in_range,
    lower_bound,
    median,
    merge_sort,
    percentile_rank,
    quickselect,
    upper_bound,
)
from app.dsa.strings import closest, diff, prefix_function, similarity


# --------------------------------------------------------------------------- linear
def test_stack_is_last_in_first_out():
    stack = Stack([1, 2])
    stack.push(3)
    assert list(stack) == [3, 2, 1]
    assert stack.peek() == 3
    assert [stack.pop(), stack.pop(), stack.pop()] == [3, 2, 1]
    assert not stack
    with pytest.raises(IndexError):
        stack.pop()


def test_queue_is_first_in_first_out():
    queue = Queue("ab")
    queue.enqueue("c")
    assert len(queue) == 3 and queue.peek() == "a"
    assert [queue.dequeue() for _ in range(3)] == ["a", "b", "c"]
    with pytest.raises(IndexError):
        queue.dequeue()
    queue.enqueue("d")  # the tail pointer must be reset after emptying
    assert list(queue) == ["d"]


def test_ring_buffer_drops_the_oldest():
    ring = RingBuffer(3)
    assert [ring.append(i) for i in range(5)] == [None, None, None, 0, 1]
    assert ring.to_list() == [2, 3, 4]
    assert len(ring) == 3
    with pytest.raises(ValueError):
        RingBuffer(0)


# --------------------------------------------------------------------------- hash map
def test_hashmap_matches_dict_on_random_operations():
    rng = random.Random(7)
    ours, reference = HashMap(capacity=2), {}
    for _ in range(3000):
        key, action = rng.randrange(200), rng.random()
        if action < 0.6:
            ours[key] = reference[key] = rng.random()
        elif action < 0.8:
            assert ours.delete(key) == (reference.pop(key, None) is not None)
        else:
            assert ours.get(key) == reference.get(key)
    assert len(ours) == len(reference)
    assert dict(ours.items()) == reference
    assert sorted(ours) == sorted(reference)
    assert ours.load_factor <= HashMap.MAX_LOAD and ours.resizes > 0


def test_hashmap_errors_and_membership():
    table = HashMap()
    table["a"] = None  # a stored None is still "present"
    assert "a" in table and "b" not in table
    with pytest.raises(KeyError):
        table["b"]
    del table["a"]
    with pytest.raises(KeyError):
        del table["a"]
    assert table.stats()["size"] == 0


# --------------------------------------------------------------------------- heap
def test_heap_pops_in_sorted_order():
    rng = random.Random(1)
    values = [rng.randrange(1000) for _ in range(500)]
    assert list(BinaryHeap(values).drain()) == sorted(values)
    assert list(BinaryHeap(values, reverse=True).drain()) == sorted(values, reverse=True)


def test_heap_matches_heapq_under_mixed_operations():
    rng = random.Random(2)
    ours, reference = BinaryHeap(), []
    for _ in range(2000):
        if reference and rng.random() < 0.4:
            assert ours.pop() == heapq.heappop(reference)
        else:
            value = rng.randrange(100)
            ours.push(value)
            heapq.heappush(reference, value)
        assert len(ours) == len(reference)
        if reference:
            assert ours.peek() == reference[0]


def test_heap_is_stable_and_never_compares_items():
    jobs = [{"id": i, "score": score} for i, score in enumerate([5, 9, 5, 9, 1])]  # dicts aren't orderable
    heap = BinaryHeap(jobs, key=lambda job: job["score"], reverse=True)
    assert [job["id"] for job in heap.drain()] == [1, 3, 0, 2, 4]
    assert BinaryHeap([3, 1]).pushpop(0) == 0 and BinaryHeap([3, 1]).pushpop(2) == 1


def test_top_k_matches_sorting():
    rng = random.Random(3)
    for _ in range(50):
        values = [rng.randrange(50) for _ in range(rng.randrange(0, 80))]
        k = rng.randrange(0, 12)
        assert top_k(values, k) == sorted(values, reverse=True)[:k]
    people = [("a", 3), ("b", 7), ("c", 7), ("d", 1)]
    assert top_k(people, 2, key=lambda p: p[1]) == [("b", 7), ("c", 7)]  # ties: first seen first


def test_k_way_merge():
    rng = random.Random(4)
    runs = [sorted(rng.randrange(100) for _ in range(rng.randrange(0, 20))) for _ in range(6)]
    assert list(k_way_merge(runs)) == sorted(value for run in runs for value in run)
    assert list(k_way_merge([])) == []


# --------------------------------------------------------------------------- trie
def test_trie_suggests_heaviest_completions_first():
    trie = Trie()
    for word, weight in [("python", 10), ("pytorch", 4), ("pytest", 7), ("java", 9), ("py", 1)]:
        trie.insert(word, weight)
    assert [word for word, _, _ in trie.suggest("py")] == ["python", "pytest", "pytorch", "py"]
    assert [word for word, _, _ in trie.suggest("py", k=2)] == ["python", "pytest"]
    assert trie.suggest("ruby") == []
    assert "python" in trie and "pyth" not in trie and trie.starts_with("pyth")
    assert len(trie) == 5


def test_trie_matches_brute_force():
    rng = random.Random(5)
    words = {"".join(rng.choice("abc") for _ in range(rng.randrange(1, 6))): rng.randrange(1, 100) for _ in range(300)}
    trie = Trie()
    for word, weight in words.items():
        trie.insert(word, weight)
    for prefix in ["", "a", "ab", "abc", "ca", "bbb", "zz"]:
        matching = [w for w in words if w.startswith(prefix)]
        everything = trie.suggest(prefix, k=1000)
        assert sorted(word for word, _, _ in everything) == sorted(matching)
        # heaviest first; words of equal weight may come in any (fixed) order
        assert [weight for _, weight, _ in everything] == sorted((words[w] for w in matching), reverse=True)
        for k in (1, 3, 10):
            top = trie.suggest(prefix, k=k)
            assert [weight for _, weight, _ in top] == sorted((words[w] for w in matching), reverse=True)[:k]
            assert all(words[word] == weight for word, weight, _ in top) and trie.suggest(prefix, k=k) == top
        assert list(trie.words(prefix)) == sorted(w for w in words if w.startswith(prefix))


def test_trie_weights_payloads_and_delete():
    trie = Trie()
    trie.insert("Machine Learning", 2, payload={"kind": "skill"})
    trie.insert("machine   learning", 3)  # same key after normalising; weights add up
    assert trie.weight("MACHINE LEARNING") == 5
    assert trie.suggest("mach")[0] == ("machine learning", 5, {"kind": "skill"})
    trie.insert("machine", 1)
    nodes = trie.node_count
    assert trie.delete("machine learning") and not trie.delete("machine learning")
    assert trie.node_count < nodes and "machine" in trie and len(trie) == 1
    assert [word for word, _, _ in trie.suggest("m")] == ["machine"]
    assert trie.delete("machine") and trie.node_count == 1


# --------------------------------------------------------------------------- LRU cache
def test_lru_evicts_least_recently_used():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1  # "a" is now the most recent
    cache.put("c", 3)  # evicts "b"
    assert "b" not in cache and cache.get("b") is None
    assert list(cache.keys()) == ["c", "a"]
    assert cache.stats() == {"size": 2, "capacity": 2, "hits": 1, "misses": 1, "evictions": 1, "hit_rate": 0.5}


def test_lru_ttl_and_get_or_set():
    now = [0.0]
    cache = LRUCache(capacity=4, ttl=10, clock=lambda: now[0])
    calls = []
    assert cache.get_or_set("k", lambda: calls.append(1) or "v") == "v"
    assert cache.get_or_set("k", lambda: calls.append(1) or "v2") == "v"
    now[0] = 11
    assert "k" not in cache and cache.get_or_set("k", lambda: "fresh") == "fresh"
    assert len(calls) == 1
    assert cache.delete("k") and not cache.delete("k")
    cache.put("x", 1)
    cache.clear()
    assert len(cache) == 0 and list(cache.keys()) == []


def test_lru_matches_reference_model():
    rng = random.Random(6)
    cache, order = LRUCache(capacity=8), []  # order: least recent first
    values = {}
    for _ in range(4000):
        key = rng.randrange(20)
        if rng.random() < 0.5:
            cache.put(key, key * 2)
            values[key] = key * 2
            if key in order:
                order.remove(key)
            elif len(order) == 8:
                del values[order.pop(0)]
            order.append(key)
        else:
            assert cache.get(key) == values.get(key)
            if key in order:
                order.remove(key)
                order.append(key)
    assert list(cache.keys()) == order[::-1]


# --------------------------------------------------------------------------- Bloom filter
def test_bloom_filter_has_no_false_negatives_and_few_false_positives():
    seen = [f"https://jobs.example.com/{i}" for i in range(2000)]
    bloom = BloomFilter(expected_items=2000, false_positive_rate=0.01, items=seen)
    assert all(url in bloom for url in seen)
    false_positives = sum(f"https://other.example.com/{i}" in bloom for i in range(5000))
    assert false_positives / 5000 < 0.03
    stats = bloom.stats()
    assert stats["items"] == 2000 and stats["hash_functions"] == 7
    assert stats["bytes"] < 3000  # ~1.2 bytes per URL; a set of these strings is ~100x that
    assert 42 not in bloom
    with pytest.raises(ValueError):
        BloomFilter(expected_items=0)


# --------------------------------------------------------------------------- union-find
def test_union_find_groups():
    dsu = UnionFind(range(6))
    assert dsu.components == 6
    assert dsu.union(0, 1) and dsu.union(1, 2) and not dsu.union(0, 2)
    dsu.union(3, 4)
    assert dsu.connected(0, 2) and not dsu.connected(2, 3)
    assert dsu.components == 3 and dsu.size_of(1) == 3
    assert dsu.groups(min_size=2) == [[0, 1, 2], [3, 4]]
    assert dsu.find("new") == "new" and len(dsu) == 7


def test_union_find_matches_graph_components():
    rng = random.Random(8)
    dsu, graph = UnionFind(range(60)), Graph()
    for node in range(60):
        graph.add_node(node)
    for _ in range(45):
        a, b = rng.randrange(60), rng.randrange(60)
        dsu.union(a, b)
        graph.add_edge(a, b)
    assert sorted(sorted(g) for g in dsu.groups()) == sorted(sorted(c) for c in graph.connected_components())


# --------------------------------------------------------------------------- graph
def _sample_graph() -> Graph:
    graph = Graph()
    for a, b, weight in [("a", "b", 1), ("b", "c", 1), ("a", "c", 5), ("c", "d", 1), ("e", "f", 1)]:
        graph.add_edge(a, b, weight)
    return graph


def test_graph_traversals_and_paths():
    graph = _sample_graph()
    assert list(graph.bfs("a")) == ["a", "b", "c", "d"]
    assert list(graph.dfs("a")) == ["a", "b", "c", "d"]
    assert graph.reachable("e") == {"e", "f"}
    assert graph.shortest_path("a", "d") == ["a", "c", "d"]  # fewest edges
    assert graph.cheapest_path("a", "d") == (["a", "b", "c", "d"], 3.0)  # lowest total weight
    assert graph.shortest_path("a", "f") is None and graph.cheapest_path("a", "f") is None
    assert graph.cheapest_path(["e", "d"], "c") == (["d", "c"], 1.0)  # nearest of several sources
    assert graph.bfs_levels("a", max_depth=1) == {"a": 0, "b": 1, "c": 1}
    assert [sorted(c) for c in graph.connected_components()] == [["a", "b", "c", "d"], ["e", "f"]]
    assert graph.edge_count() == 5 and len(graph.edges()) == 5
    assert graph.has_cycle()
    graph.remove_edge("a", "c")
    assert not graph.has_cycle() and not graph.has_edge("c", "a")


def test_dijkstra_matches_brute_force():
    rng = random.Random(9)
    graph = Graph(directed=True)
    nodes = list(range(12))
    for node in nodes:
        graph.add_node(node)
    for _ in range(40):
        graph.add_edge(rng.choice(nodes), rng.choice(nodes), rng.randrange(1, 10))
    # Bellman-Ford as the reference
    reference = {node: float("inf") for node in nodes}
    reference[0] = 0
    for _ in nodes:
        for source, target, weight in graph.edges():
            reference[target] = min(reference[target], reference[source] + weight)
    distance, _ = graph.dijkstra(0)
    assert distance == {node: d for node, d in reference.items() if d != float("inf")}
    graph.add_edge(0, 1, -1)
    with pytest.raises(ValueError):
        graph.dijkstra(0)


def test_topological_sort_layers_and_longest_path():
    dag = Graph(directed=True)
    for source, target in [("scan", "dedupe"), ("dedupe", "score"), ("resume", "score"), ("score", "review"),
                           ("review", "tailor"), ("review", "answers"), ("tailor", "fill"), ("answers", "fill")]:
        dag.add_edge(source, target)
    order = dag.topological_sort()
    assert all(order.index(source) < order.index(target) for source, target, _ in dag.edges())
    assert dag.layers() == [["scan", "resume"], ["dedupe"], ["score"], ["review"], ["tailor", "answers"], ["fill"]]
    path, length = dag.longest_path()
    assert length == 5 and path[0] == "scan" and path[-1] == "fill"
    assert not dag.has_cycle() and dag.predecessors("fill") == ["tailor", "answers"]
    dag.add_edge("fill", "scan")
    assert dag.has_cycle()
    with pytest.raises(CycleError):
        dag.topological_sort()
    with pytest.raises(ValueError):
        Graph().topological_sort()


def test_graph_export():
    graph = Graph(directed=True)
    graph.add_node("a", label="A")
    graph.add_edge("a", "b", 2, kind="needs")
    assert graph.to_dict() == {"directed": True, "nodes": [{"id": "a", "label": "A"}, {"id": "b"}],
                               "edges": [{"source": "a", "target": "b", "weight": 2, "kind": "needs"}]}
    assert graph.node_data("a") == {"label": "A"} and graph.edge_data("a", "b") == {"kind": "needs"}
    assert graph.in_degrees() == {"a": 0, "b": 1} and graph.weight("a", "b") == 2


# --------------------------------------------------------------------------- strings
def _reference_edit_distance(a: str, b: str) -> int:
    table = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            table[i][j] = min(table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    return table[-1][-1]


def test_edit_distance():
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3 and edit_distance("same", "same") == 0
    rng = random.Random(10)
    for _ in range(300):
        a = "".join(rng.choice("abcd") for _ in range(rng.randrange(0, 9)))
        b = "".join(rng.choice("abcd") for _ in range(rng.randrange(0, 9)))
        expected = _reference_edit_distance(a, b)
        assert edit_distance(a, b) == expected
        assert edit_distance(a, b, max_distance=2) == min(expected, 3)
    assert similarity("python", "python") == 1.0 and similarity("", "") == 1.0
    assert closest("pyhton", ["java", "python", "pytorch"]) == "python"
    assert closest("kubernets", ["kubernetes", "kafka"]) == "kubernetes"
    assert closest("zzz", ["python"]) is None


def test_lcs_and_diff():
    assert "".join(longest_common_subsequence("AGGTAB", "GXTXAYB")) == "GTAB"
    old = ["Built REST APIs", "Wrote tests", "Deployed on AWS"]
    new = ["Built REST APIs in FastAPI", "Wrote tests", "Deployed on AWS", "Mentored interns"]
    steps = diff(old, new)
    assert steps == [("-", "Built REST APIs"), ("+", "Built REST APIs in FastAPI"), ("=", "Wrote tests"),
                     ("=", "Deployed on AWS"), ("+", "Mentored interns")]
    assert [item for op, item in steps if op != "+"] == old and [item for op, item in steps if op != "-"] == new


def test_kmp_finds_every_occurrence():
    assert kmp_search("abababa", "aba") == [0, 2, 4]
    assert kmp_search("hello", "") == [] and kmp_search("", "a") == []
    assert prefix_function("aabaaab") == [0, 1, 0, 1, 2, 2, 3]
    rng = random.Random(11)
    for _ in range(200):
        text = "".join(rng.choice("ab") for _ in range(40))
        pattern = "".join(rng.choice("ab") for _ in range(rng.randrange(1, 5)))
        assert kmp_search(text, pattern) == [m.start() for m in re.finditer(f"(?={re.escape(pattern)})", text)]


def test_aho_corasick_matches_brute_force():
    automaton = AhoCorasick(["he", "she", "his", "hers"])
    assert automaton.search("ushers") == [(1, 4, "she"), (2, 4, "he"), (2, 6, "hers")]
    assert automaton.found("this is his") == {"his"}
    rng = random.Random(12)
    patterns = list({"".join(rng.choice("abc") for _ in range(rng.randrange(1, 5))) for _ in range(25)})
    automaton = AhoCorasick(patterns)
    for _ in range(100):
        text = "".join(rng.choice("abc") for _ in range(60))
        expected = sorted((i, i + len(p), p) for p in patterns for i in kmp_search(text, p))
        assert sorted(automaton.search(text)) == expected
    automaton.add("zz")  # adding a pattern rebuilds the failure links on the next search
    assert automaton.found("abzz") >= {"zz"}


# --------------------------------------------------------------------------- inverted index
def _job_index() -> InvertedIndex:
    index = InvertedIndex()
    docs = {
        1: {"title": "Python Backend Intern", "body": "Build REST APIs with FastAPI and PostgreSQL."},
        2: {"title": "Machine Learning Intern", "body": "Train PyTorch models. Python required."},
        3: {"title": "Frontend Intern", "body": "React and TypeScript dashboards."},
        4: {"title": "Data Analyst", "body": "SQL, Excel and dashboards for the python-curious."},
    }
    for doc_id, fields in docs.items():
        index.add(doc_id, fields, weights={"title": 3.0})
    return index


def test_bm25_ranks_title_hits_and_rare_terms_higher():
    index = _job_index()
    hits = index.search("python")
    assert next(doc for doc, _ in hits) == 1  # in the title, weighted 3x
    assert {doc for doc, _ in hits} == {1, 2, 4}
    assert next(doc for doc, _ in index.search("react dashboards")) == 3  # matches both terms
    assert index.idf("react") > index.idf("intern")  # rarer terms weigh more
    assert index.search("golang") == []
    assert len(index) == 4 and index.stats()["documents"] == 4


def test_search_fixes_typos_and_completes_the_last_word():
    index = _job_index()
    explained = index.search_explained("pyhton")
    assert explained["corrections"] == {"pyhton": "python"} and explained["hits"][0][0] == 1
    assert [doc for doc, _ in index.search("fasta")] == [1]  # prefix of "fastapi"
    assert index.search("fasta", prefix=False, fuzzy=False) == []
    assert next(doc for doc, _ in index.search("python", allowed=lambda doc: doc != 1)) != 1


def test_index_update_and_remove():
    index = _job_index()
    index.add(3, {"title": "Go Developer", "body": "Kubernetes operators"})  # re-indexing replaces the old text
    assert index.search("react") == [] and [doc for doc, _ in index.search("kubernetes")] == [3]
    assert index.remove(3) and not index.remove(3)
    assert index.search("kubernetes", fuzzy=False, prefix=False) == [] and index.document_frequency("go") == 0
    assert 3 not in index and index.vocabulary_size == index.stats()["terms"]


# --------------------------------------------------------------------------- Fenwick tree
def test_fenwick_matches_prefix_sums():
    rng = random.Random(13)
    values = [rng.randrange(10) for _ in range(64)]
    tree = FenwickTree(values)
    for _ in range(500):
        if rng.random() < 0.4:
            index, delta = rng.randrange(64), rng.randrange(1, 5)
            values[index] += delta
            tree.add(index, delta)
        left = rng.randrange(64)
        right = rng.randrange(left, 64)
        assert tree.range_sum(left, right) == sum(values[left:right + 1])
    assert tree.total() == sum(values) and tree.get(5) == values[5] and len(tree) == 64
    target = sum(values) // 2
    index = tree.lower_bound(target)
    assert sum(values[:index]) < target <= sum(values[:index + 1])
    empty = FenwickTree(4)
    assert empty.total() == 0 and empty.range_sum(3, 1) == 0
    with pytest.raises(IndexError):
        empty.add(4, 1)


# --------------------------------------------------------------------------- intervals
def test_interview_conflicts_and_free_slots():
    day = datetime(2027, 1, 11, 9)
    hour = timedelta(hours=1)
    interviews = [Interval(day, day + hour, "A"), Interval(day + hour / 2, day + 2 * hour, "B"),
                  Interval(day + 2 * hour, day + 3 * hour, "C"), Interval(day + 5 * hour, day + 6 * hour, "D")]
    assert [(a.label, b.label) for a, b in find_conflicts(interviews)] == [("A", "B")]  # B and C only touch
    assert [(i.start, i.end) for i in merge_intervals(interviews)] == [(day, day + 3 * hour), (day + 5 * hour, day + 6 * hour)]
    slots = free_slots(interviews, day - hour, day + 8 * hour, min_length=hour)
    assert [(s.start, s.end) for s in slots] == [(day - hour, day), (day + 3 * hour, day + 5 * hour),
                                                 (day + 6 * hour, day + 8 * hour)]
    assert min_rooms(interviews) == 2
    assert [i.label for i in max_non_overlapping(interviews)] == ["A", "C", "D"]


def test_intervals_match_brute_force():
    rng = random.Random(14)
    for _ in range(100):
        items = []
        for label in range(rng.randrange(0, 12)):
            start = rng.randrange(40)
            items.append(Interval(start, start + rng.randrange(1, 8), label))
        expected = {frozenset((a.label, b.label)) for a in items for b in items
                    if a.label < b.label and a.start < b.end and b.start < a.end}
        assert {frozenset((a.label, b.label)) for a, b in find_conflicts(items)} == expected
        busiest = max((sum(i.start <= t < i.end for i in items) for t in range(50)), default=0)
        assert min_rooms(items) == busiest


# --------------------------------------------------------------------------- dynamic programming
def test_knapsack_is_optimal():
    assert knapsack([1, 3, 4, 5], [1, 4, 5, 7], 7) == (9, [1, 2])
    assert knapsack([], [], 10) == (0.0, []) and knapsack([5], [10], 4) == (0.0, [])
    rng = random.Random(15)
    for _ in range(60):
        n = rng.randrange(1, 9)
        weights = [rng.randrange(1, 8) for _ in range(n)]
        values = [rng.randrange(1, 20) for _ in range(n)]
        capacity = rng.randrange(0, 20)
        best = max(sum(values[i] for i in range(n) if mask >> i & 1) for mask in range(1 << n)
                   if sum(weights[i] for i in range(n) if mask >> i & 1) <= capacity)
        value, chosen = knapsack(weights, values, capacity)
        assert value == best == sum(values[i] for i in chosen)
        assert sum(weights[i] for i in chosen) <= capacity
        assert greedy_knapsack(weights, values, capacity)[0] <= best
    # the classic case where greedy by value-per-weight loses
    assert greedy_knapsack([10, 20, 30], [60, 100, 120], 50)[0] == 160 < knapsack([10, 20, 30], [60, 100, 120], 50)[0] == 220
    with pytest.raises(ValueError):
        knapsack([1], [1, 2], 3)


def test_longest_increasing_subsequence():
    values = [10, 9, 2, 5, 3, 7, 101, 18]
    indexes = longest_increasing_subsequence(values)
    assert len(indexes) == 4 and indexes == sorted(indexes)
    assert all(values[a] < values[b] for a, b in itertools.pairwise(indexes))
    assert longest_increasing_subsequence([]) == [] and len(longest_increasing_subsequence([3, 3, 3])) == 1


# --------------------------------------------------------------------------- sorting and searching
def test_merge_sort_is_correct_and_stable():
    rng = random.Random(16)
    for _ in range(50):
        values = [rng.randrange(30) for _ in range(rng.randrange(0, 70))]
        assert merge_sort(values) == sorted(values)
        assert merge_sort(values, reverse=True) == sorted(values, reverse=True)
    rows = [("b", 2), ("a", 2), ("c", 1), ("d", 2)]
    assert merge_sort(rows, key=lambda r: r[1]) == [("c", 1), ("b", 2), ("a", 2), ("d", 2)]
    assert merge_sort(rows, key=lambda r: r[1], reverse=True) == [("b", 2), ("a", 2), ("d", 2), ("c", 1)]


def test_quickselect_and_median():
    rng = random.Random(17)
    values = [rng.randrange(100) for _ in range(201)]
    ordered = sorted(values)
    assert all(quickselect(values, k) == ordered[k] for k in (0, 1, 100, 199, 200))
    assert median(values) == ordered[100] and median([1, 2, 3, 4]) == 2.5 and median([]) is None
    with pytest.raises(IndexError):
        quickselect(values, 201)


def test_binary_search_helpers():
    scores = [10, 20, 20, 20, 35, 50]
    assert lower_bound(scores, 20) == 1 and upper_bound(scores, 20) == 4
    assert lower_bound(scores, 99) == 6 and upper_bound(scores, 0) == 0
    assert binary_search(scores, 35) == 4 and binary_search(scores, 36) == -1
    assert count_in_range(scores, 20, 35) == 4 and count_in_range(scores, 60, 70) == 0
    assert percentile_rank(scores, 35) == 66.7 and percentile_rank([], 5) == 0.0


# --------------------------------------------------------------------------- rate limiting
def test_sliding_window_limiter():
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=3, window_seconds=10, clock=lambda: now[0])
    assert [limiter.allow("u") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("other")  # keys are independent
    assert limiter.remaining("u") == 0 and limiter.retry_after("u") == 10
    now[0] = 9.9
    assert not limiter.allow("u")
    now[0] = 10.1  # the three requests at t=0 have left the window
    assert limiter.remaining("u") == 3 and limiter.allow("u") and limiter.retry_after("u") == 0
    limiter.reset()
    assert limiter.remaining("u") == 3


def test_token_bucket_allows_bursts_then_refills():
    now = [0.0]
    bucket = TokenBucket(capacity=3, rate=1, clock=lambda: now[0])
    assert [bucket.consume() for _ in range(4)] == [True, True, True, False]
    now[0] = 2
    assert bucket.tokens == 2 and bucket.consume(2) and not bucket.consume()
    now[0] = 100
    assert bucket.tokens == 3  # never above capacity
