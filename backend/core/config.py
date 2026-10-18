from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):

    ENVIRONMENT: str
    DEVELOPMENT_AUTO_CREATE_SCHEMA: bool
    CONTROL_PLANE_MASTER_KEY: Optional[str] = None
    TENANT_URL_TEMPLATE: Optional[str] = None
    API_PORT: int = Field(gt=0, le=65535)
    API_SERVICE_HOST: str
    CONTROL_PLANE_PORT: int = Field(gt=0, le=65535)
    GUNICORN_WORKERS: int = Field(gt=0)
    TENANT_DATABASE_PREFIX: str = Field(
        min_length=1, max_length=13, pattern=r"^[a-z][a-z0-9_]*$"
    )
    APP_TIMEZONE: str
    MAX_PAGE_SIZE: int = Field(gt=0)
    MIGRATION_PREVIEW_ROWS: int = Field(gt=0)
    MIGRATION_PREVIEW_ERROR_LIMIT: int = Field(gt=0)
    MIGRATION_MAX_UPLOAD_BYTES: int = Field(gt=0)
    ATTACHMENTS_STORAGE_DIR: str = "attachments"
    MAX_ATTACHMENT_UPLOAD_BYTES: int = Field(gt=0)
    MIGRATION_MAX_EXPRESSION_DEPTH: int = Field(gt=0)
    MIGRATION_MAX_INPUTS: int = Field(gt=0)
    BACKUP_INTERVAL_SECONDS: int = Field(gt=0)
    BACKUP_OUTPUT_DIR: str
    VAULT_POLL_INTERVAL_SECONDS: int = Field(gt=0)
    
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
    MAIL_PROVIDER: str
    AWS_SES_REGION: Optional[str] = None
    AZURE_COMMUNICATION_CONNECTION_STRING: Optional[str] = None
    AZURE_COMMUNICATION_SENDER: Optional[str] = None

    @field_validator("MAIL_PROVIDER")
    @classmethod
    def validate_mail_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"smtp", "aws_ses", "azure_communication_services"}:
            raise ValueError(
                "MAIL_PROVIDER must be smtp, aws_ses, or azure_communication_services."
            )
        return normalized

    BACKUP_ENCRYPTION_SALT: str

    # Payment keys
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None

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

    def tenant_url(self, slug: str) -> str:
        """Return the public base URL for a tenant using one deployment setting."""
        template = self.TENANT_URL_TEMPLATE
        if template:
            return template.replace("{slug}", slug).replace(
                "{BASE_DOMAIN}", self.BASE_DOMAIN
            )

        if self.ENVIRONMENT.lower() in {"production", "prod"}:
            return f"https://{slug}.{self.BASE_DOMAIN}"
        return f"http://{slug}.{self.BASE_DOMAIN}:{self.API_PORT}"

    @property
    def API_INTERNAL_URL(self) -> str:
        return f"http://{self.API_SERVICE_HOST}:{self.API_PORT}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()