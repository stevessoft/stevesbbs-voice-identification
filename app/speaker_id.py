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

    Returns "unknown" when any of these gates fail:
      1. No enrolled profiles (cold start).
      2. Top-1 score < confidence_threshold (call doesn't sound like any tech).
      3. Top-1 minus top-2 < min_margin (too close to call confidently).
    """
    profiles = load_profiles()
    if not profiles:
        return "unknown", 0.0, {}

    embedding = embed_audio(audio_path)
    scores = {
        name: float(np.dot(embedding, vec) / (np.linalg.norm(embedding) * np.linalg.norm(vec)))
        for name, vec in profiles.items()
    }
    sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_name, best_score = sorted_scores[0]

    if best_score < settings.confidence_threshold:
        return "unknown", best_score, scores

    # If a non-tech profile (e.g., auto_greeting) wins outright, return it.
    # No margin check — these are explicit "this isn't a tech" signals.
    if best_name in settings.non_tech_profiles:
        return best_name, best_score, scores

    # Otherwise, a tech won. Margin gate compares the winning tech to the
    # NEXT-best TECH only (skipping non-tech profiles), so a close
    # auto_greeting score doesn't drag the call into "unknown".
    tech_sorted = [(n, s) for n, s in sorted_scores if n not in settings.non_tech_profiles]
    if len(tech_sorted) >= 2:
        runner_up_name, runner_up_score = tech_sorted[1]
        if (best_score - runner_up_score) < settings.min_margin:
            log.info("Tech margin too tight: %s=%.3f vs %s=%.3f (Δ=%.3f < %.3f)",
                     best_name, best_score, runner_up_name, runner_up_score,
                     best_score - runner_up_score, settings.min_margin)
            return "unknown", best_score, scores

    return best_name, best_score, scores
