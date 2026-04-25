import logging

from fastapi import FastAPI
from pydantic import BaseModel

from app import enrollment, pipeline, speaker_id
from app.config import settings

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

app = FastAPI(title="stevesbbs-voice-identification", version="0.1.0")


class ProcessRequest(BaseModel):
    call_id: str
    audio_url: str
    callback_url: str | None = None


class ReallocateRequest(BaseModel):
    tech_name: str
    audio_url: str


@app.get("/healthz")
def healthz() -> dict:
    profiles = speaker_id.load_profiles()
    return {
        "ok": True,
        "enrolled_speakers": list(profiles),
        "whisper_model": settings.whisper_model,
        "confidence_threshold": settings.confidence_threshold,
    }


@app.post("/process")
async def process(req: ProcessRequest) -> dict:
    return await pipeline.process_call(req.call_id, req.audio_url, callback_url=req.callback_url)


@app.post("/reallocate")
async def reallocate(req: ReallocateRequest) -> dict:
    """
    Active learning hook. Godwin's UI calls this when a tech corrects a
    misattributed call. Folds the call's embedding into the corrected
    speaker's profile and re-averages. Audio deleted after.
    """
    import uuid

    import httpx

    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    local = settings.scratch_dir / f"{uuid.uuid4().hex}.audio"
    async with httpx.AsyncClient(timeout=60) as client, client.stream("GET", req.audio_url) as r:
        r.raise_for_status()
        with local.open("wb") as f:
            async for chunk in r.aiter_bytes(64 * 1024):
                f.write(chunk)

    try:
        enrollment.add_clip_to_profile(req.tech_name, local)
        speaker_id.reload_profiles()
    finally:
        try:
            local.unlink()
        except FileNotFoundError:
            pass

    return {"ok": True, "tech_name": req.tech_name, "enrolled_speakers": list(speaker_id.load_profiles())}


@app.post("/enroll/rebuild")
def enroll_rebuild() -> dict:
    """Re-scan enrollment_audio/ and rewrite the embeddings file."""
    profiles = enrollment.build_embeddings()
    speaker_id.reload_profiles()
    return {"ok": True, "enrolled_speakers": list(profiles)}
