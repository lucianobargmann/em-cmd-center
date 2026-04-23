"""Test configuration -- override database with a temp file-based SQLite."""

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _override_db(monkeypatch):
    """Replace the real database with a temp file DB.

    This patches database.py's engine/SessionLocal/get_db at the source
    before any endpoint code runs.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from models import Base

    # Create a temp file DB (not :memory:) so all sessions share state
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Patch at the source module
    import database
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)

    # Override get_db to use our test session factory
    def test_get_db():
        return TestSession()

    monkeypatch.setattr(database, "get_db", test_get_db)

    # Also patch in modules that import get_db directly
    import api.status_board
    monkeypatch.setattr(api.status_board, "get_db", test_get_db)

    yield engine, TestSession

    # Cleanup
    engine.dispose()
    try:
        os.unlink(db_path)
    except OSError:
        pass
