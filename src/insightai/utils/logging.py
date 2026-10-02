"""Database-backed run logging for console, network, and info messages."""

from __future__ import annotations

import logging
import os
from typing import Iterable

from insightai.auth.redact import redact_text
from insightai.db.database import Database, DatabaseError, get_database
from insightai.db.models import RunLog

_LOG_KINDS = frozenset({"console", "network", "info", "snapshot"})
_stdlib = logging.getLogger(__name__)


class Logging:
    """Persist redacted console / network / snapshot / info logs for a run thread.

    Best-effort: missing ``DATABASE_URL`` or insert failures never raise to callers.
    """

    def __init__(
        self,
        name: str = "insight",
        *,
        thread_id: str,
        step: int | None = None,
        db: Database | None = None,
    ):
        if not (thread_id or "").strip():
            raise ValueError("thread_id is required")
        self.name = name
        self.thread_id = thread_id.strip()
        self.step = step
        self._db = db

    def _database(self) -> Database | None:
        if self._db is not None:
            try:
                self._db.create_tables()
            except Exception:
                _stdlib.debug("create_tables failed", exc_info=True)
                return None
            return self._db
        if not (os.environ.get("DATABASE_URL") or "").strip():
            return None
        try:
            db = get_database()
            db.create_tables()
            return db
        except DatabaseError:
            return None
        except Exception:
            _stdlib.debug("get_database failed", exc_info=True)
            return None

    def log(
        self,
        message: str,
        *,
        kind: str = "info",
        step: int | None = None,
    ) -> RunLog | None:
        if kind not in _LOG_KINDS:
            raise ValueError(f"kind must be one of {sorted(_LOG_KINDS)}, got {kind!r}")
        text = redact_text(message if isinstance(message, str) else str(message))
        db = self._database()
        if db is None:
            _stdlib.info("[%s] %s", kind, text[:500])
            return None
        try:
            return db.insert_run_log(
                thread_id=self.thread_id,
                kind=kind,
                message=text,
                step=self.step if step is None else step,
                source=self.name,
            )
        except Exception:
            _stdlib.debug("insert_run_log failed", exc_info=True)
            return None

    def console(self, message: str, *, step: int | None = None) -> RunLog | None:
        return self.log(message, kind="console", step=step)

    def network(self, message: str, *, step: int | None = None) -> RunLog | None:
        return self.log(message, kind="network", step=step)

    def info(self, message: str, *, step: int | None = None) -> RunLog | None:
        return self.log(message, kind="info", step=step)

    def snapshot(self, message: str, *, step: int | None = None) -> RunLog | None:
        return self.log(message, kind="snapshot", step=step)

    def warning(self, message: str, *, step: int | None = None) -> RunLog | None:
        # Persist warnings as info (CHECK allows console|network|info|snapshot).
        return self.log(f"[warning] {message}", kind="info", step=step)

    def extend(
        self,
        messages: Iterable[str],
        *,
        kind: str = "info",
        step: int | None = None,
    ) -> list[RunLog]:
        if kind not in _LOG_KINDS:
            raise ValueError(f"kind must be one of {sorted(_LOG_KINDS)}, got {kind!r}")
        cleaned = [
            redact_text(item if isinstance(item, str) else str(item)) for item in messages
        ]
        if not cleaned:
            return []
        db = self._database()
        if db is None:
            return []
        try:
            return db.insert_run_logs(
                thread_id=self.thread_id,
                kind=kind,
                messages=cleaned,
                step=self.step if step is None else step,
                source=self.name,
            )
        except Exception:
            _stdlib.debug("insert_run_logs failed", exc_info=True)
            return []

    def get_log(
        self,
        *,
        kind: str | None = None,
        step: int | None = None,
    ) -> list[RunLog]:
        db = self._database()
        if db is None:
            return []
        return db.list_run_logs(
            self.thread_id,
            kind=kind,
            step=self.step if step is None else step,
        )

    def clear_log(self, *, kind: str | None = None) -> int:
        db = self._database()
        if db is None:
            return 0
        return db.delete_run_logs(self.thread_id, kind=kind)
