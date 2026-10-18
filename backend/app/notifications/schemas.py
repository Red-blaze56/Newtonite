"""Response bodies of the notifications feature."""

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    item_id: int | None
    message: str
    read: bool
    created_at: datetime


class NotificationPage(BaseModel):
    notifications: list[NotificationOut]
    unread_count: int
    next_cursor: str | None
