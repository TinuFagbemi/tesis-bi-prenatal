from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.base import Base

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Re-exported so callers that only need the declarative base can import it from
# here without pulling in a second registry. ``Base`` itself lives in app.db.base.
__all__ = ["Base", "SessionLocal", "engine"]
