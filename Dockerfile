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

# --- glibc allocator: give freed memory BACK to the OS -----------------------
# Both roles hand multi-MB buffers to worker threads (asyncio.to_thread: Pillow
# decodes, media file reads, base64 data URLs for the vision calls). Two glibc
# defaults conspire to keep that memory forever:
#   1. up to 8 x ncores malloc ARENAS, one per allocating thread. Freed blocks
#      stay on their own arena's free list, and malloc_trim() cannot reclaim a
#      non-main arena that is not empty at the top.
#   2. a DYNAMIC mmap threshold that ratchets up to 32MB as mmapped blocks are
#      freed — so the first media buffers are mmapped (returned to the OS on
#      free) but every later one is served from the heap and retained.
# Together they make a process climb and plateau instead of falling back to its
# idle footprint. Pinning both is a pure-config fix: on a 24-thread multi-MB
# workload, RSS retained after freeing everything went from 106MB (with
# malloc_trim reclaiming nothing) to 10MB. The cost is a few more mmap syscalls,
# which is free for a service that idles under 1% CPU.
ENV MALLOC_ARENA_MAX=2 \
    MALLOC_MMAP_THRESHOLD_=131072

WORKDIR /app

# ffmpeg: the media normalizer re-encodes uploaded retention videos to
# Telegram-friendly MP4 (H.264) and extracts poster frames with it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- precompile the stdlib ---------------------------------------------------
# python:3.11-slim ships the stdlib as .py ONLY: the official image runs
# `find /usr/local … -name '*.pyc' -exec rm -rf` right after `make install` and
# installs setuptools/wheel with --no-compile. Combined with
# PYTHONDONTWRITEBYTECODE=1 above (which stops the container from ever CACHING
# the result), every process start re-parses and re-compiles the ~180 stdlib
# modules this service imports and throws the bytecode away. Measured on exactly
# that import set: 588ms -> 125ms per start, for both roles.
#
# compileall writes .pyc even under PYTHONDONTWRITEBYTECODE=1 — neither
# compileall nor py_compile consults sys.dont_write_bytecode; the flag gates
# only the IMPORT system's write-back. The interpreter still READS those .pyc
# under it, and a stale one is ignored in favour of the source (never wrong,
# only slower), so the env var stays: it keeps the container's writable layer
# clean.
#
# site-packages is excluded: pip byte-compiles everything it installs, so those
# .pyc already exist, and compiling the base image's pip/setuptools/wheel would
# add ~12MB of bytecode nothing imports at runtime. This layer depends only on
# the base image, so it is cached on every subsequent build.
#
# `|| echo` on purpose: this layer is a pure optimization, so a single stdlib
# file the base image ships that will not compile (a Python-2 fixture in some
# future `tests/` directory the image stops stripping) must never block a
# deploy. compileall compiles everything it can and names what it could not on
# stderr, which stays visible in the build log.
RUN python -m compileall -q -x '/site-packages/' \
      "$(python -c 'import sysconfig; print(sysconfig.get_paths()["stdlib"])')" \
      || echo "stdlib precompile incomplete (non-fatal; slower process start)"

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application.
COPY . .

# The built admin SPA (main.py serves it at /admin when the dir exists).
COPY --from=admin-build /build/dist ./admin/dist

# --- precompile the application ----------------------------------------------
# .dockerignore strips every host __pycache__/*.pyc on purpose (host bytecode
# can carry a different magic number or stale content), so `COPY . .` brings
# .py only, so each entry point parses every module it imports on every start
# (the web half loads far more of the tree than the worker, which is why their
# savings differ below).
# Measured: `import app.main` 2086ms -> 1858ms, `import app.worker` 251ms ->
# 149ms — the worker's share is larger because it deliberately avoids importing
# app.main, so app parsing dominates its small footprint. ONE image layer serves
# BOTH Railway services; nothing in the start commands changes.
#
# This MUST stay the last step that touches Python source: a later COPY over a
# .py leaves its .pyc stale, which is harmless (the import falls back to the
# source) but silently loses the speedup. mcp_server IS runtime code —
# app/api/admin.py lazily imports mcp_server.catalog/.client. scripts/ is
# omitted: nothing imports it at runtime.
#
# compileall exits non-zero on a syntax error, so this doubles as a free compile
# check of everything that ships.
RUN python -m compileall -q app mcp_server

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
