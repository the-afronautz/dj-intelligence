#!/bin/bash
echo "[startup] beginning at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[startup] cwd=$(pwd)  whoami=$(whoami)"
echo "[startup] PORT=${PORT:-unset}  VINYL_DATA_DIR=${VINYL_DATA_DIR:-unset}"

# Activate the Oryx-built virtualenv where pip installed our deps.
# Locations Oryx may produce depending on version / extraction path:
VENV_ACTIVATED=""
for CANDIDATE in \
    /home/site/wwwroot/antenv/bin/activate \
    /antenv/bin/activate \
    /tmp/*/antenv/bin/activate
do
    if [ -f "$CANDIDATE" ]; then
        # shellcheck disable=SC1090
        source "$CANDIDATE"
        VENV_ACTIVATED="$CANDIDATE"
        break
    fi
done

if [ -n "$VENV_ACTIVATED" ]; then
    echo "[startup] activated venv at: $VENV_ACTIVATED"
else
    echo "[startup] WARNING: no antenv found — gunicorn will use system Python (will likely fail)"
fi

echo "[startup] python3: $(command -v python3 || echo MISSING)"
echo "[startup] gunicorn: $(command -v gunicorn || echo MISSING)"

mkdir -p "${VINYL_DATA_DIR:-/home/data}"

# ffmpeg + libsndfile let librosa decode WebM/Opus clips from MediaRecorder.
# Install in the background so it doesn't delay gunicorn startup — WAV still
# works without ffmpeg, and WebM uploads will succeed once the install
# completes a few seconds after the app comes up.
(
    if ! command -v ffmpeg >/dev/null 2>&1; then
        echo "[startup-bg] installing ffmpeg + libsndfile1..."
        if apt-get update -y >/dev/null 2>&1 && \
           apt-get install -y --no-install-recommends ffmpeg libsndfile1 >/dev/null 2>&1; then
            echo "[startup-bg] ffmpeg installed"
        else
            echo "[startup-bg] WARNING: ffmpeg install failed — WebM uploads will not decode"
        fi
    fi
) &

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
