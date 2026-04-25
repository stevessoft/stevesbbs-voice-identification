import json
import logging
from pathlib import Path

import numpy as np
from resemblyzer import VoiceEncoder, preprocess_wav

from app.config import settings

log = logging.getLogger(__name__)

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}


def build_embeddings(enroll_dir: Path | None = None, output_path: Path | None = None) -> dict[str, list[float]]:
    """
    Walks enroll_dir for subfolders. Each subfolder name = technician name.
    Each audio file inside contributes one embedding; the per-tech profile
    is the mean of all that tech's embeddings (more stable than a single clip).
    """
    enroll_dir = enroll_dir or settings.enroll_dir
    output_path = output_path or settings.embeddings_path

    encoder = VoiceEncoder()
    profiles: dict[str, list[float]] = {}

    for tech_dir in sorted(p for p in enroll_dir.iterdir() if p.is_dir()):
        clips = [p for p in tech_dir.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES]
        if not clips:
            log.warning("No audio for %s in %s", tech_dir.name, tech_dir)
            continue
        embeddings = [encoder.embed_utterance(preprocess_wav(c)) for c in clips]
        mean_embedding = np.mean(embeddings, axis=0)
        profiles[tech_dir.name] = mean_embedding.tolist()
        log.info("Enrolled %s from %d clips", tech_dir.name, len(clips))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profiles))
    log.info("Wrote %d profiles to %s", len(profiles), output_path)
    return profiles


def add_clip_to_profile(tech_name: str, audio_path: Path) -> None:
    """
    Active learning: when a tech reallocates a misattributed call, fold that
    call's embedding into their profile and re-average.
    """
    if not settings.embeddings_path.exists():
        raise RuntimeError(f"No embeddings file at {settings.embeddings_path}")

    encoder = VoiceEncoder()
    profiles = json.loads(settings.embeddings_path.read_text())
    new_embedding = encoder.embed_utterance(preprocess_wav(audio_path))

    if tech_name in profiles:
        existing = np.asarray(profiles[tech_name], dtype=np.float32)
        # Treat existing as a single sample; average with new
        merged = np.mean([existing, new_embedding], axis=0)
        profiles[tech_name] = merged.tolist()
    else:
        profiles[tech_name] = new_embedding.tolist()

    settings.embeddings_path.write_text(json.dumps(profiles))
    log.info("Updated profile for %s", tech_name)
