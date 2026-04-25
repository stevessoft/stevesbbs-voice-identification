import json
import logging
from pathlib import Path

import numpy as np
from resemblyzer import VoiceEncoder, preprocess_wav

from app.config import settings

log = logging.getLogger(__name__)

_encoder: VoiceEncoder | None = None
_profiles: dict[str, np.ndarray] | None = None


def _get_encoder() -> VoiceEncoder:
    global _encoder
    if _encoder is None:
        log.info("Loading Resemblyzer encoder")
        _encoder = VoiceEncoder()
    return _encoder


def embed_audio(audio_path: Path) -> np.ndarray:
    wav = preprocess_wav(audio_path)
    return _get_encoder().embed_utterance(wav)


def load_profiles() -> dict[str, np.ndarray]:
    global _profiles
    if _profiles is not None:
        return _profiles
    if not settings.embeddings_path.exists():
        log.warning("No embeddings file at %s — run scripts/enroll.py first", settings.embeddings_path)
        _profiles = {}
        return _profiles
    raw = json.loads(settings.embeddings_path.read_text())
    _profiles = {name: np.asarray(vec, dtype=np.float32) for name, vec in raw.items()}
    log.info("Loaded %d speaker profiles: %s", len(_profiles), list(_profiles))
    return _profiles


def reload_profiles() -> None:
    global _profiles
    _profiles = None
    load_profiles()


def identify(audio_path: Path) -> tuple[str, float, dict[str, float]]:
    """
    Returns (speaker_id, confidence, all_scores).
    speaker_id is "unknown" if best score is below settings.confidence_threshold.
    """
    profiles = load_profiles()
    if not profiles:
        return "unknown", 0.0, {}

    embedding = embed_audio(audio_path)
    scores = {
        name: float(np.dot(embedding, vec) / (np.linalg.norm(embedding) * np.linalg.norm(vec)))
        for name, vec in profiles.items()
    }
    best_name, best_score = max(scores.items(), key=lambda kv: kv[1])
    speaker_id = best_name if best_score >= settings.confidence_threshold else "unknown"
    return speaker_id, best_score, scores
