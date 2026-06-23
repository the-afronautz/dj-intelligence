# syntax=docker/dockerfile:1.6

FROM python:3.11-slim

# System dependencies for librosa audio decoding (WebM/Opus, FLAC, etc.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so changes to app code don't bust the pip cache layer.
COPY vinyl_analyzer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app itself.
COPY vinyl_analyzer/ ./

# SQLite DB lives on the persistent /home Azure Files mount (also fine locally
# as just a directory). Override at runtime if needed.
ENV VINYL_DATA_DIR=/home/data \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# Gunicorn invocation matches what we'd run locally — no shell wrappers, no
# environment-discovery code paths.
CMD ["gunicorn", \
     "--bind=0.0.0.0:8000", \
     "--timeout", "180", \
     "--workers", "1", \
     "--threads", "2", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
