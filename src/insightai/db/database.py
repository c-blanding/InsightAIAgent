"""Database helper wrapping SQLAlchemy engine, sessions, and table CRUD."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator, Optional
from urllib.parse import urlparse

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from insightai.db.models import AuthSession, Base, Run, RunArtifact, RunEvent, RunLog

_ARTIFACT_KINDS = frozenset({"screenshot", "video"})
_LOG_KINDS = frozenset({"console", "network", "info", "snapshot"})


class DatabaseError(Exception):
    """Raised when the database layer is misconfigured or a query fails."""


def _normalize_sqlalchemy_url(url: str) -> str:
    """Ensure the URL uses the psycopg3 SQLAlchemy dialect."""
    if url.startswith("postgresql+psycopg://") or url.startswith("postgres+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    raise DatabaseError("DATABASE_URL must be a postgresql:// URL")


def _is_pooled_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return "-pooler" in host


class Database:
    """Thin facade over SQLAlchemy for auth sessions and run artifacts.

    Typical usage::

        db = Database.from_env()
        db.create_tables()

        with db.session() as session:
            rows = session.scalars(select(RunArtifact)).all()

        db.upsert_auth_session(...)
        db.insert_run_artifact(...)
    """

    def __init__(self, url: str, *, echo: bool = False):
        raw = (url or "").strip()
        if not raw:
            raise DatabaseError("Database URL is empty")
        self.url = raw
        sa_url = _normalize_sqlalchemy_url(raw)
        engine_kwargs: dict[str, Any] = {
            "echo": echo,
            "pool_pre_ping": True,
        }
        # Neon pooled endpoints (PgBouncer) should not keep long-lived pools.
        if _is_pooled_url(raw):
            engine_kwargs["poolclass"] = NullPool
        self.engine: Engine = create_engine(sa_url, **engine_kwargs)
        self._Session = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    @classmethod
    def from_env(cls, *, echo: bool = False) -> Database:
        """Build from ``DATABASE_URL`` (pooled Neon URL preferred for app traffic)."""
        url = (os.environ.get("DATABASE_URL") or "").strip()
        if not url:
            raise DatabaseError("DATABASE_URL is not set")
        return cls(url, echo=echo)

    @classmethod
    def for_migrations(cls, *, echo: bool = False) -> Database:
        """Build from the direct (unpooled) URL when available — required for DDL."""
        url = (
            (os.environ.get("DATABASE_URL_UNPOOLED") or "").strip()
            or (os.environ.get("DATABASE_URL") or "").strip()
        )
        if not url:
            raise DatabaseError("DATABASE_URL_UNPOOLED or DATABASE_URL is not set")
        return cls(url, echo=echo)

    @classmethod
    def for_auth(cls, *, echo: bool = False) -> Database:
        """Prefer ``AUTH_DATABASE_URL``, otherwise fall back to ``DATABASE_URL``."""
        url = (
            (os.environ.get("AUTH_DATABASE_URL") or "").strip()
            or (os.environ.get("DATABASE_URL") or "").strip()
        )
        if not url:
            raise DatabaseError("AUTH_DATABASE_URL or DATABASE_URL is not set")
        return cls(url, echo=echo)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Yield a session that commits on success and rolls back on error."""
        session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_tables(self) -> None:
        """Create all mapped tables if they do not already exist (once per instance)."""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            Base.metadata.create_all(self.engine)
            self._ensure_run_logs_snapshot_kind()
            self._schema_ready = True

    def _ensure_run_logs_snapshot_kind(self) -> None:
        """Widen run_logs.kind CHECK so existing DBs accept ``snapshot`` rows."""
        from sqlalchemy import text as sa_text

        stmts = (
            "ALTER TABLE run_logs DROP CONSTRAINT IF EXISTS run_logs_kind_check",
            "ALTER TABLE run_logs ADD CONSTRAINT run_logs_kind_check "
            "CHECK (kind IN ('console', 'network', 'info', 'snapshot'))",
        )
        try:
            with self.engine.begin() as conn:
                for stmt in stmts:
                    conn.execute(sa_text(stmt))
        except Exception:
            # Table may not exist yet on a fresh DB, or role lacks ALTER — inserts
            # still work when create_all built the table from the updated model.
            pass

    def dispose(self) -> None:
        self.engine.dispose()

    # ------------------------------------------------------------------
    # Auth sessions
    # ------------------------------------------------------------------

    def upsert_auth_session(
        self,
        *,
        profile_id: str,
        origin: str,
        ciphertext: bytes,
        nonce: bytes,
        key_version: int,
        expires_at: datetime,
    ) -> AuthSession:
        """Insert or replace the encrypted session for ``profile_id``."""
        values = {
            "profile_id": profile_id,
            "origin": origin,
            "ciphertext": ciphertext,
            "nonce": nonce,
            "key_version": key_version,
            "expires_at": expires_at,
        }
        stmt = (
            pg_insert(AuthSession)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[AuthSession.profile_id],
                set_={
                    "origin": origin,
                    "ciphertext": ciphertext,
                    "nonce": nonce,
                    "key_version": key_version,
                    "expires_at": expires_at,
                    "created_at": func.now(),
                },
            )
            .returning(AuthSession)
        )
        with self.session() as session:
            row = session.scalars(stmt).one()
            session.expunge(row)
            return row

    def get_auth_session(
        self,
        profile_id: str,
        *,
        include_expired: bool = False,
    ) -> Optional[AuthSession]:
        """Return the session row for ``profile_id``, or ``None``."""
        with self.session() as session:
            stmt = select(AuthSession).where(AuthSession.profile_id == profile_id)
            if not include_expired:
                stmt = stmt.where(AuthSession.expires_at > func.now())
            row = session.scalar(stmt)
            if row is not None:
                session.expunge(row)
            return row

    def list_auth_sessions(self, *, include_expired: bool = False) -> list[AuthSession]:
        with self.session() as session:
            stmt = select(AuthSession).order_by(AuthSession.profile_id)
            if not include_expired:
                stmt = stmt.where(AuthSession.expires_at > func.now())
            rows = list(session.scalars(stmt).all())
            for row in rows:
                session.expunge(row)
            return rows

    def delete_auth_session(self, profile_id: str) -> bool:
        with self.session() as session:
            result = session.execute(
                delete(AuthSession).where(AuthSession.profile_id == profile_id)
            )
            return (result.rowcount or 0) > 0

    def purge_expired_auth_sessions(self) -> int:
        with self.session() as session:
            result = session.execute(
                delete(AuthSession).where(AuthSession.expires_at < func.now())
            )
            return result.rowcount or 0

    # ------------------------------------------------------------------
    # Run artifacts
    # ------------------------------------------------------------------

    def insert_run_artifact(
        self,
        *,
        thread_id: str,
        kind: str,
        bucket: str,
        object_key: str,
        step: int | None = None,
        content_type: str | None = None,
        byte_size: int | None = None,
    ) -> RunArtifact:
        if kind not in _ARTIFACT_KINDS:
            raise DatabaseError(
                f"kind must be one of {sorted(_ARTIFACT_KINDS)}, got {kind!r}"
            )
        with self.session() as session:
            row = RunArtifact(
                thread_id=thread_id,
                step=step,
                kind=kind,
                bucket=bucket,
                object_key=object_key,
                content_type=content_type,
                byte_size=byte_size,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            session.expunge(row)
            return row

    def list_run_artifacts(
        self,
        thread_id: str,
        *,
        kind: str | None = None,
        step: int | None = None,
    ) -> list[RunArtifact]:
        with self.session() as session:
            stmt = (
                select(RunArtifact)
                .where(RunArtifact.thread_id == thread_id)
                .order_by(RunArtifact.created_at)
            )
            if kind is not None:
                stmt = stmt.where(RunArtifact.kind == kind)
            if step is not None:
                stmt = stmt.where(RunArtifact.step == step)
            rows = list(session.scalars(stmt).all())
            for row in rows:
                session.expunge(row)
            return rows

    def delete_run_artifacts(self, thread_id: str) -> int:
        with self.session() as session:
            result = session.execute(
                delete(RunArtifact).where(RunArtifact.thread_id == thread_id)
            )
            return result.rowcount or 0

    # ------------------------------------------------------------------
    # Run logs (console / network / info)
    # ------------------------------------------------------------------

    def insert_run_log(
        self,
        *,
        thread_id: str,
        kind: str,
        message: str,
        step: int | None = None,
        source: str | None = None,
    ) -> RunLog:
        if kind not in _LOG_KINDS:
            raise DatabaseError(
                f"kind must be one of {sorted(_LOG_KINDS)}, got {kind!r}"
            )
        with self.session() as session:
            row = RunLog(
                thread_id=thread_id,
                step=step,
                kind=kind,
                source=source,
                message=message,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            session.expunge(row)
            return row

    def insert_run_logs(
        self,
        *,
        thread_id: str,
        kind: str,
        messages: list[str],
        step: int | None = None,
        source: str | None = None,
    ) -> list[RunLog]:
        if kind not in _LOG_KINDS:
            raise DatabaseError(
                f"kind must be one of {sorted(_LOG_KINDS)}, got {kind!r}"
            )
        if not messages:
            return []
        with self.session() as session:
            rows = [
                RunLog(
                    thread_id=thread_id,
                    step=step,
                    kind=kind,
                    source=source,
                    message=message,
                )
                for message in messages
            ]
            session.add_all(rows)
            session.flush()
            for row in rows:
                session.refresh(row)
                session.expunge(row)
            return rows

    def list_run_logs(
        self,
        thread_id: str,
        *,
        kind: str | None = None,
        step: int | None = None,
    ) -> list[RunLog]:
        with self.session() as session:
            stmt = (
                select(RunLog)
                .where(RunLog.thread_id == thread_id)
                .order_by(RunLog.created_at)
            )
            if kind is not None:
                stmt = stmt.where(RunLog.kind == kind)
            if step is not None:
                stmt = stmt.where(RunLog.step == step)
            rows = list(session.scalars(stmt).all())
            for row in rows:
                session.expunge(row)
            return rows

    def delete_run_logs(self, thread_id: str, *, kind: str | None = None) -> int:
        with self.session() as session:
            stmt = delete(RunLog).where(RunLog.thread_id == thread_id)
            if kind is not None:
                stmt = stmt.where(RunLog.kind == kind)
            result = session.execute(stmt)
            return result.rowcount or 0

    # ------------------------------------------------------------------
    # Run events (timeline / action log)
    # ------------------------------------------------------------------

    def insert_run_event(
        self,
        *,
        thread_id: str,
        event_type: str,
        title: str,
        step: int | None = None,
        tool: str | None = None,
        detail: str | None = None,
        status: str = "ok",
        ref_kind: str | None = None,
        ref_bucket: str | None = None,
        ref_object_key: str | None = None,
    ) -> RunEvent:
        with self.session() as session:
            row = RunEvent(
                thread_id=thread_id,
                step=step,
                event_type=event_type,
                tool=tool,
                title=title,
                detail=detail,
                status=status or "ok",
                ref_kind=ref_kind,
                ref_bucket=ref_bucket,
                ref_object_key=ref_object_key,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            session.expunge(row)
            return row

    def list_run_events(
        self,
        thread_id: str,
        *,
        step: int | None = None,
        event_type: str | None = None,
    ) -> list[RunEvent]:
        with self.session() as session:
            stmt = (
                select(RunEvent)
                .where(RunEvent.thread_id == thread_id)
                .order_by(RunEvent.created_at, RunEvent.id)
            )
            if step is not None:
                stmt = stmt.where(RunEvent.step == step)
            if event_type is not None:
                stmt = stmt.where(RunEvent.event_type == event_type)
            rows = list(session.scalars(stmt).all())
            for row in rows:
                session.expunge(row)
            return rows

    def delete_run_events(self, thread_id: str) -> int:
        with self.session() as session:
            result = session.execute(
                delete(RunEvent).where(RunEvent.thread_id == thread_id)
            )
            return result.rowcount or 0

    # ------------------------------------------------------------------
    # Runs (persisted QA run summaries)
    # ------------------------------------------------------------------

    def upsert_run(
        self,
        *,
        thread_id: str,
        bug_description: str,
        url: str,
        expected_behavior: str | None = None,
        plan: dict[str, Any] | None = None,
        step_findings: list[Any] | None = None,
        timeline: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> Run:
        findings = step_findings if step_findings is not None else []
        values = {
            "thread_id": thread_id,
            "bug_description": bug_description,
            "url": url,
            "expected_behavior": expected_behavior,
            "plan": plan,
            "step_findings": findings,
            "timeline": timeline,
            "report": report,
            "error": error,
            "completed": completed,
        }
        stmt = (
            pg_insert(Run)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Run.thread_id],
                set_={
                    "bug_description": bug_description,
                    "url": url,
                    "expected_behavior": expected_behavior,
                    "plan": plan,
                    "step_findings": findings,
                    "timeline": timeline,
                    "report": report,
                    "error": error,
                    "completed": completed,
                    "updated_at": func.now(),
                },
            )
            .returning(Run)
        )
        with self.session() as session:
            row = session.scalars(stmt).one()
            session.expunge(row)
            return row

    def get_run(self, thread_id: str) -> Optional[Run]:
        with self.session() as session:
            row = session.scalar(select(Run).where(Run.thread_id == thread_id))
            if row is not None:
                session.expunge(row)
            return row

    def list_runs(self, *, limit: int = 50) -> list[Run]:
        with self.session() as session:
            stmt = select(Run).order_by(Run.created_at.desc()).limit(max(1, limit))
            rows = list(session.scalars(stmt).all())
            for row in rows:
                session.expunge(row)
            return rows

    def delete_run(self, thread_id: str) -> bool:
        with self.session() as session:
            result = session.execute(delete(Run).where(Run.thread_id == thread_id))
            return (result.rowcount or 0) > 0


_default_db: Database | None = None
_auth_db: Database | None = None
_db_lock = threading.Lock()


def get_database(*, refresh: bool = False) -> Database:
    """Process-wide ``Database`` singleton from ``DATABASE_URL``."""
    global _default_db
    with _db_lock:
        if _default_db is None or refresh:
            _default_db = Database.from_env()
        return _default_db


def get_auth_database(*, refresh: bool = False) -> Database:
    """Process-wide auth ``Database`` singleton (``AUTH_DATABASE_URL`` or ``DATABASE_URL``)."""
    global _auth_db
    with _db_lock:
        if _auth_db is None or refresh:
            _auth_db = Database.for_auth()
        return _auth_db
