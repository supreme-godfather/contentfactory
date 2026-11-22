FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKER_POLL_SECONDS=30 \
    WORKER_ID=railway-renderer \
    WORKER_OUTPUT_DIR=/tmp/factory-media

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 worker \
    && mkdir -p /tmp/factory-media \
    && chown -R worker:worker /tmp/factory-media

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pillow==13.0.0

COPY --chown=worker:worker . /app/worker/
USER 10001

CMD ["python", "/app/worker/worker.py"]
