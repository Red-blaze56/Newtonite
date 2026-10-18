"""Tables of the notifications feature: the outbox of background jobs, and the notifications they produce."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import app.items.models  # noqa: F401  (registers users and work_items, the targets of our foreign keys)
from app.core.db import Base, one_of, text_enum
from app.notifications.enums import JobKind, JobStatus


class Job(Base):
    """Transactional outbox: secondary work written in the same transaction as the change."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(one_of("status", tuple(JobStatus)), name="status_valid"),
        Index("ix_jobs_due", "run_after", postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[JobKind] = mapped_column(text_enum(JobKind, 60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Same logical job enqueued twice (e.g. a retried request) collapses into one row.
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[JobStatus] = mapped_column(
        text_enum(JobStatus, 20), default=JobStatus.PENDING, server_default=JobStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default=text("5"))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user", "user_id", text("id DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    # Unique: running the same job twice can never create a second notification.
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
