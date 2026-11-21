import binascii

from cryptography.fernet import Fernet

from .config import get_settings


def get_fernet() -> Fernet:
    key = get_settings().token_encryption_key.strip()
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from exc


def validate_youtube_encryption_configuration() -> None:
    settings = get_settings()
    if settings.youtube_enabled or settings.youtube_client_id or settings.youtube_client_secret:
        get_fernet()
