"""Authorization: may this user do this action, in this team, on this item?

Every endpoint calls `authorize()`; there is no other place where permission decisions are
made. The UI hides buttons for convenience only, the server is the authority.

Two layers:
  1. Team role  -> which actions a role allows at all (ROLE_PERMISSIONS).
  2. Resource   -> for some actions a member must also own the item (OWNER_ONLY_FOR_MEMBERS).
"""

from enum import StrEnum
from typing import Protocol

from app.auth.enums import Role
from app.auth.tokens import CurrentUser
from app.core.errors import Forbidden, NotFound


class Action(StrEnum):
    VIEW = "view"
    COMMENT = "comment"
    CREATE = "create"
    EDIT = "edit"  # title, description, priority, due date
    CLAIM = "claim"  # take an unowned item yourself
    TRANSITION = "transition"  # move through the workflow (non-approval steps)
    APPROVE = "approve"  # approve or reject an item awaiting approval
    ASSIGN = "assign"  # set/replace/clear someone else as assignee
    CLOSE = "close"  # resolved -> closed


ROLE_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset({Action.VIEW, Action.COMMENT}),
    Role.MEMBER: frozenset(
        {Action.VIEW, Action.COMMENT, Action.CREATE, Action.EDIT, Action.CLAIM, Action.TRANSITION}
    ),
    Role.LEAD: frozenset(Action),
    Role.ADMIN: frozenset(Action),
}

# A member may only do these on items they own (assignee; creator also counts for EDIT).
# Leads are exempt: they can act on any item in their team.
OWNER_ONLY_FOR_MEMBERS = frozenset({Action.EDIT, Action.TRANSITION})


class TeamOwned(Protocol):
    team_id: int
    assignee_id: int | None
    created_by: int


def role_in(user: CurrentUser, team_id: int) -> Role | None:
    """The user's effective role in a team: Role.ADMIN for global admins, None if not a member."""
    return Role.ADMIN if user.is_admin else user.roles.get(team_id)


def can_view_team(user: CurrentUser, team_id: int) -> bool:
    return role_in(user, team_id) is not None


def visible_team_ids(user: CurrentUser) -> list[int] | None:
    """Teams whose items the user may see; None means all (admin). Used to scope SQL queries."""
    return None if user.is_admin else list(user.roles)


def authorize(user: CurrentUser, action: Action, team_id: int, item: TeamOwned | None = None) -> Role:
    """Raise unless allowed; return the role that granted it.

    Outside the team: 404 when acting on an item (do not reveal that it exists), 403 otherwise
    (e.g. creating an item in a team you are not part of).
    """
    role = role_in(user, team_id)
    if role is None:
        if item is not None:
            raise NotFound("Work item not found")
        raise Forbidden("You are not a member of this team", code="not_team_member")

    if action not in ROLE_PERMISSIONS[role]:
        raise Forbidden(f"A {role} cannot {action} items in this team", code="role_forbidden")

    if item is not None and role is Role.MEMBER and action in OWNER_ONLY_FOR_MEMBERS:
        owns = item.assignee_id == user.id or (action is Action.EDIT and item.created_by == user.id)
        if not owns:
            raise Forbidden(
                f"Only the assignee or a team lead can {action} this item", code="not_item_owner"
            )
    return role
