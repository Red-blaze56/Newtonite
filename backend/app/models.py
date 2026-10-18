"""Database schema.

Enumerations (roles, statuses, ...) are Python StrEnums (app/enums.py) stored as VARCHAR
guarded by CHECK constraints, rather than native Postgres ENUM types: adding a value is then
a one-line constraint change in a migration instead of an ALTER TYPE, and the values stay
readable in raw SQL.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.enums import MEMBERSHIP_ROLES, ItemType, JobStatus, Priority, Role, Status


def text_enum(enum_cls: type[StrEnum], length: int = 30) -> SqlEnum:
    """A StrEnum column stored as VARCHAR holding the member's value ('lead', not 'LEAD').

    native_enum=False: no CREATE TYPE. The allowed values are enforced by our own named
    CHECK constraints (see _in), which SQLAlchemy does not add by default.
    """
    return SqlEnum(enum_cls, native_enum=False, length=length, values_callable=lambda e: [m.value for m in e])


def _in(column: str, values: tuple[StrEnum, ...]) -> str:
    # f-string, not repr(): repr of a StrEnum member is "<Role.LEAD: 'lead'>".
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


class Base(DeclarativeBase):
    # Deterministic constraint names, so migrations can reference (and later alter) them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-cased
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Membership(Base):
    """A user's role inside one team. A user can be in many teams with different roles."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint(_in("role", MEMBERSHIP_ROLES), name="role_valid"),
        Index("ix_memberships_team_role", "team_id", "role"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[Role] = mapped_column(text_enum(Role, 20))


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint(_in("type", tuple(ItemType)), name="type_valid"),
        CheckConstraint(_in("priority", tuple(Priority)), name="priority_valid"),
        CheckConstraint(_in("status", tuple(Status)), name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        # Default list: a team's items, newest activity first (keyset pagination on updated_at, id).
        Index("ix_items_team_updated", "team_id", text("updated_at DESC"), text("id DESC")),
        Index("ix_items_team_status_updated", "team_id", "status", text("updated_at DESC"), text("id DESC")),
        # "Assigned to me".
        Index("ix_items_assignee_status", "assignee_id", "status", text("updated_at DESC")),
        # "Unassigned urgent": small partial index, only rows that need an owner right now.
        Index(
            "ix_items_unassigned_urgent",
            "team_id",
            "priority_rank",
            text("updated_at DESC"),
            postgresql_where=text(
                "assignee_id IS NULL AND priority IN ('critical', 'high') "
                "AND status NOT IN ('resolved', 'closed')"
            ),
        ),
        # "Awaiting my approval".
        Index(
            "ix_items_awaiting_approval",
            "team_id",
            text("updated_at DESC"),
            postgresql_where=text("status = 'awaiting_approval'"),
        ),
        Index("ix_items_search", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    type: Mapped[ItemType] = mapped_column(text_enum(ItemType, 30))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    priority: Mapped[Priority] = mapped_column(text_enum(Priority, 20))
    # Sortable form of priority (critical=0 ... low=3), maintained by Postgres itself.
    priority_rank: Mapped[int] = mapped_column(
        SmallInteger,
        Computed(
            "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END",
            persisted=True,
        ),
    )
    status: Mapped[Status] = mapped_column(
        text_enum(Status, 30), default=Status.OPEN, server_default=Status.OPEN.value
    )
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optimistic-locking counter: every write bumps it; writes must name the version they read.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english'::regconfig, coalesce(description, '')), 'B')",
            persisted=True,
        ),
    )


class ActivityEvent(Base):
    """Append-only history of a work item. Comments are events too (kind='comment').

    Rows are only ever inserted, in the same transaction as the change they describe.
    """

    __tablename__ = "activity_events"
    __table_args__ = (Index("ix_activity_item", "item_id", text("id DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base):
    """Remembers the response to a mutating request so a retry gets the same answer."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (Index("ix_idempotency_created", "created_at"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(SmallInteger)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """Transactional outbox: secondary work written in the same transaction as the change."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", tuple(JobStatus)), name="status_valid"),
        Index("ix_jobs_due", "run_after", postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(60))
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
