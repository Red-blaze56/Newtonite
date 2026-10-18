"""The single source of every enumerated value in the system.

StrEnum members *are* strings: Role.LEAD == "lead", they serialise to "lead" in JSON, are
stored as 'lead' in Postgres, and format as "lead" in f-strings. Typos (Role.LAED) fail at
import time instead of silently never matching.

Kept out of models.py so API schemas and business rules can use them without importing the ORM.
"""

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    MEMBER = "member"
    LEAD = "lead"
    # Effective role of a global admin. Never stored in memberships (see MEMBERSHIP_ROLES).
    ADMIN = "admin"


# Roles that may appear in the memberships table.
MEMBERSHIP_ROLES = (Role.VIEWER, Role.MEMBER, Role.LEAD)


class ItemType(StrEnum):
    INCIDENT = "incident"
    CUSTOMER_ISSUE = "customer_issue"
    PAYMENT = "payment"
    COMPLIANCE = "compliance"
    TASK = "task"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    CLOSED = "closed"


class JobStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class EventKind(StrEnum):
    """Kinds of activity_events rows. One event per mutation (comments included)."""

    CREATED = "created"
    EDITED = "edited"  # payload: {"changes": {field: {"from": ..., "to": ...}}}
    ASSIGNEE_CHANGED = "assignee_changed"  # payload: {"from", "to", "via": claim|assign|release}
    STATUS_CHANGED = "status_changed"  # payload: {"from", "to", "note"}
    COMMENT = "comment"  # payload: {"body"}


class JobKind(StrEnum):
    """Background jobs written to the outbox in the same transaction as the change."""

    NOTIFY_ASSIGNED = "notify.assigned"
    NOTIFY_APPROVAL_REQUESTED = "notify.approval_requested"
    NOTIFY_APPROVAL_DECIDED = "notify.approval_decided"
