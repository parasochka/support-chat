FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
# --retries / --timeout make the build survive transient PyPI network
# blips (files.pythonhosted.org read timeouts) instead of failing outright.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir \
        --retries 5 \
        --timeout 60 \
        -r requirements.txt

# Copy the application.
COPY . .

# Railway provides $PORT; default to 8080 for local runs.
ENV PORT=8080
EXPOSE 8080

# Use shell form so $PORT is expanded at runtime.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
