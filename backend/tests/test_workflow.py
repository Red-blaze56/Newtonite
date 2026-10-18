"""Workflow rules: which status changes exist, and who may make them."""

import pytest

from app.auth.permissions import Action
from app.core.errors import Conflict, Unprocessable
from app.items.enums import ItemType, Status
from app.items.workflow import check_transition


# --- the rules on their own (pure function, no database) ---------------------------------------


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (Status.OPEN, Status.CLOSED),
        (Status.OPEN, Status.RESOLVED),
        (Status.OPEN, Status.AWAITING_APPROVAL),
        (Status.CLOSED, Status.OPEN),
        (Status.AWAITING_APPROVAL, Status.CLOSED),
    ],
)
def test_impossible_moves_are_rejected(frm, to):
    with pytest.raises(Conflict) as exc:
        check_transition(ItemType.TASK, frm, to, has_assignee=True, note="x")
    assert exc.value.code == "invalid_transition"


@pytest.mark.parametrize("item_type", [ItemType.PAYMENT, ItemType.COMPLIANCE])
def test_payment_and_compliance_cannot_skip_approval(item_type):
    with pytest.raises(Conflict) as exc:
        check_transition(item_type, Status.IN_PROGRESS, Status.RESOLVED, has_assignee=True, note=None)
    assert exc.value.code == "approval_required"


def test_other_types_may_resolve_directly():
    assert check_transition(ItemType.TASK, Status.IN_PROGRESS, Status.RESOLVED, has_assignee=True, note=None) is Action.TRANSITION


def test_approving_and_closing_need_lead_permissions():
    assert check_transition(ItemType.PAYMENT, Status.AWAITING_APPROVAL, Status.RESOLVED, has_assignee=True, note=None) is Action.APPROVE
    assert check_transition(ItemType.TASK, Status.RESOLVED, Status.CLOSED, has_assignee=True, note=None) is Action.CLOSE


def test_work_cannot_start_without_an_owner():
    with pytest.raises(Conflict) as exc:
        check_transition(ItemType.TASK, Status.OPEN, Status.IN_PROGRESS, has_assignee=False, note=None)
    assert exc.value.code == "assignee_required"


def test_rejection_needs_a_note():
    with pytest.raises(Unprocessable):
        check_transition(ItemType.PAYMENT, Status.AWAITING_APPROVAL, Status.IN_PROGRESS, has_assignee=True, note="  ")


# --- the rules through the API -----------------------------------------------------------------


def move(client, item_id, to, version, note=None):
    return client.post(f"/items/{item_id}/transition", json={"to": to, "expected_version": version, "note": note})


def test_payment_approval_flow_end_to_end(world):
    item = world.create_item(type="payment")
    assert item["requires_approval"] is True
    member, lead = world.client("member"), world.client("lead")

    v = member.post(f"/items/{item['id']}/claim").json()["version"]
    v = move(member, item["id"], "in_progress", v).json()["version"]

    skip = move(member, item["id"], "resolved", v)
    assert skip.status_code == 409 and skip.json()["error"]["code"] == "approval_required"

    v = move(member, item["id"], "awaiting_approval", v).json()["version"]

    self_approve = move(member, item["id"], "resolved", v)
    assert self_approve.status_code == 403  # members cannot approve

    reject = move(lead, item["id"], "in_progress", v)
    assert reject.status_code == 422  # rejection needs a note

    v = move(lead, item["id"], "in_progress", v, note="Attach the refund receipt").json()["version"]
    v = move(member, item["id"], "awaiting_approval", v).json()["version"]
    approved = move(lead, item["id"], "resolved", v)
    assert approved.status_code == 200 and approved.json()["status"] == "resolved"

    closed = move(lead, item["id"], "closed", approved.json()["version"])
    assert closed.status_code == 200 and closed.json()["status"] == "closed"


def test_open_to_closed_is_rejected_through_the_api(world):
    item = world.create_item()
    r = move(world.client("lead"), item["id"], "closed", item["version"])
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_transition"
    assert r.json()["error"]["details"]["allowed"] == ["in_progress"]


def test_closed_items_cannot_be_edited(world):
    item = world.create_item()
    lead = world.client("lead")
    v = lead.post(f"/items/{item['id']}/claim").json()["version"]
    for to in ("in_progress", "resolved", "closed"):
        v = move(lead, item["id"], to, v).json()["version"]
    r = lead.patch(f"/items/{item['id']}", json={"expected_version": v, "title": "Too late"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "item_closed"


def test_in_progress_work_cannot_lose_its_owner(world):
    item = world.create_item()
    member = world.client("member")
    v = member.post(f"/items/{item['id']}/claim").json()["version"]
    v = move(member, item["id"], "in_progress", v).json()["version"]
    r = member.post(f"/items/{item['id']}/assign", json={"assignee_id": None, "expected_version": v})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cannot_unassign_active"
