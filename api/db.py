"""Database connection.

SQLite now (one local file). SQLAlchemy sits between the code and the database, so moving to
PostgreSQL later means changing DATABASE_URL, not rewriting queries.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(db_url: str) -> Engine:
    if db_url.startswith("sqlite:///"):
        Path(db_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    # timeout: wait up to 30 s for another writer (a person and the background agents) instead of failing with "database is locked"
    engine = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 30} if db_url.startswith("sqlite") else {})
    if db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return engine


def add_missing_columns(engine: Engine) -> None:
    """A small migration: when a newer version adds an optional column, add it to an existing database file."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have and col.nullable:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'))


class Database:
    def __init__(self, db_url: str):
        from api import models  # noqa: F401  (registers the tables)
        self.engine = make_engine(db_url)
        Base.metadata.create_all(self.engine)
        add_missing_columns(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        s: Session = self.sessions()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
