"""Database engine and per-request session."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

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
