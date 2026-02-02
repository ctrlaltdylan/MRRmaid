"""Database configuration and session management."""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()

# Global engine and session factory
_engine = None
_SessionLocal = None


def init_db(database_url: str = "sqlite:///mrrmaid.db") -> None:
    """
    Initialize the database connection.

    Args:
        database_url: SQLAlchemy database URL
    """
    global _engine, _SessionLocal

    # For SQLite, ensure the directory exists
    if database_url.startswith("sqlite:///"):
        db_path = database_url.replace("sqlite:///", "")
        if db_path and not db_path.startswith(":"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
        echo=False,
    )
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    # Create all tables
    Base.metadata.create_all(bind=_engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Get a database session as a context manager.

    Yields:
        SQLAlchemy Session
    """
    if _SessionLocal is None:
        init_db()

    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine():
    """Get the database engine."""
    if _engine is None:
        init_db()
    return _engine
