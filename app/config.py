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
    confidence_threshold: float = 0.78

    # Minimum top-1 vs top-2 cosine similarity margin. If the winning
    # speaker's score is within this delta of the runner-up, the call is
    # too close to call confidently, so we tag it "unknown" instead of
    # guessing. Catches the case where a tech's enrollment happens to
    # match phone-codec characteristics better than the others.
    min_margin: float = 0.04

    # Minimum seconds of detected speech (after VAD filtering) for the
    # call to be considered identifiable. Below this, we skip speaker ID
    # and return "unknown". Catches dead-air calls, hold music, and
    # voicemail-only audio that would otherwise spuriously match a tech.
    min_speech_seconds: float = 5.0

    # Skip the first N seconds of incoming-call audio to drop the
    # automated greeting. Set to 0 to disable, or pass per-call via
    # the API request to override. Default 18s covers both the short
    # ("Thank you for calling") and long ("Monday to Friday 10am-2pm
    # please wait while your call is connected") greeting variants
    # observed in real Steve's Computers call audio.
    greeting_skip_seconds: float = 18.0

    enroll_dir: Path = Path("./enrollment_audio")
    embeddings_path: Path = Path("./enrolled_voices/embeddings.json")
    scratch_dir: Path = Path("./scratch")

    log_level: str = "INFO"

    # Shared secret for admin endpoints (/enroll/import, /enroll/rebuild).
    # Required as the X-Admin-Secret header. Empty disables the check
    # (open by default for local dev; set in prod).
    admin_secret: str = ""


settings = Settings()
