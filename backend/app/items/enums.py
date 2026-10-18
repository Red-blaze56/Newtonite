"""Enumerated values of the items feature.

StrEnum members *are* strings: Status.OPEN == "open", they serialise to "open" in JSON, are
stored as 'open' in Postgres, and format as "open" in f-strings. Typos (Status.OPNE) fail at
import time instead of silently never matching.

Kept out of models.py so schemas and business rules can use them without importing the ORM.
"""

from enum import StrEnum


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


class EventKind(StrEnum):
    """Kinds of activity_events rows. One event per mutation (comments included)."""

    CREATED = "created"
    EDITED = "edited"  # payload: {"changes": {field: {"from": ..., "to": ...}}}
    ASSIGNEE_CHANGED = "assignee_changed"  # payload: {"from", "to", "via": claim|assign|release}
    STATUS_CHANGED = "status_changed"  # payload: {"from", "to", "note"}
    COMMENT = "comment"  # payload: {"body"}
