from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .db import init_db
from .discovery import add_analytics_snapshot, add_trend, analytics_summary, list_trends
from .quality import validate_content
from .security import require_admin_api_token, require_worker_token, verify_webhook_secret
from .services import approve_job, claim_render_job, complete_render_job, create_job, generate_content, is_paused, list_jobs, publish_job, worker_heartbeat
from .telegram import handle_update


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
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
    }


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
def worker_claim(_: None = Depends(require_worker_token)) -> dict[str, object]:
    if is_paused():
        return {"job": None, "reason": "factory_paused"}
    job = claim_render_job()
    if job is None:
        return {"job": None, "reason": "queue_empty"}
    return {"job": {"id": job.id, "topic": job.topic, "brand": job.brand, "platform": job.platform}}


@app.post("/api/worker/{job_id}/complete")
def worker_complete(job_id: str, render_path: str, _: None = Depends(require_worker_token)) -> dict[str, object]:
    try:
        job = complete_render_job(job_id, "worker", render_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}
