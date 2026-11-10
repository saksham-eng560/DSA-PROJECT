"""The agent pipeline as a directed acyclic graph (DAG) of nodes.

Every stage of the agent is one node: what it does, the module that implements it, and the data
structures and algorithms it relies on. An edge ``A -> B`` means B needs A's output.

Because it is a DAG, graph algorithms describe the whole system:

* **Topological sort** (Kahn) gives a valid execution order.
* **Layers** group the nodes that have no dependency on each other - they can run in parallel
  (tailoring the resume, writing the cover letter and answering questions all need only the
  kept job).
* **Longest path** is the critical path: the chain of stages that decides how long one
  application takes end to end.

The dashboard's *System graph* page draws this, and ``tests/test_dsa_services.py`` checks that the
graph stays acyclic and that every module it names really exists, so the diagram can't drift
from the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.dsa.graph import Graph


@dataclass(frozen=True)
class PipelineNode:
    id: str
    label: str
    group: str  # Discover | Rank | Review | Prepare | Apply | Track | Assist
    summary: str
    module: str  # path under backend/app
    structures: tuple[str, ...] = ()  # data structures / algorithms used here
    ai: str = ""  # how AI is involved, if at all
    cost: float = 1.0  # relative time, used as the edge weight into this node
    needs: tuple[str, ...] = field(default_factory=tuple)


NODES: tuple[PipelineNode, ...] = (
    PipelineNode("resume", "Parse resume", "Discover",
                 "Reads your PDF / DOCX into structured JSON: contact, education, experience, projects, skills.",
                 "services/resume_parser.py", ("Aho-Corasick skill automaton", "hash set"),
                 ai="LLM with a JSON schema; keyword heuristics as fallback", cost=2),
    PipelineNode("sources", "Search job sources", "Discover",
                 "Queries every job board at once (thread pool) and collects raw postings.",
                 "services/agent/discovery.py", ("thread pool + queue of futures", "sliding-window rate limits"),
                 cost=5),
    PipelineNode("dedupe", "Dedupe and save", "Discover",
                 "Drops postings already saved and merges the same job from different sites.",
                 "services/agent/jobs.py", ("Bloom filter", "hash index on dedupe_key", "union-find clusters"),
                 needs=("sources",)),
    PipelineNode("filter", "Hard filters", "Rank",
                 "Removes what your settings rule out: wrong role level, avoided companies, expired deadlines.",
                 "services/job_matcher.py", ("hash sets", "compiled regular expressions"),
                 needs=("dedupe", "resume")),
    PipelineNode("embed", "Embed text", "Rank",
                 "Turns each job and your resume into a 1536-dimension vector for similarity search.",
                 "services/embeddings.py", ("feature hashing", "cosine similarity", "pgvector index"),
                 ai="embedding model (OpenAI / Ollama) or the built-in hashing embedder", needs=("dedupe", "resume"),
                 cost=2),
    PipelineNode("score", "Score the match", "Rank",
                 "Gives every job a 0-100 score in five parts, with strong matches and missing skills.",
                 "services/agent/scoring.py", ("skill graph (neighbour lookup)", "set intersection", "weighted sum"),
                 ai="LLM scores the best candidates; a rule-based scorer handles the rest", needs=("filter", "embed"),
                 cost=3),
    PipelineNode("rank", "Rank the deck", "Rank",
                 "Orders the deck: multi-key sort in SQL, best-K by heap, percentile by binary search.",
                 "services/insights.py", ("binary heap (top-K)", "stable multi-key sort", "binary search"),
                 needs=("score",)),
    PipelineNode("review", "Swipe Review", "Review",
                 "You keep or skip each card. Undo pops the last decision off a stack.",
                 "services/agent/review.py", ("stack (undo)", "state machine graph"), needs=("rank",)),
    PipelineNode("tailor", "Tailor resume", "Prepare",
                 "Reorders or rewrites the resume for the job, then checks nothing was invented.",
                 "services/resume_tailor.py", ("0/1 knapsack (one-page fit)", "LCS diff vs. the original"),
                 ai="LLM rewrite guarded by a truthfulness check", needs=("review",), cost=3),
    PipelineNode("cover", "Cover letter", "Prepare",
                 "Writes a short letter that refers to the actual role and company.",
                 "services/cover_letter.py", ai="LLM; a template when no model is configured", needs=("review",),
                 cost=2),
    PipelineNode("answers", "Answer questions", "Prepare",
                 "Fills every form question from saved answers, the resume, rules, then the model.",
                 "services/question_answerer.py", ("hash map of saved answers", "edit-distance option matching"),
                 ai="LLM for open-ended questions only; eligibility is never guessed", needs=("review",), cost=2),
    PipelineNode("fill", "Fill the form", "Apply",
                 "Opens the application in Chromium and fills each field, verifying the page kept the value.",
                 "submitters/form_engine.py", ("DOM tree traversal", "queue of form steps"),
                 needs=("tailor", "cover", "answers"), cost=4),
    PipelineNode("submit", "Approve and submit", "Apply",
                 "Submits once you approve (or automatically when every required answer is yours).",
                 "services/agent/submission.py", ("state machine graph", "token-bucket pacing"), needs=("fill",),
                 cost=2),
    PipelineNode("inbox", "Read replies", "Track",
                 "Classifies e-mails from employers: rejection, interview, assessment, offer.",
                 "services/email_parser.py", ("Naive Bayes classifier", "keyword rules"),
                 ai="LLM classification; Naive Bayes + rules as fallback", needs=("submit",), cost=2),
    PipelineNode("calendar", "Schedule interviews", "Track",
                 "Adds interviews to your calendar, spots clashes and finds free slots.",
                 "services/calendar_manager.py", ("interval sweep", "min-heap of end times"),
                 ai="LLM writes the interview prep notes", needs=("inbox",)),
    PipelineNode("analytics", "Analytics", "Track",
                 "Response, interview and offer rates; rolling activity; a response-likelihood model.",
                 "services/analytics.py", ("Fenwick tree (range sums)", "logistic regression", "k-means"),
                 ai="machine learning trained on your own history", needs=("inbox",)),
    PipelineNode("search", "Search index", "Assist",
                 "Keeps a search engine over your jobs: suggestions as you type and ranked full-text results.",
                 "services/search_index.py", ("trie", "inverted index + BM25", "LRU cache", "edit distance"),
                 needs=("dedupe",)),
    PipelineNode("copilot", "Career copilot", "Assist",
                 "A chatbot that looks up your jobs, applications and skill gaps before it answers.",
                 "services/copilot.py", ("inverted index retrieval", "ring buffer memory", "skill graph (Dijkstra)"),
                 ai="LLM answers grounded in retrieved data (RAG); rule-based answers as fallback",
                 needs=("search", "score", "analytics"), cost=2),
)

GROUPS = ("Discover", "Rank", "Review", "Prepare", "Apply", "Track", "Assist")


@lru_cache(maxsize=1)
def pipeline_graph() -> Graph[str]:
    graph: Graph[str] = Graph(directed=True)
    by_id = {node.id: node for node in NODES}
    for node in NODES:
        graph.add_node(node.id, label=node.label, group=node.group, summary=node.summary, module=node.module,
                       structures=list(node.structures), ai=node.ai, cost=node.cost)
    for node in NODES:
        for dependency in node.needs:
            if dependency not in by_id:
                raise KeyError(f"{node.id} needs unknown node {dependency}")
            graph.add_edge(dependency, node.id, node.cost)
    return graph


def describe() -> dict[str, Any]:
    graph = pipeline_graph()
    order = graph.topological_sort()
    layers = graph.layers()
    critical, length = graph.longest_path()
    data = graph.to_dict()
    layer_of = {node: index for index, layer in enumerate(layers) for node in layer}
    for node in data["nodes"]:
        node["layer"] = layer_of[node["id"]]
        node["order"] = order.index(node["id"])
        node["needs"] = graph.predecessors(node["id"])
        node["on_critical_path"] = node["id"] in critical
    data.update({
        "groups": list(GROUPS),
        "order": order,
        "layers": layers,
        "critical_path": critical,
        "critical_length": length,
        "totals": {"nodes": len(graph), "edges": graph.edge_count(), "layers": len(layers),
                   "ai_nodes": sum(1 for node in NODES if node.ai)},
    })
    return data
