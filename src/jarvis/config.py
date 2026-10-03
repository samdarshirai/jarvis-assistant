from functools import lru_cache

from pydantic import Field, field_validator
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
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    cartesia_voice_id: str = ""
    fcm_credentials_path: str = ""  # Firebase service-account JSON, kept outside the repo
    tavily_api_key: str = ""  # optional: web search for the research domain; empty = web_search says it is not configured
    brief_enabled: bool = True
    brief_time: str = "07:30"  # local HH:MM, weekdays only
    leave_lead_minutes: int = Field(default=30, ge=1)  # "leave now" fires this long before an event with a location
    mail_poll_minutes: int = Field(default=5, ge=1)
    auto_event_cap: int = Field(default=5, ge=0)  # events auto-created from email per rolling 24 h

    @field_validator("brief_time")
    @classmethod
    def _brief_time_is_hh_mm(cls, v: str) -> str:
        h, sep, m = v.partition(":")
        if not (sep and len(h) == 2 and len(m) == 2 and h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
            raise ValueError("brief_time must be HH:MM")
        return v

    def models(self, tier: str) -> list[str]:
        raw = {"fast": self.models_fast, "strong": self.models_strong}[tier]
        return [m.strip() for m in raw.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
