from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import CurrentUserDep
from app.db import get_session
from app.schemas import DashboardCounts
from app.services import items as svc

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/counts", response_model=DashboardCounts)
def counts(user: CurrentUserDep, session: Annotated[Session, Depends(get_session)]) -> DashboardCounts:
    """Badge counts for the "needs attention" views. Each is one indexed COUNT, scoped to the user's teams."""
    return svc.dashboard_counts(session, user)
