from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_paused: bool = True
    database_url: str = "sqlite:///./data/factory.db"
    public_base_url: str = ""
    admin_api_token: str = ""
    worker_token: str = ""
    worker_poll_seconds: int = 30
    worker_lease_seconds: int = 900
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_owner_ids: str = "1112018699"
    telegram_language: str = "english"
    youtube_enabled: bool = False
    instagram_enabled: bool = False
    max_daily_publications: int = 6
    max_concurrent_jobs: int = 1
    max_storage_gb: int = 4
    mock_publishing: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def owner_ids(self) -> set[int]:
        values: set[int] = set()
        for raw_id in self.telegram_owner_ids.split(","):
            try:
                values.add(int(raw_id.strip()))
            except ValueError:
                continue
        return values


@lru_cache
def get_settings() -> Settings:
    return Settings()
