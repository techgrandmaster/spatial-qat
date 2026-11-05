"""
Application configuration via pydantic-settings BaseSettings.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the project root, two levels up from this file
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    # Meta Model API
    model_api_key: str = ""
    model_api_base_url: str = "https://api.meta.ai/v1"

    # Model IDs (configurable via environment)
    model_spark: str = "muse-spark-1.3"
    model_sam: str = "sam-3.1"
    model_voice: str = "muse-voice-transcribe-1.0"
    model_image: str = "muse-image-1.0"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/spatial_qat"

    # SAM concurrency
    max_sam_concurrency: int = 4

    # Budget guard
    budget_usd_hard_cap: float = 120.0

    # Agent loop
    agent_max_steps: int = 8
    agent_max_parallel_tools: int = 6
    agent_timeout_seconds: int = 90

    # Upload limits
    max_image_size_mb: int = 10
    max_pdf_size_mb: int = 20
    max_audio_size_mb: int = 10

    # Public URL used to build absolute asset URLs for external model APIs.
    # Relative /api/v1/assets/... URLs are unreachable from Meta's servers.
    public_base_url: str = "http://localhost:8000"

    # Session
    guest_session_ttl_days: int = 7

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
    )


settings = Settings()
