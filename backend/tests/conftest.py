"""Test setup: a real Postgres test database, migrated once, emptied before every test.

The tests deliberately use Postgres, not SQLite or mocks: the behaviours under test (row
locks, conditional UPDATEs, ON CONFLICT, SKIP LOCKED) are Postgres behaviours.
"""

import os

# Must run before anything imports app.core.config.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/newtonite_test"
)

import uuid  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import insert, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.all_models import Membership, Team, User  # noqa: E402
from app.auth.enums import Role  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.auth.tokens import create_access_token  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

TABLES = "notifications, jobs, idempotency_keys, activity_events, work_items, memberships, teams, users"


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    db_name = make_url(settings.database_url).database or ""
    if not db_name.endswith("_test"):
        pytest.exit(f"Refusing to run tests against '{db_name}': the database name must end in _test")
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def empty_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@dataclass
class World:
    """Two teams and one user per role. Payments is the team under test; Platform is 'elsewhere'."""

    payments: int
    platform: int
    users: dict[str, int] = field(default_factory=dict)

    def client(self, name: str) -> TestClient:
        c = TestClient(app)
        c.headers["Authorization"] = f"Bearer {create_access_token(self.users[name])}"
        return c

    def create_item(self, who: str = "member", **overrides: Any) -> dict[str, Any]:
        body = {"team_id": self.payments, "type": "task", "title": "Test item", "priority": "high", **overrides}
        r = self.client(who).post("/items", json=body)
        assert r.status_code == 201, r.text
        return r.json()


_PASSWORD_HASH = hash_password("pw")  # argon2 is slow on purpose: hash once for every test user


@pytest.fixture
def world() -> Iterator[World]:
    with SessionLocal() as s, s.begin():
        payments = s.scalar(insert(Team).values(name="Payments").returning(Team.id))
        platform = s.scalar(insert(Team).values(name="Platform").returning(Team.id))
        w = World(payments=payments, platform=platform)
        people = {
            # name: (is_admin, {team: role})
            "lead": (False, {payments: Role.LEAD}),
            "member": (False, {payments: Role.MEMBER}),
            "member2": (False, {payments: Role.MEMBER}),
            "viewer": (False, {payments: Role.VIEWER}),
            "outsider": (False, {platform: Role.LEAD}),  # a lead, but of another team
            "admin": (True, {}),
        }
        for name, (is_admin, roles) in people.items():
            uid = s.scalar(
                insert(User)
                .values(email=f"{name}@test.example", name=name.title(), password_hash=_PASSWORD_HASH, is_admin=is_admin)
                .returning(User.id)
            )
            w.users[name] = uid
            for team_id, role in roles.items():
                s.execute(insert(Membership).values(user_id=uid, team_id=team_id, role=role))
    yield w


def new_key() -> str:
    return str(uuid.uuid4())
