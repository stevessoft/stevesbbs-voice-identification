import logging
from pathlib import Path

from faster_whisper import WhisperModel

from app.config import settings

log = logging.getLogger(__name__)

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        log.info("Loading faster-whisper model: %s (CPU, int8)", settings.whisper_model)
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    return _model


def transcribe(audio_path: Path) -> str:
    segments, _info = _get_model().transcribe(str(audio_path), beam_size=1, vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()
