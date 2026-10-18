from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep, authenticate, create_access_token, load_current_user
from app.db import get_session
from app.errors import Unauthorized
from app.models import Team
from app.permissions import role_in
from app.schemas import LoginIn, MeOut, TeamRoleOut, TokenOut

router = APIRouter(tags=["auth"])


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
def login(body: LoginIn, session: Annotated[Session, Depends(get_session)]) -> TokenOut:
    user = authenticate(session, body.email, body.password)
    if user is None:
        # Same message for unknown email and wrong password: no account enumeration.
        raise Unauthorized("Incorrect email or password", code="invalid_credentials")
    current = load_current_user(session, user.id)
    assert current is not None
    return TokenOut(access_token=create_access_token(user.id), user=build_me(session, current))


@router.get("/me", response_model=MeOut)
def me(user: CurrentUserDep, session: Annotated[Session, Depends(get_session)]) -> MeOut:
    return build_me(session, user)
