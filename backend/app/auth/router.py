"""HTTP endpoints of the auth feature: log in, who am I, who is in a team."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Membership, Team, User
from app.auth.permissions import Action, authorize, role_in
from app.auth.schemas import LoginIn, MeOut, TeamMemberOut, TeamRoleOut, TokenOut
from app.auth.tokens import CurrentUser, CurrentUserDep, authenticate, create_access_token, load_current_user
from app.core.db import get_session
from app.core.errors import Unauthorized

router = APIRouter(tags=["auth"])

SessionDep = Annotated[Session, Depends(get_session)]


def build_me(session: Session, user: CurrentUser) -> MeOut:
    query = select(Team).order_by(Team.name)
    if not user.is_admin:
        query = query.where(Team.id.in_(list(user.roles)))
    teams = []
    for team in session.scalars(query):
        role = role_in(user, team.id)
        assert role is not None  # the query only returns teams the user can see
        teams.append(TeamRoleOut(id=team.id, name=team.name, role=role))
    return MeOut(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin, teams=teams)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, session: SessionDep) -> TokenOut:
    user = authenticate(session, body.email, body.password)
    if user is None:
        # Same message for unknown email and wrong password: no account enumeration.
        raise Unauthorized("Incorrect email or password", code="invalid_credentials")
    current = load_current_user(session, user.id)
    assert current is not None
    return TokenOut(access_token=create_access_token(user.id), user=build_me(session, current))


@router.get("/me", response_model=MeOut)
def me(user: CurrentUserDep, session: SessionDep) -> MeOut:
    return build_me(session, user)


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
def team_members(team_id: int, user: CurrentUserDep, session: SessionDep) -> list[TeamMemberOut]:
    """People in a team (used to pick an assignee). Visible to anyone in the team."""
    authorize(user, Action.VIEW, team_id)
    rows = session.execute(
        select(User.id, User.name, User.email, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id == team_id)
        .order_by(User.name)
    ).all()
    return [TeamMemberOut(id=r.id, name=r.name, email=r.email, role=r.role) for r in rows]
