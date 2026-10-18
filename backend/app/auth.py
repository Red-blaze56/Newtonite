"""Authentication: who is making this request?

Follows FastAPI's "OAuth2 with Password and JWT" recipe. The token only carries the user id;
team roles are loaded from the database on every request, so removing someone from a team
(or demoting a lead) takes effect immediately instead of when their token expires.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.enums import Role
from app.errors import Unauthorized
from app.models import Membership, User
from app.passwords import hash_password, verify_password

ALGORITHM = "HS256"

# Verified against when the email is unknown, so a login attempt takes the same time whether
# or not the account exists (no user enumeration through response timing).
_DUMMY_HASH = hash_password("timing-equaliser")

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    name: str
    is_admin: bool
    roles: dict[int, Role] = field(default_factory=dict)  # team_id -> role


def create_access_token(user_id: int) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def load_current_user(session: Session, user_id: int) -> CurrentUser | None:
    user = session.get(User, user_id)
    if user is None:
        return None
    # Membership.role is a text_enum column, so values arrive as Role members already.
    rows = session.execute(select(Membership.team_id, Membership.role).where(Membership.user_id == user_id))
    roles = {team_id: role for team_id, role in rows}
    return CurrentUser(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin, roles=roles)


def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_session)],
) -> CurrentUser:
    """FastAPI dependency: the authenticated user, or 401."""
    if creds is None:
        raise Unauthorized()
    try:
        payload = jwt.decode(
            creds.credentials, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
        user_id = int(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Session expired, please log in again", code="token_expired") from None
    except (jwt.InvalidTokenError, ValueError):
        raise Unauthorized("Invalid token", code="invalid_token") from None

    user = load_current_user(session, user_id)
    if user is None:  # account deleted after the token was issued
        raise Unauthorized("Invalid token", code="invalid_token")
    # End the read-only transaction opened by the lookups, so the endpoint starts with a clean one.
    session.commit()
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
