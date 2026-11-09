"""Text helpers shared by the heuristic (no-LLM) code paths."""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable

from bs4 import BeautifulSoup

STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or our that the their this to we
    will with you your they them who what when where which while about into over under more most such
    than then there these those also can able across all any each other using use used work working
    team teams role roles job jobs company experience experiences year years strong good great new
    including include includes etc via per within without must should would could may might well
    plus skills skill ability abilities knowledge understanding responsibilities requirements
    qualifications preferred required minimum bonus nice help build building make making""".split()
)

# Common technical / professional skills used for heuristic matching when no LLM is configured.
SKILL_VOCABULARY: tuple[str, ...] = (
    # languages
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c", "c++", "c#", "ruby", "php",
    "kotlin", "swift", "objective-c", "scala", "r", "matlab", "julia", "perl", "haskell", "elixir", "dart",
    "sql", "bash", "shell", "powershell", "html", "css", "sass", "graphql", "solidity", "lua", "clojure",
    # web / frameworks
    "react", "react native", "next.js", "nextjs", "vue", "vue.js", "angular", "svelte", "redux", "node.js",
    "nodejs", "express", "nestjs", "django", "flask", "fastapi", "spring", "spring boot", "rails",
    "ruby on rails", "laravel", ".net", "asp.net", "tailwind", "tailwind css", "jquery", "webpack", "vite",
    "flutter", "android", "ios", "swiftui", "electron", "rest", "rest api", "restful", "grpc", "websockets",
    "microservices", "oauth", "jwt",
    # data / ml
    "machine learning", "deep learning", "nlp", "natural language processing", "computer vision", "llm",
    "llms", "generative ai", "pytorch", "tensorflow", "keras", "scikit-learn", "sklearn", "pandas", "numpy",
    "scipy", "spark", "pyspark", "hadoop", "airflow", "dbt", "kafka", "flink", "snowflake", "bigquery",
    "redshift", "databricks", "tableau", "power bi", "looker", "excel", "statistics", "data analysis",
    "data science", "data engineering", "etl", "a/b testing", "experimentation", "mlops", "hugging face",
    "langchain", "rag", "prompt engineering", "reinforcement learning", "recommendation systems",
    # databases
    "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis", "elasticsearch", "cassandra",
    "dynamodb", "oracle", "sql server", "neo4j", "firebase", "supabase", "pgvector",
    # cloud / devops
    "aws", "amazon web services", "gcp", "google cloud", "azure", "docker", "kubernetes", "k8s",
    "terraform", "ansible", "jenkins", "github actions", "gitlab ci", "ci/cd", "linux", "unix", "nginx",
    "serverless", "lambda", "ec2", "s3", "cloudformation", "helm", "prometheus", "grafana", "datadog",
    "observability", "sre", "devops", "networking", "security", "cybersecurity", "penetration testing",
    # practices / tools
    "git", "agile", "scrum", "kanban", "jira", "tdd", "unit testing", "testing", "pytest", "jest",
    "cypress", "playwright", "selenium", "system design", "distributed systems", "algorithms",
    "data structures", "object-oriented programming", "oop", "api design", "performance optimization",
    "accessibility", "figma", "ui/ux", "product management", "project management", "technical writing",
    "embedded systems", "fpga", "verilog", "robotics", "ros", "blockchain", "unity", "unreal engine",
    "sap", "salesforce", "hubspot", "seo", "marketing", "financial modeling", "accounting",
    # soft skills
    "communication", "leadership", "mentoring", "collaboration", "problem solving", "stakeholder management",
)

SOFT_SKILLS = frozenset(
    {"communication", "leadership", "mentoring", "collaboration", "problem solving", "stakeholder management"}
)

_SKILL_ALIASES = {
    "golang": "go",
    "nodejs": "node.js",
    "nextjs": "next.js",
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "sklearn": "scikit-learn",
    "amazon web services": "aws",
    "google cloud": "gcp",
    "restful": "rest api",
    "rest": "rest api",
    "llms": "llm",
    "vue.js": "vue",
    "natural language processing": "nlp",
}


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    if "<" not in value and "&" not in value:
        return value.strip()
    text = BeautifulSoup(html.unescape(value), "lxml").get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value.lower()).strip()


def tokenize(value: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9+#.\-/]*[a-z0-9+#]|[a-z0-9]", normalize_text(value)) if t not in STOPWORDS]


def canonical_skill(skill: str) -> str:
    s = normalize_text(skill).strip(" .,;:")
    return _SKILL_ALIASES.get(s, s)


def _skill_pattern(skill: str) -> re.Pattern[str]:
    escaped = re.escape(skill)
    return re.compile(rf"(?<![a-z0-9+#]){escaped}(?![a-z0-9+#])")


_PATTERNS = {s: _skill_pattern(s) for s in SKILL_VOCABULARY}


def extract_skills(text: str, extra_vocabulary: Iterable[str] = ()) -> list[str]:
    """Return canonical skills mentioned in ``text`` (vocabulary + caller-provided skills)."""
    norm = normalize_text(text)
    found: list[str] = []
    seen: set[str] = set()
    for skill, pattern in _PATTERNS.items():
        if len(skill) == 1:  # "c" / "r" only count when written as a standalone language mention
            if not re.search(rf"(?<![a-z0-9]){skill}(?:\s+programming|\s+language|,|/)", norm):
                continue
        elif not pattern.search(norm):
            continue
        canon = canonical_skill(skill)
        if canon not in seen:
            seen.add(canon)
            found.append(canon)
    for skill in extra_vocabulary:
        canon = canonical_skill(skill)
        if canon and canon not in seen and _skill_pattern(normalize_text(skill)).search(norm):
            seen.add(canon)
            found.append(canon)
    return found


def normalize_company(name: str | None) -> str:
    name = normalize_text(name or "")
    name = re.sub(r"[,.]|\b(inc|llc|ltd|corp|corporation|co|gmbh|plc|limited|technologies|labs)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def normalize_title(title: str | None) -> str:
    title = normalize_text(title or "")
    title = re.sub(r"\(.*?\)|\[.*?\]", " ", title)
    title = re.sub(r"\b(sr|snr)\b\.?", "senior", title)
    title = re.sub(r"\b(jr)\b\.?", "junior", title)
    title = re.sub(r"[^a-z0-9+# ]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def dedupe_key(company: str | None, title: str | None, location: str | None = None) -> str:
    loc = normalize_text(location or "").split(",")[0].strip()
    return f"{normalize_company(company)}|{normalize_title(title)}|{loc}"[:512]


def keyword_overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(tb)


def truncate(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def years_of_experience_required(text: str) -> int | None:
    matches = re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)", normalize_text(text))
    values = [int(m) for m in matches if 0 < int(m) < 30]
    return min(values) if values else None


_DISPLAY = {
    "javascript": "JavaScript", "typescript": "TypeScript", "node.js": "Node.js", "next.js": "Next.js", "vue": "Vue",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB", "graphql": "GraphQL", "fastapi": "FastAPI",
    "nestjs": "NestJS", "pytorch": "PyTorch", "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn", "numpy": "NumPy",
    "pyspark": "PySpark", "dynamodb": "DynamoDB", "bigquery": "BigQuery", "github actions": "GitHub Actions",
    "gitlab ci": "GitLab CI", "ci/cd": "CI/CD", "rest api": "REST APIs", "grpc": "gRPC", "ios": "iOS", "swiftui": "SwiftUI",
    "objective-c": "Objective-C", "c++": "C++", "c#": "C#", ".net": ".NET", "asp.net": "ASP.NET", "ui/ux": "UI/UX",
    "a/b testing": "A/B testing", "mlops": "MLOps", "llm": "LLMs", "nlp": "NLP", "oop": "OOP", "tdd": "TDD", "sre": "SRE",
    "devops": "DevOps", "jquery": "jQuery", "elasticsearch": "Elasticsearch", "redis": "Redis", "sql": "SQL",
    "aws": "AWS", "gcp": "GCP", "etl": "ETL", "html": "HTML", "css": "CSS", "php": "PHP", "sap": "SAP", "seo": "SEO",
    "jwt": "JWT", "oauth": "OAuth", "rag": "RAG", "ros": "ROS", "fpga": "FPGA", "k8s": "Kubernetes", "go": "Go",
    "power bi": "Power BI", "hugging face": "Hugging Face", "langchain": "LangChain", "pgvector": "pgvector",
}


def display_skill(skill: str) -> str:
    """Human-facing spelling of a canonical skill key ("postgresql" -> "PostgreSQL")."""
    key = canonical_skill(skill)
    if key in _DISPLAY:
        return _DISPLAY[key]
    if skill and skill != key:  # already user-provided casing
        return skill.strip()
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in key.split(" "))


def clip(text: str, limit: int) -> str:
    """Shorten to ``limit`` characters at a sentence or word boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in (". ", "! ", "? "):
        pos = cut.rfind(mark)
        if pos >= limit * 0.6:
            return cut[: pos + 1].strip()
    pos = cut.rfind(" ")
    return (cut[:pos] if pos >= limit * 0.6 else cut).rstrip(" ,;:-")
