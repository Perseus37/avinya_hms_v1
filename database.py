"""Database and cache bootstrap for the Avinya HMS application.

This module provides the infrastructure layer used by every healthcare domain:
identity, patient workflow, ward operations, and clinical master data. Keeping
service-level configuration centralized makes environment-specific changes simple.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import redis
from config import settings

# -----------------------------------------------------------------------------
# Infrastructure configuration
# -----------------------------------------------------------------------------
DATABASE_URL = "postgresql://user:password@127.0.0.1:5432/avinya_hms"
# DATABASE_URL = "postgresql+psycopg2://user:password@127.0.0.1:5432/avinya_hms"
# DATABASE_URL = "postgresql://user:password@localhost:5432/avinya_hms"
REDIS_URL = "redis://localhost:6379/0"

# Shared runtime resources used by all business domains.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_size=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

class Base(DeclarativeBase):
    """Base class used by all SQLAlchemy models in the application."""

    pass


def get_db():
    """Yield a database session for each request and close it afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_redis():
    """Provide the shared Redis client to routes that need caching or invalidation."""
    yield redis_client