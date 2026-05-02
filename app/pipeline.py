import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import speaker_id, transcribe
from app.config import settings
from app.transcribe import TranscriptSegment
from app.webhook import post_result

log = logging.getLogger(__name__)

# Aliases tech may use when introducing themselves on calls. Maps the
# spoken form to the enrolled profile name. Add new aliases here as they
# show up in real transcripts.
TECH_NAME_ALIASES = {
    "jonathan": "john",
    "stonewall": "stonewall",
    "stone wall": "stonewall",
    "isaiah": "isaiah",
    "steve": "steve",
    "john": "john",
}

# Voicemail-message text fingerprints. If any of these appear in a segment
# transcript, that segment is forced to auto_greeting regardless of the
# embedding match. These phrases are FIXED text in Steve's voicemail
# recording so the pattern is reliable.
VOICEMAIL_SIGNATURES = [
    "sorry we missed you",
    "leave a message",
    "after the tone",
    "after the beep",
]

# Below this score in a per-segment window, even with a transcript match,
# we don't trust the override. Lower than the global confidence_threshold
# because per-segment windows have less signal.
TRANSCRIPT_OVERRIDE_FLOOR = 0.70

# Label for any segment where no enrolled tech matches with confidence.
# This is the customer (or any non-enrolled speaker) on the call.
EXTERNAL_CALLER = "external_caller"

# Self-intro patterns reused for customer name extraction. These run on
# external_caller segments to pull out a name like "Barbara" / "John" when
# the customer self-introduces. Whatever name is captured propagates
# forward across subsequent external_caller segments in the call (until a
# different name appears) so Godwin's UI can render "Barbara 3:34" in
# place of the generic "Client 3:34".
#
# Patterns are case-insensitive but capture against the ORIGINAL text so
# we can verify the captured token is title-cased (Whisper title-cases
# proper names but not common words). That capitalization check filters
# out false positives like "I'm not" / "more" / "like maybe" that the
# regex would otherwise grab.
_SELF_INTRO_PATTERNS = [
    re.compile(r"\bthis\s+is\s+(?:the\s+)?([A-Za-z]+)\b"),
    re.compile(r"\bI'?m\s+([A-Za-z]+)\b"),
    re.compile(r"\bI\s+am\s+([A-Za-z]+)\b"),
    re.compile(r"\bmy\s+name\s+is\s+([A-Za-z]+)\b"),
    re.compile(r"\b([A-Za-z]+)\s+speaking\b"),
]

# Common English filler words that look like names to a regex but are
# clearly not. Used as a secondary block-list after the capitalization
# check, in case Whisper ever title-cases a non-name (e.g. start of a
# sentence: "Calling about your invoice").
_NOT_A_NAME = {
    "sorry", "calling", "trying", "going", "hoping", "looking",
    "wondering", "regarding", "about", "just", "still", "probably",
    "actually", "really", "definitely", "good", "fine", "okay",
    "yes", "yeah", "right", "doing", "here", "there", "back",
    "today", "tomorrow", "ready", "all", "with", "from", "for",
    "to", "in", "on", "at", "by", "not", "more", "less", "like",
    "the", "a", "an", "and", "but", "or", "if", "so", "very",
    "well", "now", "then", "before", "after", "even", "also",
    "always", "never", "sometimes", "maybe", "probably", "definitely",
    "another", "something", "anything", "everything", "nothing",
    "someone", "anyone", "everyone", "noone", "wanting",
}


def _customer_name_from_text(text: str, max_chars: int = 400) -> str | None:
    """
    Pull a customer's first name out of the segment text when they
    self-introduce. Returns the extracted name (Title Case) or None.

    Filters:
      1. Captured token must be title-cased in original text (Whisper
         title-cases proper names but not common words).
      2. Skips enrolled tech aliases (those go through the tech path).
      3. Skips common English filler words that may slip through
         capitalization (e.g. "Sorry" at sentence start).
      4. Rejects candidates that are substrings of tech aliases
         (e.g. "Stone" inside "Stonewall").
    """
    snippet = text[:max_chars]
    for pat in _SELF_INTRO_PATTERNS:
        for m in pat.finditer(snippet):
            candidate_orig = m.group(1).strip()
            if not candidate_orig or len(candidate_orig) < 2:
                continue
            # Capitalization check: must start with uppercase, rest lowercase.
            # "Carrie" passes, "not" / "MORE" / "lIke" all fail.
            if not (candidate_orig[0].isupper() and candidate_orig[1:].islower()):
                continue
            candidate = candidate_orig.lower()
            if candidate in TECH_NAME_ALIASES or candidate in _NOT_A_NAME:
                continue
            if any(candidate in alias or alias in candidate for alias in TECH_NAME_ALIASES):
                continue
            return candidate_orig.title()
    return None


def _voicemail_in_text(text: str) -> bool:
    lower = text.lower()
    return any(sig in lower for sig in VOICEMAIL_SIGNATURES)


def _named_tech_from_text(text: str, max_chars: int = 400) -> str | None:
    """
    Look for a tech being named (self-intro or addressed) in text.
    Returns the enrolled profile name (e.g. "john", "stonewall") if matched,
    None otherwise.

    Patterns matched:
      "this is <name>"     <- Steve's greeting: "Steve's Computer Repair, this is Stonewall"
      "i'm <name>"
      "i am <name>"
      "my name is <name>"
      "<name> speaking"
      "Mr./Ms. <name>"     <- customer addressing tech
      "hi <name>"
    """
    text = text[:max_chars].lower()
    patterns = [
        r"this\s+is\s+(?:the\s+)?([a-z]+(?:\s+[a-z]+)?)",
        r"i'?m\s+([a-z]+)",
        r"i\s+am\s+([a-z]+)",
        r"my\s+name\s+is\s+([a-z]+)",
        r"([a-z]+)\s+speaking",
        r"(?:mr|ms|mrs|miss|mister)\s*\.?\s*([a-z]+)",
        r"(?:hi|hey|hello),?\s+([a-z]+)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            candidate = m.group(1).strip()
            if candidate in TECH_NAME_ALIASES:
                return TECH_NAME_ALIASES[candidate]
            for alias, target in TECH_NAME_ALIASES.items():
                if alias in candidate:
                    return target

    # Fallback: bare mention of any enrolled tech name. The "this is X"
    # pattern catches Steve's standard greeting, but Whisper sometimes
    # mishears it. A bare "Isaiah" or "Stonewall" word-bounded is still
    # a strong signal.
    for alias, target in TECH_NAME_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return target
    return None


def _trim_prefix(src: Path, seconds: float) -> Path:
    """Drop the first `seconds` of audio with ffmpeg. Returns a new path."""
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
    try:
        os.remove(path)
        log.info("Deleted audio: %s", path)
    except FileNotFoundError:
        pass


@dataclass
class ClassifiedSegment:
    """A transcript segment with speaker attribution attached."""
    start_s: float
    end_s: float
    text: str
    speaker: str
    confidence: float
    matched_via: str
    scores: dict[str, float] = field(default_factory=dict)
    # Captured customer name when the segment is external_caller and a
    # self-intro was extracted from the transcript. Propagates forward
    # across subsequent external_caller segments in the same call until
    # a different name appears. Always None for tech segments.
    caller_name: str | None = None


def _classify_segments(
    wav,  # numpy ndarray, the preprocessed full call audio
    segments: list[TranscriptSegment],
    greeting_skip_seconds: float,
) -> list[ClassifiedSegment]:
    """
    Run per-segment speaker ID + transcript-override hybrid + voicemail
    detection across every transcript segment. Returns a parallel list of
    ClassifiedSegment, one per input transcript segment.

    Sticky-speaker rule: when a segment is too short to embed reliably
    (< MIN_WINDOW_SECONDS) it inherits the most recent classified speaker.
    Avoids breaking up a single tech's continuous run with an "unknown"
    blip on a quick "yeah" / "mhm".
    """
    classified: list[ClassifiedSegment] = []
    last_speaker = EXTERNAL_CALLER  # before any classification, assume external
    # Sticky caller name: once we catch a customer's name on this call, it
    # propagates forward across subsequent external_caller segments until
    # a different name appears.
    current_caller_name: str | None = None

    for seg in segments:
        # 1. Voicemail signature in the segment text -> auto_greeting
        if _voicemail_in_text(seg.text):
            classified.append(ClassifiedSegment(
                start_s=seg.start_s + greeting_skip_seconds,
                end_s=seg.end_s + greeting_skip_seconds,
                text=seg.text,
                speaker="auto_greeting",
                confidence=1.0,
                matched_via="transcript_voicemail",
            ))
            last_speaker = "auto_greeting"
            continue

        # 2. Try to classify the window via embedding
        result = speaker_id.classify_window(wav, seg.start_s, seg.end_s)

        if result is None:
            # Window too short to embed. Inherit the previous speaker
            # (sticky rule). matched_via reflects the inheritance.
            inherited_caller = current_caller_name if last_speaker == EXTERNAL_CALLER else None
            classified.append(ClassifiedSegment(
                start_s=seg.start_s + greeting_skip_seconds,
                end_s=seg.end_s + greeting_skip_seconds,
                text=seg.text,
                speaker=last_speaker,
                confidence=0.0,
                matched_via="window_too_short",
                caller_name=inherited_caller,
            ))
            continue

        spk, conf, scores = result

        # 3. Transcript-named-tech override applies even per-segment.
        # If the segment text names a tech AND that tech's score in this
        # window is at least the override floor, trust the transcript.
        named = _named_tech_from_text(seg.text)
        if named and named in scores and scores[named] >= TRANSCRIPT_OVERRIDE_FLOOR:
            if spk != named:
                spk, conf = named, scores[named]
                matched_via = "transcript_self_intro"
            else:
                matched_via = "transcript_confirmed"
        elif spk == "unknown":
            # Gates failed and no transcript clue. This segment is the
            # customer / non-enrolled speaker.
            spk = EXTERNAL_CALLER
            matched_via = "no_match"
        else:
            matched_via = "embedding"

        # 4. Customer name extraction. Only runs on external_caller
        # segments. Updates the sticky current_caller_name when a new
        # name is captured; otherwise the prior name keeps propagating.
        caller_name: str | None = None
        if spk == EXTERNAL_CALLER:
            extracted = _customer_name_from_text(seg.text)
            if extracted:
                current_caller_name = extracted
            caller_name = current_caller_name

        classified.append(ClassifiedSegment(
            start_s=seg.start_s + greeting_skip_seconds,
            end_s=seg.end_s + greeting_skip_seconds,
            text=seg.text,
            speaker=spk,
            confidence=conf,
            matched_via=matched_via,
            scores=scores,
            caller_name=caller_name,
        ))
        last_speaker = spk

    return classified


def _merge_consecutive_same_speaker(segments: list[ClassifiedSegment]) -> list[ClassifiedSegment]:
    """
    Collapse adjacent segments tagged to the same speaker into one.
    Confidence becomes the max across the merged run, matched_via becomes
    the strongest signal in the run (transcript_self_intro > transcript_confirmed
    > embedding > inherited tags).
    """
    if not segments:
        return []

    priority = {
        "transcript_voicemail": 5,
        "transcript_self_intro": 4,
        "transcript_confirmed": 3,
        "embedding": 2,
        "no_match": 1,
        "window_too_short": 0,
    }

    merged: list[ClassifiedSegment] = []
    for seg in segments:
        if merged and merged[-1].speaker == seg.speaker:
            prev = merged[-1]
            prev.end_s = seg.end_s
            prev.text = (prev.text + " " + seg.text).strip()
            prev.confidence = max(prev.confidence, seg.confidence)
            if priority.get(seg.matched_via, 0) > priority.get(prev.matched_via, 0):
                prev.matched_via = seg.matched_via
                if seg.scores:
                    prev.scores = seg.scores
            # Prefer the more recent caller_name when it's non-null;
            # propagation already carried it forward, so the latest value
            # is the most current.
            if seg.caller_name:
                prev.caller_name = seg.caller_name
        else:
            merged.append(seg)
    return merged


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
    Multi-speaker pipeline. Returns a payload with a per-segment timeline:

        segments: [
            {speaker, start_s, end_s, transcript, confidence, matched_via},
            ...
        ]

    Speaker labels: enrolled tech names (e.g. "stonewall"), "auto_greeting"
    for voicemail, "external_caller" for any speaker not matching an
    enrolled tech with confidence (i.e. the customer).
    """
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(audio_source, str) and audio_source.startswith("http"):
        local = settings.scratch_dir / f"{uuid.uuid4().hex}.audio"
        await _download(audio_source, local)
    else:
        local = Path(audio_source)

    skip = (greeting_skip_seconds if greeting_skip_seconds is not None else settings.greeting_skip_seconds)
    work_path = _trim_prefix(local, skip) if direction == "inbound" else local
    skip_offset = skip if direction == "inbound" else 0.0

    classified: list[ClassifiedSegment] = []
    speech_seconds = 0.0
    full_text = ""

    try:
        segments, speech_seconds = transcribe.transcribe(work_path)
        full_text = transcribe.merged_text(segments)

        if speech_seconds < settings.min_speech_seconds:
            log.info("Skipping speaker ID for %s: only %.1fs of speech (< %.1fs floor)",
                     call_id, speech_seconds, settings.min_speech_seconds)
            # Return a single segment representing the whole call as unknown.
            classified = [ClassifiedSegment(
                start_s=skip_offset,
                end_s=skip_offset + speech_seconds,
                text=full_text,
                speaker=EXTERNAL_CALLER,
                confidence=0.0,
                matched_via="speech_floor",
            )]
        else:
            wav = speaker_id.load_full_wav(work_path)
            classified = _classify_segments(wav, segments, skip_offset)
            classified = _merge_consecutive_same_speaker(classified)
    finally:
        _delete_audio(local)
        if work_path != local:
            _delete_audio(work_path)

    # Speakers detected (deduped, ordered by first appearance)
    seen = set()
    speakers_detected = []
    for s in classified:
        if s.speaker not in seen:
            seen.add(s.speaker)
            speakers_detected.append(s.speaker)

    payload = {
        "uuid": call_id,
        "call_id": call_id,
        "started_on": started_on,
        "started_on_ts": started_on_ts,
        "speakers": speakers_detected,
        "segments": [
            {
                "start_s": round(s.start_s, 2),
                "end_s": round(s.end_s, 2),
                "speaker": s.speaker,
                "transcript": s.text,
                "confidence": round(s.confidence, 4),
                "matched_via": s.matched_via,
                "caller_name": s.caller_name,
            }
            for s in classified
        ],
        "transcript": full_text,
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "greeting_skip_seconds": skip if direction == "inbound" else 0,
        "speech_seconds": round(speech_seconds, 2),
    }
    log.info("Processed %s [%s, skip=%.1fs, speech=%.1fs]: speakers=%s segments=%d",
             call_id, direction, skip if direction == "inbound" else 0,
             speech_seconds, speakers_detected, len(classified))

    # Fire the webhook only when at least one enrolled tech is on the call.
    # Pure-customer or pure-voicemail calls have no tech to attribute to,
    # so Godwin's DB has nothing to record against.
    has_tech = any(
        s.speaker not in {EXTERNAL_CALLER, "auto_greeting"}
        for s in classified
    )
    if not has_tech:
        log.info("Skipping webhook for %s (no tech speakers detected: %s)",
                 call_id, speakers_detected)
    else:
        await post_result(payload, callback_url=callback_url)
    return payload
