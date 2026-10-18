"""Database plumbing shared by every feature: engine, per-request session, model base class.

Enumerations (roles, statuses, ...) are Python StrEnums stored as VARCHAR guarded by CHECK
constraints, rather than native Postgres ENUM types: adding a value is then a one-line
constraint change in a migration instead of an ALTER TYPE, and the values stay readable in
raw SQL. `text_enum` and `one_of` below implement that.
"""

from collections.abc import Iterator
from enum import StrEnum

from sqlalchemy import Enum as SqlEnum
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # survive Postgres restarts without serving dead connections
)

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session (and at most one open transaction) per request."""
    with SessionLocal() as session:
        yield session


class Base(DeclarativeBase):
    # Deterministic constraint names, so migrations can reference (and later alter) them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def text_enum(enum_cls: type[StrEnum], length: int = 30) -> SqlEnum:
    """A StrEnum column stored as VARCHAR holding the member's value ('lead', not 'LEAD').

    native_enum=False: no CREATE TYPE. The allowed values are enforced by our own named
    CHECK constraints (see one_of), which SQLAlchemy does not add by default.
    """
    return SqlEnum(enum_cls, native_enum=False, length=length, values_callable=lambda e: [m.value for m in e])


def one_of(column: str, values: tuple[StrEnum, ...]) -> str:
    """SQL for a CHECK constraint: "role IN ('viewer', 'member', 'lead')"."""
    # f-string, not repr(): repr of a StrEnum member is "<Role.LEAD: 'lead'>".
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"
