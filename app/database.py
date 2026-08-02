"""SQLAlchemy engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # check_same_thread=False lets the Celery worker and the API share a file.
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True}


engine = create_engine(
    settings.sqlalchemy_url,
    future=True,
    **_engine_kwargs(settings.sqlalchemy_url),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create tables. Alembic is available for real migrations; this keeps the
    cold-start path to a single command."""
    from app import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for background tasks and scripts."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
