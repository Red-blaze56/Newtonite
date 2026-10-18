"""Queue background work. Called by other features inside their own transaction."""

from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.notifications.enums import JobKind
from app.notifications.models import Job


def enqueue(session: Session, kind: JobKind, payload: dict[str, Any], dedupe_key: str) -> None:
    """Write a job row in the caller's transaction: it exists if and only if the change commits.

    ON CONFLICT DO NOTHING on dedupe_key: the same logical job is only ever queued once.
    """
    session.execute(
        insert(Job)
        .values(kind=kind, payload=jsonable_encoder(payload), dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
