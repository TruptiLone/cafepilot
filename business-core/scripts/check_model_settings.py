"""Check the model settings: which provider and models are set, and whether the key can use each model.

Run from business-core/ in the shell where you exported the settings:
    PYTHONPATH=. python scripts/check_model_settings.py
"""

from __future__ import annotations

from openai import OpenAI

from backend.db.config import get_settings


def main() -> None:
    settings = get_settings()
    print(f"Provider: {settings.model_provider}")
    print(f"Address:  {settings.model_api_base_url}")
    key = settings.model_api_key.get_secret_value().strip() if settings.model_api_key else ""
    if not key:
        print("No key: set MODEL_API_KEY first.")
        return
    print(f"Key:      ...{key[-4:]}")

    try:
        available = {model.id for model in OpenAI(api_key=key, base_url=settings.model_api_base_url).models.list()}
    except Exception as error:  # wrong key, no network, no billing
        print(f"Couldn't list models: {type(error).__name__}: {str(error).splitlines()[0][:300]}")
        return

    print()
    for setting, name in (("PLANNER_MODEL", settings.planner_model), ("VISION_MODEL", settings.vision_model),
                          ("IMAGE_MODEL", settings.image_model)):
        print(f"{setting:14} {name:24} {'OK' if name in available else 'NOT FOUND'}")
    if not available:
        print("\n(The list came back empty. Some keys list nothing even when calls work.)")
        return
    print("\nModels this key can use:")
    for name in sorted(available):
        print(f"  {name}")


if __name__ == "__main__":
    main()
