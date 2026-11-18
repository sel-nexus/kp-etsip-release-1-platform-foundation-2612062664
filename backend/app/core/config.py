"""Load and validate runtime configuration."""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Keep secrets explicit and restrict development credentials to development."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "production"
    jwt_secret: str
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_db: str = "ketsip"
    cors_origins: str = "http://localhost:5173"
    seed_on_startup: bool = False
    seed_admin_password: str = ""
    seed_demo_password: str = ""

    @model_validator(mode="after")
    def validate_secrets(self) -> "Settings":
        """Reject weak secrets and production use of documented demo values."""
        if self.environment not in {"development", "production", "test"}:
            raise ValueError("ENVIRONMENT must be development, test, or production")
        if len(self.jwt_secret) < 32 or len(set(self.jwt_secret)) < 12:
            raise ValueError("JWT_SECRET must contain at least 32 characters and sufficient variety")
        if self.environment == "production":
            lower = self.jwt_secret.lower()
            if any(term in lower for term in ("dev", "change", "secret", "default", "example")):
                raise ValueError("JWT_SECRET must not be a known development value")
        if self.seed_on_startup:
            for value in (self.seed_admin_password, self.seed_demo_password):
                if len(value) < 12 or len(value.encode()) > 72:
                    raise ValueError("Seed passwords must be 12-72 UTF-8 bytes")
                if self.environment == "production" and any(term in value.lower() for term in ("dev", "demo", "change", "password", "default", "admin")):
                    raise ValueError("Production seed passwords must not be known defaults")
        return self
