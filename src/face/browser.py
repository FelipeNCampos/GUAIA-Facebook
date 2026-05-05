from __future__ import annotations

from face.config import Settings, get_settings


def playwright_launch_options(settings: Settings | None = None) -> dict[str, object]:
    resolved = settings or get_settings()
    return {
        "headless": resolved.playwright_headless,
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    }


def playwright_context_kwargs(settings: Settings | None = None) -> dict[str, object]:
    resolved = settings or get_settings()
    return {
        "user_data_dir": resolved.playwright_user_data_dir,
    }
