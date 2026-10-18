"""Enumerated values of the notifications feature. See app/items/enums.py for why these are StrEnums."""

from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class JobKind(StrEnum):
    """Background jobs written to the outbox in the same transaction as the change."""

    NOTIFY_ASSIGNED = "notify.assigned"
    NOTIFY_APPROVAL_REQUESTED = "notify.approval_requested"
    NOTIFY_APPROVAL_DECIDED = "notify.approval_decided"
