"""Owner-supplied script, voice-over and cover assets for a single job.

These flows never spend provider credits and never touch another job. Every
upload is validated by content, stored inside the media root with a
job-scoped name, and recorded on the job so the renderer and publisher use it.
"""
import json
from pathlib import Path
from typing import Any

from .job_inputs import set_custom_cover, set_custom_script, set_custom_voiceover

MAX_SCRIPT_CHARS = 8000
MAX_VOICE_BYTES = 25 * 1024 * 1024
MAX_COVER_BYTES = 12 * 1024 * 1024
MAX_COVER_EDGE = 4096


def _is_mp3(data: bytes) -> bool:
    if data[:3] == b"ID3":
        return True
    if data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3" or data[:2] == b"\xff\xf2":
        return True
    return data[4:8] == b"ftyp" and b"M4A" in data[4:12]


def _is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


def save_custom_voice(media_root: Path, job_id: str, data: bytes) -> str:
    """Store an owner voice-over and return its file name."""
    if not data or len(data) > MAX_VOICE_BYTES:
        raise ValueError("Voice-over upload must be between 1 byte and 25 MB")
    if not _is_mp3(data):
        raise ValueError("Voice-over must be an MP3, M4A or AAC file")
    media_root.mkdir(parents=True, exist_ok=True)
    filename = f"{job_id}-custom-voice.mp3"
    (media_root / filename).write_bytes(data)
    return filename


def save_custom_cover(media_root: Path, job_id: str, platform: str, data: bytes) -> str:
    """Store an owner-designed cover and return its file name."""
    if platform not in {"youtube", "instagram"}:
        raise ValueError("Unsupported cover platform")
    if not data or len(data) > MAX_COVER_BYTES:
        raise ValueError("Cover upload must be between 1 byte and 12 MB")
    if not _is_jpeg(data):
        raise ValueError("Cover must be a JPEG image")
    media_root.mkdir(parents=True, exist_ok=True)
    filename = f"{job_id}-cover-{platform}.jpg"
    (media_root / filename).write_bytes(data)
    return filename


def apply_custom_script(job_id: str, version: int, text: str):
    return set_custom_script(job_id, version, text)


def apply_custom_voiceover(job_id: str, version: int, filename: str):
    return set_custom_voiceover(job_id, version, filename)


def apply_custom_cover(job_id: str, platform: str, filename: str):
    return set_custom_cover(job_id, platform, filename)


def owner_cover_names(job: Any) -> list[str]:
    """Cover files the owner supplied for a job, if any."""
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(metadata, dict):
        return []
    covers = metadata.get("thumbnails")
    if not isinstance(covers, dict):
        return []
    names = []
    for name in covers.values():
        if isinstance(name, str) and Path(name).name == name:
            names.append(name)
    return names
