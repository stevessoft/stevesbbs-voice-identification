# stevesbbs-voice-identification

Speaker identification + transcription service for Steve's Computers. Identifies which of the enrolled technicians is on a call (Resemblyzer cosine similarity) and transcribes the audio (faster-whisper, CPU-only). Posts the result to a webhook so it can be saved to the Planka-side database.

## Architecture

```
Cytracom (call recordings, retained ~3 months)
   │
   ▼
Godwin's daily cron / API ─── audio file ───►  THIS SERVICE
                                                     │
                                                     ├── Resemblyzer → speaker_id + confidence
                                                     ├── faster-whisper → transcript
                                                     ├── delete local audio
                                                     │
                                                     ▼
                                               POST to webhook
                                                     │
                                                     ▼
                                               Godwin's DB → Planka UI (search, play button via Cytracom)
```

### Responsibilities

This service:
- Speaker identification against enrolled technician voices
- Transcription
- Local audio deletion after processing
- Result POST to webhook

Out of scope (Godwin handles):
- Cytracom polling / call ingestion orchestration
- Planka card creation, custom fields, search UI
- Database storage of results
- Reallocation UI / training tab
- Daily cron orchestration
- Playback UI (queries Cytracom directly)

## Stack

| Component | Choice |
|---|---|
| Web framework | FastAPI + uvicorn |
| Speaker ID | Resemblyzer (256-dim embeddings, cosine similarity) |
| Transcription | faster-whisper (small model, CPU, ~1-2x real-time) |
| HTTP client | httpx |
| Config | pydantic-settings (env-based) |
| Container | Python 3.11-slim, Coolify-deployable |

## Quick start (local dev)

```bash
# Install
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env with real values

# Drop technician enrollment audio in enrollment_audio/<tech_name>/*.wav
# 30-60s per tech minimum, multiple short clips beats one long clip

# Build embeddings
python -m scripts.enroll

# Run service
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Endpoints

### `GET /healthz`
Health check.

### `POST /process`
Process a single call. Body:
```json
{
  "call_id": "string",
  "audio_url": "https://... (signed download URL)",
  "callback_url": "https://godwin's webhook (optional, falls back to env)"
}
```

Returns:
```json
{
  "call_id": "string",
  "speaker_id": "tech_name|unknown",
  "confidence": 0.84,
  "transcript": "...",
  "transcribed_at": "2026-04-25T20:00:00Z"
}
```

### `POST /process/batch`
Process all unprocessed calls returned by Godwin's calls-by-date-range API. Used by the daily sweep.

## Configuration (.env)

See [.env.example](.env.example).

| Var | Purpose |
|---|---|
| `CYTRACOM_API_TOKEN` | Direct Cytracom token (fallback if not using Godwin's API) |
| `CYTRACOM_BASE_URL` | `https://api.cytracom.net/v1.0/` |
| `GODWIN_API_URL` | Godwin's wrapper API base URL |
| `GODWIN_API_TOKEN` | Auth for Godwin's API |
| `WEBHOOK_URL` | Where to POST results |
| `WEBHOOK_SECRET` | Shared secret for HMAC header |
| `WHISPER_MODEL` | `tiny`, `base`, `small`, default `small` |
| `CONFIDENCE_THRESHOLD` | Default `0.72`, below = `unknown` |
| `ENROLL_DIR` | Default `./enrollment_audio` |
| `EMBEDDINGS_PATH` | Default `./enrolled_voices/embeddings.json` |

## Deployment (Coolify)

```bash
docker compose up -d
```

Coolify reads `Dockerfile` and `docker-compose.yml`. The service binds to `0.0.0.0:8000`. Coolify reverse-proxies to a public URL.

## Privacy

- Audio files are deleted in code immediately after embedding + transcription, before any other step.
- Embeddings (256-dim float vectors) are non-reversible.
- Cytracom retains source audio for ~3 months, used for the Planka play-button UI (handled by Godwin's side, not this service).
- This service stores: enrolled-voice embeddings (per technician) and a per-call log of similarity scores for audit/threshold tuning. No audio.

## Open integration questions for Godwin

These are wired through env-vars but the contract is pending Godwin's confirmation:
- Exact endpoint shape of his calls-by-date-range API + audio delivery format
- Webhook URL + payload shape preference
- Auth model (HMAC, bearer, shared secret in header)
- Daily cron timing
