# How to Add or Remove a Technician

This runbook covers every change needed when a tech joins, leaves, or
the auto-greeting itself changes. Three pieces have to stay in sync for
speaker ID to stay accurate:

1. **Vocabulary hint** — biases Whisper transcription toward real names
2. **Name alias list** — maps spoken-name variations ("Jonathan") to the enrolled profile ("john")
3. **Voice profile** — the Resemblyzer embedding the new tech is matched against

All three live in different places. Touching only one will leave the
service in a half-updated state — for example, a new profile without an
alias entry will get correctly identified by embedding but their
transcript-based self-intro override won't fire.

---

## 1. Vocabulary hint (Coolify env var)

**Where:** Coolify → Voice ID project → `voice-id` application → Configuration → Environment Variables → `WHISPER_INITIAL_PROMPT`

**Default:** `Steve's Computer Repair. Technicians: Stonewall, Isaiah, John, Steve.`

**When to change:** any time a tech is added or removed. The model uses
this string to bias transcription toward known names. Without it,
phone-codec audio causes "Stonewall" to come out as "Phil Alcomb" or
similar.

**How:** edit the value in Coolify, then click **Restart** on the
application. No rebuild needed (env-only change). About 30 seconds end
to end.

**Format rule:** keep the shop-name sentence first, then `Technicians:`
followed by a comma-separated list ending with a period.

---

## 2. Name alias list (repo edit)

**Where:** `app/pipeline.py`, `TECH_NAME_ALIASES` dict near the top of the file.

**What it does:** maps every spoken form a tech might use into their
enrolled profile name. For example, "Jonathan from Steve's" maps to the
`john` profile. Without the alias, the transcript self-intro override
won't fire and identification falls back to embedding-only.

**When to change:** any time a tech is added, removed, or starts using
a new nickname on the phone.

**How (add a new tech named "Marcus" who also goes by "Marc"):**

```python
TECH_NAME_ALIASES = {
    "jonathan": "john",
    "stonewall": "stonewall",
    ...
    "marcus": "marcus",
    "marc": "marcus",
}
```

Rules:
- Keys are lowercase
- The profile name on the right side **must match the enrolled profile
  name exactly** (the directory under `enrollment_audio/` and the key
  in `embeddings.json`)
- Map every nickname/variant to the same profile name
- Always include the canonical name itself as a key (e.g., `"marcus":
  "marcus"`)

**When removing a tech:** delete every key whose value points to the
departing tech's profile.

**Deploy:** commit + push to `main`. Coolify auto-deploys on push.

---

## 3. Voice profile (admin enrollment endpoint)

This is the only step that needs an audio sample. Voice profiles are
256-dim embedding vectors stored in `enrolled_voices/embeddings.json`
on the live container. Audio itself is never retained.

### Adding a new tech

**You need:** 30-60 seconds of clean speech from the new tech, ideally
recorded off a real Cytracom call (matches the phone-codec audio the
service sees in production). One file is fine; multiple short clips is
better than one long monologue.

**Hand-off to the dev:** drop the audio file in the shared folder along
with the tech's profile name (e.g., `marcus`). The dev runs the
enrollment pipeline locally to generate the embedding, then POSTs it to
the live service:

```bash
# Local: build embedding from new audio
mkdir -p enrollment_audio/marcus
cp /path/to/marcus_sample.mp3 enrollment_audio/marcus/
python -m scripts.enroll  # writes enrolled_voices/embeddings.json

# Push the new embedding to the live service
ADMIN_SECRET="<from .credentials>"
curl -X POST "https://voice-id.stevesbbs.com/enroll/import" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -d "$(jq -c '{profiles: ., replace: true}' enrolled_voices/embeddings.json)"
```

`replace: true` overwrites the live profile set with whatever's in the
JSON. Use this when you're rebuilding the full list. To add a single
tech without disturbing the others, set `replace: false`.

**Verify:**

```bash
curl https://voice-id.stevesbbs.com/healthz
# enrolled_speakers should include the new tech
```

### Removing a tech

Delete the tech's directory under `enrollment_audio/`, re-run
`python -m scripts.enroll`, then push with `replace: true`. The live
service will only carry the remaining profiles after the import.

Cheap alternative if you don't have the original audio: edit
`enrolled_voices/embeddings.json` directly, drop the departing tech's
key, then POST the trimmed JSON via `/enroll/import` with `replace:
true`.

---

## 4. Auto-greeting changed (one-time re-enrollment)

The `auto_greeting` profile catches calls where the automated greeting
plays and no tech actually picks up. When the greeting recording itself
changes, that profile has to be rebuilt or the service will start
mistakenly tagging real techs as `auto_greeting` (and skipping the
webhook).

**Steps:**

1. Get a clean recording of the new auto-greeting (15-30 seconds is
   plenty)
2. Replace the audio in `enrollment_audio/auto_greeting/`
3. Re-run `python -m scripts.enroll`
4. Push the rebuilt embeddings via `/enroll/import` (`replace: true`)

This is rare — only triggered when Steve actually changes the greeting
script Cytracom plays at the start of incoming calls.

---

## Common gotchas

- **Profile name mismatch.** The directory name under `enrollment_audio/`,
  the key in `embeddings.json`, and the right-hand side of the alias
  dict must all be identical. A typo means the service runs but never
  matches that tech.
- **Forgetting the vocabulary hint.** If you add a tech but skip step 1,
  Whisper will mis-transcribe their name and the transcript override
  won't fire, dropping accuracy on calls where the tech self-introduces.
- **Container redeploy wipes embeddings.** `enrolled_voices/embeddings.json`
  is gitignored. After every redeploy, re-import the embeddings via
  `/enroll/import`. (A persistent volume mount can be added later if
  this becomes annoying.)
- **Audio retention.** No raw audio is stored on the service. Cytracom
  retains source recordings ~3 months. Build embeddings locally from
  audio you already have, push the JSON, delete the local audio if you
  want.
