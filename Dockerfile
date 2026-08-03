# --- stage 1: build the React Admin SPA (admin/ -> admin/dist) --------------
FROM node:20-alpine AS admin-build

WORKDIR /build
COPY admin/package.json admin/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY admin/ ./
# No VITE_API_URL: the SPA is served same-origin by the FastAPI service, so
# API calls use relative URLs.
RUN npm run build

# --- stage 2: the FastAPI service --------------------------------------------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg: the media normalizer re-encodes uploaded retention videos to
# Telegram-friendly MP4 (H.264) and extracts poster frames with it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application.
COPY . .

# The built admin SPA (main.py serves it at /admin when the dir exists).
COPY --from=admin-build /build/dist ./admin/dist

# Railway provides $PORT; default to 8080 for local runs.
ENV PORT=8080
EXPOSE 8080

# ONE image, two services (see railway.toml): this CMD is the WEB half. The
# background half is the same image started with `python -m app.worker` and
# SERVICE_ROLE=worker — a start-command override on its own Railway service, so
# the two always ship the same code and the image needs no second entrypoint.
# The worker binds $PORT too (a minimal health endpoint over the loops'
# heartbeats), so the platform healthcheck works on both services.
# Use shell form so $PORT is expanded at runtime.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
