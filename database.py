"""Database initialization and session management."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base

DB_PATH = Path(__file__).parent / "tasks.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


def get_db() -> Session:
    """Get a new database session.

    Use as a context manager or call .close() manually.
    """
    return SessionLocal()
