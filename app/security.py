import hmac

from fastapi import Header, HTTPException

from .config import get_settings


def require_owner(user_id: int) -> None:
    if user_id not in get_settings().owner_ids:
        raise HTTPException(status_code=403, detail="Telegram user is not authorized")


def require_admin_api_token(token: str | None = Header(default=None, alias="X-Admin-Token")) -> None:
    expected = get_settings().admin_api_token
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def require_worker_token(token: str | None = Header(default=None, alias="X-Worker-Token")) -> None:
    expected = get_settings().worker_token
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid worker token")


def verify_webhook_secret(secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token")) -> None:
    expected = get_settings().telegram_webhook_secret
    if expected and not secret:
        raise HTTPException(status_code=401, detail="Missing webhook secret")
    if expected and not hmac.compare_digest(secret or "", expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
