from types import SimpleNamespace

import pytest

from backend.db import config
from backend.db.config import Settings

POSTGRES_ENVIRONMENT_VARIABLES = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
)


@pytest.fixture(autouse=True)
def isolate_settings_from_postgres_environment(monkeypatch) -> None:
    for variable_name in POSTGRES_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable_name, raising=False)


def test_settings_builds_a_postgresql_psycopg_url_with_special_characters(
    tmp_path,
) -> None:
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "\n".join(
            [
                "POSTGRES_DB=test_cafe",
                "POSTGRES_USER=test_user",
                "POSTGRES_PASSWORD=test:/?#[]@password",
                "POSTGRES_HOST=db",
                "POSTGRES_PORT=5432",
            ]
        )
    )

    settings = Settings(_env_file=environment_file)

    assert settings.database_url.drivername == "postgresql+psycopg"
    assert settings.database_url.host == "db"
    assert settings.database_url.port == 5432
    assert settings.database_url.database == "test_cafe"
    assert settings.database_url.username == "test_user"
    assert "%2F" in settings.database_url.render_as_string(hide_password=False)


def test_settings_uses_local_development_host_and_port_by_default(tmp_path) -> None:
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "\n".join(
            [
                "POSTGRES_DB=test_cafe",
                "POSTGRES_USER=test_user",
                "POSTGRES_PASSWORD=test_password",
            ]
        )
    )

    settings = Settings(_env_file=environment_file)

    assert settings.postgres_host == "127.0.0.1"
    assert settings.postgres_port == 5433


def _model_settings(tmp_path, *lines: str) -> Settings:
    environment_file = tmp_path / ".env"
    environment_file.write_text("\n".join(["POSTGRES_DB=d", "POSTGRES_USER=u", "POSTGRES_PASSWORD=p", *lines]))
    return Settings(_env_file=environment_file)


@pytest.fixture
def no_model_environment(monkeypatch) -> None:
    for name in ("MODEL_PROVIDER", "MODEL_API_BASE_URL", "PLANNER_MODEL", "VISION_MODEL", "IMAGE_MODEL", "CHAT_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_meta_is_the_default_provider(tmp_path, no_model_environment) -> None:
    settings = _model_settings(tmp_path)
    assert settings.model_provider == "meta"
    assert settings.model_api_base_url == "https://api.meta.ai/v1"


def test_openai_uses_its_own_address_and_model_names(tmp_path, no_model_environment) -> None:
    settings = _model_settings(tmp_path, "MODEL_PROVIDER=openai", "PLANNER_MODEL=o-plan", "VISION_MODEL=o-see",
                               "IMAGE_MODEL=o-draw")
    assert settings.model_api_base_url == "https://api.openai.com/v1"
    assert (settings.planner_model, settings.vision_model, settings.image_model) == ("o-plan", "o-see", "o-draw")
    assert settings.chat_model == "o-see"  # chat follows the vision model unless CHAT_MODEL is set


def test_chat_uses_muse_spark_by_default_and_chat_model_when_set(tmp_path, no_model_environment) -> None:
    assert _model_settings(tmp_path).chat_model == "muse-spark-1.3"
    assert _model_settings(tmp_path, "CHAT_MODEL=muse-other").chat_model == "muse-other"


def test_an_explicit_base_url_wins(tmp_path, no_model_environment) -> None:
    settings = _model_settings(tmp_path, "MODEL_API_BASE_URL=https://proxy.example/v1")
    assert settings.model_api_base_url == "https://proxy.example/v1"


def test_openai_with_muse_model_names_stops_with_a_clear_message(tmp_path, no_model_environment) -> None:
    with pytest.raises(ValueError, match="still Muse models: VISION_MODEL=muse-spark-1.3"):
        _model_settings(tmp_path, "MODEL_PROVIDER=openai", "PLANNER_MODEL=o-plan", "IMAGE_MODEL=o-draw")


def test_the_owner_is_told_muse_on_meta_and_cafepilot_on_openai(monkeypatch) -> None:
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(model_provider="meta"))
    assert config.assistant_name() == "Muse"
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(model_provider="openai"))
    assert config.assistant_name() == "CaféPilot"
