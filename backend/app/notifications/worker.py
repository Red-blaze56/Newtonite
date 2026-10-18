"""Background worker: processes jobs from the outbox table.

    uv run python -m app.notifications.worker          # run forever (Ctrl+C to stop)
    uv run python -m app.notifications.worker --once   # drain due jobs, then exit

How a job is processed (one job, one transaction):
  1. SELECT ... FOR UPDATE SKIP LOCKED  -> take one due job; other workers skip it.
  2. Run its handler inside a SAVEPOINT.
       success -> mark it done.
       failure -> undo the handler's writes, count the attempt, schedule a retry with
                  exponential backoff, or mark it failed after max_attempts.
  3. COMMIT. The handler's effects and the job's new status are saved together.

What happens when things go wrong:
  * Worker crashes mid-job: the transaction never commits, Postgres releases the row lock,
    and the job is simply picked up again. No lease timeouts, no stuck "running" jobs.
  * Job runs twice anyway (e.g. crash after commit but before logging): handlers are
    idempotent. Each notification has a unique dedupe_key, so a repeat inserts nothing.
  * Several workers: SKIP LOCKED gives each one a different job; none ever wait on each other.
"""

import argparse
import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import app.all_models  # noqa: F401  (registers every table)
from app.auth.enums import Role
from app.auth.models import Membership, User
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.idempotency import IdempotencyKey
from app.items.models import WorkItem
from app.notifications.enums import JobKind, JobStatus
from app.notifications.models import Job, Notification

log = logging.getLogger("worker")

BACKOFF_BASE_SECONDS = 5
BACKOFF_MAX_SECONDS = 300
IDEMPOTENCY_KEY_TTL = timedelta(hours=24)
CLEANUP_EVERY = timedelta(minutes=10)


# --- handlers: one per job kind. Each must be safe to run more than once. -----------------------


def _notify(session: Session, job: Job, user_id: int, item_id: int | None, message: str) -> None:
    session.execute(
        insert(Notification)
        .values(user_id=user_id, item_id=item_id, message=message, dedupe_key=f"{job.dedupe_key}:u{user_id}")
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


def _names(session: Session, item_id: int, actor_id: int) -> tuple[str, str]:
    title = session.scalar(select(WorkItem.title).where(WorkItem.id == item_id)) or f"item #{item_id}"
    actor = session.scalar(select(User.name).where(User.id == actor_id)) or "Someone"
    return title, actor


def handle_assigned(session: Session, job: Job) -> None:
    p = job.payload
    title, actor = _names(session, p["item_id"], p["actor_id"])
    _notify(session, job, p["user_id"], p["item_id"], f'{actor} assigned you "{title}"')


def handle_approval_requested(session: Session, job: Job) -> None:
    p = job.payload
    title, actor = _names(session, p["item_id"], p["actor_id"])
    # Leads are resolved when the job runs, so a lead added in the meantime is included.
    leads = session.scalars(
        select(Membership.user_id).where(Membership.team_id == p["team_id"], Membership.role == Role.LEAD)
    ).all()
    for lead_id in leads:
        if lead_id != p["actor_id"]:
            _notify(session, job, lead_id, p["item_id"], f'{actor} requested approval for "{title}"')


def handle_approval_decided(session: Session, job: Job) -> None:
    p = job.payload
    title, actor = _names(session, p["item_id"], p["actor_id"])
    note = f": {p['note']}" if p.get("note") else ""
    _notify(session, job, p["user_id"], p["item_id"], f'{actor} {p["decision"]} "{title}"{note}')


HANDLERS: dict[JobKind, Callable[[Session, Job], None]] = {
    JobKind.NOTIFY_ASSIGNED: handle_assigned,
    JobKind.NOTIFY_APPROVAL_REQUESTED: handle_approval_requested,
    JobKind.NOTIFY_APPROVAL_DECIDED: handle_approval_decided,
}


# --- processing --------------------------------------------------------------------------------


def backoff(attempts: int) -> timedelta:
    """5s, 10s, 20s, 40s ... capped at 5 minutes."""
    return timedelta(seconds=min(BACKOFF_BASE_SECONDS * 2 ** (attempts - 1), BACKOFF_MAX_SECONDS))


def process_one(session: Session) -> bool:
    """Process one due job in its own transaction. Returns False when nothing is due."""
    with session.begin():
        job = session.scalars(
            select(Job)
            .where(Job.status == JobStatus.PENDING, Job.run_after <= func.now())
            .order_by(Job.run_after, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if job is None:
            return False

        attempts = job.attempts + 1
        try:
            with session.begin_nested():  # SAVEPOINT: a failing handler's writes are undone
                HANDLERS[job.kind](session, job)
        except Exception as exc:  # noqa: BLE001  (any handler failure is retried, not fatal)
            gave_up = attempts >= job.max_attempts
            values: dict[str, Any] = {
                "attempts": attempts,
                "last_error": f"{type(exc).__name__}: {exc}"[:2000],
                "updated_at": func.now(),
            }
            if gave_up:
                values["status"] = JobStatus.FAILED
            else:
                values["run_after"] = datetime.now(UTC) + backoff(attempts)
            session.execute(update(Job).where(Job.id == job.id).values(**values))
            log.warning("job %s (%s) attempt %s failed%s: %s", job.id, job.kind, attempts, ", giving up" if gave_up else "", exc)
            return True

        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(status=JobStatus.DONE, attempts=attempts, last_error=None, updated_at=func.now())
        )
        log.info("job %s (%s) done", job.id, job.kind)
        return True


def drain(session: Session, limit: int) -> int:
    """Process up to `limit` due jobs; return how many were processed."""
    done = 0
    while done < limit and process_one(session):
        done += 1
    return done


def cleanup(session: Session) -> int:
    """Forget idempotency keys older than 24h: clients only retry within seconds or minutes."""
    with session.begin():
        result = session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.created_at < datetime.now(UTC) - IDEMPOTENCY_KEY_TTL)
        )
    return result.rowcount or 0


def run(once: bool = False) -> None:
    stopping = False

    def stop(*_: Any) -> None:
        nonlocal stopping
        log.info("stopping after the current job...")
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    last_cleanup = datetime.min.replace(tzinfo=UTC)
    log.info("worker started (poll every %ss)", settings.worker_poll_seconds)
    while not stopping:
        try:
            with SessionLocal() as session:
                processed = drain(session, settings.worker_batch_size)
                if datetime.now(UTC) - last_cleanup > CLEANUP_EVERY:
                    removed = cleanup(session)
                    last_cleanup = datetime.now(UTC)
                    if removed:
                        log.info("removed %s expired idempotency keys", removed)
        except Exception:  # database down etc.: log, wait, try again. Never crash the loop.
            log.exception("worker loop error; retrying shortly")
            processed = 0
        if once and processed == 0:
            break
        if processed < settings.worker_batch_size:  # queue drained: wait before polling again
            time.sleep(settings.worker_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Outbox worker")
    parser.add_argument("--once", action="store_true", help="process all due jobs, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(once=args.once)


if __name__ == "__main__":
    main()
