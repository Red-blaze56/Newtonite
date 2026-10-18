"""Reading work items: one item, lists (filters, search, pagination), history, dashboard counts.

Nothing in this module changes data. Every read is scoped to what the user may see:
  * one item  -> 404 if it does not exist *or* belongs to a team the user is not in
  * lists     -> the SQL WHERE clause only ever includes the user's own teams (_scope)
"""

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.auth.enums import Role
from app.auth.models import Team, User
from app.auth.permissions import Action, authorize, visible_team_ids
from app.auth.tokens import CurrentUser
from app.core.errors import AppError, Forbidden, NotFound, Unprocessable
from app.items.enums import ItemType, Priority, Status
from app.items.models import ActivityEvent, WorkItem
from app.items.schemas import (
    ActivityOut,
    ActivityPage,
    AllowedActions,
    DashboardCounts,
    ItemDetail,
    ItemPage,
    ItemSummary,
    UserRef,
)
from app.items.workflow import DONE_STATUSES, TRANSITIONS, check_transition, requires_approval, targets_from

ACTIVE_STATUSES = (Status.OPEN, Status.IN_PROGRESS, Status.AWAITING_APPROVAL)
URGENT_PRIORITIES = (Priority.CRITICAL, Priority.HIGH)


# --- one item ----------------------------------------------------------------------------------


def load_visible_item(session: Session, user: CurrentUser, item_id: int) -> WorkItem:
    """The item, or 404 if it does not exist *or* the user may not see it (same answer)."""
    item = session.get(WorkItem, item_id)
    if item is None:
        raise NotFound("Work item not found")
    authorize(user, Action.VIEW, item.team_id, item)
    return item


def _is_overdue(due_at: datetime | None, status: Status, now: datetime) -> bool:
    return due_at is not None and due_at < now and status not in DONE_STATUSES


def _allowed(user: CurrentUser, action: Action, item: WorkItem) -> bool:
    try:
        authorize(user, action, item.team_id, item)
        return True
    except (Forbidden, NotFound):
        return False


def allowed_actions(user: CurrentUser, item: WorkItem) -> AllowedActions:
    """Same rules as service_command, evaluated without raising. For the UI only."""
    transitions = []
    for to in targets_from(item.status):
        try:
            check_transition(item.type, item.status, to, has_assignee=item.assignee_id is not None, note="ui")
        except AppError:
            continue
        if _allowed(user, TRANSITIONS[(item.status, to)], item):
            transitions.append(to)
    is_open = item.status is Status.OPEN
    not_closed = item.status is not Status.CLOSED
    return AllowedActions(
        edit=not_closed and _allowed(user, Action.EDIT, item),
        claim=is_open and item.assignee_id is None and _allowed(user, Action.CLAIM, item),
        assign=not_closed and _allowed(user, Action.ASSIGN, item),
        release=is_open and item.assignee_id == user.id and _allowed(user, Action.CLAIM, item),
        comment=_allowed(user, Action.COMMENT, item),
        transitions=transitions,
    )


def item_detail(session: Session, user: CurrentUser, item: WorkItem) -> ItemDetail:
    user_ids = {item.created_by} | ({item.assignee_id} if item.assignee_id else set())
    names = {uid: name for uid, name in session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))}
    team_name = session.scalar(select(Team.name).where(Team.id == item.team_id))
    return ItemDetail(
        id=item.id,
        team_id=item.team_id,
        team_name=team_name or "",
        type=item.type,
        title=item.title,
        description=item.description,
        priority=item.priority,
        status=item.status,
        assignee=UserRef(id=item.assignee_id, name=names[item.assignee_id]) if item.assignee_id else None,
        created_by=UserRef(id=item.created_by, name=names[item.created_by]),
        due_at=item.due_at,
        overdue=_is_overdue(item.due_at, item.status, datetime.now(UTC)),
        requires_approval=requires_approval(item.type),
        version=item.version,
        created_at=item.created_at,
        updated_at=item.updated_at,
        allowed=allowed_actions(user, item),
    )


def get_item(session: Session, user: CurrentUser, item_id: int) -> ItemDetail:
    return item_detail(session, user, load_visible_item(session, user, item_id))


# --- lists: filters, views, search, pagination -------------------------------------------------


@dataclass(frozen=True)
class ItemFilters:
    team_id: int | None = None
    status: list[Status] | None = None
    priority: list[Priority] | None = None
    type: list[ItemType] | None = None
    assignee: str | None = None  # "me" | "unassigned" | "<user id>"
    q: str | None = None
    view: str | None = None  # assigned_to_me | unassigned_urgent | awaiting_my_approval | overdue
    sort: str | None = None  # updated | priority
    cursor: str | None = None
    limit: int = 25


VIEWS = ("assigned_to_me", "unassigned_urgent", "awaiting_my_approval", "overdue")
SORTS = ("updated", "priority")


def _scope(user: CurrentUser, team_id: int | None) -> list[ColumnElement[bool]]:
    """Restrict to teams the user may see. This is what makes lists authorization-safe."""
    teams = visible_team_ids(user)
    if team_id is not None:
        if teams is not None and team_id not in teams:
            raise Forbidden("You are not a member of this team", code="not_team_member")
        return [WorkItem.team_id == team_id]
    return [] if teams is None else [WorkItem.team_id.in_(teams)]


def _view(user: CurrentUser, view: str, now: datetime) -> list[ColumnElement[bool]]:
    if view == "assigned_to_me":
        return [WorkItem.assignee_id == user.id, WorkItem.status.in_(ACTIVE_STATUSES)]
    if view == "unassigned_urgent":
        # Matches the partial index ix_items_unassigned_urgent.
        return [
            WorkItem.assignee_id.is_(None),
            WorkItem.priority.in_(URGENT_PRIORITIES),
            WorkItem.status.not_in(DONE_STATUSES),
        ]
    if view == "awaiting_my_approval":
        conds: list[ColumnElement[bool]] = [WorkItem.status == Status.AWAITING_APPROVAL]
        if not user.is_admin:
            conds.append(WorkItem.team_id.in_([t for t, role in user.roles.items() if role is Role.LEAD]))
        return conds
    if view == "overdue":
        return [WorkItem.due_at.is_not(None), WorkItem.due_at < now, WorkItem.status.not_in(DONE_STATUSES)]
    raise Unprocessable(f"Unknown view '{view}'", code="invalid_view", details={"allowed": VIEWS})


def _search_condition(q: str) -> ColumnElement[bool] | None:
    """Prefix full-text search: 'refu fail' matches 'Refund failed'. Uses the GIN index."""
    words = re.findall(r"[^\W_]+", q.lower())[:8]
    if not words:
        return None
    return WorkItem.search_vector.op("@@")(func.to_tsquery("english", " & ".join(f"{w}:*" for w in words)))


def _encode_cursor(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def _decode_cursor(cursor: str, sort: str) -> dict[str, Any]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if data["s"] != sort:
            raise ValueError("cursor belongs to another sort order")
        data["u"] = datetime.fromisoformat(data["u"])
        data["i"] = int(data["i"])
        return data
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
        raise Unprocessable("Invalid or expired cursor", code="invalid_cursor") from None


def list_items(session: Session, user: CurrentUser, f: ItemFilters) -> ItemPage:
    now = datetime.now(UTC)
    conds = _scope(user, f.team_id)
    if f.view:
        conds += _view(user, f.view, now)
    if f.status:
        conds.append(WorkItem.status.in_(f.status))
    if f.priority:
        conds.append(WorkItem.priority.in_(f.priority))
    if f.type:
        conds.append(WorkItem.type.in_(f.type))
    if f.assignee == "me":
        conds.append(WorkItem.assignee_id == user.id)
    elif f.assignee == "unassigned":
        conds.append(WorkItem.assignee_id.is_(None))
    elif f.assignee:
        if not f.assignee.isdigit():
            raise Unprocessable("assignee must be 'me', 'unassigned' or a user id", code="invalid_assignee_filter")
        conds.append(WorkItem.assignee_id == int(f.assignee))
    if f.q and (search := _search_condition(f.q)) is not None:
        conds.append(search)

    # Keyset pagination: "rows after the last one I saw" instead of OFFSET, so page 200 is as
    # cheap as page 1 and rows don't shift between pages when new items arrive.
    sort = f.sort or ("priority" if f.view == "unassigned_urgent" else "updated")
    if sort not in SORTS:
        raise Unprocessable(f"Unknown sort '{sort}'", code="invalid_sort", details={"allowed": SORTS})
    after = tuple_(WorkItem.updated_at, WorkItem.id)
    if sort == "priority":
        order = (WorkItem.priority_rank.asc(), WorkItem.updated_at.desc(), WorkItem.id.desc())
        if f.cursor:
            c = _decode_cursor(f.cursor, sort)
            conds.append(
                or_(
                    WorkItem.priority_rank > c["r"],
                    and_(WorkItem.priority_rank == c["r"], after < tuple_(c["u"], c["i"])),
                )
            )
    else:
        order = (WorkItem.updated_at.desc(), WorkItem.id.desc())
        if f.cursor:
            c = _decode_cursor(f.cursor, sort)
            conds.append(after < tuple_(c["u"], c["i"]))

    Assignee = aliased(User)
    rows = session.execute(
        select(
            WorkItem.id,
            WorkItem.team_id,
            Team.name.label("team_name"),
            WorkItem.type,
            WorkItem.title,
            WorkItem.priority,
            WorkItem.priority_rank,
            WorkItem.status,
            WorkItem.assignee_id,
            Assignee.name.label("assignee_name"),
            WorkItem.due_at,
            WorkItem.version,
            WorkItem.created_at,
            WorkItem.updated_at,
        )
        .join(Team, Team.id == WorkItem.team_id)
        .outerjoin(Assignee, Assignee.id == WorkItem.assignee_id)
        .where(*conds)
        .order_by(*order)
        .limit(f.limit + 1)  # one extra row tells us whether there is a next page
    ).all()

    has_more = len(rows) > f.limit
    rows = rows[: f.limit]
    items = [
        ItemSummary(
            id=r.id,
            team_id=r.team_id,
            team_name=r.team_name,
            type=r.type,
            title=r.title,
            priority=r.priority,
            status=r.status,
            assignee=UserRef(id=r.assignee_id, name=r.assignee_name) if r.assignee_id else None,
            due_at=r.due_at,
            overdue=_is_overdue(r.due_at, r.status, now),
            requires_approval=requires_approval(r.type),
            version=r.version,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
    next_cursor = None
    if has_more:
        last = rows[-1]
        data = {"s": sort, "u": last.updated_at.isoformat(), "i": last.id}
        if sort == "priority":
            data["r"] = last.priority_rank
        next_cursor = _encode_cursor(data)
    return ItemPage(items=items, next_cursor=next_cursor)


def dashboard_counts(session: Session, user: CurrentUser) -> DashboardCounts:
    now = datetime.now(UTC)
    scope = _scope(user, None)
    counts = {
        view: session.scalar(select(func.count()).select_from(WorkItem).where(*scope, *_view(user, view, now)))
        for view in VIEWS
    }
    return DashboardCounts(**counts)


# --- history ----------------------------------------------------------------------------------


def list_activity(
    session: Session, user: CurrentUser, item_id: int, cursor: str | None, limit: int
) -> ActivityPage:
    load_visible_item(session, user, item_id)
    conds = [ActivityEvent.item_id == item_id]
    if cursor:
        if not cursor.isdigit():
            raise Unprocessable("Invalid cursor", code="invalid_cursor")
        conds.append(ActivityEvent.id < int(cursor))
    rows = session.execute(
        select(ActivityEvent, User.name)
        .join(User, User.id == ActivityEvent.actor_id)
        .where(*conds)
        .order_by(ActivityEvent.id.desc())
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    events = [
        ActivityOut(
            id=e.id, kind=e.kind, actor=UserRef(id=e.actor_id, name=name), payload=e.payload, created_at=e.created_at
        )
        for e, name in rows
    ]
    return ActivityPage(events=events, next_cursor=str(rows[-1][0].id) if has_more else None)
