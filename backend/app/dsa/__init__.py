"""Data structures and algorithms, written from scratch and used by the app.

Nothing in this package imports a third-party library or another part of the app, so every
structure can be read, tested and explained on its own. Each module's docstring says what the
structure is, its time and space complexity, and where AutoApply AI uses it.

==================  =======================================  ===================================
Module              Structure / algorithm                    Used for
==================  =======================================  ===================================
``linear``          Stack, Queue (linked list), RingBuffer   undo last swipe, BFS, chat memory
``hashmap``         Hash table (separate chaining)           study + benchmark of ``dict``
``heap``            Binary heap, top-K, k-way merge          best-K deck, autocomplete, Dijkstra
``trie``            Prefix tree                              search-as-you-type suggestions
``lru_cache``       Hash map + doubly linked list            caching search indexes and answers
``bloom_filter``    Bit array + k hash functions             "have we seen this posting?"
``union_find``      Disjoint sets (rank + path compression)  grouping duplicate postings
``graph``           Adjacency list: BFS, DFS, Dijkstra,      skill graph, status state machine,
                    topological sort, components             the agent pipeline DAG
``strings``         Edit distance, LCS, KMP, Aho-Corasick    typo fixes, diffs, skill extraction
``inverted_index``  Postings lists + BM25 ranking            full-text job search, chatbot recall
``fenwick``         Binary indexed tree                      date-range counts in analytics
``intervals``       Sort + sweep, min-heap of end times      interview clashes and free slots
``dp``              0/1 knapsack, LIS                        one-page resume optimiser
``sorting``         Merge sort, quickselect, binary search   stable ranking, percentiles
``rate_limit``      Sliding window (deque), token bucket     chatbot and search throttling
==================  =======================================  ===================================
"""

from app.dsa.bloom_filter import BloomFilter
from app.dsa.fenwick import FenwickTree
from app.dsa.graph import Graph
from app.dsa.hashmap import HashMap
from app.dsa.heap import BinaryHeap, k_way_merge, top_k
from app.dsa.inverted_index import InvertedIndex
from app.dsa.linear import Queue, RingBuffer, Stack
from app.dsa.lru_cache import LRUCache
from app.dsa.rate_limit import SlidingWindowLimiter, TokenBucket
from app.dsa.strings import AhoCorasick, edit_distance, kmp_search, longest_common_subsequence
from app.dsa.trie import Trie
from app.dsa.union_find import UnionFind

__all__ = [
    "AhoCorasick",
    "BinaryHeap",
    "BloomFilter",
    "FenwickTree",
    "Graph",
    "HashMap",
    "InvertedIndex",
    "LRUCache",
    "Queue",
    "RingBuffer",
    "SlidingWindowLimiter",
    "Stack",
    "TokenBucket",
    "Trie",
    "UnionFind",
    "edit_distance",
    "k_way_merge",
    "kmp_search",
    "longest_common_subsequence",
    "top_k",
]
