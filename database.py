"""Database initialization and session management."""

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from models import Base

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "tasks.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def _migrate_add_columns() -> None:
    """Add columns that may be missing from existing tables."""
    migrations = [
        ("developer_roster", "slack_user_id", "VARCHAR(50)"),
        ("tasks", "reviewed_at", "DATETIME"),
        ("tasks", "reviewed_jira_updated", "VARCHAR(50)"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                logger.info("Added column %s.%s", table, column)
            except Exception:
                # Column already exists
                pass


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)
    _migrate_add_columns()


def get_db() -> Session:
    """Get a new database session.

    Use as a context manager or call .close() manually.
    """
    return SessionLocal()
