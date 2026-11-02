FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-dejavu-core fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Bake the multilingual Whisper model into the image so production does not
# download a speech model during a user's first editing request.
RUN mkdir -p /opt/models/whisper-base && python -c "from faster_whisper.utils import download_model; download_model('base', output_dir='/opt/models/whisper-base')"
COPY app.py engine.py ./
COPY static/ ./static/

# The checked-out source never contains user's uploads or credentials.
RUN mkdir -p /data
ENV REEL_DATA_DIR=/data MAX_UPLOAD_MB=450 WHISPER_MODEL=/opt/models/whisper-base PORT=10000
EXPOSE 10000
CMD ["/bin/sh","-c","exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --timeout-keep-alive 15"]
