"""HTTP endpoints of the notifications feature. Users only ever see their own notifications."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.tokens import CurrentUserDep
from app.core.db import get_session
from app.core.errors import NotFound, Unprocessable
from app.notifications.models import Notification
from app.notifications.schemas import NotificationOut, NotificationPage

router = APIRouter(tags=["notifications"])

SessionDep = Annotated[Session, Depends(get_session)]


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(id=n.id, item_id=n.item_id, message=n.message, read=n.read_at is not None, created_at=n.created_at)


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    user: CurrentUserDep,
    session: SessionDep,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NotificationPage:
    conds = [Notification.user_id == user.id]
    if unread_only:
        conds.append(Notification.read_at.is_(None))
    if cursor:
        if not cursor.isdigit():
            raise Unprocessable("Invalid cursor", code="invalid_cursor")
        conds.append(Notification.id < int(cursor))
    rows = session.scalars(select(Notification).where(*conds).order_by(Notification.id.desc()).limit(limit + 1)).all()
    unread = session.scalar(
        select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return NotificationPage(
        notifications=[_out(n) for n in rows[:limit]],
        unread_count=unread or 0,
        next_cursor=str(rows[limit - 1].id) if len(rows) > limit else None,
    )


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(notification_id: int, user: CurrentUserDep, session: SessionDep) -> None:
    """Idempotent: marking an already-read notification read again changes nothing."""
    found = session.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=func.coalesce(Notification.read_at, func.now()))
        .returning(Notification.id)
    ).first()
    if found is None:  # missing or someone else's: same answer
        raise NotFound("Notification not found")
    session.commit()


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(user: CurrentUserDep, session: SessionDep) -> None:
    session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    session.commit()
