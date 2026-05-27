#!/bin/bash
set -e

# ffmpeg + libsndfile are needed to decode WebM/Opus clips from MediaRecorder.
# Azure's Python App Service image is Debian-based but doesn't ship these by
# default, so install them on first boot.
if ! command -v ffmpeg &> /dev/null; then
    apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1
fi

# Ensure the SQLite parent directory exists on the persistent /home mount.
mkdir -p "${VINYL_DATA_DIR:-/home/data}"

cd "$(dirname "$0")"

exec gunicorn \
    --bind=0.0.0.0:8000 \
    --timeout 180 \
    --workers 1 \
    --threads 2 \
    app:app
