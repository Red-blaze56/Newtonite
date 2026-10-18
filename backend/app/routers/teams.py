from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUserDep
from app.db import get_session
from app.models import Membership, User
from app.permissions import Action, authorize
from app.schemas import TeamMemberOut

router = APIRouter(tags=["teams"])


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
def team_members(
    team_id: int, user: CurrentUserDep, session: Annotated[Session, Depends(get_session)]
) -> list[TeamMemberOut]:
    """People in a team (used to pick an assignee). Visible to anyone in the team."""
    authorize(user, Action.VIEW, team_id)
    rows = session.execute(
        select(User.id, User.name, User.email, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id == team_id)
        .order_by(User.name)
    ).all()
    return [TeamMemberOut(id=r.id, name=r.name, email=r.email, role=r.role) for r in rows]
