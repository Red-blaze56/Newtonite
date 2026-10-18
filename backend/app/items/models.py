"""Tables of the items feature: work items and their append-only history."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

import app.auth.models  # noqa: F401  (registers users and teams, the targets of our foreign keys)
from app.core.db import Base, one_of, text_enum
from app.items.enums import EventKind, ItemType, Priority, Status


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint(one_of("type", tuple(ItemType)), name="type_valid"),
        CheckConstraint(one_of("priority", tuple(Priority)), name="priority_valid"),
        CheckConstraint(one_of("status", tuple(Status)), name="status_valid"),
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
        # "Overdue": only unfinished items that have a due date.
        Index(
            "ix_items_overdue",
            "team_id",
            "due_at",
            postgresql_where=text("due_at IS NOT NULL AND status NOT IN ('resolved', 'closed')"),
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
    kind: Mapped[EventKind] = mapped_column(text_enum(EventKind, 40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
