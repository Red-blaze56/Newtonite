"""Tables of the auth feature: who exists, which teams exist, and who has which role where."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.enums import MEMBERSHIP_ROLES, Role
from app.core.db import Base, one_of, text_enum


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-cased
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Membership(Base):
    """A user's role inside one team. A user can be in many teams with different roles."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint(one_of("role", MEMBERSHIP_ROLES), name="role_valid"),
        Index("ix_memberships_team_role", "team_id", "role"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[Role] = mapped_column(text_enum(Role, 20))
