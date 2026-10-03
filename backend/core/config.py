from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):

    ENVIRONMENT: str = "development"  # Set to "production" in live deployments
    CONTROL_PLANE_MASTER_KEY: Optional[str] = None  # When set, protects onboarding_app.py endpoints
    
    # 1. Platform & Default Tenant Identity
    PLATFORM_NAME: str
    BASE_DOMAIN: str
    DEFAULT_TENANT_SLUG: str
    DEFAULT_HOSPITAL_NAME: str
    DEFAULT_BRANCH_CODE: str
    VERSION: str = "2.0.0"

    # 2. Atomic PostgreSQL Settings (Single Source of Truth with the 'db' container)
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    CONTROL_PLANE_DB_NAME: str = "hms_control_plane"

    # 3. Atomic Redis Settings
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # 4. Security & Initial Seed
    SECRET_KEY: str = Field(min_length=32)
    MASTER_ONBOARDING_KEY: str = Field(min_length=16)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    CORS_ORIGINS: str = "http://localhost:3000"
    SEED_ADMIN_EMAIL: str
    SEED_ADMIN_PASSWORD: str

    # 5. SMTP & Backup Settings
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: str = "provisioning@avinyahms.com"

    BACKUP_ENCRYPTION_SALT: str
    S3_ENDPOINT_URL: Optional[str] = None
    S3_BUCKET_NAME: str = "hms-catastrophic-backups"
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None

    @property
    def DATABASE_URL(self) -> str:
        """Dynamically constructs the URL from POSTGRES_* variables so 'db' and 'api' never mismatch."""
        return URL.create(
            drivername="postgresql+psycopg2",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        ).render_as_string(hide_password=False)

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def PROJECT_NAME(self) -> str:
        return self.PLATFORM_NAME

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()