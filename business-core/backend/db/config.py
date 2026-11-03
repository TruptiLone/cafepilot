from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
UTC_CONNECTION_OPTIONS = "-c timezone=UTC"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5433
    model_api_key: SecretStr | None = None
    model_api_base_url: str = "https://api.meta.ai/v1"
    planner_model: str = "muse-wonder-1.0"
    vision_model: str = "muse-spark-1.3"
    image_model: str = "muse-image-1.0"
    media_upload_token: SecretStr | None = None
    media_storage_dir: Path = Path(__file__).resolve().parents[2] / "var" / "media"
    media_max_upload_bytes: int = 100 * 1024 * 1024
    video_storage_dir: Path = Path(__file__).resolve().parents[2] / "uploads" / "videos"
    video_max_seconds: int = 30
    video_max_frames: int = 30
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    barista_shared_photos_dir: Path = Path.home() / "Pictures" / "BaristaShared"
    barista_photo_port: int = 8004

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_database_connect_args() -> dict[str, str]:
    return {"options": UTC_CONNECTION_OPTIONS}
