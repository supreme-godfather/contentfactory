from contextlib import asynccontextmanager
import asyncio
import json
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .config import get_settings
from .db import ContentJob, SessionLocal, init_db
from .discovery import add_analytics_snapshot, add_trend, analytics_summary, list_trends
from .quality import validate_content
from .security import require_admin_api_token, require_worker_token, verify_webhook_secret
from .services import approve_job, claim_render_job, complete_render_job, create_job, fail_render_job, generate_content, is_paused, list_jobs, publish_job, worker_heartbeat
from .telegram import configure_webhook, handle_update
from .youtube_oauth import connection_status, create_authorization_url, exchange_code


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    asyncio.create_task(configure_webhook())
    yield


app = FastAPI(title="Hindi Content Factory", version="0.1.0", lifespan=lifespan)


class GenerateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=240)
    platform: str = Field(default="both", pattern="^(youtube|instagram|both)$")
    brand: str = Field(default="learn_with_elite_explainer", max_length=80)


class TrendRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=240)
    source: str = Field(default="manual", max_length=80)
    source_url: str | None = Field(default=None, max_length=500)
    freshness: int = Field(default=50, ge=0, le=100)
    relevance: int = Field(default=50, ge=0, le=100)
    originality: int = Field(default=100, ge=0, le=100)
    risk: int = Field(default=0, ge=0, le=100)
    transformation_notes: str | None = Field(default=None, max_length=500)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "hindi-content-factory"}


@app.get("/api/status")
def status(_: None = Depends(require_admin_api_token)) -> dict[str, object]:
    settings = get_settings()
    return {
        "paused": is_paused(),
        "mock_publishing": settings.mock_publishing,
        "youtube_enabled": settings.youtube_enabled,
        "instagram_enabled": settings.instagram_enabled,
        "daily_publication_limit": settings.max_daily_publications,
        "youtube_oauth": connection_status(),
    }


@app.get("/api/youtube/oauth/authorize")
def youtube_authorize(_: None = Depends(require_admin_api_token)) -> dict[str, str]:
    try:
        return {"authorization_url": create_authorization_url()}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/oauth/youtube/callback")
async def youtube_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> dict[str, str]:
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth denied access: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state")
    try:
        await exchange_code(code, state)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "authorized", "provider": "youtube"}


@app.get("/api/jobs")
def jobs(_: None = Depends(require_admin_api_token)) -> list[dict[str, object]]:
    return [
        {
            "id": job.id,
            "topic": job.topic,
            "brand": job.brand,
            "platform": job.platform,
            "status": job.status,
            "content_version": job.content_version,
            "approval_version": job.approval_version,
            "stage": job.stage,
        }
        for job in list_jobs()
    ]


@app.post("/api/jobs")
def generate(request: GenerateRequest, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        job = create_job(request.topic, request.platform, request.brand)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "topic": job.topic}


@app.post("/api/jobs/{job_id}/generate-content")
def generate_job_content(job_id: str, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        job = generate_content(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}


@app.get("/api/trends")
def trends(_: None = Depends(require_admin_api_token)) -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "topic": item.topic,
            "source": item.source,
            "score": item.total_score,
            "status": item.status,
        }
        for item in list_trends()
    ]


@app.post("/api/trends")
def add_trend_candidate(request: TrendRequest, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        item = add_trend(
            request.topic,
            request.source,
            request.source_url,
            request.freshness,
            request.relevance,
            request.originality,
            request.risk,
            request.transformation_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": item.id, "topic": item.topic, "score": item.total_score}


@app.get("/api/analytics")
def analytics(_: None = Depends(require_admin_api_token)) -> list[dict[str, object]]:
    return analytics_summary()


@app.get("/api/jobs/{job_id}/quality")
def quality(job_id: str, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    from .db import SessionLocal, ContentJob

    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        report = validate_content(
            topic=job.topic,
            script=job.script_text,
            metadata_json=job.metadata_json,
            render_path=job.render_path,
        )
    return {"passed": report.passed, "score": report.score, "blockers": report.blockers}


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: str, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        job = approve_job(job_id, 0)
    except (ValueError, HTTPException) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "approval_version": job.approval_version}


@app.post("/api/jobs/{job_id}/publish")
def publish(job_id: str, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        job = publish_job(job_id)
    except (ValueError, HTTPException) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "mock": get_settings().mock_publishing}


@app.post("/telegram/webhook")
async def telegram_webhook(update: dict[str, object], _: None = Depends(verify_webhook_secret)) -> dict[str, bool]:
    await handle_update(update)
    return {"ok": True}


@app.post("/api/worker/heartbeat")
def worker_heartbeat_endpoint(worker_id: str, _: None = Depends(require_worker_token)) -> dict[str, str]:
    worker_heartbeat(worker_id)
    return {"status": "online"}


@app.post("/api/worker/claim")
def worker_claim(worker_id: str, _: None = Depends(require_worker_token)) -> dict[str, object]:
    if is_paused():
        return {"job": None, "reason": "factory_paused"}
    job = claim_render_job(worker_id)
    if job is None:
        return {"job": None, "reason": "queue_empty"}
    metadata = {}
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except json.JSONDecodeError:
        pass
    return {
        "job": {
            "id": job.id,
            "topic": job.topic,
            "brand": job.brand,
            "platform": job.platform,
            "duration_seconds": metadata.get("duration_seconds", 60),
            "script": job.script_text or "",
        }
    }


@app.post("/api/worker/{job_id}/complete")
def worker_complete(job_id: str, worker_id: str, render_path: str, _: None = Depends(require_worker_token)) -> dict[str, object]:
    try:
        job = complete_render_job(job_id, worker_id, render_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}


@app.post("/api/worker/{job_id}/media")
def worker_upload_media(
    job_id: str,
    file: UploadFile = File(...),
    _: None = Depends(require_worker_token),
) -> dict[str, str]:
    if file.content_type not in {"video/mp4", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Only MP4 media is accepted")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job or job.status != "rendering":
            raise HTTPException(status_code=409, detail="Job is not currently rendering")
    settings = get_settings()
    media_root = Path(settings.media_root).resolve()
    media_root.mkdir(parents=True, exist_ok=True)
    destination = (media_root / f"{job_id}.mp4").resolve()
    if destination.parent != media_root:
        raise HTTPException(status_code=400, detail="Invalid media destination")
    max_bytes = settings.max_media_mb * 1024 * 1024
    total = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Media file exceeds the configured size limit")
                output.write(chunk)
    finally:
        file.file.close()
    if total < 1024:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Media file is empty or invalid")
    return {"render_path": str(destination)}


@app.post("/api/worker/{job_id}/fail")
def worker_fail(job_id: str, worker_id: str, error_message: str, _: None = Depends(require_worker_token)) -> dict[str, object]:
    try:
        job = fail_render_job(job_id, worker_id, error_message)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}
