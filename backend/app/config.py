from functools import lru_cache

from pydantic import EmailStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Save Sixes Rd API"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./save_sixes.db"
    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 60
    admin_email: EmailStr
    admin_setup_token: str | None = None
    cors_origins: str = "http://localhost:4173,https://save-sixes-rd-website-production.up.railway.app"

    email_provider: str = "plunk"
    plunk_api_base_url: str = "https://next-api.useplunk.com"
    plunk_secret_key: str | None = None
    plunk_from_address: str | None = None
    plunk_from_name: str = "Save Sixes Rd"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
