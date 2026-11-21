from contextlib import asynccontextmanager
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from .automation import automation_enabled, automation_loop, run_automation_cycle, set_automation_enabled
from .backups import create_backup_archive, restore_backup_archive
from .config import get_settings
from .db import ClipCandidate, ContentJob, SessionLocal, init_db
from .clipping import (
    claim_clip_job,
    complete_clip_job,
    create_clip_job,
    fail_clip_job,
    list_clip_candidates,
    list_clip_jobs,
    promote_clip_candidate,
    register_clip_candidate,
    retry_clip_job,
    renew_clip_lease,
    review_clip_candidate,
)
from sqlalchemy import select
from .discovery import add_analytics_snapshot, add_trend, analytics_recommendations, analytics_summary, list_trends, monetisation_readiness
from .encryption import validate_youtube_encryption_configuration
from .quality import validate_content
from .security import require_admin_api_token, require_worker_token, verify_webhook_secret
from .services import approve_job, approve_script, claim_render_job, complete_render_job, create_job, fail_render_job, generate_assets, generate_content, is_paused, list_jobs, publish_job, retry_job, worker_heartbeat
from .telegram import configure_webhook, handle_update, telegram_polling_loop
from .youtube_oauth import connection_status, create_authorization_url, exchange_code, sync_channel_analytics


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_youtube_encryption_configuration()
    init_db()
    settings = get_settings()
    telegram_task = asyncio.create_task(
        telegram_polling_loop()
        if settings.telegram_mode == "polling"
        else configure_webhook()
    )
    automation_task = asyncio.create_task(automation_loop())
    try:
        yield
    finally:
        for task in (telegram_task, automation_task):
            task.cancel()
        for task in (telegram_task, automation_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Hindi Content Factory", version="0.1.0", lifespan=lifespan)


class GenerateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=240)
    platform: str | None = Field(default=None, pattern="^(youtube|instagram|both)$")
    brand: str = Field(default="learn_with_elite_explainer", max_length=80)


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ClipCreateRequest(BaseModel):
    source_url: str = Field(min_length=1, max_length=2048)
    source_kind: str = Field(pattern="^(vod|live)$")
    capture_seconds: int | None = Field(default=None, ge=90, le=1800)
    request_id: str | None = Field(default=None, max_length=160)


class ClipReviewRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")


class ClipLeaseRequest(BaseModel):
    stage: str = Field(pattern="^(ingest|analyse|render|upload)$")


class ClipCompleteRequest(BaseModel):
    duration_ms: int = Field(ge=45000, le=8 * 60 * 60 * 1000)
    extractor: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=300)
    actual_kind: str = Field(pattern="^(vod|live)$")
    caption_source: str = Field(default="none", max_length=80)
    audio_signal_events: int = Field(default=0, ge=0, le=10000)
    source_id: str = Field(default="", max_length=160)
    creator: str = Field(default="", max_length=160)


class ClipFailRequest(BaseModel):
    error_code: str = Field(min_length=1, max_length=200)


class QueueCleanupRequest(BaseModel):
    confirmation: str = Field(pattern="^CLEAR_PENDING_QUEUES$")


class PublishingStateRequest(BaseModel):
    live_enabled: bool
    youtube_enabled: bool
    instagram_enabled: bool


class VoiceConfigurationRequest(BaseModel):
    provider: str = Field(pattern="^(fish_audio|minimax|elevenlabs)$")
    voice_id: str = Field(min_length=1, max_length=160)
    model_id: str = Field(min_length=1, max_length=160)
    activate: bool = False


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


def _policy_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title} · Content Factory</title>"
        "<style>body{max-width:780px;margin:48px auto;padding:0 20px;"
        "font:16px/1.65 system-ui;color:#172033;background:#f7f8fb}"
        "main{background:#fff;padding:32px;border-radius:18px;box-shadow:0 8px 32px #17203312}"
        "h1,h2{line-height:1.2}a{color:#175cd3}</style></head>"
        f"<body><main><h1>{title}</h1><p><strong>Content Factory</strong></p>{body}"
        "<p><small>Last updated: November 21, 2026</small></p></main></body></html>"
    )


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy() -> HTMLResponse:
    return _policy_page(
        "Privacy Policy",
        "<p>Content Factory is a private, owner-operated content creation and publishing tool. "
        "It uses Meta account information only to connect the owner's selected Instagram "
        "Professional account and publish content that the owner explicitly approves.</p>"
        "<h2>Data we process</h2><p>We may process the Instagram account ID, username, encrypted "
        "access token, publication metadata, media URLs, and publication results required to "
        "operate the service.</p>"
        "<h2>How data is used</h2><p>Data is used only for account verification, approved media "
        "publishing, status reporting, and troubleshooting. We do not sell personal data.</p>"
        "<h2>Storage and security</h2><p>Credentials are encrypted at rest, access is restricted "
        "to the configured owner, and publishing remains approval-gated.</p>"
        "<h2>Deletion</h2><p>See the <a href='/data-deletion'>User Data Deletion instructions</a>."
        "</p><h2>Contact</h2><p>Email: sheoranstudios@gmail.com</p>",
    )


@app.get("/data-deletion", response_class=HTMLResponse)
def data_deletion_instructions() -> HTMLResponse:
    return _policy_page(
        "User Data Deletion",
        "<p>To remove Instagram data stored by Content Factory:</p>"
        "<ol><li>Open the private Content Factory Telegram bot.</li>"
        "<li>Open Settings, then Accounts.</li>"
        "<li>Disconnect the Instagram account and confirm the action.</li>"
        "<li>Revoke the app under Facebook Settings, Business Integrations if desired.</li></ol>"
        "<p>You may also request deletion by emailing sheoranstudios@gmail.com with the subject "
        "<strong>Content Factory Data Deletion</strong>. Include the Instagram username, but do "
        "not send passwords or access tokens. Requests are verified and completed within 30 days.</p>"
        "<p>Deletion removes stored Instagram identifiers and encrypted platform credentials. "
        "Records that must be retained for security, fraud prevention, or legal compliance may "
        "be retained only for the required period.</p>",
    )


@app.get("/terms", response_class=HTMLResponse)
def terms_of_service() -> HTMLResponse:
    return _policy_page(
        "Terms of Service",
        "<p>Content Factory is provided for use by its configured owner and authorized agency "
        "clients. Users are responsible for their source material, account permissions, platform "
        "compliance, provider charges, and final publication decisions.</p>"
        "<p>The service is provided as-is without guarantees of views, monetization, availability, "
        "or income. Generated scripts, media, captions, and metadata require human review before "
        "publication.</p><p>Contact: sheoranstudios@gmail.com</p>",
    )


@app.get("/ready")
def ready() -> dict[str, str]:
    """Readiness check that verifies the database, unlike liveness."""
    from sqlalchemy import text

    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database_not_ready") from exc
    return {"status": "ready", "service": "hindi-content-factory"}


@app.get("/media/{filename}")
def public_media(filename: str) -> FileResponse:
    """Serve completed videos and generated platform covers.

    Only filenames explicitly attached to a completed job are accepted;
    source assets and arbitrary paths are never exposed.
    """
    if Path(filename).name != filename or not filename.endswith((".mp4", ".jpg")):
        raise HTTPException(status_code=404, detail="Media not found")
    with SessionLocal() as session:
        if filename.endswith(".mp4"):
            job = session.scalar(select(ContentJob).where(ContentJob.render_path.endswith(filename)))
        else:
            job = session.scalar(
                select(ContentJob).where(
                    ContentJob.status.in_(("approved", "published")),
                    ContentJob.metadata_json.contains(f'"{filename}"'),
                )
            )
            if job is not None:
                try:
                    covers = json.loads(job.metadata_json or "{}").get("thumbnails", {})
                except json.JSONDecodeError:
                    covers = {}
                if filename not in covers.values():
                    job = None
        if job is None or job.status not in {"approved", "published"}:
            raise HTTPException(status_code=404, detail="Media not found")
    path = (Path(get_settings().media_root).resolve() / filename).resolve()
    media_root = Path(get_settings().media_root).resolve()
    if path.parent != media_root or not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    media_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"
    return FileResponse(path, media_type=media_type)

@app.get("/api/status")
def status(_: None = Depends(require_admin_api_token)) -> dict[str, object]:
    settings = get_settings()
    from .hq import get_publish_target, is_mock_publishing
    from .platform_credentials import platform_enabled
    from .provider_credentials import (
        get_provider_for_role,
        media_generation_enabled,
        no_paid_providers_enabled,
        provider_status,
    )

    return {
        "paused": is_paused(),
        "mock_publishing": is_mock_publishing(),
        "youtube_enabled": platform_enabled("youtube", settings.youtube_enabled),
        "instagram_enabled": platform_enabled("instagram", settings.instagram_enabled),
        "daily_publication_limit": settings.max_daily_publications,
        "automation_enabled": automation_enabled(),
        "platform": get_publish_target(),
        "youtube_oauth": connection_status(),
        "media_generation_enabled": media_generation_enabled(),
        "no_paid_providers": no_paid_providers_enabled(),
        "video_provider": get_provider_for_role("video"),
        "provider_roles": {
            role: get_provider_for_role(role)
            for role in ("script", "image", "voice", "video")
        },
        "provider_status": provider_status(),
    }


class PaidProviderToggleRequest(BaseModel):
    no_paid: bool


@app.post("/api/providers/paid-mode")
def paid_provider_mode(
    request: PaidProviderToggleRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, bool]:
    from .provider_credentials import set_no_paid_providers_enabled

    set_no_paid_providers_enabled(request.no_paid)
    return {"no_paid": request.no_paid}


class ProviderRoleRequest(BaseModel):
    role: str = Field(default="video", min_length=1, max_length=20)
    provider: str = Field(min_length=1, max_length=40)


@app.post("/api/providers/video/select")
def select_video_provider(
    request: ProviderRoleRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, str]:
    from .provider_credentials import set_provider_for_role

    try:
        set_provider_for_role("video", request.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"video_provider": request.provider}


@app.post("/api/providers/role/select")
def select_provider_role(
    request: ProviderRoleRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, str]:
    from .provider_credentials import set_provider_for_role

    try:
        set_provider_for_role(request.role, request.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"role": request.role, "provider": request.provider}


class MediaGenerationToggleRequest(BaseModel):
    enabled: bool


@app.post("/api/media-generation/toggle")
def media_generation_toggle(
    request: MediaGenerationToggleRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, bool]:
    from .provider_credentials import set_media_generation_enabled

    set_media_generation_enabled(request.enabled)
    return {"enabled": request.enabled}


class PauseToggleRequest(BaseModel):
    paused: bool


@app.post("/api/pause/toggle")
def pause_toggle(
    request: PauseToggleRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, bool]:
    from .services import set_paused

    set_paused(request.paused)
    return {"paused": request.paused}


@app.get("/api/backup/export")
def backup_export(_: None = Depends(require_admin_api_token)) -> FileResponse:
    """Create an authenticated portable database-and-media archive."""
    path = create_backup_archive()
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=path.name,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@app.post("/api/backup/restore")
async def backup_restore(
    backup: UploadFile = File(...),
    _: None = Depends(require_admin_api_token),
) -> dict[str, int]:
    """Restore only when explicitly enabled and the destination is empty."""
    settings = get_settings()
    if not settings.backup_restore_enabled:
        raise HTTPException(status_code=403, detail="backup_restore_disabled")
    archive_path: Path | None = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(prefix="factory-restore-", suffix=".tar.gz", delete=False) as handle:
            archive_path = Path(handle.name)
            while chunk := await backup.read(1024 * 1024):
                size += len(chunk)
                if size > settings.backup_max_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="backup_too_large")
                handle.write(chunk)
        try:
            return restore_backup_archive(archive_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await backup.close()
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)


@app.get("/api/youtube/oauth/authorize")
def youtube_authorize(_: None = Depends(require_admin_api_token)) -> dict[str, str]:
    try:
        return {"authorization_url": create_authorization_url()}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/oauth/youtube/callback")
async def youtube_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth denied access: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state")
    try:
        await exchange_code(code, state)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HTMLResponse(
        content=(
            "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>YouTube connected</title>"
            "<body style=\"font:16px system-ui;max-width:560px;margin:12vh auto;padding:24px;"
            "background:#101820;color:#f5f7fa;border-radius:16px\">"
            "<h1>YouTube connected</h1>"
            "<p>Your authorization was saved securely. Return to your private Telegram bot, "
            "open <b>Controls → Channel accounts</b>, and tap "
            "<b>Verify YouTube channel</b>.</p>"
            "<p>This does not publish anything. Posting remains behind your bot controls "
            "and approval step.</p></body></html>"
        )
    )


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
            "script_approval_version": (
                json.loads(job.metadata_json or "{}").get("script_approval_version")
                if job.metadata_json
                else None
            ),
            "stage": job.stage,
            "render_path": job.render_path,
            "error_message": job.error_message,
            "asset_keys": sorted(
                json.loads(job.metadata_json or "{}").get("assets", {}).keys()
            ) if job.metadata_json else [],
            "video_provider": json.loads(job.metadata_json or "{}").get(
                "video_provider", "off"
            ) if job.metadata_json else "off",
        }
        for job in list_jobs()
    ]


@app.post("/api/jobs")
def generate(request: GenerateRequest, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        from .hq import get_publish_target

        job = create_job(request.topic, request.platform or get_publish_target(), request.brand)
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


def _generate_content_background(job_id: str) -> None:
    try:
        generate_content(job_id)
    except Exception as exc:
        from .db import ContentJob, SessionLocal

        with SessionLocal() as session:
            job = session.get(ContentJob, job_id)
            if job is not None and job.status in {"queued", "drafting_script"}:
                job.status = "failed"
                job.stage = "script"
                job.error_message = f"{type(exc).__name__}: script generation failed"
                session.commit()


@app.post("/api/jobs/{job_id}/generate-content/async", status_code=202)
def generate_job_content_async(
    job_id: str,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    background_tasks.add_task(_generate_content_background, job_id)
    return {"id": job_id, "status": "generation_started", "stage": "topic"}


@app.post("/api/jobs/{job_id}/script/approve")
def approve_job_script(
    job_id: str,
    request: VersionRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    try:
        job = approve_script(job_id, 0, request.expected_version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}


def _generate_assets_background(job_id: str, expected_version: int) -> None:
    try:
        generate_assets(job_id, expected_version)
    except Exception as exc:
        from .db import ContentJob, SessionLocal

        with SessionLocal() as session:
            job = session.get(ContentJob, job_id)
            if (
                job is not None
                and job.content_version == expected_version
                and job.status in {"queued", "generating_assets"}
            ):
                job.status = "failed"
                job.stage = "assets"
                job.error_message = f"{type(exc).__name__}: asset generation failed"
                session.commit()


@app.post("/api/jobs/{job_id}/assets/generate/async", status_code=202)
def generate_job_assets_async(
    job_id: str,
    request: VersionRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    background_tasks.add_task(
        _generate_assets_background,
        job_id,
        request.expected_version,
    )
    return {"id": job_id, "status": "asset_generation_started", "stage": "assets"}


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


@app.get("/api/analytics/recommendations")
def analytics_recs(_: None = Depends(require_admin_api_token)) -> dict[str, object]:
    return analytics_recommendations()


@app.get("/api/analytics/monetisation")
def analytics_monetisation(_: None = Depends(require_admin_api_token)) -> dict[str, object]:
    return monetisation_readiness()


@app.post("/api/automation/run")
def automation_run(_: None = Depends(require_admin_api_token)) -> dict[str, object]:
    return run_automation_cycle()


@app.get("/api/automation/status")
def automation_status(_: None = Depends(require_admin_api_token)) -> dict[str, object]:
    settings = get_settings()
    return {
        "enabled": automation_enabled(),
        "interval_seconds": settings.automation_interval_seconds,
        "min_trend_score": settings.automation_min_trend_score,
        "platform": "instagram",
        "paused": is_paused(),
    }


class AutomationToggleRequest(BaseModel):
    enabled: bool


@app.post("/api/automation/toggle")
def automation_toggle(request: AutomationToggleRequest, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    set_automation_enabled(request.enabled)
    return {"enabled": automation_enabled(), "platform": "youtube"}


@app.post("/api/youtube/analytics/sync")
def youtube_analytics_sync(_: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        return sync_channel_analytics()
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    return {"passed": report.passed, "score": report.score, "blockers": report.blockers, "warnings": report.warnings}


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: str, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        job = approve_job(job_id, 0)
    except (ValueError, HTTPException) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "approval_version": job.approval_version}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: str, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        job = retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "retry_count": job.retry_count}


@app.post("/api/jobs/{job_id}/publish")
def publish(job_id: str, _: None = Depends(require_admin_api_token)) -> dict[str, object]:
    try:
        job = publish_job(job_id)
    except (ValueError, HTTPException) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    from .hq import is_mock_publishing

    return {"id": job.id, "status": job.status, "mock": is_mock_publishing()}


@app.post("/api/clips", status_code=202)
def create_clip(
    request: ClipCreateRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    try:
        job = create_clip_job(
            request.source_url,
            request.source_kind,
            capture_seconds=request.capture_seconds,
            request_id=request.request_id,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "source_kind": job.source_kind,
    }


@app.post("/api/admin/queues/clear")
def clear_queues(
    request: QueueCleanupRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, int]:
    from .maintenance import clear_pending_queues

    try:
        return clear_pending_queues(request.confirmation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/publishing/state")
def set_publishing_state(
    request: PublishingStateRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, bool]:
    from .hq import set_mock_publishing
    from .platform_credentials import set_platform_enabled
    from .automation import set_automation_enabled
    from .services import set_paused

    set_mock_publishing(not request.live_enabled)
    set_platform_enabled("youtube", request.youtube_enabled)
    set_platform_enabled("instagram", request.instagram_enabled)
    set_automation_enabled(False)
    set_paused(False)
    return {
        "live_enabled": request.live_enabled,
        "youtube_enabled": request.youtube_enabled,
        "instagram_enabled": request.instagram_enabled,
        "human_approval_required": True,
        "automation_enabled": False,
    }


@app.post("/api/admin/providers/voice/configure")
def configure_voice_provider(
    request: VoiceConfigurationRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    from .provider_credentials import (
        provider_status,
        set_elevenlabs_tts_model,
        set_elevenlabs_voice_id,
        set_fish_audio_model_id,
        set_fish_audio_reference_id,
        set_minimax_speech_model,
        set_minimax_voice_id,
        set_provider_for_role,
    )

    try:
        if request.provider == "fish_audio":
            set_fish_audio_reference_id(request.voice_id)
            set_fish_audio_model_id(request.model_id)
        elif request.provider == "minimax":
            set_minimax_voice_id(request.voice_id)
            set_minimax_speech_model(request.model_id)
        else:
            set_elevenlabs_voice_id(request.voice_id)
            set_elevenlabs_tts_model(request.model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ready = bool(provider_status().get(request.provider))
    if request.activate and not ready:
        raise HTTPException(status_code=409, detail="Provider API key is not configured")
    if request.activate:
        set_provider_for_role("voice", request.provider)
    return {
        "provider": request.provider,
        "voice_id": request.voice_id,
        "model_id": request.model_id,
        "ready": ready,
        "active": request.activate and ready,
    }


@app.get("/api/clips")
def clips(_: None = Depends(require_admin_api_token)) -> list[dict[str, object]]:
    return [
        {
            "id": job.id,
            "source": job.source_display,
            "source_kind": job.source_kind,
            "capture_seconds": job.capture_seconds,
            "status": job.status,
            "stage": job.stage,
            "attempt": job.attempt,
            "error_message": job.error_message,
            "created_at": job.created_at.isoformat(),
        }
        for job in list_clip_jobs()
    ]


@app.get("/api/clips/{job_id}/candidates")
def clip_candidates(
    job_id: str,
    _: None = Depends(require_admin_api_token),
) -> list[dict[str, object]]:
    return [
        {
            "id": candidate.id,
            "rank": candidate.rank,
            "start_ms": candidate.start_ms,
            "end_ms": candidate.end_ms,
            "score": candidate.score,
            "title": candidate.title,
            "reason": candidate.reason,
            "status": candidate.status,
            "size_bytes": candidate.size_bytes,
        }
        for candidate in list_clip_candidates(job_id)
    ]


@app.post("/api/clips/{job_id}/retry")
def retry_clip(
    job_id: str,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    try:
        job = retry_clip_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}


@app.post("/api/clips/candidates/{candidate_id}/review")
def review_clip(
    candidate_id: str,
    request: ClipReviewRequest,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    try:
        candidate = review_clip_candidate(candidate_id, request.decision, 0)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": candidate.id, "status": candidate.status}


@app.post("/api/clips/candidates/{candidate_id}/promote")
def promote_clip(
    candidate_id: str,
    platform: str,
    _: None = Depends(require_admin_api_token),
) -> dict[str, object]:
    try:
        job = promote_clip_candidate(candidate_id, platform, 0)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "platform": job.platform}


@app.get("/api/clips/candidates/{candidate_id}/media")
def clip_candidate_media(
    candidate_id: str,
    _: None = Depends(require_admin_api_token),
) -> FileResponse:
    with SessionLocal() as session:
        candidate = session.get(ClipCandidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Clip candidate not found")
        filename = candidate.render_filename
    media_root = Path(get_settings().media_root).resolve()
    path = (media_root / filename).resolve()
    if path.parent != media_root or not path.is_file():
        raise HTTPException(status_code=404, detail="Clip media not found")
    return FileResponse(path, media_type="video/mp4", filename=filename)


@app.post("/telegram/webhook")
async def telegram_webhook(update: dict[str, object], _: None = Depends(verify_webhook_secret)) -> dict[str, bool]:
    await handle_update(update)
    return {"ok": True}


@app.post("/api/clip-worker/claim")
def clip_worker_claim(
    worker_id: str,
    _: None = Depends(require_worker_token),
) -> dict[str, object]:
    claimed = claim_clip_job(worker_id)
    if claimed is None:
        return {"job": None, "reason": "queue_empty"}
    job, lease_token, source_url = claimed
    settings = get_settings()
    return {
        "job": {
            "id": job.id,
            "attempt": job.attempt,
            "lease_token": lease_token,
            "source_url": source_url,
            "source_kind": job.source_kind,
            "capture_seconds": job.capture_seconds,
            "limits": {
                "max_source_bytes": settings.max_clip_source_mb * 1024 * 1024,
                "max_source_duration_ms": 8 * 60 * 60 * 1000,
                "candidate_count": 3,
                "candidate_min_ms": 15000,
                "candidate_target_ms": 30000,
                "candidate_max_ms": 60000,
                "max_candidate_bytes": 49 * 1024 * 1024,
            },
            "render": {
                "width": 1080,
                "height": 1920,
                "fps": 30,
                "captions": "burned_ass",
            },
        }
    }


@app.post("/api/clip-worker/jobs/{job_id}/lease/renew")
def clip_worker_renew(
    job_id: str,
    worker_id: str,
    request: ClipLeaseRequest,
    lease_token: str = Header(alias="X-Clip-Lease-Token"),
    _: None = Depends(require_worker_token),
) -> dict[str, object]:
    try:
        job = renew_clip_lease(job_id, worker_id, lease_token, request.stage)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "stage": job.stage, "lease_expires_at": job.lease_expires_at}


@app.post("/api/clip-worker/jobs/{job_id}/candidates/{rank}")
def clip_worker_upload_candidate(
    job_id: str,
    rank: int,
    worker_id: str,
    start_ms: int,
    end_ms: int,
    score: int,
    title: str,
    reason: str = "",
    transcript_excerpt: str = "",
    file: UploadFile = File(...),
    youtube_thumbnail: UploadFile = File(...),
    instagram_thumbnail: UploadFile = File(...),
    lease_token: str = Header(alias="X-Clip-Lease-Token"),
    _: None = Depends(require_worker_token),
) -> dict[str, object]:
    media_root = Path(get_settings().media_root).resolve()
    media_root.mkdir(parents=True, exist_ok=True)
    filename = f"{job_id}-clip-{rank}.mp4"
    destination = (media_root / filename).resolve()
    youtube_thumbnail_filename = f"{job_id}-clip-{rank}-youtube.jpg"
    instagram_thumbnail_filename = f"{job_id}-clip-{rank}-instagram.jpg"
    youtube_thumbnail_path = (media_root / youtube_thumbnail_filename).resolve()
    instagram_thumbnail_path = (media_root / instagram_thumbnail_filename).resolve()
    if destination.parent != media_root:
        raise HTTPException(status_code=400, detail="Invalid clip destination")
    maximum = 49 * 1024 * 1024
    total = 0
    digest = hashlib.sha256()
    prefix = b""
    created_paths = [destination, youtube_thumbnail_path, instagram_thumbnail_path]
    try:
        with destination.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise HTTPException(status_code=413, detail="Clip candidate exceeds 49 MB")
                if len(prefix) < 32:
                    prefix = (prefix + chunk)[:32]
                digest.update(chunk)
                output.write(chunk)
        if total < 1024 or b"ftyp" not in prefix:
            raise HTTPException(status_code=400, detail="Clip candidate is not a valid MP4")
        for upload, target in (
            (youtube_thumbnail, youtube_thumbnail_path),
            (instagram_thumbnail, instagram_thumbnail_path),
        ):
            data = upload.file.read(6 * 1024 * 1024)
            if len(data) < 1024 or len(data) > 5 * 1024 * 1024 or not data.startswith(b"\xff\xd8\xff"):
                raise HTTPException(status_code=400, detail="Clip thumbnail is not a valid JPEG")
            target.write_bytes(data)
        candidate = register_clip_candidate(
            job_id,
            worker_id,
            lease_token,
            rank=rank,
            start_ms=start_ms,
            end_ms=end_ms,
            score=score,
            title=title,
            reason=reason,
            transcript_excerpt=transcript_excerpt,
            filename=filename,
            youtube_thumbnail_filename=youtube_thumbnail_filename,
            instagram_thumbnail_filename=instagram_thumbnail_filename,
            checksum=digest.hexdigest(),
            size_bytes=total,
        )
    except Exception:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    return {"id": candidate.id, "rank": candidate.rank, "status": candidate.status}


@app.post("/api/clip-worker/jobs/{job_id}/transcribe")
def clip_worker_transcribe(
    job_id: str,
    worker_id: str,
    file: UploadFile = File(...),
    lease_token: str = Header(alias="X-Clip-Lease-Token"),
    _: None = Depends(require_worker_token),
) -> dict[str, object]:
    try:
        renew_clip_lease(job_id, worker_id, lease_token, "analyse")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    from .provider_credentials import no_paid_providers_enabled, provider_status

    if no_paid_providers_enabled() or not provider_status().get("fish_audio"):
        raise HTTPException(status_code=409, detail="fish_transcription_unavailable")
    maximum = 25 * 1024 * 1024
    data = file.file.read(maximum + 1)
    if not data or len(data) > maximum:
        raise HTTPException(status_code=413, detail="Transcription audio exceeds 25 MB")
    from .voice_providers import transcribe_fish_audio

    try:
        return transcribe_fish_audio(data, file.filename or "clip-audio.mp3")
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"fish_transcription_failed:{type(exc).__name__}",
        ) from exc


@app.post("/api/clip-worker/jobs/{job_id}/complete")
def clip_worker_complete(
    job_id: str,
    worker_id: str,
    request: ClipCompleteRequest,
    lease_token: str = Header(alias="X-Clip-Lease-Token"),
    _: None = Depends(require_worker_token),
) -> dict[str, object]:
    try:
        job = complete_clip_job(
            job_id,
            worker_id,
            lease_token,
            request.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}


@app.post("/api/clip-worker/jobs/{job_id}/fail")
def clip_worker_fail(
    job_id: str,
    worker_id: str,
    request: ClipFailRequest,
    lease_token: str = Header(alias="X-Clip-Lease-Token"),
    _: None = Depends(require_worker_token),
) -> dict[str, object]:
    try:
        job = fail_clip_job(job_id, worker_id, lease_token, request.error_code)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status}


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
            "aspect_ratio": metadata.get("aspect_ratio", "9:16"),
            "format": metadata.get("format", "vertical_short"),
            "script": job.script_text or "",
            "title": metadata.get("title", job.topic),
            "thumbnail_names": {
                platform: f"{job.id}-{platform}.jpg"
                for platform in (
                    ["youtube", "instagram"]
                    if job.platform == "both"
                    else [job.platform]
                )
            },
            "story_arc": metadata.get("story_arc", ["problem", "stakes", "solution", "how", "payoff"]),
            "storyboard": metadata.get("storyboard", []),
            "caption_cues": metadata.get("caption_cues", []),
            "caption_timing_source": metadata.get("caption_timing_source", "estimated"),
            "audio_plan": metadata.get("audio_plan", {}),
            "watermark": metadata.get("watermark", {}),
            "assets": metadata.get("assets", {}),
            "video_provider": metadata.get("video_provider", "off"),
            "voice_provider": metadata.get("voice_provider", "off"),
        }
    }


@app.get("/api/worker/{job_id}/asset/{asset_name}")
def worker_asset(job_id: str, asset_name: str, _: None = Depends(require_worker_token)) -> FileResponse:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job or job.status != "rendering":
            raise HTTPException(status_code=409, detail="Job is not currently rendering")
        try:
            assets = json.loads(job.metadata_json or "{}").get("assets", {})
        except json.JSONDecodeError:
            assets = {}
    if asset_name not in assets.values() or Path(asset_name).name != asset_name:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = (Path(get_settings().media_root).resolve() / asset_name).resolve()
    if path.parent != Path(get_settings().media_root).resolve() or not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(path)


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
    storage_cap_bytes = max(0, settings.max_storage_gb) * 1024 * 1024 * 1024
    existing_destination_size = destination.stat().st_size if destination.exists() else 0
    existing_total = 0
    for existing_file in media_root.rglob("*"):
        if existing_file.is_file():
            try:
                existing_total += existing_file.stat().st_size
            except OSError:
                continue
    existing_total -= existing_destination_size
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
                if existing_total + total > storage_cap_bytes:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Storage limit exceeded")
                output.write(chunk)
    finally:
        file.file.close()
    if total < 1024:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Media file is empty or invalid")
    return {"render_path": str(destination)}


@app.post("/api/worker/{job_id}/thumbnail/{platform}")
def worker_upload_thumbnail(
    job_id: str,
    platform: str,
    file: UploadFile = File(...),
    _: None = Depends(require_worker_token),
) -> dict[str, str]:
    if platform not in {"youtube", "instagram"}:
        raise HTTPException(status_code=400, detail="Unsupported thumbnail platform")
    if file.content_type not in {"image/jpeg", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Only JPEG thumbnails are accepted")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job or job.status != "rendering":
            raise HTTPException(status_code=409, detail="Job is not currently rendering")
    settings = get_settings()
    media_root = Path(settings.media_root).resolve()
    media_root.mkdir(parents=True, exist_ok=True)
    filename = f"{job_id}-{platform}.jpg"
    destination = (media_root / filename).resolve()
    if destination.parent != media_root:
        raise HTTPException(status_code=400, detail="Invalid thumbnail destination")
    max_bytes = 8 * 1024 * 1024
    try:
        try:
            existing_destination_size = destination.stat().st_size
        except FileNotFoundError:
            existing_destination_size = 0
        existing_total = sum(
            existing_file.stat().st_size
            for existing_file in media_root.rglob("*")
            if existing_file.is_file()
        ) - existing_destination_size
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Media storage is unavailable") from exc
    total = 0
    prefix = b""
    try:
        with destination.open("wb") as output:
            while True:
                chunk = file.file.read(64 * 1024)
                if not chunk:
                    break
                if len(prefix) < 3:
                    prefix = (prefix + chunk)[:3]
                total += len(chunk)
                if total > max_bytes:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Thumbnail exceeds 8 MB")
                if existing_total + total > max(0, settings.max_storage_gb) * 1024 * 1024 * 1024:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Storage limit exceeded")
                output.write(chunk)
    finally:
        file.file.close()
    if total < 1024 or prefix != b"\xff\xd8\xff":
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Thumbnail is not a valid JPEG")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job or job.status != "rendering":
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Job is no longer rendering")
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        thumbnails = metadata.setdefault("thumbnails", {})
        thumbnails[platform] = filename
        job.metadata_json = json.dumps(metadata, ensure_ascii=False)
        session.commit()
    return {"filename": filename}


@app.post("/api/worker/{job_id}/fail")
def worker_fail(job_id: str, worker_id: str, error_message: str, _: None = Depends(require_worker_token)) -> dict[str, object]:
    try:
        job = fail_render_job(job_id, worker_id, error_message)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status, "stage": job.stage}
