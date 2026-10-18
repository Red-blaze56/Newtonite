"""History always matches the item, and background work is reliable."""

from sqlalchemy import func, select, update

from app.all_models import ActivityEvent, Job, Notification
from app.core.db import SessionLocal
from app.notifications import worker
from app.notifications.enums import JobKind, JobStatus


def events_for(item_id: int) -> list[str]:
    with SessionLocal() as s:
        return list(s.scalars(select(ActivityEvent.kind).where(ActivityEvent.item_id == item_id).order_by(ActivityEvent.id)))


def count(model, *where) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def drain() -> int:
    with SessionLocal() as s:
        return worker.drain(s, 100)


# --- history -----------------------------------------------------------------------------------


def test_every_change_writes_exactly_one_history_event(world):
    item = world.create_item()
    lead = world.client("lead")
    v = lead.patch(f"/items/{item['id']}", json={"expected_version": 1, "priority": "critical"}).json()["version"]
    v = lead.post(f"/items/{item['id']}/assign", json={"assignee_id": world.users["member"], "expected_version": v}).json()["version"]
    lead.post(f"/items/{item['id']}/comments", json={"body": "On it?"})
    world.client("member").post(f"/items/{item['id']}/transition", json={"to": "in_progress", "expected_version": v})

    assert events_for(item["id"]) == ["created", "edited", "assignee_changed", "comment", "status_changed"]
    history = lead.get(f"/items/{item['id']}/activity").json()["events"]
    assert history[-2]["payload"] == {"changes": {"priority": {"from": "high", "to": "critical"}}}


def test_rejected_changes_leave_no_history_and_no_jobs(world):
    item = world.create_item()
    lead = world.client("lead")
    stale = lead.post(
        f"/items/{item['id']}/assign", json={"assignee_id": world.users["member"], "expected_version": 99}
    )
    assert stale.status_code == 409
    assert events_for(item["id"]) == ["created"]
    assert count(Job) == 0


# --- outbox worker -----------------------------------------------------------------------------


def test_assignment_queues_a_job_that_becomes_one_notification(world):
    item = world.create_item()
    world.client("lead").post(f"/items/{item['id']}/assign", json={"assignee_id": world.users["member"], "expected_version": 1})
    assert count(Job, Job.kind == JobKind.NOTIFY_ASSIGNED, Job.status == JobStatus.PENDING) == 1

    assert drain() == 1
    assert count(Notification, Notification.user_id == world.users["member"]) == 1

    notes = world.client("member").get("/notifications").json()
    assert notes["unread_count"] == 1
    assert "assigned you" in notes["notifications"][0]["message"]


def test_running_a_job_twice_does_not_duplicate_the_notification(world):
    item = world.create_item()
    world.client("lead").post(f"/items/{item['id']}/assign", json={"assignee_id": world.users["member"], "expected_version": 1})
    drain()
    with SessionLocal() as s, s.begin():
        s.execute(update(Job).values(status=JobStatus.PENDING))  # simulate redelivery
    drain()
    assert count(Notification) == 1


def test_failing_job_is_retried_with_backoff_then_marked_failed(world, monkeypatch):
    def broken(session, job):
        raise RuntimeError("downstream unavailable")

    monkeypatch.setitem(worker.HANDLERS, JobKind.NOTIFY_ASSIGNED, broken)
    with SessionLocal() as s, s.begin():
        s.add(Job(kind=JobKind.NOTIFY_ASSIGNED, payload={}, dedupe_key="t:broken", max_attempts=3))

    delays = []
    for _ in range(3):
        drain()
        with SessionLocal() as s, s.begin():
            job = s.scalar(select(Job).where(Job.dedupe_key == "t:broken"))
            delays.append(round((job.run_after - s.scalar(select(func.now()))).total_seconds()))
            s.execute(update(Job).where(Job.id == job.id).values(run_after=func.now()))  # don't wait in tests

    with SessionLocal() as s:
        job = s.scalar(select(Job).where(Job.dedupe_key == "t:broken"))
    assert job.attempts == 3
    assert job.status == JobStatus.FAILED
    assert "downstream unavailable" in job.last_error
    assert delays[:2] == [5, 10]  # exponential backoff between attempts


def test_a_failed_handler_leaves_no_partial_writes(world, monkeypatch):
    """The handler runs in a savepoint: what it wrote before failing is undone."""

    def half_done(session, job):
        worker.handle_assigned(session, job)  # writes the notification...
        raise RuntimeError("...then fails")

    item = world.create_item()
    world.client("lead").post(f"/items/{item['id']}/assign", json={"assignee_id": world.users["member"], "expected_version": 1})
    monkeypatch.setitem(worker.HANDLERS, JobKind.NOTIFY_ASSIGNED, half_done)
    drain()
    assert count(Notification) == 0
    assert count(Job, Job.attempts == 1, Job.status == JobStatus.PENDING) == 1  # will be retried


def test_two_workers_never_process_the_same_job(world):
    """SKIP LOCKED: a job locked by one worker is invisible to the other."""
    with SessionLocal() as s, s.begin():
        s.add(Job(kind=JobKind.NOTIFY_ASSIGNED, payload={}, dedupe_key="t:locked"))
    with SessionLocal() as holder, holder.begin():
        held = holder.scalar(select(Job).with_for_update())  # worker A holds the job mid-processing
        assert held is not None
        with SessionLocal() as other:
            assert worker.process_one(other) is False  # worker B finds nothing to do, without waiting
