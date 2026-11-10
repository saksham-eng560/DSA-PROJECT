"""The skill graph: which skills are close to which, and the cheapest way to learn a missing one.

A weighted, undirected :class:`~app.dsa.graph.Graph`. Nodes are canonical skill names (the same
vocabulary the matcher uses); an edge means "knowing one makes the other easier", and its weight
is the rough effort to cross it (1 = nearly the same thing, 3 = a real step).

What it answers:

* **Transferable skills** - a job wants Flask and you know FastAPI: the two are joined by an
  effort-1 edge, so the card says "close: Flask (you know FastAPI)" instead of treating it as a
  plain miss. Only effort-1 edges count: "close" has to mean nearly the same thing.
* **Related skills** - breadth-first search from a skill gives its neighbourhood, level by level
  (used to draw the part of the graph around what you know).
* **Learning path** - Dijkstra from *all* the skills you have at once (multi-source) to a missing
  skill gives the cheapest route, e.g. ``python -> pytorch -> deep learning``.
* **What to learn next** - every skill your saved jobs ask for that you lack, ranked by
  demand / effort with a heap (``top_k``).
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from app.dsa.graph import Graph
from app.dsa.heap import top_k
from app.services.text_utils import canonical_skill, display_skill

# (skill, skill, effort). Grouped by family; the family label colours the graph in the dashboard.
_FAMILIES: dict[str, list[tuple[str, str, float]]] = {
    "Languages": [
        ("python", "javascript", 3), ("javascript", "typescript", 1), ("java", "kotlin", 2), ("java", "c#", 2),
        ("c", "c++", 2), ("c++", "rust", 3), ("go", "rust", 3), ("python", "go", 3), ("java", "scala", 2),
        ("javascript", "html", 1), ("html", "css", 1), ("python", "r", 2), ("swift", "kotlin", 2),
        ("python", "bash", 2), ("bash", "linux", 1), ("c", "embedded systems", 2), ("python", "java", 3),
    ],
    "Backend": [
        ("python", "fastapi", 1), ("python", "django", 1), ("python", "flask", 1), ("fastapi", "flask", 1),
        ("django", "flask", 1), ("fastapi", "rest api", 1), ("django", "rest api", 1), ("flask", "rest api", 1),
        ("javascript", "node.js", 1), ("node.js", "express", 1), ("express", "rest api", 1),
        ("java", "spring boot", 2), ("spring boot", "rest api", 1), ("rest api", "graphql", 2),
        ("rest api", "microservices", 2), ("microservices", "grpc", 2), ("microservices", "kafka", 2),
        ("rest api", "oauth", 2), ("oauth", "jwt", 1), ("microservices", "system design", 2),
        ("system design", "distributed systems", 2), ("fastapi", "websockets", 2), ("node.js", "websockets", 2),
        ("c#", ".net", 1), ("ruby", "rails", 1), ("php", "laravel", 1), ("typescript", "nestjs", 2),
        ("node.js", "nestjs", 2),
    ],
    "Frontend": [
        ("javascript", "react", 1), ("typescript", "react", 1), ("react", "next.js", 1), ("react", "redux", 1),
        ("react", "react native", 2), ("javascript", "vue", 1), ("javascript", "angular", 2),
        ("typescript", "angular", 1), ("css", "tailwind", 1), ("css", "sass", 1), ("react", "tailwind", 1),
        ("javascript", "svelte", 2), ("css", "ui/ux", 2), ("ui/ux", "figma", 1), ("react", "jest", 1),
        ("html", "accessibility", 1), ("dart", "flutter", 1), ("kotlin", "android", 1), ("swift", "ios", 1),
        ("react native", "android", 2), ("react native", "ios", 2),
    ],
    "Data & databases": [
        ("sql", "postgresql", 1), ("sql", "mysql", 1), ("postgresql", "mysql", 1), ("sql", "sqlite", 1),
        ("postgresql", "pgvector", 1), ("sql", "data analysis", 1), ("data analysis", "excel", 1),
        ("data analysis", "tableau", 1), ("data analysis", "power bi", 1), ("python", "pandas", 1),
        ("pandas", "numpy", 1), ("pandas", "data analysis", 1), ("data analysis", "statistics", 2),
        ("sql", "etl", 2), ("etl", "airflow", 2), ("etl", "data engineering", 1), ("data engineering", "spark", 2),
        ("spark", "kafka", 2), ("data engineering", "snowflake", 2), ("data engineering", "bigquery", 2),
        ("data engineering", "dbt", 2), ("mongodb", "node.js", 1), ("mongodb", "dynamodb", 2),
        ("redis", "postgresql", 2), ("elasticsearch", "mongodb", 2), ("sql", "mongodb", 2),
        ("firebase", "mongodb", 2), ("redis", "kafka", 3),
    ],
    "AI & ML": [
        ("python", "machine learning", 2), ("statistics", "machine learning", 2), ("numpy", "machine learning", 1),
        ("machine learning", "scikit-learn", 1), ("machine learning", "deep learning", 2),
        ("deep learning", "pytorch", 1), ("deep learning", "tensorflow", 1), ("pytorch", "tensorflow", 1),
        ("tensorflow", "keras", 1), ("deep learning", "nlp", 2), ("deep learning", "computer vision", 2),
        ("nlp", "llm", 1), ("llm", "generative ai", 1), ("llm", "prompt engineering", 1), ("llm", "rag", 1),
        ("llm", "langchain", 1), ("rag", "langchain", 1), ("rag", "pgvector", 1), ("nlp", "hugging face", 1),
        ("pytorch", "hugging face", 1), ("machine learning", "mlops", 2), ("mlops", "docker", 1),
        ("machine learning", "data science", 1), ("data science", "data analysis", 1),
        ("machine learning", "recommendation systems", 2), ("machine learning", "reinforcement learning", 3),
        ("python", "llm", 2), ("rag", "elasticsearch", 2),
    ],
    "Cloud & DevOps": [
        ("linux", "docker", 1), ("docker", "kubernetes", 2), ("kubernetes", "helm", 1), ("docker", "ci/cd", 1),
        ("ci/cd", "github actions", 1), ("ci/cd", "jenkins", 1), ("git", "github actions", 1), ("git", "ci/cd", 1),
        ("aws", "docker", 2), ("aws", "gcp", 2), ("aws", "azure", 2), ("gcp", "azure", 2), ("aws", "terraform", 2),
        ("terraform", "ansible", 2), ("kubernetes", "prometheus", 2), ("prometheus", "grafana", 1),
        ("aws", "serverless", 1), ("aws", "s3", 1), ("aws", "lambda", 1), ("lambda", "serverless", 1),
        ("linux", "nginx", 1), ("linux", "networking", 2), ("networking", "security", 2), ("devops", "ci/cd", 1),
        ("devops", "kubernetes", 2), ("devops", "sre", 2), ("sre", "observability", 1),
        ("observability", "prometheus", 1), ("gcp", "bigquery", 1), ("docker", "microservices", 2),
        ("git", "linux", 2),
    ],
    "Practices": [
        ("python", "pytest", 1), ("pytest", "unit testing", 1), ("jest", "unit testing", 1),
        ("unit testing", "tdd", 1), ("unit testing", "testing", 1), ("testing", "selenium", 2),
        ("testing", "playwright", 2), ("selenium", "playwright", 1), ("testing", "cypress", 2),
        ("data structures", "algorithms", 1), ("algorithms", "system design", 3), ("python", "data structures", 2),
        ("java", "data structures", 2), ("c++", "data structures", 2), ("java", "oop", 1), ("c++", "oop", 1),
        ("python", "oop", 1), ("agile", "scrum", 1), ("scrum", "jira", 1), ("agile", "kanban", 1),
        ("rest api", "api design", 1), ("python", "playwright", 2), ("git", "agile", 3),
    ],
}

TRANSFER_MAX_EFFORT = 1.0  # a missing skill joined to one you have by an edge this cheap counts as "transferable"


@lru_cache(maxsize=1)
def skill_graph() -> Graph[str]:
    graph: Graph[str] = Graph()
    for family, edges in _FAMILIES.items():
        for a, b, effort in edges:
            a, b = canonical_skill(a), canonical_skill(b)
            for node in (a, b):
                if node not in graph:
                    graph.add_node(node, label=display_skill(node), family=family)
            graph.add_edge(a, b, effort)
    return graph


def _canon(skills: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(canonical_skill(s) for s in skills if s))


def related_skills(skill: str, hops: int = 1) -> list[str]:
    """Skills within ``hops`` edges (BFS), nearest first."""
    graph, skill = skill_graph(), canonical_skill(skill)
    levels = graph.bfs_levels(skill, max_depth=hops)
    return [node for node, depth in sorted(levels.items(), key=lambda item: item[1]) if depth > 0]


def transferable(have: Iterable[str], missing: Iterable[str]) -> list[dict[str, Any]]:
    """For each missing skill that is nearly the same as one you have: the skill and the one it's close to."""
    graph, owned = skill_graph(), set(_canon(have))
    result: list[dict[str, Any]] = []
    for skill in _canon(missing):
        if skill in owned or skill not in graph:
            continue
        near = sorted((graph.weight(skill, neighbor), neighbor) for neighbor in graph.neighbors(skill)
                      if neighbor in owned and graph.weight(skill, neighbor) <= TRANSFER_MAX_EFFORT)
        if near:
            effort, via = near[0]
            result.append({"skill": skill, "label": display_skill(skill), "via": via, "via_label": display_skill(via),
                           "effort": effort})
    return result


def learning_path(have: Iterable[str], target: str) -> dict[str, Any] | None:
    """Cheapest route from any skill you have to ``target`` (multi-source Dijkstra)."""
    graph, target = skill_graph(), canonical_skill(target)
    owned = [s for s in _canon(have) if s in graph]
    if target not in graph:
        return None
    if target in owned:
        return {"target": target, "path": [target], "labels": [display_skill(target)], "effort": 0.0, "steps": []}
    found = graph.cheapest_path(owned, target) if owned else None
    if found is None:
        return None
    path, effort = found
    steps = [{"from": a, "to": b, "effort": graph.weight(a, b)} for a, b in itertools.pairwise(path)]
    return {"target": target, "path": path, "labels": [display_skill(s) for s in path], "effort": effort, "steps": steps}


def learn_next(have: Iterable[str], demand: Counter[str] | dict[str, int], limit: int = 8) -> list[dict[str, Any]]:
    """Missing skills ranked by how many jobs ask for them per unit of effort to learn (heap top-K)."""
    owned = set(_canon(have))
    candidates: list[dict[str, Any]] = []
    for skill, count in demand.items():
        skill = canonical_skill(skill)
        if skill in owned or count <= 0:
            continue
        path = learning_path(owned, skill)
        effort = path["effort"] if path else 4.0  # not connected to anything you know: a fresh start
        candidates.append({
            "skill": skill, "label": display_skill(skill), "jobs": count, "effort": effort,
            "priority": round(count / (1.0 + effort), 3),
            "path": path["labels"] if path else [display_skill(skill)],
        })
    return top_k(candidates, limit, key=lambda c: (c["priority"], c["jobs"]))


def graph_for(have: Iterable[str], wanted: Iterable[str] = (), hops: int = 1, limit: int = 90) -> dict[str, Any]:
    """The part of the skill graph around your skills and the ones your jobs want, for the visualiser."""
    graph, owned, wanted_set = skill_graph(), set(_canon(have)), set(_canon(wanted))
    keep: dict[str, int] = {}
    for seed in [s for s in (*owned, *wanted_set) if s in graph]:
        for node, depth in graph.bfs_levels(seed, max_depth=hops).items():
            keep[node] = min(keep.get(node, depth), depth)
    if not keep:  # no resume yet: show the best-connected skills
        keep = dict.fromkeys(top_k(graph.nodes(), 40, key=lambda n: len(graph.neighbors(n))), 0)
    chosen = set(sorted(keep, key=lambda n: (keep[n], -len(graph.neighbors(n)), n))[:limit])
    nodes = [{
        "id": node, "label": graph.node_data(node).get("label", node), "family": graph.node_data(node).get("family"),
        "degree": len(graph.neighbors(node)),
        "state": "have" if node in owned else ("wanted" if node in wanted_set else "related"),
    } for node in graph.nodes() if node in chosen]
    edges = [{"source": a, "target": b, "weight": w} for a, b, w in graph.edges() if a in chosen and b in chosen]
    return {"nodes": nodes, "edges": edges, "families": list(_FAMILIES),
            "totals": {"skills": len(graph), "links": graph.edge_count(),
                       "clusters": len(graph.connected_components())}}
