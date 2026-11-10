"""Graph on adjacency lists, with the classic traversals and path algorithms.

``Graph`` stores, for every node, a map of its neighbours and the weight of each edge. That is
O(V + E) space and makes "who are my neighbours?" O(degree) - the right trade-off for the sparse
graphs this app has.

==========================  ==================  ==========================================
Algorithm                   Time                What the app uses it for
==========================  ==================  ==========================================
BFS                         O(V + E)            fewest status changes between two states
DFS (iterative)             O(V + E)            everything reachable from a status
Shortest path (BFS)         O(V + E)            unweighted paths in the state machine
Dijkstra (binary heap)      O((V + E) log V)    cheapest learning path to a missing skill
Topological sort (Kahn)     O(V + E)            execution order of the agent pipeline
Cycle detection             O(V + E)            the pipeline must be a DAG
Connected components        O(V + E)            skill families
Longest path in a DAG       O(V + E)            the pipeline's critical path
==========================  ==================  ==========================================

Three graphs are built on it: the skill graph (``services/skill_graph.py``), the application
status state machine (``services/state_machine.py``) and the agent pipeline
(``services/pipeline_graph.py``).
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Iterator
from typing import Any, Generic, TypeVar

from app.dsa.heap import BinaryHeap
from app.dsa.linear import Queue

N = TypeVar("N", bound=Hashable)


class CycleError(ValueError):
    """Raised by :meth:`Graph.topological_sort` when the graph has a cycle."""


class Graph(Generic[N]):
    def __init__(self, directed: bool = False) -> None:
        self.directed = directed
        self._adj: dict[N, dict[N, float]] = {}
        self._data: dict[N, dict[str, Any]] = {}
        self._edge_data: dict[tuple[N, N], dict[str, Any]] = {}

    # ------------------------------------------------------------------ construction
    def add_node(self, node: N, **data: Any) -> None:
        self._adj.setdefault(node, {})
        if data:
            self._data.setdefault(node, {}).update(data)

    def add_edge(self, source: N, target: N, weight: float = 1.0, **data: Any) -> None:
        self.add_node(source)
        self.add_node(target)
        self._adj[source][target] = weight
        if not self.directed:
            self._adj[target][source] = weight
        if data:
            self._edge_data[(source, target)] = data
            if not self.directed:
                self._edge_data[(target, source)] = data

    def remove_edge(self, source: N, target: N) -> None:
        self._adj.get(source, {}).pop(target, None)
        self._edge_data.pop((source, target), None)
        if not self.directed:
            self._adj.get(target, {}).pop(source, None)
            self._edge_data.pop((target, source), None)

    # ------------------------------------------------------------------ inspection
    def __contains__(self, node: object) -> bool:
        return node in self._adj

    def __len__(self) -> int:
        return len(self._adj)

    def nodes(self) -> list[N]:
        return list(self._adj)

    def node_data(self, node: N) -> dict[str, Any]:
        return self._data.get(node, {})

    def edge_data(self, source: N, target: N) -> dict[str, Any]:
        return self._edge_data.get((source, target), {})

    def neighbors(self, node: N) -> list[N]:
        return list(self._adj.get(node, {}))

    def weight(self, source: N, target: N) -> float | None:
        return self._adj.get(source, {}).get(target)

    def has_edge(self, source: N, target: N) -> bool:
        return target in self._adj.get(source, {})

    def edges(self) -> list[tuple[N, N, float]]:
        """Each edge once (an undirected edge is reported from the node added first)."""
        seen: set[tuple[N, N]] = set()
        result: list[tuple[N, N, float]] = []
        for source, targets in self._adj.items():
            for target, weight in targets.items():
                if not self.directed and (target, source) in seen:
                    continue
                seen.add((source, target))
                result.append((source, target, weight))
        return result

    def edge_count(self) -> int:
        total = sum(len(targets) for targets in self._adj.values())
        return total if self.directed else total // 2

    def in_degrees(self) -> dict[N, int]:
        degrees = dict.fromkeys(self._adj, 0)
        for targets in self._adj.values():
            for target in targets:
                degrees[target] += 1
        return degrees

    def predecessors(self, node: N) -> list[N]:
        return [source for source, targets in self._adj.items() if node in targets]

    # ------------------------------------------------------------------ traversal
    def bfs(self, start: N) -> Iterator[N]:
        """Breadth-first order: every node at distance d before any node at distance d + 1."""
        if start not in self._adj:
            return
        seen = {start}
        queue: Queue[N] = Queue([start])
        while queue:
            node = queue.dequeue()
            yield node
            for neighbor in self._adj[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.enqueue(neighbor)

    def dfs(self, start: N) -> Iterator[N]:
        """Depth-first (pre-order) with an explicit stack, so deep graphs can't overflow recursion."""
        if start not in self._adj:
            return
        seen: set[N] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            yield node
            # Reversed so the first neighbour added is the first one visited.
            stack.extend(n for n in reversed(list(self._adj[node])) if n not in seen)

    def reachable(self, start: N) -> set[N]:
        return set(self.bfs(start))

    def bfs_levels(self, start: N, max_depth: int | None = None) -> dict[N, int]:
        """Number of edges from ``start`` to every node within ``max_depth``."""
        if start not in self._adj:
            return {}
        depth = {start: 0}
        queue: Queue[N] = Queue([start])
        while queue:
            node = queue.dequeue()
            if max_depth is not None and depth[node] >= max_depth:
                continue
            for neighbor in self._adj[node]:
                if neighbor not in depth:
                    depth[neighbor] = depth[node] + 1
                    queue.enqueue(neighbor)
        return depth

    # ------------------------------------------------------------------ paths
    def shortest_path(self, start: N, goal: N) -> list[N] | None:
        """Fewest edges from ``start`` to ``goal`` (BFS), or ``None`` when there is no route."""
        if start not in self._adj or goal not in self._adj:
            return None
        parent: dict[N, N | None] = {start: None}
        queue: Queue[N] = Queue([start])
        while queue:
            node = queue.dequeue()
            if node == goal:
                return _walk_back(parent, goal)
            for neighbor in self._adj[node]:
                if neighbor not in parent:
                    parent[neighbor] = node
                    queue.enqueue(neighbor)
        return None

    def dijkstra(self, sources: Iterable[N] | N, goal: N | None = None) -> tuple[dict[N, float], dict[N, N | None]]:
        """Cheapest distance from the nearest of ``sources`` to every node (weights must be >= 0).

        Returns ``(distance, parent)``. Starting from several sources at once answers "which of
        the skills I already have is closest to this one?" in a single run. Stops early at ``goal``.
        """
        if isinstance(sources, (str, bytes)) or not isinstance(sources, Iterable) or (
                isinstance(sources, Hashable) and sources in self._adj):
            sources = [sources]  # type: ignore[list-item]  # a single node (strings are iterable too)
        distance: dict[N, float] = {}
        parent: dict[N, N | None] = {}
        heap: BinaryHeap[tuple[float, N]] = BinaryHeap(key=lambda entry: entry[0])
        for source in sources:  # type: ignore[union-attr]
            if source in self._adj:
                distance[source] = 0.0
                parent[source] = None
                heap.push((0.0, source))
        done: set[N] = set()
        while heap:
            dist, node = heap.pop()
            if node in done:
                continue  # a stale entry: this node was already settled with a shorter distance
            done.add(node)
            if node == goal:
                break
            for neighbor, weight in self._adj[node].items():
                if weight < 0:
                    raise ValueError("Dijkstra needs non-negative edge weights")
                candidate = dist + weight
                if candidate < distance.get(neighbor, math.inf):
                    distance[neighbor] = candidate
                    parent[neighbor] = node
                    heap.push((candidate, neighbor))
        return distance, parent

    def cheapest_path(self, sources: Iterable[N] | N, goal: N) -> tuple[list[N], float] | None:
        """``(path, total weight)`` from the nearest source to ``goal``, or ``None``."""
        distance, parent = self.dijkstra(sources, goal)
        if goal not in distance:
            return None
        return _walk_back(parent, goal), distance[goal]

    # ------------------------------------------------------------------ DAG algorithms
    def topological_sort(self) -> list[N]:
        """Kahn's algorithm: an order in which every edge points forwards. Raises on a cycle."""
        if not self.directed:
            raise ValueError("topological sort needs a directed graph")
        degrees = self.in_degrees()
        queue: Queue[N] = Queue(node for node in self._adj if degrees[node] == 0)
        order: list[N] = []
        while queue:
            node = queue.dequeue()
            order.append(node)
            for neighbor in self._adj[node]:
                degrees[neighbor] -= 1
                if degrees[neighbor] == 0:
                    queue.enqueue(neighbor)
        if len(order) != len(self._adj):
            raise CycleError("the graph has a cycle")
        return order

    def has_cycle(self) -> bool:
        if self.directed:
            try:
                self.topological_sort()
            except CycleError:
                return True
            return False
        # Undirected: a cycle exists when DFS meets a visited node that isn't the one it came from.
        seen: set[N] = set()
        for root in self._adj:
            if root in seen:
                continue
            stack: list[tuple[N, N | None]] = [(root, None)]
            while stack:
                node, came_from = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                for neighbor in self._adj[node]:
                    if neighbor not in seen:
                        stack.append((neighbor, node))
                    elif neighbor != came_from:
                        return True
        return False

    def layers(self) -> list[list[N]]:
        """Nodes of a DAG grouped by depth: layer 0 has no prerequisites, layer i needs layer i - 1.

        Everything in one layer could run in parallel. This is also the left-to-right layout of
        the pipeline diagram.
        """
        depth: dict[N, int] = {}
        for node in self.topological_sort():
            depth.setdefault(node, 0)
            for neighbor in self._adj[node]:
                depth[neighbor] = max(depth.get(neighbor, 0), depth[node] + 1)
        result: list[list[N]] = [[] for _ in range(max(depth.values(), default=-1) + 1)]
        for node, level in depth.items():
            result[level].append(node)
        return result

    def longest_path(self) -> tuple[list[N], float]:
        """The heaviest path through a DAG (dynamic programming over the topological order)."""
        best: dict[N, float] = {}
        parent: dict[N, N | None] = {}
        for node in self.topological_sort():
            best.setdefault(node, 0.0)
            parent.setdefault(node, None)
            for neighbor, weight in self._adj[node].items():
                if best[node] + weight > best.get(neighbor, -math.inf):
                    best[neighbor] = best[node] + weight
                    parent[neighbor] = node
        if not best:
            return [], 0.0
        end = max(best, key=lambda node: best[node])
        return _walk_back(parent, end), best[end]

    def connected_components(self) -> list[list[N]]:
        """Groups of nodes linked to each other (edge direction ignored), largest first."""
        undirected: dict[N, set[N]] = {node: set(targets) for node, targets in self._adj.items()}
        if self.directed:
            for source, targets in self._adj.items():
                for target in targets:
                    undirected[target].add(source)
        seen: set[N] = set()
        components: list[list[N]] = []
        for root in self._adj:
            if root in seen:
                continue
            component: list[N] = []
            stack = [root]
            seen.add(root)
            while stack:
                node = stack.pop()
                component.append(node)
                for neighbor in undirected[node]:
                    if neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            components.append(component)
        components.sort(key=len, reverse=True)
        return components

    def to_dict(self) -> dict[str, Any]:
        """Nodes and edges as plain data, for the API and the visualisers."""
        return {
            "directed": self.directed,
            "nodes": [{"id": node, **self._data.get(node, {})} for node in self._adj],
            "edges": [{"source": source, "target": target, "weight": weight, **self._edge_data.get((source, target), {})}
                      for source, target, weight in self.edges()],
        }


def _walk_back(parent: dict[Any, Any], end: Any) -> list[Any]:
    path = [end]
    while parent.get(path[-1]) is not None:
        path.append(parent[path[-1]])
    path.reverse()
    return path
