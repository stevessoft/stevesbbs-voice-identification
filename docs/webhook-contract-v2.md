# Webhook Payload Contract — v2 (Multi-Speaker Timeline)

**Status:** PROPOSED. Pending Godwin's confirmation before deploy to live service.
**Prepared:** 2026-04-30
**Engagement:** #2 ($175 multi-speaker timeline)

This document describes the new webhook payload shape that the voice-id
service will POST to Godwin's endpoint after the next deploy. It is a
**breaking change** from v1 — Godwin's webhook handler must be updated to
accept the new shape before the service is flipped to v2.

---

## TL;DR

The webhook now sends a `segments` array per call. Each segment is a span
of speech with one attributed speaker. Customer / non-enrolled speakers
are labeled `external_caller`. The previous single-speaker fields
(`speaker_id`, `confidence`, `scores`, `matched_via` at the top level)
are removed — those fields now live inside each segment.

---

## v1 (current, single-speaker)

```json
{
  "uuid": "3467633f-d8a7-461a-9893-c5c6bc31fdca",
  "call_id": "3467633f-d8a7-461a-9893-c5c6bc31fdca",
  "started_on": "2026-04-29T12:46:56.454632-05:00",
  "started_on_ts": 1777484817725,
  "speaker_id": "stonewall",
  "confidence": 0.961,
  "scores": {
    "auto_greeting": 0.8406,
    "isaiah": 0.9331,
    "john": 0.9476,
    "steve": 0.9193,
    "stonewall": 0.961
  },
  "transcript": "Hey, this is Stonewall calling from Steve's Computer Repair. ...",
  "transcribed_at": "2026-04-30T13:30:00Z",
  "direction": "outbound",
  "greeting_skip_seconds": 0,
  "speech_seconds": 217.98,
  "matched_via": "transcript_self_intro"
}
```

---

## v2 (new, multi-speaker timeline)

```json
{
  "uuid": "3467633f-d8a7-461a-9893-c5c6bc31fdca",
  "call_id": "3467633f-d8a7-461a-9893-c5c6bc31fdca",
  "started_on": "2026-04-29T12:46:56.454632-05:00",
  "started_on_ts": 1777484817725,
  "speakers": ["stonewall", "external_caller"],
  "segments": [
    {
      "start_s": 1.07,
      "end_s": 7.07,
      "speaker": "stonewall",
      "transcript": "Hey, this is Stonewall calling from Steve's Computer Repair. How are you?",
      "confidence": 0.901,
      "matched_via": "transcript_self_intro"
    },
    {
      "start_s": 7.07,
      "end_s": 74.78,
      "speaker": "external_caller",
      "transcript": "Doing good. Doing good. So I took a look at the device that you had brought back. ...",
      "confidence": 0.92,
      "matched_via": "no_match"
    },
    {
      "start_s": 74.78,
      "end_s": 76.78,
      "speaker": "stonewall",
      "transcript": "Or the BIOS or anything like that.",
      "confidence": 0.819,
      "matched_via": "embedding"
    }
  ],
  "transcript": "Hey, this is Stonewall calling from Steve's Computer Repair. ...",
  "transcribed_at": "2026-04-30T13:30:00Z",
  "direction": "outbound",
  "greeting_skip_seconds": 0,
  "speech_seconds": 217.98
}
```

---

## Field Reference (v2)

### Top-level

| Field | Type | Description |
|---|---|---|
| `uuid` | string | Cytracom call uuid (Godwin's existing primary key) |
| `call_id` | string | Same as `uuid`, kept as alias |
| `started_on` | string \| null | ISO 8601 call start, echoed from Cytracom |
| `started_on_ts` | int \| null | Epoch ms call start, echoed from Cytracom |
| `speakers` | string[] | Deduped list of speaker labels in order of first appearance |
| `segments` | object[] | Per-segment timeline (see below). Always at least one segment. |
| `transcript` | string | Full transcript text concatenated (kept for backward-compat / search) |
| `transcribed_at` | string | ISO 8601 UTC when transcription completed |
| `direction` | string | `"inbound"` or `"outbound"` |
| `greeting_skip_seconds` | float | Seconds of leading audio skipped (inbound only). Segment timestamps are RELATIVE TO THE FULL ORIGINAL AUDIO, so this offset is already applied to `start_s` / `end_s`. |
| `speech_seconds` | float | Total speech duration after VAD filtering |

### Per-segment (`segments[]`)

| Field | Type | Description |
|---|---|---|
| `start_s` | float | Seconds from start of original recording |
| `end_s` | float | Seconds from start of original recording |
| `speaker` | string | Speaker label (see below) |
| `transcript` | string | Speech text in this segment |
| `confidence` | float | 0.0 to 1.0 cosine similarity score for the assigned speaker |
| `matched_via` | string | How the assignment was made (see below) |

### Speaker labels

| Label | Meaning |
|---|---|
| `stonewall`, `isaiah`, `john`, `steve` | One of the 4 enrolled techs |
| `auto_greeting` | Voicemail message text. Steve's auto-attendant. |
| `external_caller` | Speaker not matching any enrolled tech. The customer. |

The set is closed: any speaker label in `speakers` will always be one of
the values above. If a 5th tech is later enrolled, that name joins the set.

### `matched_via` values

| Value | When it fires |
|---|---|
| `embedding` | Pure voice match. Top score passed both confidence floor and margin gate. |
| `transcript_self_intro` | Tech named themselves in the segment text (e.g. "this is Stonewall") and the embedding agreed enough to confirm. |
| `transcript_confirmed` | Embedding picked a tech AND the segment text confirmed by naming the same tech. |
| `transcript_voicemail` | Voicemail signature phrase detected. Always tagged `auto_greeting`. |
| `no_match` | Gates failed and no transcript clue → `external_caller` (customer). |
| `window_too_short` | Segment was shorter than 1.6s → inherits speaker from previous segment. |
| `speech_floor` | Whole call had less than 5s of speech. Single segment, all `external_caller`. |

---

## Webhook Firing Rules

The webhook **fires** when at least one segment's speaker is an enrolled
tech (i.e. `speakers` contains anything other than `external_caller` and
`auto_greeting`).

The webhook **does NOT fire** when:
- The call had no speech (dead-air)
- The call was pure voicemail
- The call was pure customer (e.g. customer left a recording but no tech ever picked up)

This keeps Godwin's DB free of rows that have no tech to attribute against.

---

## Auth (unchanged)

`Authorization: Bearer <WEBHOOK_SECRET>` — same as v1.

---

## Known V1 Behavior That's Different

1. **No top-level `speaker_id`.** The single-speaker fields are removed.
   Read `segments[*].speaker` instead.
2. **No top-level `confidence`.** Confidence is per-segment now.
3. **No top-level `scores` dictionary.** That was a debugging convenience
   field; per-segment classification doesn't expose all 5 scores per
   segment to keep the payload size reasonable on long calls.

If any of those v1 fields are critical for your handler's logic, let me
know and we can either bring them back as derived top-level fields (e.g.
`primary_speaker = speakers[0]`, `primary_confidence = max segment conf`)
or keep them under a `legacy` sub-object for transition.

---

## Known Limitation

When Whisper's VAD groups speech from multiple speakers into a single
transcript chunk (rare, but happens on calls where the customer talks
over the tech for an extended turn), the entire chunk gets attributed to
the dominant voice in that window. A future V3 could slide a 2-second
embedding window across each transcript chunk to detect speaker change
points within a single VAD chunk. Out of scope for V2.

---

## Sample Files Used for Verification

- `feedback-samples/stonewall_339.mp3` (Stonewall + customer, outbound, 3:39)
- `Voices/edge case_ steve_john_stonewall.mp3` (4-way: steve + john + stonewall + customer, ~4:35)
- `feedback-samples/isaiah_47.mp3` (customer + Isaiah, inbound, 0:47)
- `feedback-samples/stonewall_108.mp3` (Stonewall intro + customer, inbound, 1:08)

All four produced sensible multi-segment timelines under the new pipeline.

---

## Cutover Plan

1. Godwin reviews this contract.
2. Godwin updates his webhook handler to accept the new payload shape.
3. Patrick merges feature branch to `main`. Coolify auto-redeploys.
4. Patrick re-imports embeddings (existing process).
5. Smoke test: call `/process` with a known Cytracom recording and confirm
   the new payload reaches Godwin's endpoint and is processed correctly.
6. Godwin runs a sweep over a recent date range to verify behavior at scale.
