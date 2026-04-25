import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import speaker_id, transcribe
from app.config import settings
from app.webhook import post_result

log = logging.getLogger(__name__)


def _trim_prefix(src: Path, seconds: float) -> Path:
    """
    Use ffmpeg to drop the first `seconds` of audio. Returns a new path.
    Caller is responsible for deleting the trimmed file too.
    """
    if seconds <= 0:
        return src
    dst = src.with_suffix(".trimmed.wav")
    cmd = ["ffmpeg", "-y", "-ss", str(seconds), "-i", str(src), "-ar", "16000", "-ac", "1", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.warning("ffmpeg trim failed; falling back to original audio: %s", proc.stderr[-200:])
        return src
    return dst


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


async def process_call(
    call_id: str,
    audio_source: str | Path,
    callback_url: str | None = None,
    direction: str = "inbound",
    greeting_skip_seconds: float | None = None,
) -> dict:
    """
    Process one call end-to-end.

    audio_source: a URL (str starting http) or a local Path.
    direction: "inbound" applies the greeting skip; "outbound" does not.
    greeting_skip_seconds: override for the env default (only used on inbound).

    Local audio (and any trimmed copy) is deleted after processing.
    Source on the originating system (Cytracom, etc.) is untouched.
    """
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(audio_source, str) and audio_source.startswith("http"):
        local = settings.scratch_dir / f"{uuid.uuid4().hex}.audio"
        await _download(audio_source, local)
    else:
        local = Path(audio_source)

    skip = (greeting_skip_seconds if greeting_skip_seconds is not None else settings.greeting_skip_seconds)
    work_path = _trim_prefix(local, skip) if direction == "inbound" else local

    try:
        spk, conf, scores = speaker_id.identify(work_path)
        text = transcribe.transcribe(work_path)
    finally:
        _delete_audio(local)
        if work_path != local:
            _delete_audio(work_path)

    payload = {
        "call_id": call_id,
        "speaker_id": spk,
        "confidence": round(conf, 4),
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "transcript": text,
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "greeting_skip_seconds": skip if direction == "inbound" else 0,
    }
    log.info("Processed %s [%s, skip=%.1fs]: speaker=%s conf=%.3f chars=%d",
             call_id, direction, skip if direction == "inbound" else 0, spk, conf, len(text))

    await post_result(payload, callback_url=callback_url)
    return payload
