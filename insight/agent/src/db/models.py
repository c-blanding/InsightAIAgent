"""SQLAlchemy ORM models for project tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuthSession(Base):
    """Encrypted Playwright storage-state blob keyed by profile_id."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("auth_sessions_expires_at_idx", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    profile_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunArtifact(Base):
    """Metadata for screenshot and video evidence in object storage."""

    __tablename__ = "run_artifacts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('screenshot', 'video')",
            name="run_artifacts_kind_check",
        ),
        Index("run_artifacts_thread_step_idx", "thread_id", "step"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    byte_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RunLog(Base):
    """Console, network, snapshot, and info logs for a QA run (text, not blobs)."""

    __tablename__ = "run_logs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('console', 'network', 'info', 'snapshot')",
            name="run_logs_kind_check",
        ),
        Index("run_logs_thread_step_idx", "thread_id", "step"),
        Index("run_logs_thread_kind_idx", "thread_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
