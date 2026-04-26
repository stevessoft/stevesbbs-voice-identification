FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System deps:
#   ffmpeg, libsndfile1 — audio decoding for librosa / soundfile / faster-whisper
#   build-essential, python3-dev — to compile webrtcvad (resemblyzer's C extension dep)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only torch FIRST (avoids ~3GB CUDA libs from the default index).
# Resemblyzer needs torch; faster-whisper does not (it uses ctranslate2).
RUN pip install --upgrade pip && \
    pip install --index-url https://download.pytorch.org/whl/cpu torch==2.4.1

COPY pyproject.toml ./
RUN pip install -e .

COPY app ./app
COPY scripts ./scripts
COPY enrollment_audio ./enrollment_audio
COPY enrolled_voices ./enrolled_voices

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
