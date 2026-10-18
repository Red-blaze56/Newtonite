"""The work-item workflow: which status changes exist and who may perform them.

Happy path:  open -> in_progress -> awaiting_approval -> resolved -> closed
Shortcuts and loops:
    in_progress -> resolved           only for types where approval is not mandatory
    in_progress -> open               pause / hand back to the queue
    awaiting_approval -> in_progress  rejected by a lead (needs a note)
    resolved -> in_progress           reopened

Pure functions over enums, no database access, so the rules are easy to read and to test.
"""

from app.auth.permissions import Action
from app.core.errors import Conflict, Unprocessable
from app.items.enums import ItemType, Status

# (from, to) -> the permission needed to make that move.
TRANSITIONS: dict[tuple[Status, Status], Action] = {
    (Status.OPEN, Status.IN_PROGRESS): Action.TRANSITION,  # start work
    (Status.IN_PROGRESS, Status.OPEN): Action.TRANSITION,  # pause / put back in the queue
    (Status.IN_PROGRESS, Status.AWAITING_APPROVAL): Action.TRANSITION,  # request approval
    (Status.IN_PROGRESS, Status.RESOLVED): Action.TRANSITION,  # only if approval is not mandatory
    (Status.AWAITING_APPROVAL, Status.RESOLVED): Action.APPROVE,  # approve
    (Status.AWAITING_APPROVAL, Status.IN_PROGRESS): Action.APPROVE,  # reject, back to the assignee
    (Status.RESOLVED, Status.CLOSED): Action.CLOSE,
    (Status.RESOLVED, Status.IN_PROGRESS): Action.TRANSITION,  # reopen
}

# Decided by the server from the item type; clients cannot switch it off.
REQUIRES_APPROVAL = frozenset({ItemType.PAYMENT, ItemType.COMPLIANCE})

# Statuses in which an item no longer needs anyone's attention.
DONE_STATUSES = frozenset({Status.RESOLVED, Status.CLOSED})


def requires_approval(item_type: ItemType) -> bool:
    return item_type in REQUIRES_APPROVAL


def targets_from(status: Status) -> list[Status]:
    return [to for (frm, to) in TRANSITIONS if frm is status]


def is_rejection(frm: Status, to: Status) -> bool:
    return frm is Status.AWAITING_APPROVAL and to is Status.IN_PROGRESS


def check_transition(
    item_type: ItemType, frm: Status, to: Status, *, has_assignee: bool, note: str | None
) -> Action:
    """Validate a status change against the workflow; return the permission it needs.

    Raises Conflict (409) when the move is impossible from the item's *current* state,
    Unprocessable (422) when the request itself is incomplete.
    """
    action = TRANSITIONS.get((frm, to))
    if action is None:
        raise Conflict(
            f"Cannot move an item from {frm} to {to}",
            code="invalid_transition",
            details={"allowed": targets_from(frm)},
        )
    if to is Status.IN_PROGRESS and not has_assignee:
        raise Conflict("Assign the item before starting work on it", code="assignee_required")
    if frm is Status.IN_PROGRESS and to is Status.RESOLVED and requires_approval(item_type):
        raise Conflict(
            f"{item_type} items must be approved by a team lead before they are resolved",
            code="approval_required",
            details={"allowed": [Status.AWAITING_APPROVAL]},
        )
    if is_rejection(frm, to) and not (note and note.strip()):
        raise Unprocessable("A rejection needs a note explaining why", code="note_required")
    return action
