import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import speaker_id, transcribe
from app.config import settings
from app.webhook import post_result

log = logging.getLogger(__name__)


async def _download(url: str, dest: Path) -> None:
    async with httpx.AsyncClient(timeout=60) as client, client.stream("GET", url) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            async for chunk in r.aiter_bytes(64 * 1024):
                f.write(chunk)


def _delete_audio(path: Path) -> None:
    """Delete the audio file. Privacy promise enforced here, in code."""
    try:
        os.remove(path)
        log.info("Deleted audio: %s", path)
    except FileNotFoundError:
        pass


async def process_call(call_id: str, audio_source: str | Path, callback_url: str | None = None) -> dict:
    """
    Process one call end-to-end.

    audio_source: a URL (str starting http) or a local Path.
    Local audio is deleted after processing. Source on the originating system
    (Cytracom, etc.) is untouched.
    """
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(audio_source, str) and audio_source.startswith("http"):
        local = settings.scratch_dir / f"{uuid.uuid4().hex}.audio"
        await _download(audio_source, local)
    else:
        local = Path(audio_source)

    try:
        spk, conf, scores = speaker_id.identify(local)
        text = transcribe.transcribe(local)
    finally:
        # Audio gone immediately after embedding + transcription, before webhook.
        _delete_audio(local)

    payload = {
        "call_id": call_id,
        "speaker_id": spk,
        "confidence": round(conf, 4),
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "transcript": text,
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("Processed %s: speaker=%s conf=%.3f chars=%d", call_id, spk, conf, len(text))

    await post_result(payload, callback_url=callback_url)
    return payload
