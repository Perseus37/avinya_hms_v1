from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):

    ENVIRONMENT: str = "development"  # Safe default for local instances
    CONTROL_PLANE_MASTER_KEY: Optional[str] = None
    
    # 1. Platform & Default Tenant Identity
    PLATFORM_NAME: str
    BASE_DOMAIN: str
    DEFAULT_TENANT_SLUG: str
    DEFAULT_HOSPITAL_NAME: str
    DEFAULT_BRANCH_CODE: str
    VERSION: str

    # 2. Atomic PostgreSQL Settings 
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    CONTROL_PLANE_DB_NAME: str

    # 3. Atomic Redis Settings
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    REDIS_PASSWORD: str

    # 4. Security & Initial Seed
    SECRET_KEY: str = Field(min_length=32)
    MASTER_ONBOARDING_KEY: str = Field(min_length=16)
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    CORS_ORIGINS: str
    SEED_ADMIN_EMAIL: str
    SEED_ADMIN_PASSWORD: str
    ENVELOPE_ENCRYPTION_SALT: str

    # 5. SMTP & Backup Settings
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: str

    BACKUP_ENCRYPTION_SALT: str
    S3_ENDPOINT_URL: Optional[str] = None
    S3_BUCKET_NAME: str
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
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def PROJECT_NAME(self) -> str:
        return self.PLATFORM_NAME

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()