from backend.db.config import Settings


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
