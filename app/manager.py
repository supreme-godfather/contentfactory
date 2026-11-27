"""Owner-facing factory manager with a guarded tool interface."""
import html
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update

from .config import get_settings
from .db import (
    ManagerAudit,
    ManagerConfirmation,
    SessionLocal,
)
from .services import (
    approve_job,
    cancel_job,
    create_job,
    is_paused,
    publish_job,
    revise_job,
    retry_job,
    set_paused,
)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_ -]?key|access[_ -]?token|token|password|client[_ -]?secret|signature)\b"
    r"\s*[:=]\s*[\"']?([A-Za-z0-9_./+=:-]{12,})"
)
_LONG_SECRET = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")
_LIKELY_TOKEN = re.compile(
    r"\b(?=[A-Za-z0-9_-]{24,}\b)(?=[A-Za-z0-9_-]*[A-Za-z])"
    r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"
)
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _contains_likely_token(value: str) -> bool:
    return any(not _UUID.fullmatch(match.group()) for match in _LIKELY_TOKEN.finditer(value))


def _audit(owner_id: str, channel: str, request: str, decision: str, detail: str = "") -> None:
    request = _redact_sensitive(request)
    detail = _redact_sensitive(detail)
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


def _redact_sensitive(value: str) -> str:
    value = _SECRET_ASSIGNMENT.sub(r"[REDACTED CREDENTIAL]", value)
    value = _LONG_SECRET.sub("[REDACTED TOKEN]", value)
    return _LIKELY_TOKEN.sub(
        lambda match: match.group() if _UUID.fullmatch(match.group()) else "[REDACTED TOKEN]",
        value,
    )


def _resolve_job(job_id: str):
    from .db import ContentJob
    from .services import _resolve_job as resolve

    with SessionLocal() as session:
        job = resolve(session, job_id)
        session.expunge(job)
        return job


def _create_confirmation(owner_id: str, channel: str, action: str, args: dict[str, Any]) -> str:
    settings = get_settings()
    token = secrets.token_hex(6).upper()
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
    elif action in {"retry", "approve", "publish", "cancel", "revise"}:
        label = f"{action} job {args.get('job_id', '')}"
    elif action == "set_provider_role":
        label = f"set {args.get('role')} provider to {args.get('provider')}"
    elif action in {"set_media_generation", "set_automation", "set_mock_publishing"}:
        setting = {
            "set_media_generation": "AI media generation",
            "set_automation": "factory automation",
            "set_mock_publishing": "mock publishing",
        }[action]
        label = f"{'enable' if args.get('enabled') else 'disable'} {setting}"
    elif action == "set_publish_target":
        label = f"set publishing target to {args.get('target')}"
    elif action == "set_daily_publish_limit":
        label = f"set daily publication limit to {args.get('limit')}"
    elif action == "set_niche":
        label = f"set new-job niche to {args.get('niche')}"
    elif action == "set_format":
        label = f"set new-job format to {args.get('format')}"
    elif action == "set_publishing_enabled":
        label = f"{'enable' if args.get('enabled') else 'disable'} {args.get('platform')} publishing"
    elif action == "delete_provider_credential":
        label = f"remove the stored {args.get('provider')} provider credential"
    elif action == "run_automation":
        label = "run one automation cycle (may create paid generation jobs)"
    elif action == "set_cost_ceiling":
        label = f"set per-video cost ceiling to ${args.get('value')}"
    elif action in {"review_clip_candidate", "promote_clip_candidate"}:
        label = f"{action.replace('_', ' ')} {args.get('candidate_id', '')}"
        if action == "promote_clip_candidate":
            label += f" to an approved {args.get('platform')} job"
    else:
        label = f"{action} with {json.dumps(args, ensure_ascii=False)}"
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
        job = _resolve_job(str(args["job_id"]))
        job = retry_job(job.id)
        return f"Retried {job.id[:8]} · {html.escape(job.topic)}"
    if action == "cancel":
        job = _resolve_job(str(args["job_id"]))
        job = cancel_job(job.id)
        return f"Cancelled {job.id[:8]} · {html.escape(job.topic)}"
    if action == "revise":
        job = _resolve_job(str(args["job_id"]))
        job = revise_job(job.id)
        return f"Queued revision for {job.id[:8]} · {html.escape(job.topic)}"
    if action == "set_provider_role":
        from .provider_credentials import set_provider_for_role

        set_provider_for_role(str(args["role"]), str(args["provider"]))
        return f"Set {html.escape(str(args['role']))} provider to {html.escape(str(args['provider']))}."
    if action == "set_media_generation":
        from .provider_credentials import set_media_generation_enabled

        enabled = bool(args["enabled"])
        set_media_generation_enabled(enabled)
        return f"AI media generation {'enabled' if enabled else 'disabled'}."
    if action == "set_automation":
        from .automation import set_automation_enabled

        enabled = bool(args["enabled"])
        set_automation_enabled(enabled)
        return f"Automation {'enabled' if enabled else 'disabled'}."
    if action == "set_publish_target":
        from .hq import set_publish_target

        target = set_publish_target(str(args["target"]))
        return f"New jobs will target {html.escape(target)}."
    if action == "set_niche":
        from .hq import set_niche

        return f"New jobs will use the {html.escape(set_niche(str(args['niche'])))} niche."
    if action == "set_format":
        from .hq import set_format

        selected = set_format(str(args["format"]))
        return f"New jobs will use {html.escape(str(selected['label']))}."
    if action == "set_daily_publish_limit":
        from .hq import set_daily_publish_limit

        limit = set_daily_publish_limit(int(args["limit"]))
        return f"Daily publication limit set to {limit}."
    if action == "set_mock_publishing":
        from .hq import set_mock_publishing

        enabled = bool(args["enabled"])
        set_mock_publishing(enabled)
        return f"Mock publishing {'enabled' if enabled else 'disabled'}."
    if action == "set_publishing_enabled":
        from .platform_credentials import set_platform_enabled

        platform = str(args["platform"])
        enabled = bool(args["enabled"])
        set_platform_enabled(platform, enabled)
        return f"{platform.capitalize()} publishing {'enabled' if enabled else 'disabled'}."
    if action == "delete_provider_credential":
        from .provider_credentials import delete_provider_secret

        provider = str(args["provider"])
        deleted = delete_provider_secret(provider)
        return (
            f"Removed stored credentials for {html.escape(provider)}."
            if deleted
            else f"No stored credentials found for {html.escape(provider)}."
        )
    if action == "set_cost_ceiling":
        from .hq import set_auto_publish_max_cost_usd

        value = set_auto_publish_max_cost_usd(float(args["value"]))
        return f"Per-video generation cost ceiling set to ${value:.2f}."
    if action == "review_clip_candidate":
        from .clipping import review_clip_candidate

        candidate = review_clip_candidate(
            str(args["candidate_id"]),
            str(args["decision"]),
            int(owner_id) if owner_id.isdigit() else 0,
        )
        return f"Clip candidate {candidate.id[:8]} marked {candidate.status}."
    if action == "promote_clip_candidate":
        from .clipping import promote_clip_candidate

        job = promote_clip_candidate(
            str(args["candidate_id"]),
            str(args["platform"]),
            int(owner_id) if owner_id.isdigit() else 0,
        )
        return (
            f"Promoted clip to approved job {job.id[:8]} · {html.escape(job.topic)}. "
            "Publishing still requires a separate confirmation."
        )
    if action == "run_automation":
        from .automation import run_automation_cycle

        result = run_automation_cycle()
        return "Automation cycle completed: " + html.escape(json.dumps(result, ensure_ascii=False))
    raise ValueError("Unsupported manager action")


def confirm_pending(owner_id: str, channel: str, token: str) -> str:
    clean = token.strip().upper()
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        confirmation = session.get(ManagerConfirmation, clean)
        if confirmation is None:
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Unknown confirmation token")
            return "Unknown or already-used confirmation token."
        if confirmation.owner_id != owner_id or confirmation.channel != channel:
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Token mismatch")
            return "This confirmation belongs to another manager session."
        expires_at = confirmation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if confirmation.executed:
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Token already used")
            return "Unknown or already-used confirmation token."
        if expires_at <= now:
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Confirmation expired")
            return "Confirmation expired. Make the request again."
        try:
            args = json.loads(confirmation.args_json)
        except (json.JSONDecodeError, TypeError):
            _audit(owner_id, channel, f"confirm {clean}", "failed", "Invalid confirmation arguments")
            return "Action not completed: confirmation data is invalid."
        action = confirmation.action
        claimed = session.execute(
            update(ManagerConfirmation)
            .where(
                ManagerConfirmation.token == clean,
                ManagerConfirmation.owner_id == owner_id,
                ManagerConfirmation.channel == channel,
                ManagerConfirmation.executed == 0,
                ManagerConfirmation.expires_at > now,
            )
            .values(executed=1)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            session.rollback()
            _audit(owner_id, channel, f"confirm {clean}", "denied", "Token already used or expired")
            return "Unknown, expired, or already-used confirmation token."
        session.commit()
    try:
        detail = _execute(action, args, owner_id)
    except Exception as exc:
        safe_error = _redact_sensitive(str(exc)[:1000])
        _audit(owner_id, channel, f"confirm {clean}", "failed", safe_error)
        return f"Action not completed: {html.escape(safe_error)}"
    _audit(owner_id, channel, f"confirm {clean}", "executed", detail)
    return detail


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

    if len(clean) > 4000:
        _audit(owner_id, channel, clean[:4000], "rejected", "Request exceeds manager limit")
        return "That request is too long. Please keep it under 4,000 characters."
    if (
        _SECRET_ASSIGNMENT.search(clean)
        or _LONG_SECRET.search(clean)
        or _contains_likely_token(clean)
    ):
        _audit(owner_id, channel, clean, "rejected", "Credential-like text was not sent to MiniMax")
        return "For safety, BANTI does not accept API keys or passwords in chat. Add provider credentials through the secure provider setup instead."
    _audit(owner_id, channel, clean, "received")
    try:
        from .manager_brain import process_with_minimax

        return process_with_minimax(clean, owner_id=owner_id, channel=channel)
    except Exception as exc:
        _audit(owner_id, channel, clean, "failed", type(exc).__name__)
        return "BANTI could not complete that request. Check the manager configuration and try again."
