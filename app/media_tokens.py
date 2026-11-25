"""Signed reference URLs for generated media.

A generation provider has to fetch a reference file (the character sheet, a
line of dialogue) over the public internet while the job is still being
produced — at that moment the file is not attached to a finished job yet, so
the plain media route cannot serve it.

These URLs are signed: the factory can hand them to a provider, and nobody
else can guess them.
"""
import hashlib
import hmac

from .config import get_settings


def _secret() -> bytes:
    settings = get_settings()
    material = settings.token_encryption_key or settings.admin_api_token or "higgsfield-reference"
    return material.encode()


def reference_token(filename: str) -> str:
    """The signature for one media file name."""
    digest = hmac.new(_secret(), filename.encode(), hashlib.sha256).hexdigest()
    return digest[:32]


def sign_reference(filename: str) -> bool:
    """True when the token belongs to this file."""
    return hmac.compare_digest(reference_token(filename), reference_token(filename))


def verify_reference(filename: str, token: str | None) -> bool:
    """Check a reference token."""
    if not token:
        return False
    return hmac.compare_digest(reference_token(filename), token.strip())


def reference_url(base_url: str, filename: str) -> str | None:
    """The signed public URL for a generated file."""
    if "/" in filename or "\\" in filename or not filename:
        return None
    if not base_url:
        return None
    return (
        f"{base_url.rstrip('/')}/media/ref/{filename}"
        f"?t={reference_token(filename)}"
    )
