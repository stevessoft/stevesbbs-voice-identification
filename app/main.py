import hmac
import json
import logging

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app import enrollment, pipeline, speaker_id
from app.config import settings

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

app = FastAPI(title="stevesbbs-voice-identification", version="0.1.0")


def _require_admin(x_admin_secret: str | None) -> None:
    """Constant-time check of the admin shared secret. If admin_secret is
    unset (empty), admin endpoints are open for local dev."""
    if not settings.admin_secret:
        return
    if not x_admin_secret or not hmac.compare_digest(x_admin_secret, settings.admin_secret):
        raise HTTPException(status_code=401, detail="invalid X-Admin-Secret")


class ProcessRequest(BaseModel):
    call_id: str
    audio_url: str
    callback_url: str | None = None
    direction: str = "inbound"  # "inbound" applies the greeting skip; "outbound" does not
    greeting_skip_seconds: float | None = None  # override env default; None = use env


class ReallocateRequest(BaseModel):
    tech_name: str
    audio_url: str


class ImportRequest(BaseModel):
    profiles: dict[str, list[float]]
    replace: bool = True  # if False, merge with existing


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
    return await pipeline.process_call(
        req.call_id,
        req.audio_url,
        callback_url=req.callback_url,
        direction=req.direction,
        greeting_skip_seconds=req.greeting_skip_seconds,
    )


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
def enroll_rebuild(x_admin_secret: str | None = Header(default=None)) -> dict:
    """Re-scan enrollment_audio/ and rewrite the embeddings file."""
    _require_admin(x_admin_secret)
    profiles = enrollment.build_embeddings()
    speaker_id.reload_profiles()
    return {"ok": True, "enrolled_speakers": list(profiles)}


@app.post("/enroll/import")
def enroll_import(req: ImportRequest, x_admin_secret: str | None = Header(default=None)) -> dict:
    """
    Import pre-computed embeddings directly. Lets us load enrollment data
    onto a deployed instance without uploading raw audio. Each profile is
    a 256-dim float vector (Resemblyzer output).

    With replace=true (default), overwrites the embeddings file entirely.
    With replace=false, merges the imported profiles into the existing set.
    """
    _require_admin(x_admin_secret)

    # Validate vector shape
    for tech, vec in req.profiles.items():
        if not isinstance(vec, list) or not vec:
            raise HTTPException(status_code=422, detail=f"empty vector for {tech}")
        if len(vec) != 256:
            raise HTTPException(status_code=422, detail=f"{tech} vector len {len(vec)} != 256")

    settings.embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    if req.replace or not settings.embeddings_path.exists():
        merged = req.profiles
    else:
        existing = json.loads(settings.embeddings_path.read_text())
        merged = {**existing, **req.profiles}

    settings.embeddings_path.write_text(json.dumps(merged))
    speaker_id.reload_profiles()
    return {"ok": True, "enrolled_speakers": list(merged), "imported": list(req.profiles), "replace": req.replace}
