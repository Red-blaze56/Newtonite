"""A user repeating a request because they are unsure the first one worked."""

import threading

from sqlalchemy import func, select

from app.all_models import WorkItem
from app.core.db import SessionLocal
from tests.conftest import new_key


def count_items() -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(WorkItem))


def body_for(world, **extra):
    return {"team_id": world.payments, "type": "task", "title": "Retry me", **extra}


def test_same_key_twice_creates_one_item_and_replays_the_response(world):
    member, key = world.client("member"), new_key()
    first = member.post("/items", json=body_for(world), headers={"Idempotency-Key": key})
    second = member.post("/items", json=body_for(world), headers={"Idempotency-Key": key})

    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert count_items() == 1


def test_same_key_sent_concurrently_creates_one_item(world):
    key = new_key()
    clients = [world.client("member") for _ in range(4)]
    barrier = threading.Barrier(len(clients))
    codes: list[int] = []

    def send(c):
        barrier.wait()
        codes.append(c.post("/items", json=body_for(world), headers={"Idempotency-Key": key}).status_code)

    threads = [threading.Thread(target=send, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert codes == [201] * 4  # everyone gets the same successful answer...
    assert count_items() == 1  # ...but only one item exists


def test_reusing_a_key_for_a_different_request_is_rejected(world):
    member, key = world.client("member"), new_key()
    member.post("/items", json=body_for(world), headers={"Idempotency-Key": key})
    r = member.post("/items", json=body_for(world, title="Something else"), headers={"Idempotency-Key": key})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_reused"
    assert count_items() == 1


def test_failed_requests_are_not_remembered(world):
    """An error rolls back the key too, so the same key can be used once the request is fixed."""
    member, key = world.client("member"), new_key()
    failed = member.post("/items", json=body_for(world, team_id=world.platform), headers={"Idempotency-Key": key})
    assert failed.status_code == 403
    fixed = member.post("/items", json=body_for(world), headers={"Idempotency-Key": key})
    assert fixed.status_code == 201


def test_keys_are_scoped_per_user(world):
    key = new_key()
    a = world.client("member").post("/items", json=body_for(world), headers={"Idempotency-Key": key})
    b = world.client("member2").post("/items", json=body_for(world), headers={"Idempotency-Key": key})
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]  # no one ever receives another user's stored response


def test_retried_approval_returns_the_original_success(world):
    """Without the key, the retry would hit a version conflict and the user would think it failed."""
    item = world.create_item(type="payment")
    member, lead = world.client("member"), world.client("lead")
    v = member.post(f"/items/{item['id']}/claim").json()["version"]
    v = member.post(f"/items/{item['id']}/transition", json={"to": "in_progress", "expected_version": v}).json()["version"]
    v = member.post(f"/items/{item['id']}/transition", json={"to": "awaiting_approval", "expected_version": v}).json()["version"]

    key = new_key()
    approve = {"to": "resolved", "expected_version": v}
    first = lead.post(f"/items/{item['id']}/transition", json=approve, headers={"Idempotency-Key": key})
    retry = lead.post(f"/items/{item['id']}/transition", json=approve, headers={"Idempotency-Key": key})
    assert first.status_code == retry.status_code == 200
    assert retry.json()["status"] == "resolved"
