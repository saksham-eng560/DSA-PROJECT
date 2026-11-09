"""SQLAlchemy engine, session factory and portable column types.

The schema targets PostgreSQL 16 + pgvector in production, but every custom type
degrades gracefully on SQLite so the test-suite and quick local trials work without
any external services.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Text, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.config import settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- types
class UTCDateTime(TypeDecorator):
    """Timezone-aware datetime that always round-trips as UTC (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


JSONType = JSON().with_variant(JSONB(), "postgresql")


class EmbeddingType(TypeDecorator):
    """pgvector ``vector(n)`` on PostgreSQL, JSON-encoded list elsewhere."""

    impl = Text
    cache_ok = True

    def __init__(self, dim: int = 1536, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dim = dim

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        values = [float(v) for v in value]
        if dialect.name == "postgresql":
            return values
        return json.dumps(values)

    def process_result_value(self, value: Any, dialect: Any) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            if value.startswith("["):
                return [float(v) for v in json.loads(value)]
            return None
        return [float(v) for v in value]

    class comparator_factory(TypeDecorator.Comparator):  # noqa: N801 - SQLAlchemy naming
        def cosine_distance(self, other: Any) -> Any:
            from sqlalchemy import Float

            return self.op("<=>", return_type=Float)(other)


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- base
class Base(DeclarativeBase):
    type_annotation_map: dict[Any, Any] = {}


# --------------------------------------------------------------------------- engine
def _build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # Background tasks and API requests share the file: wait for the writer instead of failing fast.
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        eng = create_engine(url, **kwargs)

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if url not in ("sqlite://", "sqlite:///:memory:"):
                cursor.execute("PRAGMA journal_mode=WAL")  # readers never block on the writer
                cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        return eng
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_POOL_SIZE * 2,
        pool_recycle=1800,
    )


engine: Engine = _build_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def configure_engine(url: str) -> Engine:
    """Re-point the global engine/session factory (used by tests and scripts)."""
    global engine
    engine = _build_engine(url)
    SessionLocal.configure(bind=engine)
    return engine


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def checkpoint(db: Session) -> None:
    """Commit progress before a long network / browser step so the write lock isn't held throughout.

    With SQLite (local mode) one open write transaction blocks every other writer, e.g. your swipes
    while a scan runs. With PostgreSQL it simply makes progress visible to the dashboard sooner.
    """
    if db.in_transaction():
        db.commit()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for workers and scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def wait_for_db(retries: int | None = None, delay: float = 1.0) -> None:
    """Block until the database accepts connections (connection-pool retry policy: 5 x 1s)."""
    attempts = retries if retries is not None else settings.DB_CONNECT_RETRIES
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            if attempt == attempts:
                raise
            logger.warning("Database not ready (attempt %s/%s): %s", attempt, attempts, exc)
            time.sleep(delay)


def create_all() -> None:
    """Create tables directly (SQLite / tests). Production uses Alembic migrations."""
    from app import models  # noqa: F401 - register models

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        _add_missing_sqlite_columns()


def _add_missing_sqlite_columns() -> None:
    """Local SQLite databases have no Alembic history: add columns introduced by newer versions."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(dialect=engine.dialect)}'
                if column.server_default is not None:
                    default = column.server_default.arg
                    default_sql = default.compile(dialect=engine.dialect) if hasattr(default, "compile") else f"'{default}'"
                    ddl += f" NOT NULL DEFAULT {default_sql}" if not column.nullable else f" DEFAULT {default_sql}"
                conn.execute(text(ddl))
                logger.info("Added column %s.%s", table.name, column.name)
