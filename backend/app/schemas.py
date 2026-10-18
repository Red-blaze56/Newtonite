"""Request and response bodies (validated at the API boundary by Pydantic)."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.enums import EventKind, ItemType, Priority, Role, Status

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=10_000)]
CommentBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5_000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2_000)]
Version = Annotated[int, Field(ge=1, description="The item version the client last saw")]


# --- auth / teams -----------------------------------------------------------------------------


class LoginIn(BaseModel):
    # EmailStr rejects malformed addresses with a 422 before any database work. It only
    # lower-cases the domain part, so authenticate() still normalises the whole address.
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TeamRoleOut(BaseModel):
    id: int
    name: str
    role: Role  # the caller's effective role in this team (admin for global admins)


class MeOut(BaseModel):
    id: int
    email: str
    name: str
    is_admin: bool
    teams: list[TeamRoleOut]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: MeOut


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str


class TeamMemberOut(UserBrief):
    role: Role


# --- work items: requests ---------------------------------------------------------------------


class ItemCreate(BaseModel):
    team_id: int
    type: ItemType
    title: Title
    description: LongText = ""
    priority: Priority = Priority.MEDIUM
    due_at: AwareDatetime | None = None


class ItemUpdate(BaseModel):
    """Partial edit. Only fields present in the request are changed (due_at may be set to null)."""

    expected_version: Version
    title: Title | None = None
    description: LongText | None = None
    priority: Priority | None = None
    due_at: AwareDatetime | None = None


class AssignIn(BaseModel):
    assignee_id: int | None = Field(description="User to assign, or null to unassign")
    expected_version: Version


class TransitionIn(BaseModel):
    to: Status
    expected_version: Version
    note: Note | None = None


class CommentIn(BaseModel):
    body: CommentBody


# --- work items: responses --------------------------------------------------------------------


class UserRef(BaseModel):
    id: int
    name: str


class ItemSummary(BaseModel):
    id: int
    team_id: int
    team_name: str
    type: ItemType
    title: str
    priority: Priority
    status: Status
    assignee: UserRef | None
    due_at: datetime | None
    overdue: bool
    requires_approval: bool
    version: int
    created_at: datetime
    updated_at: datetime


class AllowedActions(BaseModel):
    """What the *current user* may do right now. A UI hint only: the server re-checks everything."""

    edit: bool
    claim: bool
    assign: bool
    release: bool
    comment: bool
    transitions: list[Status]


class ItemDetail(ItemSummary):
    description: str
    created_by: UserRef
    allowed: AllowedActions


class ItemPage(BaseModel):
    items: list[ItemSummary]
    next_cursor: str | None = Field(description="Pass as ?cursor= to get the next page; null on the last page")


class ActivityOut(BaseModel):
    id: int
    kind: EventKind
    actor: UserRef
    payload: dict[str, Any]
    created_at: datetime


class ActivityPage(BaseModel):
    events: list[ActivityOut]
    next_cursor: str | None


class DashboardCounts(BaseModel):
    assigned_to_me: int
    unassigned_urgent: int
    awaiting_my_approval: int
    overdue: int
