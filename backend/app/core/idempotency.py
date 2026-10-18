"""Run a mutation exactly once per Idempotency-Key, and commit it.

Every mutating endpoint goes through `execute_mutation`. With an `Idempotency-Key` header:

  1. In the request's transaction, reserve the key:
         INSERT INTO idempotency_keys ... ON CONFLICT DO NOTHING
     If another request with the same key is still running, Postgres makes this INSERT wait
     until that transaction finishes. So:
       - inserted        -> we are first: run the mutation.
       - not inserted    -> the first request committed: replay its stored response.
         (If the first one rolled back, its reservation vanished and our INSERT succeeds.)
  2. Run the mutation, then store its response in the reserved row.
  3. Commit once: the change, its activity event, its outbox job and the stored response
     become visible together, or not at all.

Errors are not stored: a failed request rolls back (including the reservation), so retrying
it re-evaluates against the current state.

The same key with a different request body is a client bug and gets 422.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import DateTime, ForeignKey, Index, SmallInteger, String, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base
from app.core.errors import Unprocessable

MAX_KEY_LENGTH = 200


class IdempotencyKey(Base):
    """Remembers the response to a mutating request so a retry gets the same answer."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (Index("ix_idempotency_created", "created_at"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(SmallInteger)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def request_fingerprint(method: str, path: str, body: Any) -> str:
    canonical = json.dumps(jsonable_encoder(body), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method} {path}\n{canonical}".encode()).hexdigest()


def execute_mutation(
    session: Session,
    *,
    user_id: int,
    key: str | None,
    fingerprint: str,
    action: Callable[[], tuple[int, Any]],
) -> JSONResponse:
    """Run `action` (returns (status_code, body)) at most once per key, then commit."""
    if key is not None and not (0 < len(key) <= MAX_KEY_LENGTH):
        raise Unprocessable(f"Idempotency-Key must be 1-{MAX_KEY_LENGTH} characters", code="invalid_idempotency_key")
    try:
        if key is not None:
            reserved = session.execute(
                insert(IdempotencyKey)
                .values(user_id=user_id, key=key, request_hash=fingerprint, status_code=0, response={})
                .on_conflict_do_nothing(index_elements=["user_id", "key"])
                .returning(IdempotencyKey.key)
            ).first()
            if reserved is None:
                stored = session.execute(
                    select(IdempotencyKey.request_hash, IdempotencyKey.status_code, IdempotencyKey.response).where(
                        IdempotencyKey.user_id == user_id, IdempotencyKey.key == key
                    )
                ).one()
                session.rollback()
                if stored.request_hash != fingerprint:
                    raise Unprocessable(
                        "This Idempotency-Key was already used for a different request",
                        code="idempotency_key_reused",
                    )
                return JSONResponse(stored.response, status_code=stored.status_code, headers={"Idempotent-Replayed": "true"})

        status_code, body = action()
        content = jsonable_encoder(body)
        if key is not None:
            session.execute(
                update(IdempotencyKey)
                .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
                .values(status_code=status_code, response=content)
            )
        session.commit()
        return JSONResponse(content, status_code=status_code)
    except Exception:
        session.rollback()
        raise
