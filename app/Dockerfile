# Small, single-stage image running as a non-root user.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8080 \
    LOCAL_STORAGE_DIR=/app/var/media

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App source.
COPY family_media_bot ./family_media_bot

# Non-root user; owns the writable media dir.
RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /app/var/media \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Liveness check hits /healthz (no downstream deps).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["python", "-m", "family_media_bot"]
