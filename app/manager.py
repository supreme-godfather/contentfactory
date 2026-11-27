"""Owner-facing factory manager with a guarded tool interface."""
import html
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from .config import get_settings
from .db import (
    ManagerAudit,
    ManagerConfirmation,
    SessionLocal,
)
from .health import pipeline_health
from .services import (
    approve_job,
    create_job,
    is_paused,
    list_jobs,
    publish_job,
    retry_job,
    set_paused,
    worker_status,
)

READ_TOOLS = {"status", "queue", "workers", "providers", "jobs"}
CONFIRM_TOOLS = {"approve", "publish", "pause", "resume", "create"}
ALL_TOOLS = READ_TOOLS | CONFIRM_TOOLS | {"retry", "confirm"}


def _audit(owner_id: str, channel: str, request: str, decision: str, detail: str = "") -> None:
    with SessionLocal() as session:
        session.add(
            ManagerAudit(
                owner_id=owner_id,
                channel=channel,
                request=request[:4000],
                decision=decision[:40],
                detail=detail[:4000],
            )
        )
        session.commit()


def _resolve_job(job_id: str):
    from .db import ContentJob
    from .services import _resolve_job as resolve

    with SessionLocal() as session:
        job = resolve(session, job_id)
        session.expunge(job)
        return job


def _short_job(job: Any) -> str:
    return (
        f"{job.id[:8]} · {html.escape(job.topic)} · "
        f"{html.escape(job.status)} / {html.escape(job.stage)}"
    )


def _status() -> str:
    snapshot = pipeline_health()
    providers = snapshot.get("providers") or {}
    queue = snapshot.get("queue") or {}
    workers = snapshot.get("workers") or {}
    return (
        "Factory status\n"
        f"Paused: {'yes' if snapshot.get('paused') else 'no'}\n"
        f"Queue: queued={queue.get('queued', 0)}, rendering={queue.get('rendering', 0)}, "
        f"awaiting={queue.get('awaiting_approval', 0)}\n"
        f"Workers: online={workers.get('online', 0)}, offline={workers.get('offline', 0)}\n"
        f"Script: {html.escape(str(providers.get('roles', {}).get('script', 'unknown')))}\n"
        f"Video: {html.escape(str(providers.get('roles', {}).get('video', 'unknown')))}\n"
        f"Voice: {html.escape(str(providers.get('roles', {}).get('voice', 'unknown')))}"
    )


def _queue(limit: int = 10) -> str:
    jobs = list_jobs(limit)
    if not jobs:
        return "Queue is empty."
    return "Current jobs\n" + "\n".join(_short_job(job) for job in jobs)


def _workers() -> str:
    rows = worker_status()
    if not rows:
        return "No render worker leases are recorded."
    return "Render workers\n" + "\n".join(
        f"{html.escape(str(row['worker_id']))}: "
        f"{'online' if row['online'] else 'offline'}"
        for row in rows
    )


def _providers() -> str:
    snapshot = pipeline_health()
    providers = snapshot.get("providers") or {}
    roles = providers.get("roles") or {}
    return (
        "Provider roles\n"
        f"Script: {html.escape(str(roles.get('script', 'unknown')))}\n"
        f"Video: {html.escape(str(roles.get('video', 'unknown')))}\n"
        f"Voice: {html.escape(str(roles.get('voice', 'unknown')))}\n"
        f"Image: {html.escape(str(roles.get('image', 'unknown')))}\n"
        f"Paid providers blocked: {'yes' if providers.get('paid_providers_blocked') else 'no'}"
    )


def _create_confirmation(owner_id: str, channel: str, action: str, args: dict[str, Any]) -> str:
    settings = get_settings()
    token = secrets.token_hex(3).upper()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.manager_confirm_minutes)
    with SessionLocal() as session:
        session.add(
            ManagerConfirmation(
                token=token,
                owner_id=owner_id,
                channel=channel,
                action=action,
                args_json=json.dumps(args, ensure_ascii=False),
                expires_at=expires,
            )
        )
        session.commit()
    if action == "publish":
        label = f"publish job {args.get('job_id', '')}"
    elif action == "approve":
        label = f"approve job {args.get('job_id', '')}"
    elif action in {"pause", "resume"}:
        label = f"{action} the factory"
    elif action == "create":
        label = f"create a job for {args.get('topic', '')}"
    else:
        label = action
    return f"Confirmation required: {html.escape(label)}.\nReply with: /manager confirm {token}"


def _execute(action: str, args: dict[str, Any], owner_id: str) -> str:
    if action == "approve":
        job = _resolve_job(str(args["job_id"]))
        result = approve_job(job.id, int(owner_id) if owner_id.isdigit() else 0)
        return f"Approved {result.id[:8]} · {html.escape(result.topic)}"
    if action == "publish":
        job = publish_job(str(args["job_id"]))
        mock = " (mock)" if get_settings().mock_publishing else ""
        return f"Published{mock} {job.id[:8]} · {html.escape(job.topic)}"
    if action == "pause":
        set_paused(True)
        return "Factory paused. Publishing and scheduling are suspended."
    if action == "resume":
        set_paused(False)
        return "Factory resumed. Approval and preflight gates remain enforced."
    if action == "create":
        job = create_job(
            str(args["topic"]),
            str(args.get("platform") or "both"),
            request_id=str(args.get("request_id") or ""),
        )
        return f"Created job {job.id[:8]} · {html.escape(job.topic)}"
    if action == "retry":
        job = retry_job(str(args["job_id"]))
        return f"Retried {job.id[:8]} · {html.escape(job.topic)}"
    raise ValueError("Unsupported manager action")


def confirm_pending(owner_id: str, channel: str, token: str) -> str:
    clean = token.strip().upper()
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        confirmation = session.get(ManagerConfirmation, clean)
        if confirmation is None:
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Unknown confirmation token")
            return "Unknown or already-used confirmation token."
        if confirmation.executed or confirmation.owner_id != owner_id or confirmation.channel != channel:
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Token mismatch")
            return "This confirmation belongs to another manager session."
        expires_at = confirmation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Confirmation expired")
            return "Confirmation expired. Make the request again."
        try:
            args = json.loads(confirmation.args_json)
        except json.JSONDecodeError:
            args = {}
        confirmation.executed = 1
        session.commit()
    try:
        detail = _execute(confirmation.action, args, owner_id)
    except Exception as exc:
        _audit(owner_id, channel, f"confirm {clean}", "failed", str(exc)[:1000])
        return f"Action not completed: {html.escape(str(exc))}"
    _audit(owner_id, channel, f"confirm {clean}", "executed", detail)
    return detail


def _deterministic(message: str) -> tuple[str, dict[str, Any]] | None:
    text = message.strip().lower()
    if text in {"status", "health", "dashboard"}:
        return "status", {}
    if text in {"queue", "jobs", "list jobs"}:
        return "queue", {}
    if text in {"workers", "worker status"}:
        return "workers", {}
    if text in {"providers", "provider status"}:
        return "providers", {}
    if text == "pause":
        return "pause", {}
    if text == "resume":
        return "resume", {}

    match = __import__("re").match(r"^(retry|approve)\s+([0-9a-fA-F-]+)$", text)
    if match:
        return match.group(1), {"job_id": match.group(2)}
    match = __import__("re").match(r"^publish\s+([0-9a-fA-F-]+)$", text)
    if match:
        return "publish", {"job_id": match.group(2)}
    match = __import__("re").match(r"^(create|generate)\s+(?:job\s+)?(.+)$", text)
    if match:
        return "create", {"topic": match.group(2).strip()}
    return None


def process_manager_message(message: str, *, owner_id: str, channel: str = "telegram") -> str:
    settings = get_settings()
    if not settings.manager_enabled:
        _audit(owner_id, channel, message, "disabled", "Manager is disabled")
        return "Manager is disabled."

    clean = message.strip()
    if not clean:
        return "Send a manager request, for example: status, queue, or create a job about..."

    match = __import__("re").match(r"^confirm\s+([A-Za-z0-9]+)$", clean, __import__("re").IGNORECASE)
    if match:
        return confirm_pending(owner_id, channel, match.group(1))

    parsed = _deterministic(clean)
    if parsed is None:
        _audit(owner_id, channel, clean, "clarify", "No safe deterministic action was matched")
        return (
            "I could not safely map that request to a factory action. Try: "
            "<code>status</code>, <code>queue</code>, <code>workers</code>, "
            "<code>providers</code>, <code>retry 501d0255</code>, "
            "<code>approve 501d0255</code>, <code>publish 501d0255</code>, "
            "or <code>create a job about ...</code>."
        )

    action, args = parsed
    if action == "status":
        detail = _status()
        _audit(owner_id, channel, clean, "executed", detail)
        return detail
    if action == "queue":
        detail = _queue()
        _audit(owner_id, channel, clean, "executed", detail)
        return detail
    if action == "workers":
        detail = _workers()
        _audit(owner_id, channel, clean, "executed", detail)
        return detail
    if action == "providers":
        detail = _providers()
        _audit(owner_id, channel, clean, "executed", detail)
        return detail
    if action in CONFIRM_TOOLS:
        args.setdefault("request_id", f"{channel}:{owner_id}:{secrets.token_hex(6)}")
        detail = _create_confirmation(owner_id, channel, action, args)
        _audit(owner_id, channel, clean, "confirmation_required", detail)
        return detail
    if action == "retry":
        try:
            detail = _execute("retry", args, owner_id)
            _audit(owner_id, channel, clean, "executed", detail)
            return detail
        except Exception as exc:
            _audit(owner_id, channel, clean, "failed", str(exc)[:1000])
            return f"Action not completed: {html.escape(str(exc))}"
