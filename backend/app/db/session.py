from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.base import Base

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding one SQLAlchemy session per request.

    It opens and closes, and nothing else. It does **not** commit and does not
    roll back: the transaction belongs to whoever asked for the session, which
    for the ingestion endpoint is the router. Keeping the decision out of here
    is what makes it visible at the place where it is taken, and what lets the
    integration tests swap this dependency for a session bound to a transaction
    they own and always revert.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ``Base`` is re-exported so callers that only need the declarative base can
# import it from here without pulling in a second registry. It lives in
# app.db.base.
__all__ = ["Base", "SessionLocal", "engine", "get_db"]
