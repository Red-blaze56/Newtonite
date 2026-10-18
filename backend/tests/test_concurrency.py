"""Several people acting on the same item at the same moment.

These use real threads and real Postgres connections, so the race is genuine: each request
gets its own database transaction, exactly as in production.
"""

import threading
from collections.abc import Callable

from httpx2 import Response
from sqlalchemy import func, select

from app.all_models import ActivityEvent, WorkItem
from app.core.db import SessionLocal
from app.items.enums import EventKind


def run_together(*calls: Callable[[], Response]) -> list[Response]:
    """Start all calls at the same instant (barrier) and return their responses in order."""
    barrier = threading.Barrier(len(calls))
    results: list[Response | None] = [None] * len(calls)

    def worker(i: int, call: Callable[[], Response]) -> None:
        barrier.wait()
        results[i] = call()

    threads = [threading.Thread(target=worker, args=(i, c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [r for r in results if r is not None]


def test_parallel_claims_exactly_one_wins(world):
    for _ in range(5):  # repeat: a race that only fails sometimes must still be caught
        item = world.create_item()
        clients = [world.client(n) for n in ("member", "member2", "lead")]
        responses = run_together(*(lambda c=c: c.post(f"/items/{item['id']}/claim") for c in clients))

        codes = sorted(r.status_code for r in responses)
        assert codes == [200, 409, 409]
        losers = [r for r in responses if r.status_code == 409]
        assert all(r.json()["error"]["code"] == "already_claimed" for r in losers)

        winner_id = next(r for r in responses if r.status_code == 200).json()["assignee"]["id"]
        with SessionLocal() as s:
            row = s.get(WorkItem, item["id"])
            assert row.assignee_id == winner_id
            assert row.version == 2  # bumped exactly once
            claims = s.scalar(
                select(func.count())
                .select_from(ActivityEvent)
                .where(ActivityEvent.item_id == item["id"], ActivityEvent.kind == EventKind.ASSIGNEE_CHANGED)
            )
            assert claims == 1  # history records one claim, not three


def test_claiming_your_own_item_again_is_a_harmless_no_op(world):
    item = world.create_item()
    member = world.client("member")
    first = member.post(f"/items/{item['id']}/claim")
    second = member.post(f"/items/{item['id']}/claim")
    assert first.status_code == second.status_code == 200
    assert second.json()["version"] == first.json()["version"]  # nothing changed the second time


def test_stale_update_is_rejected_and_changes_nothing(world):
    item = world.create_item(title="Original")
    lead = world.client("lead")
    stale_version = item["version"]

    fresh = lead.patch(f"/items/{item['id']}", json={"expected_version": stale_version, "priority": "critical"})
    assert fresh.status_code == 200

    stale = lead.patch(f"/items/{item['id']}", json={"expected_version": stale_version, "title": "Overwrite"})
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["details"]["current"]["version"] == stale_version + 1  # client gets the latest copy

    current = lead.get(f"/items/{item['id']}").json()
    assert current["title"] == "Original"
    assert current["priority"] == "critical"


def test_simultaneous_edits_from_the_same_version_one_wins(world):
    item = world.create_item()
    v = item["version"]
    lead, admin = world.client("lead"), world.client("admin")
    responses = run_together(
        lambda: lead.patch(f"/items/{item['id']}", json={"expected_version": v, "title": "Lead's title"}),
        lambda: admin.patch(f"/items/{item['id']}", json={"expected_version": v, "title": "Admin's title"}),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    with SessionLocal() as s:
        assert s.get(WorkItem, item["id"]).version == v + 1  # no lost update: exactly one write landed


def test_comments_do_not_cause_version_conflicts(world):
    item = world.create_item()
    world.client("viewer").post(f"/items/{item['id']}/comments", json={"body": "Any update?"})
    r = world.client("lead").patch(f"/items/{item['id']}", json={"expected_version": item["version"], "priority": "low"})
    assert r.status_code == 200
