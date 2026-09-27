from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import redis

# Database Configurations
DATABASE_URL = "postgresql://user:password@localhost:5432/avinya_hms"
REDIS_URL = "redis://localhost:6379/0"

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_redis():
    yield redis_client