"""The application lifecycle as a directed graph (a finite state machine).

Nodes are the 17 application statuses; an edge ``A -> B`` means the agent, an e-mail or you can
move an application from A to B in the normal flow. Graph algorithms then answer questions the
dashboard asks:

* ``next_statuses`` - the neighbours of a node: what can happen next.
* ``steps_to`` - BFS shortest path: the fewest steps from here to an offer.
* ``can_reach`` - DFS reachability: is an offer still possible from this status? (No, once the
  application is rejected or withdrawn - those nodes have no way out.)
* ``is_expected`` - is this status change an edge of the graph? ``application_service.set_status``
  logs the ones that aren't, which is how unusual jumps (a manual edit, an out-of-order e-mail)
  show up in the logs without being blocked.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.dsa.graph import Graph
from app.models.enums import ApplicationStatus as S

STAGES: dict[str, list[S]] = {
    "Found": [S.DISCOVERED, S.MATCHED, S.SKIPPED],
    "Preparing": [S.PREPARING, S.PENDING_APPROVAL, S.APPROVED, S.FAILED],
    "Applied": [S.APPLIED, S.ACKNOWLEDGED],
    "Interviewing": [S.SCREENING, S.ASSESSMENT, S.INTERVIEW, S.FINAL_ROUND],
    "Outcome": [S.OFFER, S.ACCEPTED, S.REJECTED, S.WITHDRAWN],
}

LABELS: dict[S, str] = {
    S.DISCOVERED: "Discovered", S.MATCHED: "In Swipe Review", S.SKIPPED: "Skipped", S.PREPARING: "Preparing",
    S.PENDING_APPROVAL: "Needs approval", S.APPROVED: "Approved", S.FAILED: "Failed", S.APPLIED: "Applied",
    S.ACKNOWLEDGED: "Acknowledged", S.SCREENING: "Screening", S.ASSESSMENT: "Assessment", S.INTERVIEW: "Interview",
    S.FINAL_ROUND: "Final round", S.OFFER: "Offer", S.ACCEPTED: "Accepted", S.REJECTED: "Rejected",
    S.WITHDRAWN: "Withdrawn",
}

# who -> edges. "agent" = the pipeline, "you" = a click in the dashboard, "email" = a parsed reply.
_TRANSITIONS: list[tuple[S, S, str]] = [
    (S.DISCOVERED, S.MATCHED, "agent"), (S.DISCOVERED, S.SKIPPED, "agent"), (S.DISCOVERED, S.PREPARING, "agent"),
    (S.MATCHED, S.PREPARING, "you"), (S.MATCHED, S.SKIPPED, "you"), (S.MATCHED, S.APPLIED, "you"),
    (S.SKIPPED, S.MATCHED, "you"), (S.SKIPPED, S.DISCOVERED, "agent"), (S.SKIPPED, S.PREPARING, "you"),
    (S.SKIPPED, S.APPLIED, "you"), (S.DISCOVERED, S.APPLIED, "you"),
    (S.PREPARING, S.PENDING_APPROVAL, "agent"), (S.PREPARING, S.APPROVED, "agent"), (S.PREPARING, S.FAILED, "agent"),
    (S.PREPARING, S.MATCHED, "you"), (S.PREPARING, S.APPLIED, "you"),
    (S.PENDING_APPROVAL, S.APPROVED, "you"), (S.PENDING_APPROVAL, S.SKIPPED, "you"),
    (S.PENDING_APPROVAL, S.PREPARING, "you"), (S.PENDING_APPROVAL, S.APPLIED, "you"),
    (S.APPROVED, S.APPLIED, "agent"), (S.APPROVED, S.FAILED, "agent"), (S.APPROVED, S.PENDING_APPROVAL, "agent"),
    (S.FAILED, S.PREPARING, "you"), (S.FAILED, S.APPLIED, "you"), (S.FAILED, S.SKIPPED, "you"),
    (S.APPLIED, S.ACKNOWLEDGED, "email"), (S.APPLIED, S.SCREENING, "email"), (S.APPLIED, S.ASSESSMENT, "email"),
    (S.APPLIED, S.INTERVIEW, "email"), (S.APPLIED, S.REJECTED, "email"), (S.APPLIED, S.OFFER, "email"),
    (S.APPLIED, S.WITHDRAWN, "you"),
    (S.ACKNOWLEDGED, S.SCREENING, "email"), (S.ACKNOWLEDGED, S.ASSESSMENT, "email"),
    (S.ACKNOWLEDGED, S.INTERVIEW, "email"), (S.ACKNOWLEDGED, S.REJECTED, "email"),
    (S.ACKNOWLEDGED, S.OFFER, "email"), (S.ACKNOWLEDGED, S.WITHDRAWN, "you"),
    (S.SCREENING, S.ASSESSMENT, "email"), (S.SCREENING, S.INTERVIEW, "email"), (S.SCREENING, S.REJECTED, "email"),
    (S.SCREENING, S.OFFER, "email"), (S.SCREENING, S.WITHDRAWN, "you"),
    (S.ASSESSMENT, S.INTERVIEW, "email"), (S.ASSESSMENT, S.FINAL_ROUND, "email"),
    (S.ASSESSMENT, S.REJECTED, "email"), (S.ASSESSMENT, S.OFFER, "email"), (S.ASSESSMENT, S.WITHDRAWN, "you"),
    (S.INTERVIEW, S.FINAL_ROUND, "email"), (S.INTERVIEW, S.OFFER, "email"), (S.INTERVIEW, S.REJECTED, "email"),
    (S.INTERVIEW, S.WITHDRAWN, "you"),
    (S.FINAL_ROUND, S.OFFER, "email"), (S.FINAL_ROUND, S.REJECTED, "email"), (S.FINAL_ROUND, S.WITHDRAWN, "you"),
    (S.OFFER, S.ACCEPTED, "you"), (S.OFFER, S.REJECTED, "email"), (S.OFFER, S.WITHDRAWN, "you"),
]

TERMINAL = frozenset({S.ACCEPTED, S.REJECTED, S.WITHDRAWN})


@lru_cache(maxsize=1)
def lifecycle_graph() -> Graph[S]:
    graph: Graph[S] = Graph(directed=True)
    for stage, statuses in STAGES.items():
        for status in statuses:
            graph.add_node(status, label=LABELS[status], stage=stage, terminal=status in TERMINAL)
    for source, target, actor in _TRANSITIONS:
        graph.add_edge(source, target, 1.0, actor=actor)
    return graph


def next_statuses(status: S) -> list[S]:
    return lifecycle_graph().neighbors(status)


def is_expected(old: S | None, new: S) -> bool:
    return old is None or old == new or lifecycle_graph().has_edge(old, new)


def can_reach(status: S, goal: S) -> bool:
    return goal in lifecycle_graph().reachable(status)


def steps_to(status: S, goal: S = S.OFFER) -> list[S] | None:
    """Fewest status changes from ``status`` to ``goal`` (BFS), or ``None`` when it can't be reached."""
    return lifecycle_graph().shortest_path(status, goal)


def describe(status: S | None = None, visited: list[S] | None = None) -> dict[str, Any]:
    """The whole machine as nodes and edges; with ``status``, also where that application stands."""
    graph = lifecycle_graph()
    data = graph.to_dict()
    for node in data["nodes"]:
        node["id"] = node["id"].value
    for edge in data["edges"]:
        edge["source"], edge["target"] = edge["source"].value, edge["target"].value
    data["stages"] = [{"name": stage, "statuses": [s.value for s in statuses]} for stage, statuses in STAGES.items()]
    data["totals"] = {"states": len(graph), "transitions": graph.edge_count(), "terminal": len(TERMINAL)}
    if status is not None:
        path = steps_to(status)
        data["current"] = {
            "status": status.value,
            "label": LABELS[status],
            "next": [{"status": s.value, "label": LABELS[s], "actor": graph.edge_data(status, s).get("actor")}
                     for s in next_statuses(status)],
            "offer_reachable": can_reach(status, S.OFFER),
            "steps_to_offer": [s.value for s in path] if path else None,
            "visited": [s.value for s in (visited or [])],
        }
    return data
