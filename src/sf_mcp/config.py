from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    onboard_api_base_url: str
    default_client_id: str | None
    request_timeout_seconds: float

    @staticmethod
    def from_env() -> "Settings":
        base_url = os.getenv("ONBOARD_API_BASE_URL", "https://onboard.signal-found.com").rstrip("/")
        default_client_id = os.getenv("ONBOARD_API_CLIENT_ID") or None
        timeout_raw = os.getenv("ONBOARD_API_TIMEOUT_SECONDS", "60")

        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = 60.0

        return Settings(
            onboard_api_base_url=base_url,
            default_client_id=default_client_id,
            request_timeout_seconds=max(timeout_seconds, 5.0),
        )
