# Enrollment Audio

Drop technician voice samples here, one folder per technician, then run:

```
python -m scripts.enroll
```

## Layout

```
enrollment_audio/
├── steve/
│   ├── sample-001.wav
│   ├── sample-002.wav
│   └── sample-003.wav
├── john/
│   └── ...
└── <other_tech>/
    └── ...
```

## Audio guidance

- 30 to 60 seconds per technician minimum, more is better
- Multiple short clips beat one long clip (averages a more stable profile)
- Natural conversational speech preferred over scripted reads
- **Phone-quality audio strongly preferred** over studio-mic recordings.
  Real call audio from Cytracom is 8kHz mono ~32 kbps. Studio recordings
  are 44.1kHz/128kbps+ and produce embeddings that don't match phone audio
  even after format conversion. The enrollment pipeline runs samples through
  a G.711 µ-law roundtrip to normalize, but starting with phone-quality
  audio gets the cleanest result.
- Best path: collect 4-6 real call recordings per tech from Cytracom (via
  the calls API or pulling from the recording_url), trim to clean speech,
  drop them here.
- Common formats accepted: .wav, .mp3, .m4a, .flac, .ogg

## Privacy

This folder is the only place audio is retained on disk. The embeddings file
(`enrolled_voices/embeddings.json`) contains 256-dim float vectors that are
non-reversible. Source audio in this folder can be removed after enrollment
if desired; the embeddings remain valid until you re-enroll.
