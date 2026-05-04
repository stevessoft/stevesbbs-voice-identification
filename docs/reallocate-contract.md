# Reallocate Endpoint Contract — Active Learning

When a tech sees a call tagged with the wrong speaker on a Planka card,
Godwin's UI calls this endpoint to fold the call's audio into the
correct tech's voice profile. After enough reallocations, the model
gets steadily more accurate on each tech's voice.

The audio itself is never persisted by the voice ID service — it is
downloaded, embedded, blended into the profile, and deleted. Only the
256-dim embedding vector remains.

---

## Endpoint

`POST https://voice-id.stevesbbs.com/reallocate`

No auth required (internal only — restrict by network/firewall if you
want this locked down later).

### Request body

```json
{
  "tech_name": "john",
  "audio_url": "https://cytracom-recording-url-for-the-misattributed-call.mp3"
}
```

| Field | Type | Notes |
|---|---|---|
| `tech_name` | string | The CORRECT tech's profile name. Must match an existing enrolled profile (lowercase, matches `enrollment_audio/<name>/`). If the tech doesn't exist yet, the call is enrolled as a NEW profile under that name — useful for first-time tech onboarding. |
| `audio_url` | string | Direct download URL to the misattributed call's audio. Cytracom's `recording_url` works. The service streams the file, embeds it, then deletes it. |

### Response

```json
{
  "ok": true,
  "tech_name": "john",
  "enrolled_speakers": ["auto_greeting", "isaiah", "john", "steve", "stonewall"]
}
```

`enrolled_speakers` is the post-reallocation profile list. Use it as a
sanity check that the merge happened.

### Errors

- `400` — invalid body (missing tech_name or audio_url)
- `500` — audio download failed, embedding failed, or no profiles file exists yet on the live service

---

## Two integration patterns

### Pattern 1: Direct reassign button

A tech sees a wrong tag, picks the correct tech from a dropdown, and
the call is immediately reallocated.

```
[Wrong: Steve] Reassign to: [John ▼] [Reassign]
                              │
                              ▼
              POST /reallocate {tech_name: "john", audio_url: ...}
                              │
                              ▼
              Update Planka card speaker label to "john"
              Re-fetch /healthz to confirm "john" still in profile list
```

**Use when:** small team, all techs trusted to reassign correctly, no
review step needed.

### Pattern 2: Flag-then-batch-retrain

Techs flag wrong calls without immediately changing the model. An admin
later reviews flagged calls and triggers retraining in a batch.

```
Tech step:
  [Flag this call as wrong]
       │
       ▼
  Mark call.flagged = true in Godwin's DB
  (no model change yet)

Admin step (Steve only):
  Open /admin/flagged-calls
  Review each: pick correct tech or "discard"
  Click [Retrain on selected]
       │
       ▼
  For each flagged call:
    POST /reallocate {tech_name: <admin's pick>, audio_url: ...}
    Mark call.flagged_resolved = true
```

**Use when:** multiple techs use the system, you want review before
the model is updated, or you want to discard noisy calls without
folding them in.

---

## Operational notes

### Embeddings are not durable across redeploys

Reallocation writes to `enrolled_voices/embeddings.json` inside the
container. When Coolify redeploys (e.g., on a code push), the file is
wiped and the service falls back to whatever is re-imported via
`/enroll/import`. To survive redeploys without losing reallocation
gains, either:

1. After every reallocation, fetch the live profile JSON and store a
   copy in Godwin's DB or repo so it can be re-imported. (Simplest.)
2. Mount a Coolify persistent volume at `/app/enrolled_voices`. (Cleaner
   long-term, requires a deploy-time config change.)

Without one of these, manual `/reallocate` calls accumulate in-memory
gains that disappear on the next deploy.

### Quality control

Each reallocation merges the new call's embedding 50/50 with the
existing profile (`(existing + new) / 2`). This means:

- One reallocation has noticeable but not dominant impact
- A short, noisy call can dilute a clean profile if folded in carelessly
- A junk call (background music, dead air) reallocated to a tech will
  measurably degrade their profile

**Recommendation:** prefer Pattern 2 (flag-then-batch) so an admin
reviews each flagged call before it touches the model.

### Verifying a reallocation worked

Re-process the original call after reallocating. If the speaker_id
flips to the corrected tech with higher confidence than before, the
fold-in took effect.

```bash
curl -X POST "https://voice-id.stevesbbs.com/process" \
  -H "Content-Type: application/json" \
  -d '{"call_id":"<uuid>","audio_url":"<recording_url>"}'
```

---

## What this endpoint does NOT do

- **Does not** change the tag on Godwin's database side. Godwin's UI is
  responsible for updating the call record's `speaker_id` field after
  the reallocation succeeds.
- **Does not** retroactively re-tag prior calls from this tech. Only
  future calls benefit from the improved profile.
- **Does not** keep an audit log of who reallocated what and when. If
  you want that, log it on Godwin's side (`reallocated_by`,
  `reallocated_at`, `reallocated_from_speaker`, `reallocated_to_speaker`).

---

## Daily sweep — how previous-day calls get tagged in the first place

Before reallocation matters, Godwin's daily cron job needs to be
running. The flow:

1. Cron fires at e.g. 2am
2. Godwin's job pulls yesterday's calls from Cytracom (he already has
   the API wrapper for this)
3. For each call: `POST https://voice-id.stevesbbs.com/process` with
   `{call_id, audio_url}`
4. The voice ID service identifies the speaker, transcribes, and POSTs
   the result back to Godwin's webhook
5. Godwin saves the result to his DB → Planka phone metrics shows the
   speaker name

OR simpler — Godwin's cron can just fire one batch sweep:

```bash
curl -X POST "https://voice-id.stevesbbs.com/sweep" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -d "$(jq -nc --arg d "$(date -d yesterday +%Y-%m-%d)" '{start_date:$d,end_date:$d}')"
```

The `/sweep` endpoint pulls Cytracom calls itself and processes them
all in one job. Use `/sweep/status?job_id=<id>` to monitor progress.

If the cron stops running, Phone Metrics will show blank Speaker
columns for the days that were missed (this is what Steve saw on May 2-3
before the manual sweep was run).
