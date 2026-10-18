"""Import every table so Base.metadata knows the whole schema.

Used by Alembic (autogenerate compares this metadata with the database) and by scripts that
run outside the web app. Each feature owns its tables; this file only gathers them.
"""

from app.auth.models import Membership, Team, User
from app.core.db import Base
from app.core.idempotency import IdempotencyKey
from app.items.models import ActivityEvent, WorkItem
from app.notifications.models import Job, Notification

__all__ = [
    "Base",
    "User",
    "Team",
    "Membership",
    "WorkItem",
    "ActivityEvent",
    "IdempotencyKey",
    "Job",
    "Notification",
]
