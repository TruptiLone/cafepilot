from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
UTC_CONNECTION_OPTIONS = "-c timezone=UTC"
PROVIDER_BASE_URLS = {"meta": "https://api.meta.ai/v1", "openai": "https://api.openai.com/v1"}


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
    # Which model API to call: "meta" (Muse, the default) or "openai". Both use the OpenAI library;
    # backend/modules/agent/ai_calls.py adjusts the request options for each.
    model_provider: Literal["meta", "openai"] = "meta"
    model_api_key: SecretStr | None = None
    model_api_base_url: str = ""  # empty: the provider's own address (PROVIDER_BASE_URLS)
    planner_model: str = "muse-wonder-1.0"
    vision_model: str = "muse-spark-1.3"
    image_model: str = "muse-image-1.0"
    chat_model: str = ""  # Ask CaféPilot chat (CHAT_MODEL); empty: the vision model
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
    # Public demo (see backend/modules/demo): PUBLIC_DEMO=1 hides the API docs and needs DEMO_ACCESS_CODE.
    public_demo: bool = False
    demo_access_code: SecretStr | None = None
    muse_daily_limit: int | None = None
    # A private copy of the demo café per visitor (backend/db/visitors.py, scripts/build_template.py).
    per_visitor_databases: bool = False
    template_database: str = "cafepilot_template"
    max_visitor_databases: int = 15
    visitor_idle_minutes: int = 120
    # The built React app (frontend/dist), served by FastAPI in the container; unset locally (Vite serves it).
    frontend_dist: Path | None = None

    @model_validator(mode="after")
    def _match_the_provider(self) -> "Settings":
        if not self.model_api_base_url:
            self.model_api_base_url = PROVIDER_BASE_URLS[self.model_provider]
        if not self.chat_model:
            self.chat_model = self.vision_model
        if self.model_provider == "openai":
            muse = [f"{name.upper()}={value}" for name, value in
                    (("planner_model", self.planner_model), ("vision_model", self.vision_model),
                     ("image_model", self.image_model), ("chat_model", self.chat_model)) if value.startswith("muse-")]
            if muse:
                raise ValueError(f"MODEL_PROVIDER=openai, but these are still Muse models: {', '.join(muse)}. "
                                 "Set PLANNER_MODEL, VISION_MODEL and IMAGE_MODEL to OpenAI model names.")
        return self

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


def assistant_name() -> str:
    """Who the owner is told did the work: Muse on Meta's models, CaféPilot on another provider's
    (the screens never credit Muse for work it didn't do). frontend/src/utils/brand.ts matches it."""
    return "Muse" if get_settings().model_provider == "meta" else "CaféPilot"


def get_database_connect_args() -> dict[str, str]:
    return {"options": UTC_CONNECTION_OPTIONS}
