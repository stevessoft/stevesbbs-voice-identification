import json
import logging
from pathlib import Path

import numpy as np
from resemblyzer import VoiceEncoder, preprocess_wav

from app.config import settings

log = logging.getLogger(__name__)

# Resemblyzer's reference sample rate. preprocess_wav always returns audio
# at this rate, so we use it for time -> sample index conversion when
# slicing per-segment windows.
SAMPLE_RATE = 16000

# Per-segment window minimum. Resemblyzer's encoder expects at least ~1.6s
# of audio to produce a stable embedding. Shorter windows get folded into
# the next segment (or labeled unknown for that span).
MIN_WINDOW_SECONDS = 1.6

_encoder: VoiceEncoder | None = None
_profiles: dict[str, np.ndarray] | None = None


def _get_encoder() -> VoiceEncoder:
    global _encoder
    if _encoder is None:
        log.info("Loading Resemblyzer encoder")
        _encoder = VoiceEncoder()
    return _encoder


def load_full_wav(audio_path: Path) -> np.ndarray:
    """
    Load + preprocess the full call audio once. Returns a 16kHz mono
    normalized float32 array suitable for slicing into per-segment windows.
    """
    return preprocess_wav(audio_path)


def embed_audio(audio_path: Path) -> np.ndarray:
    """Embed the entire utterance. Used for legacy whole-call ID."""
    return _get_encoder().embed_utterance(load_full_wav(audio_path))


def embed_window(wav: np.ndarray, start_s: float, end_s: float) -> np.ndarray | None:
    """
    Slice a time window out of the preprocessed wav and embed it.
    Returns None if the window is shorter than MIN_WINDOW_SECONDS,
    since Resemblyzer is not reliable below ~1.6s of speech.
    """
    if (end_s - start_s) < MIN_WINDOW_SECONDS:
        return None
    start_sample = max(0, int(start_s * SAMPLE_RATE))
    end_sample = min(len(wav), int(end_s * SAMPLE_RATE))
    if (end_sample - start_sample) < int(MIN_WINDOW_SECONDS * SAMPLE_RATE):
        return None
    window = wav[start_sample:end_sample]
    return _get_encoder().embed_utterance(window)


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


def _score_embedding(embedding: np.ndarray, profiles: dict[str, np.ndarray]) -> dict[str, float]:
    return {
        name: float(np.dot(embedding, vec) / (np.linalg.norm(embedding) * np.linalg.norm(vec)))
        for name, vec in profiles.items()
    }


def _classify_scores(scores: dict[str, float]) -> tuple[str, float]:
    """
    Apply confidence floor + margin gate to a scores dict and return
    (winner_name, winner_score). Returns ("unknown", best_score) when any
    gate fails. Shared by whole-call identify() and per-segment classify().
    """
    if not scores:
        return "unknown", 0.0
    sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_name, best_score = sorted_scores[0]

    if best_score < settings.confidence_threshold:
        return "unknown", best_score

    # Non-tech profiles (auto_greeting) win outright, no margin check.
    if best_name in settings.non_tech_profiles:
        return best_name, best_score

    # Margin gate compares top tech to next-best tech (skipping non-tech).
    tech_sorted = [(n, s) for n, s in sorted_scores if n not in settings.non_tech_profiles]
    if len(tech_sorted) >= 2:
        runner_up_name, runner_up_score = tech_sorted[1]
        if (best_score - runner_up_score) < settings.min_margin:
            return "unknown", best_score

    return best_name, best_score


def identify(audio_path: Path) -> tuple[str, float, dict[str, float]]:
    """
    Whole-call identification. Returns (speaker_id, confidence, all_scores).
    Kept for backward compat and for the legacy /process whole-call path.
    """
    profiles = load_profiles()
    if not profiles:
        return "unknown", 0.0, {}

    embedding = embed_audio(audio_path)
    scores = _score_embedding(embedding, profiles)
    winner, conf = _classify_scores(scores)
    return winner, conf, scores


def classify_window(wav: np.ndarray, start_s: float, end_s: float) -> tuple[str, float, dict[str, float]] | None:
    """
    Per-segment identification. Embed the [start_s, end_s] window from the
    pre-loaded wav, score against all profiles, apply gates.

    Returns (speaker_id, confidence, all_scores) when the window is long
    enough to embed. Returns None when the window is shorter than the
    Resemblyzer minimum — caller decides how to handle (merge, drop, etc.).
    """
    profiles = load_profiles()
    if not profiles:
        return None

    embedding = embed_window(wav, start_s, end_s)
    if embedding is None:
        return None

    scores = _score_embedding(embedding, profiles)
    winner, conf = _classify_scores(scores)
    return winner, conf, scores
