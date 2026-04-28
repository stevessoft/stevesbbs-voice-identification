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


def transcribe(audio_path: Path) -> tuple[str, float]:
    """
    Returns (transcript_text, speech_seconds).
    speech_seconds is the total duration of speech detected after VAD,
    used downstream to gate speaker ID against dead-air calls.
    """
    segments, _info = _get_model().transcribe(str(audio_path), beam_size=1, vad_filter=True)
    seg_list = list(segments)
    text = " ".join(seg.text.strip() for seg in seg_list).strip()
    speech_seconds = sum((seg.end - seg.start) for seg in seg_list)
    return text, speech_seconds
