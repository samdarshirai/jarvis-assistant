from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JARVIS_", extra="ignore")

    openrouter_api_key: str
    models_fast: str
    models_strong: str
    database_url: str
    telegram_bot_token: str
    telegram_owner_chat_id: int
    fernet_key: str
    google_client_secrets: str = "client_secret.json"
    timezone: str = "Europe/Berlin"

    def models(self, tier: str) -> list[str]:
        raw = {"fast": self.models_fast, "strong": self.models_strong}[tier]
        return [m.strip() for m in raw.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
