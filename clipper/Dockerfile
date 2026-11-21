FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLIP_WORKER_ID=railway-clipper \
    CLIP_POLL_SECONDS=30 \
    CLIP_WORK_DIR=/tmp/factory-clips

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg fonts-noto-core tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 clipper \
    && mkdir -p /tmp/factory-clips \
    && chown -R clipper:clipper /tmp/factory-clips

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=clipper:clipper worker.py .

USER 10001
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/worker.py"]
