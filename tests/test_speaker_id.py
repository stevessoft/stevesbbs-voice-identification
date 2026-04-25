import json

import numpy as np
import pytest

from app import speaker_id
from app.config import settings


@pytest.fixture
def fake_profiles(tmp_path, monkeypatch):
    rng = np.random.default_rng(seed=42)
    p1 = rng.normal(size=256).astype(np.float32)
    p2 = rng.normal(size=256).astype(np.float32)
    embeddings = {"alice": p1.tolist(), "bob": p2.tolist()}
    path = tmp_path / "embeddings.json"
    path.write_text(json.dumps(embeddings))
    monkeypatch.setattr(settings, "embeddings_path", path)
    speaker_id.reload_profiles()
    return p1, p2


def test_load_profiles(fake_profiles):
    profiles = speaker_id.load_profiles()
    assert set(profiles) == {"alice", "bob"}
    assert profiles["alice"].shape == (256,)


def test_identify_returns_alice_for_alice_embedding(fake_profiles, monkeypatch):
    p1, _ = fake_profiles

    # Bypass actual audio embedding; inject the known vector.
    monkeypatch.setattr(speaker_id, "embed_audio", lambda _path: p1.copy())
    name, conf, scores = speaker_id.identify("/dev/null")  # path unused due to monkeypatch
    assert name == "alice"
    assert conf > 0.99
    assert "bob" in scores


def test_identify_unknown_below_threshold(fake_profiles, monkeypatch):
    rng = np.random.default_rng(seed=1)
    odd_vec = rng.normal(size=256).astype(np.float32)
    monkeypatch.setattr(speaker_id, "embed_audio", lambda _p: odd_vec)
    monkeypatch.setattr(settings, "confidence_threshold", 0.99)
    name, _conf, _scores = speaker_id.identify("/dev/null")
    assert name == "unknown"
