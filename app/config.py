from functools import lru_cache
from typing import Literal

from pydantic import Field
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
    clip_worker_lease_seconds: int = 2700
    max_clip_source_mb: int = 1024
    max_clip_capture_seconds: int = 1800
    telegram_bot_token: str = ""
    telegram_mode: Literal["webhook", "polling"] = "webhook"
    telegram_webhook_secret: str = ""
    telegram_owner_ids: str = ""
    telegram_language: str = "english"
    youtube_enabled: bool = False
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = ""
    token_encryption_key: str = ""
    media_root: str = "data/media"
    backup_restore_enabled: bool = False
    backup_max_mb: int = Field(default=1024, ge=1, le=10240)
    max_media_mb: int = 250
    youtube_privacy_status: str = "private"
    youtube_analytics_days: int = 30
    google_media_enabled: bool = False
    media_generation_enabled: bool = False
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    google_service_account_json: str = ""
    google_tts_voice: str = "hi-IN-Neural2-A"
    google_image_model: str = "imagen-3.0-generate-002"
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v2"
    minimax_model: str = "MiniMax-H3"
    minimax_duration_seconds: int = 5
    higgsfield_base_url: str = "https://api.higgsfield.ai"
    media_provider: str = "google"
    instagram_enabled: bool = False
    instagram_user_id: str = ""
    instagram_access_token: str = ""
    instagram_graph_version: str = "v27.0"
    meta_app_id: str = ""
    meta_app_secret: str = ""
    reel_profile_name: str = "quiet_wolf_hindi"
    reel_default_duration_seconds: int = 55
    automation_enabled: bool = False
    automation_interval_seconds: int = 300
    automation_min_trend_score: int = 70
    # Instagram Reels is the active automation target. Publishing is gated
    # separately by the runtime platform switch and current-version approval.
    automation_platform: str = "instagram"
    automation_max_jobs_per_cycle: int = 1
    automation_max_retries: int = 3
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
