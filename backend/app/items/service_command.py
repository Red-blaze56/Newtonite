"""Changing work items: create, claim, edit, assign, move through the workflow, comment.

Rules that hold for every function in this module:
  * Authorization goes through auth.permissions.authorize(), always against the item's team.
  * The item row, its activity event and any outbox job are written in the caller's single
    transaction (committed by core.idempotency.execute_mutation), so history can never
    disagree with the item and a notification is queued if and only if the change happened.
  * Changes are optimistic: the UPDATE only applies if the row still has the version the
    client saw (`WHERE version = :expected`). Claim is the exception: it is conditional on
    the item still being unowned, which is exactly the user's intent.
  * Each function returns (status_code, body) for execute_mutation to store and send.
"""

from typing import Any

from fastapi import status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.enums import Role
from app.auth.models import Membership, Team
from app.auth.permissions import Action, authorize
from app.auth.tokens import CurrentUser
from app.core.errors import Conflict, Unprocessable
from app.items.enums import EventKind, Status
from app.items.models import ActivityEvent, WorkItem
from app.items.schemas import (
    ActivityOut,
    AssignIn,
    CommentIn,
    ItemCreate,
    ItemDetail,
    ItemUpdate,
    TransitionIn,
    UserRef,
)
from app.items.service_query import item_detail, load_visible_item
from app.items.workflow import check_transition
from app.notifications.enums import JobKind
from app.notifications.outbox import enqueue

EDITABLE_FIELDS = ("title", "description", "priority", "due_at")
NOT_NULLABLE_FIELDS = ("title", "description", "priority")


# --- helpers -----------------------------------------------------------------------------------


def _record(session: Session, item_id: int, actor_id: int, kind: EventKind, payload: dict[str, Any]) -> ActivityEvent:
    """Append one history event in the current transaction."""
    event = ActivityEvent(item_id=item_id, actor_id=actor_id, kind=kind, payload=jsonable_encoder(payload))
    session.add(event)
    return event


def _check_version(session: Session, user: CurrentUser, item: WorkItem, expected: int) -> None:
    if item.version != expected:
        raise Conflict(
            "This item was changed by someone else. Review the latest version and try again.",
            code="version_conflict",
            details={"current": item_detail(session, user, item)},
        )


def _versioned_update(session: Session, user: CurrentUser, item: WorkItem, expected: int, **values: Any) -> None:
    """UPDATE ... WHERE version = :expected. Zero rows means someone changed it since we read it."""
    updated = session.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.version == expected)
        .values(**values, version=WorkItem.version + 1, updated_at=func.now())
        .returning(WorkItem.id)
        .execution_options(synchronize_session=False)
    ).first()
    session.refresh(item)
    if updated is None:
        _check_version(session, user, item, expected)  # raises with the now-current item
        raise Conflict("The item changed while saving, please retry", code="version_conflict")


def _ensure_not_closed(item: WorkItem) -> None:
    if item.status is Status.CLOSED:
        raise Conflict("Closed items cannot be changed", code="item_closed")


# --- commands ----------------------------------------------------------------------------------


def create_item(session: Session, user: CurrentUser, data: ItemCreate) -> tuple[int, ItemDetail]:
    if session.get(Team, data.team_id) is None:
        raise Unprocessable("Unknown team", code="unknown_team")
    authorize(user, Action.CREATE, data.team_id)
    item = WorkItem(
        team_id=data.team_id,
        type=data.type,
        title=data.title,
        description=data.description,
        priority=data.priority,
        due_at=data.due_at,
        created_by=user.id,
    )
    session.add(item)
    session.flush()  # assigns item.id
    _record(session, item.id, user.id, EventKind.CREATED, {"title": item.title, "type": item.type, "priority": item.priority})
    session.refresh(item)
    return status.HTTP_201_CREATED, item_detail(session, user, item)


def claim_item(session: Session, user: CurrentUser, item_id: int) -> tuple[int, ItemDetail]:
    item = load_visible_item(session, user, item_id)
    authorize(user, Action.CLAIM, item.team_id, item)

    # The whole race is decided by this one statement. If two people claim at once, Postgres
    # runs the second UPDATE after the first commits, re-checks the WHERE clause against the
    # new row, finds an assignee, and updates nothing. Exactly one claim wins.
    claimed = session.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.assignee_id.is_(None), WorkItem.status == Status.OPEN)
        .values(assignee_id=user.id, version=WorkItem.version + 1, updated_at=func.now())
        .returning(WorkItem.id)
        .execution_options(synchronize_session=False)
    ).first()
    session.refresh(item)

    if claimed is None:
        if item.assignee_id == user.id:
            return status.HTTP_200_OK, item_detail(session, user, item)  # already yours: a repeated click is a no-op
        if item.assignee_id is None:
            raise Conflict(f"Only open items can be claimed (this one is {item.status})", code="not_claimable")
        raise Conflict(
            "Someone else claimed this item first",
            code="already_claimed",
            details={"current": item_detail(session, user, item)},
        )
    _record(session, item.id, user.id, EventKind.ASSIGNEE_CHANGED, {"from": None, "to": user.id, "via": "claim"})
    return status.HTTP_200_OK, item_detail(session, user, item)


def update_item(session: Session, user: CurrentUser, item_id: int, data: ItemUpdate) -> tuple[int, ItemDetail]:
    item = load_visible_item(session, user, item_id)
    authorize(user, Action.EDIT, item.team_id, item)
    _ensure_not_closed(item)
    _check_version(session, user, item, data.expected_version)

    changes: dict[str, dict[str, Any]] = {}
    for field in EDITABLE_FIELDS:
        if field not in data.model_fields_set:
            continue
        new = getattr(data, field)
        if new is None and field in NOT_NULLABLE_FIELDS:
            raise Unprocessable(f"{field} cannot be null", code="validation_error")
        old = getattr(item, field)
        if new != old:
            changes[field] = {"from": old, "to": new}
    if not changes:
        return status.HTTP_200_OK, item_detail(session, user, item)  # nothing to change: no version bump, no event

    _versioned_update(session, user, item, data.expected_version, **{k: v["to"] for k, v in changes.items()})
    _record(session, item.id, user.id, EventKind.EDITED, {"changes": changes})
    return status.HTTP_200_OK, item_detail(session, user, item)


def assign_item(session: Session, user: CurrentUser, item_id: int, data: AssignIn) -> tuple[int, ItemDetail]:
    item = load_visible_item(session, user, item_id)
    target = data.assignee_id
    # Members may hand back their own item ("release"); anything else is a lead's decision.
    releasing = target is None and item.assignee_id == user.id
    authorize(user, Action.CLAIM if releasing else Action.ASSIGN, item.team_id, item)
    _ensure_not_closed(item)
    _check_version(session, user, item, data.expected_version)

    if target == item.assignee_id:
        return status.HTTP_200_OK, item_detail(session, user, item)
    if target is None and item.status is not Status.OPEN:
        # Invariant: work in progress always has an owner.
        raise Conflict("Move the item back to open before removing its assignee", code="cannot_unassign_active")
    if target is not None:
        role = session.scalar(
            select(Membership.role).where(Membership.user_id == target, Membership.team_id == item.team_id)
        )
        if role not in (Role.MEMBER, Role.LEAD):
            raise Unprocessable("Items can only be assigned to members or leads of the item's team", code="invalid_assignee")

    previous = item.assignee_id
    _versioned_update(session, user, item, data.expected_version, assignee_id=target)
    _record(
        session,
        item.id,
        user.id,
        EventKind.ASSIGNEE_CHANGED,
        {"from": previous, "to": target, "via": "release" if releasing else "assign"},
    )
    if target is not None and target != user.id:
        enqueue(
            session,
            JobKind.NOTIFY_ASSIGNED,
            {"item_id": item.id, "user_id": target, "actor_id": user.id},
            dedupe_key=f"{JobKind.NOTIFY_ASSIGNED}:{item.id}:v{item.version}",
        )
    return status.HTTP_200_OK, item_detail(session, user, item)


def transition_item(session: Session, user: CurrentUser, item_id: int, data: TransitionIn) -> tuple[int, ItemDetail]:
    item = load_visible_item(session, user, item_id)
    _check_version(session, user, item, data.expected_version)
    frm, to = item.status, data.to
    action = check_transition(item.type, frm, to, has_assignee=item.assignee_id is not None, note=data.note)
    authorize(user, action, item.team_id, item)

    _versioned_update(session, user, item, data.expected_version, status=to)
    _record(session, item.id, user.id, EventKind.STATUS_CHANGED, {"from": frm, "to": to, "note": data.note or None})

    if to is Status.AWAITING_APPROVAL:
        enqueue(
            session,
            JobKind.NOTIFY_APPROVAL_REQUESTED,
            {"item_id": item.id, "team_id": item.team_id, "actor_id": user.id},
            dedupe_key=f"{JobKind.NOTIFY_APPROVAL_REQUESTED}:{item.id}:v{item.version}",
        )
    elif frm is Status.AWAITING_APPROVAL and item.assignee_id and item.assignee_id != user.id:
        enqueue(
            session,
            JobKind.NOTIFY_APPROVAL_DECIDED,
            {
                "item_id": item.id,
                "user_id": item.assignee_id,
                "actor_id": user.id,
                "decision": "approved" if to is Status.RESOLVED else "rejected",
                "note": data.note or None,
            },
            dedupe_key=f"{JobKind.NOTIFY_APPROVAL_DECIDED}:{item.id}:v{item.version}",
        )
    return status.HTTP_200_OK, item_detail(session, user, item)


def add_comment(session: Session, user: CurrentUser, item_id: int, data: CommentIn) -> tuple[int, ActivityOut]:
    item = load_visible_item(session, user, item_id)
    authorize(user, Action.COMMENT, item.team_id, item)
    # Comments don't change the item, so they don't bump its version: chatting on an item
    # never makes someone else's pending edit fail with a conflict.
    event = _record(session, item.id, user.id, EventKind.COMMENT, {"body": data.body})
    session.flush()
    session.refresh(event)
    return status.HTTP_201_CREATED, ActivityOut(
        id=event.id,
        kind=event.kind,
        actor=UserRef(id=user.id, name=user.name),
        payload=event.payload,
        created_at=event.created_at,
    )
