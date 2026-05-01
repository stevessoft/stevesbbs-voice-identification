import logging
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from app.config import settings

log = logging.getLogger(__name__)

_model: WhisperModel | None = None


@dataclass
class TranscriptSegment:
    """One contiguous span of speech from Whisper, with timestamps relative
    to the audio passed in (which may be the trimmed work_path, not the
    original recording)."""
    start_s: float
    end_s: float
    text: str

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        log.info("Loading faster-whisper model: %s (CPU, int8)", settings.whisper_model)
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    return _model


def transcribe(audio_path: Path) -> tuple[list[TranscriptSegment], float]:
    """
    Returns (segments, speech_seconds).
    segments: ordered list of TranscriptSegment (Whisper VAD-filtered chunks).
    speech_seconds: total speech duration after VAD, gates dead-air calls.
    """
    raw_segments, _info = _get_model().transcribe(
        str(audio_path),
        beam_size=1,
        vad_filter=True,
        initial_prompt=settings.whisper_initial_prompt or None,
    )
    segments = [
        TranscriptSegment(start_s=s.start, end_s=s.end, text=s.text.strip())
        for s in raw_segments
    ]
    speech_seconds = sum(s.duration_s for s in segments)
    return segments, speech_seconds


def merged_text(segments: list[TranscriptSegment]) -> str:
    """Convenience: rebuild the full transcript string for matching/logging."""
    return " ".join(s.text for s in segments if s.text).strip()
