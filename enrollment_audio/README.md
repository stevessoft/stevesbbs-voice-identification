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
- Match the call channel where possible (phone audio quality, not studio mic)
- Common formats accepted: .wav, .mp3, .m4a, .flac, .ogg

## Privacy

This folder is the only place audio is retained on disk. The embeddings file
(`enrolled_voices/embeddings.json`) contains 256-dim float vectors that are
non-reversible. Source audio in this folder can be removed after enrollment
if desired; the embeddings remain valid until you re-enroll.
