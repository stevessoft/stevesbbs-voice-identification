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


# Voicemail-message transcript signatures. The auto-attendant message text
# is fixed and identical across every voicemail, so a transcript match is
# a much harder signal than the acoustic match (which can be confused by
# any tech with similar voice characteristics).
_VOICEMAIL_PATTERNS = (
    "sorry we missed you",
    "leave a message",
    "after the tone",
    "after the beep",
)


def _is_voicemail_transcript(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(pat in lower for pat in _VOICEMAIL_PATTERNS)


async def process_call(
    call_id: str,
    audio_source: str | Path,
    callback_url: str | None = None,
    direction: str = "inbound",
    greeting_skip_seconds: float | None = None,
    started_on: str | None = None,
    started_on_ts: int | None = None,
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
        text, speech_seconds = transcribe.transcribe(work_path)
        if speech_seconds < settings.min_speech_seconds:
            # Dead-air / hold-music / voicemail-only call. Skip speaker ID
            # entirely so we don't match the audio to one of the enrolled
            # voices spuriously.
            log.info("Skipping speaker ID for %s: only %.1fs of speech detected (< %.1fs floor)",
                     call_id, speech_seconds, settings.min_speech_seconds)
            spk, conf, scores = "unknown", 0.0, {}
        elif _is_voicemail_transcript(text):
            # The voicemail message is fixed text. If the transcript matches
            # the voicemail signature, force auto_greeting regardless of how
            # the embedding match landed. This prevents long voicemail
            # recordings (which have acoustic characteristics that can fool
            # the encoder) from being attributed to a tech.
            log.info("Voicemail transcript signature matched for %s, forcing auto_greeting", call_id)
            spk, conf, scores = "auto_greeting", 1.0, speaker_id.identify(work_path)[2]
        else:
            spk, conf, scores = speaker_id.identify(work_path)
    finally:
        _delete_audio(local)
        if work_path != local:
            _delete_audio(work_path)

    # `uuid` is Cytracom's native call identifier (Godwin's endpoint expects
    # it under that name). `call_id` is included as an alias for any consumer
    # that prefers the more descriptive name. `started_on` / `started_on_ts`
    # echo Cytracom's call-start timestamps when known.
    payload = {
        "uuid": call_id,
        "call_id": call_id,
        "started_on": started_on,
        "started_on_ts": started_on_ts,
        "speaker_id": spk,
        "confidence": round(conf, 4),
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "transcript": text,
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "greeting_skip_seconds": skip if direction == "inbound" else 0,
        "speech_seconds": round(speech_seconds, 2),
    }
    log.info("Processed %s [%s, skip=%.1fs, speech=%.1fs]: speaker=%s conf=%.3f chars=%d",
             call_id, direction, skip if direction == "inbound" else 0, speech_seconds, spk, conf, len(text))

    # Only fire the webhook when there's a real tech to attribute the call
    # to. "unknown" and non-tech profiles (auto_greeting) get skipped per
    # Godwin's preference, since his DB needs a user to tie the result to.
    if spk == "unknown" or spk in settings.non_tech_profiles:
        log.info("Skipping webhook for %s (speaker=%s, no tech to attribute)", call_id, spk)
    else:
        await post_result(payload, callback_url=callback_url)
    return payload
