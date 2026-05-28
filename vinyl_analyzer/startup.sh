#!/bin/bash
# Note: NOT using `set -e` — we want to launch gunicorn even if optional
# steps (like ffmpeg install) fail, so the app at least serves WAV uploads.

echo "[startup] beginning at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[startup] cwd=$(pwd)  whoami=$(whoami)"
echo "[startup] PORT=${PORT:-unset}  VINYL_DATA_DIR=${VINYL_DATA_DIR:-unset}"

mkdir -p "${VINYL_DATA_DIR:-/home/data}"

# ffmpeg + libsndfile let librosa decode WebM/Opus clips from MediaRecorder.
# Without them the app still works for WAV uploads — so we don't abort
# startup if the install fails.
if command -v ffmpeg >/dev/null 2>&1; then
    echo "[startup] ffmpeg already present"
else
    echo "[startup] installing ffmpeg + libsndfile1..."
    apt-get update -y >/dev/null 2>&1 && \
        apt-get install -y --no-install-recommends ffmpeg libsndfile1 >/dev/null 2>&1 \
        && echo "[startup] ffmpeg installed" \
        || echo "[startup] WARNING: ffmpeg install failed — WebM uploads will not decode"
fi

cd "$(dirname "$0")"
echo "[startup] launching gunicorn on port ${PORT:-8000} from $(pwd)"
exec gunicorn \
    --bind=0.0.0.0:${PORT:-8000} \
    --timeout 180 \
    --workers 1 \
    --threads 2 \
    --access-logfile - \
    --error-logfile - \
    app:app
