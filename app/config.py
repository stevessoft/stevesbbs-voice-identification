from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    cytracom_api_token: str = ""
    cytracom_base_url: str = "https://api.cytracom.net/v1.0"

    godwin_api_url: str = ""
    godwin_api_token: str = ""

    webhook_url: str = ""
    webhook_secret: str = ""

    whisper_model: str = "small"
    confidence_threshold: float = 0.72

    # Skip the first N seconds of incoming-call audio to drop the
    # automated greeting. Set to 0 to disable, or pass per-call via
    # the API request to override.
    greeting_skip_seconds: float = 11.0

    enroll_dir: Path = Path("./enrollment_audio")
    embeddings_path: Path = Path("./enrolled_voices/embeddings.json")
    scratch_dir: Path = Path("./scratch")

    log_level: str = "INFO"


settings = Settings()
